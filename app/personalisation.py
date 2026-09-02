"""
What we hold about a contact that can honestly personalise a message to them.

The outreach sequence asks for two things a person then has to write: a comment
on one of their posts, and an email. Both are only worth sending if they read as
though the sender knows who they are writing to — and that is the one thing this
app must not fake, because a message assembled from a job title says nothing a
mail merge couldn't. So the evidence is counted before anything is generated,
and when it isn't there the panel says so instead of producing a draft that
sounds personal and isn't.

Signals are tiered by whose fact they are, the same distinction
opportunities.py draws:

  said     Their own posts, interest chips and focus line. Strongest — they
           raised it themselves.
  company  Their employer's researched description, industry and keywords. True
           of the employer, which is what makes a message company-specific
           rather than generic.
  web      Corroborated web findings: third parties writing about them. Only
           corroborated ones, for the reason research._corroborate exists — an
           uncorroborated row may be a namesake, and a message built on a
           stranger's conference talk is worse than a shorter message.
  role     Title, seniority, department. From the CSV, so solid about what they
           own, but a role is not a person.

Hence the one rule this module exists to enforce: `role` alone is never enough.
Everyone on the list has a designation, so a draft grounded in nothing else
would be available for all of them and personal to none — confidently
impersonal, and going out under the user's own name.
"""

TIER_SAID = "said"
TIER_COMPANY = "company"
TIER_WEB = "web"
TIER_ROLE = "role"

# Whose fact this is, and how the panel words it.
TIER_LABELS = {
    TIER_SAID: "their own words",
    TIER_COMPANY: "their employer",
    TIER_WEB: "written about them",
    TIER_ROLE: "their role on file",
}

# Order signals are offered to the model in: what the person said first, so a
# draft leans on that rather than on the employer's marketing copy.
TIER_ORDER = {TIER_SAID: 0, TIER_COMPANY: 1, TIER_WEB: 2, TIER_ROLE: 3}

# How many signals from outside `role` a message has to stand on. Two, matching
# interests.MIN_EVIDENCE: one fact is thin ground for a paragraph, and the
# second is what lets a draft connect two things a stranger could not.
MIN_PERSONAL_SIGNALS = 2

# The kinds of message this module will vouch for. Anything else is a caller
# bug, and assess() says so rather than guessing.
KINDS = ("comment", "email")


def _company_name(person):
    return (person.company.name if person.company else None) or "their employer"


def _activity_signals(person):
    """Posts of theirs carrying text worth referencing.

    Trusted rows only. A searched row that could not be tied to this contact
    may be a namesake's, and models.LinkedInActivity.trusted is the same gate
    interests.py uses — a row good enough to display is not automatically good
    enough to write from.
    """
    from .messages import substance_of

    out = []
    for activity in person.activities:
        if not activity.trusted:
            continue
        body = substance_of(activity.text)
        if not body:
            continue
        when = (activity.activity_date.isoformat()
                if activity.activity_date else "date unknown")
        out.append({
            "tier": TIER_SAID,
            "label": f"their {activity.type_label.lower()} of {when}",
            "text": body,
            "url": activity.url,
            "activity_id": activity.id,
            "words": len(body.split()),
        })
    return out


def _said_signals(person):
    """Their posts, plus the two derived things that are still their words."""
    out = _activity_signals(person)
    if person.focus_line:
        out.append({
            "tier": TIER_SAID,
            "label": "their current focus",
            "text": person.focus_line,
            "url": None,
        })
    chips = [i.label for i in person.interests if i.label]
    if chips:
        out.append({
            "tier": TIER_SAID,
            "label": "their interests",
            "text": ", ".join(chips),
            "url": None,
        })
    return out


def _company_signals(person):
    company = person.company
    if not company:
        return []
    out = []
    if company.description:
        out.append({
            "tier": TIER_COMPANY,
            "label": f"what {company.name} does",
            "text": company.clean_description or company.description,
            "url": company.description_source_url,
        })
    if company.industry:
        out.append({
            "tier": TIER_COMPANY,
            "label": f"{company.name}'s industry",
            "text": company.industry,
            "url": None,
        })
    if company.keywords:
        out.append({
            "tier": TIER_COMPANY,
            "label": f"{company.name}'s keywords",
            "text": company.keywords,
            "url": None,
        })
    return out


def _web_signals(person):
    out = []
    for finding in person.findings:
        # Same rule as interests._build_evidence: an uncorroborated finding may
        # be about a namesake, and a message referencing it would be
        # confidently about somebody else.
        if not finding.corroborated:
            continue
        text = f"{finding.title} — {finding.clean_snippet or ''}".strip(" —")
        if not text:
            continue
        out.append({
            "tier": TIER_WEB,
            "label": f"web: {finding.title}"[:80],
            "text": text,
            "url": finding.url,
        })
    return out


def _role_signals(person):
    bits = [b for b in (person.title, person.seniority, person.department) if b]
    if not bits:
        return []
    return [{
        "tier": TIER_ROLE,
        "label": "their role on file",
        "text": " · ".join(bits) + f" at {_company_name(person)}",
        "url": None,
    }]


def signals(person):
    """Everything usable about this contact, strongest tier first."""
    out = (_said_signals(person) + _company_signals(person)
           + _web_signals(person) + _role_signals(person))
    out.sort(key=lambda s: TIER_ORDER[s["tier"]])
    return out


def personal_signals(person):
    """The signals that say something about *this* person or their employer.

    `role` is excluded on purpose — see the module docstring. It is still given
    to the model as context; it just cannot be the reason a draft is allowed.
    """
    return [s for s in signals(person) if s["tier"] != TIER_ROLE]


def comment_target(person, prefer_index=0):
    """The post a comment should reply to, or None.

    Ordered by how much there is to reference, then by recency: a post with 60
    words of the person's own argument makes a better comment than a two-line
    one published yesterday. `prefer_index` lets the second comment step pick
    the next post down rather than commenting twice on the same one.
    """
    rows = _activity_signals(person)
    if not rows:
        return None
    rows.sort(key=lambda s: -s["words"])
    if prefer_index < len(rows):
        return rows[prefer_index]
    return rows[0]


# What would fix an unpersonalisable contact. Keyed so the template can link to
# the right page rather than telling the reader to go and find it.
FIX_RESEARCH = "research"
FIX_PASTE = "paste"
FIX_COMPANY = "company"

FIX_LABELS = {
    FIX_RESEARCH: "Run research on them — it fills the company description, "
                  "their posts and their interest chips in one pass.",
    FIX_PASTE: "Paste one of their posts by hand, with its text. A link on its "
               "own carries no words to reply to.",
    FIX_COMPANY: "Research their company, so at least the employer's own "
                 "description is available to write from.",
}


def assess(person, kind, prefer_index=0):
    """Whether a `kind` of message ("comment" | "email") can be personalised.

    Returns a verdict the panel renders either way: `ok` with the evidence a
    draft may use, or `ok=False` with what is missing and what would fix it.
    Never raises — an unpersonalisable contact is an expected outcome on a list
    where most people have thin panels, not an error.
    """
    every = signals(person)
    personal = [s for s in every if s["tier"] != TIER_ROLE]
    verdict = {
        "kind": kind,
        "ok": False,
        "signals": every,
        "personal": personal,
        "target": None,
        "reason": None,
        "missing": [],
        "fixes": [],
    }

    # Anything but the two known kinds fell through to the email branch and
    # could come back draftable, which made a typo in a caller look like a
    # working feature.
    if kind not in KINDS:
        verdict["reason"] = (f"{kind!r} is not a kind of message this drafts — "
                             f"expected one of {', '.join(sorted(KINDS))}")
        return verdict

    if kind == "comment":
        target = comment_target(person, prefer_index=prefer_index)
        verdict["target"] = target
        if not target:
            has_rows = bool(person.activities)
            verdict["reason"] = (
                "No post of theirs carries any text to reply to, so a comment "
                "here could only be about nothing."
                if has_rows else
                "We hold no LinkedIn post of theirs, so there is nothing to "
                "comment on."
            )
            verdict["missing"] = ["a post of theirs, with its text"]
            verdict["fixes"] = ([FIX_PASTE] if has_rows
                                else [FIX_RESEARCH, FIX_PASTE])
            return verdict
        # The post itself is the personalisation: a comment replies to it, so
        # one strong signal is enough where an email needs two.
        verdict["ok"] = True
        return verdict

    # ---- email
    if len(personal) < MIN_PERSONAL_SIGNALS:
        held = ", ".join(s["label"] for s in personal) or "nothing"
        verdict["reason"] = (
            f"We hold {len(personal)} of the {MIN_PERSONAL_SIGNALS} things an "
            f"email needs to sound like it knows them ({held}). Everything else "
            "on this record is their job title, which every contact on the list "
            "has — an email written from it would read as a template."
        )
        if not any(s["tier"] == TIER_SAID for s in personal):
            verdict["missing"].append("anything in their own words — a post, "
                                      "an interest chip or a focus line")
        if not any(s["tier"] == TIER_COMPANY for s in personal):
            verdict["missing"].append(f"a description of {_company_name(person)}")
        if not any(s["tier"] == TIER_WEB for s in personal):
            verdict["missing"].append("any corroborated web finding about them")
        verdict["fixes"] = [FIX_RESEARCH, FIX_PASTE]
        return verdict

    verdict["ok"] = True
    return verdict
