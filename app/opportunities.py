"""
Opportunities for Screwdriver: what we can offer the contact on screen.

Screwdriver's four capabilities are matched against signals already on the
record. Nothing is generated and nothing is guessed — a capability appears only
when a term for it is actually present in the person's own words, their
designation, or their employer's record, and the row names the exact field it
fired on and quotes the words around the match. An unmatched capability is left
out rather than padded in, for the same reason interest chips return empty on
thin evidence: a plausible-looking pitch nobody can trace is worse than a
shorter list.

Matching is per field, never against a merged blob of everything. An earlier
version concatenated a contact's industry, description and CSV keywords and
searched that, which found the right term but then cited the wrong source —
"Hibernia College works in this space — e-learning" for a term that had in fact
matched in the keywords column. A citation that points at the wrong field is
worse than no citation, because it looks checkable and isn't.

Three tiers, strongest first, because they are not equally about the person:

  said     Their own posts, interests or focus line. They raised it themselves,
           so it is the only tier that reflects this person's stated priorities.
  role     Their designation and department. From the CSV, so it is solid about
           what they own, but a job description is not an intent.
  company  Their employer's industry, description and keywords. Weakest: true of
           the employer, not necessarily of the person reading the pitch.

The tier is shown on each row, so a reader can tell "they said this" from "their
employer is in this sector" without opening anything.
"""
import re

# Tier label, and the caveat the panel shows for it.
TIERS = {
    "said": ("they said this",
             "matched in this person's own posts, interests or focus line"),
    "role": ("their role",
             "matched in the designation and department on file"),
    "company": ("their employer",
                "matched in the company record — true of the employer, "
                "not necessarily of this person"),
}
TIER_ORDER = {"said": 0, "role": 1, "company": 2}

# Screwdriver's capabilities, and the terms that indicate a contact has the
# problem each one solves. Terms match on word boundaries: "ai" must be the word
# "ai", or it fires on "said", "email" and "training".
CAPABILITIES = (
    {
        "key": "ai",
        "label": "AI & Automation",
        "offer": ("AI-assisted content pipelines for {company} — auto-tagging, "
                  "transcription and repurposing of the library they already "
                  "own, so the team ships more without adding headcount."),
        "terms": ("ai", "artificial intelligence", "machine learning", "ml",
                  "automation", "automate", "automated", "agentic", "llm",
                  "genai", "generative", "chatbot", "copilot"),
    },
    {
        "key": "video",
        "label": "Video & Multimedia",
        "offer": ("Video and multimedia production capacity for {company} — "
                  "scripted, edited and versioned for their channels, without "
                  "them standing up an in-house studio."),
        "terms": ("video", "videos", "videographer", "videography", "multimedia",
                  "podcast", "podcasts", "film", "animation", "animated",
                  "audio", "webinar", "webinars", "youtube", "streaming"),
    },
    {
        "key": "software",
        "label": "Custom Software",
        "offer": ("Custom platform and integration work for {company} — the "
                  "portals, internal tools and integrations that sit between "
                  "their content and the people meant to use it."),
        "terms": ("platform", "platforms", "software", "engineering",
                  "technology", "technical", "integration", "integrations",
                  "lms", "portal", "api", "digital transformation",
                  "infrastructure", "saas"),
    },
    {
        "key": "learning",
        "label": "Learning Design",
        "offer": ("Course and curriculum build-out for {company} — instructional "
                  "design and production for their programmes, from outline to "
                  "published module."),
        "terms": ("learning", "e-learning", "elearning", "course", "courses",
                  "curriculum", "training", "certification", "instructional",
                  "education", "educational", "academy", "teaching", "teach",
                  "pedagogy", "onboarding", "upskilling"),
    },
)

# How the citation is worded per tier. Kept beside the tiers rather than in the
# template so the wording and the evidence stay in one place.
PHRASING = {
    "said": "They raised it themselves — {where} reads “{quote}”",
    "role": "It sits in their remit — {where} is “{quote}”",
    "company": "Context at their employer — {where} reads “{quote}”",
}

_SEPARATORS = r"[\s/\-_]+"


def _term_pattern(term):
    """A term as a regex tolerant of the separators real data uses.

    "e-learning" has to match "e-learning", "e learning" and "E/Learning", and
    "digital transformation" has to survive a line break. Building the pattern
    once means the search that finds a term and the excerpt that shows it agree
    on what matched — they used to disagree.
    """
    words = [re.escape(w) for w in re.split(_SEPARATORS, term.lower()) if w]
    return r"\b" + _SEPARATORS.join(words) + r"\b"


def _matched_term(terms, text):
    """The first term present in `text` as a whole word, or None."""
    if not text:
        return None
    for term in terms:
        if re.search(_term_pattern(term), text, re.I):
            return term
    return None


def _excerpt(text, term, width=130):
    """`text` around the match, so the reader sees the word that fired.

    Truncating from the start would routinely cut the match off — the term that
    justified the row would be missing from the quote meant to justify it.
    """
    flat = " ".join((text or "").split())
    if len(flat) <= width:
        return flat
    m = re.search(_term_pattern(term), flat, re.I)
    if not m:
        return flat[:width].rsplit(" ", 1)[0] + "…"
    start = max(0, m.start() - width // 3)
    end = min(len(flat), start + width)
    out = flat[start:end]
    if start:
        out = "…" + out.split(" ", 1)[-1] if " " in out else "…" + out
    if end < len(flat):
        out = (out.rsplit(" ", 1)[0] if " " in out else out) + "…"
    return out


def _said_signals(person):
    """What this person themselves said, each piece labelled with its source.

    Only trusted activity rows: an unattributed search row may be a namesake's,
    and a pitch built on a stranger's post is the failure this app guards
    against everywhere else.
    """
    out = []
    if (person.focus_line or "").strip():
        out.append(("their focus line", person.focus_line))
    for interest in person.interests:
        if (interest.label or "").strip():
            out.append(("an interest chip", interest.label))
    for activity in person.activities:
        if activity.trusted and (activity.text or "").strip():
            out.append((f"a LinkedIn {activity.type_label.lower()}", activity.text))
    return out


def _role_signals(person):
    out = []
    if (person.title or "").strip():
        out.append(("their designation", person.title))
    if (person.department or "").strip():
        out.append(("their department", person.department))
    if (person.sub_department or "").strip():
        out.append(("their sub-department", person.sub_department))
    return out


def _company_signals(person):
    company = person.company
    if not company:
        return []
    name = company.name or "their employer"
    out = []
    if (company.industry or "").strip():
        out.append((f"{name}'s industry", company.industry))
    if (company.clean_description or "").strip():
        out.append((f"what {name} does", company.clean_description))
    if (company.keywords or "").strip():
        out.append((f"{name}'s keywords", company.keywords))
    return out


def _match(signals, terms):
    """(term, where, quote) for the first field that fires, or None."""
    for where, text in signals:
        term = _matched_term(terms, text)
        if term:
            return term, where, _excerpt(text, term)
    return None


def for_person(person):
    """Ranked opportunities for this contact. Possibly empty, deliberately.

    Each row carries the tier that fired, the term that matched, the field it
    matched in and the words around it, so every pitch on the page traces back
    to something on the record.
    """
    company_name = (person.company.name if person.company else None) or "their team"
    tiers = (
        ("said", _said_signals(person)),
        ("role", _role_signals(person)),
        ("company", _company_signals(person)),
    )

    rows = []
    for order, capability in enumerate(CAPABILITIES):
        for tier, signals in tiers:
            found = _match(signals, capability["terms"])
            if not found:
                continue
            term, where, quote = found
            rows.append(dict(
                capability,
                offer=capability["offer"].format(company=company_name),
                tier=tier,
                tier_label=TIERS[tier][0],
                tier_note=TIERS[tier][1],
                term=term,
                where=where,
                quote=quote,
                because=PHRASING[tier].format(where=where, quote=quote),
                order=order,
            ))
            break   # strongest tier wins; don't list the same capability twice

    rows.sort(key=lambda r: (TIER_ORDER[r["tier"]], r["order"]))
    return rows
