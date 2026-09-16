"""
The Screwdriver outreach email, assembled from the approved skeleton.

Five blocks in a fixed order — hook, solution, about us, portfolio, close —
of which only the first two vary. That split is the whole point of the
template, and it is enforced structurally here rather than asked for in a
prompt:

    hook       vertical-specific, merges the lead's own facts
    solution   vertical-specific, fixed wording per vertical
    about us   one paragraph, identical for every lead
    portfolio  one paragraph, identical for every lead
    close      identical, with the company name merged in

"About Us and Portfolio blocks should NOT be regenerated per lead — keep them
fixed for brand consistency across all outreach." A language model asked to
write a whole email cannot honour that: it paraphrases, and the brand paragraph
drifts a little on every send until no two are the same. So the email is
**composed, not generated** — the same decision summary.py makes for the
profile paragraph, for the same reason. Nothing here calls an LLM.

What varies per lead is therefore only what the record actually supplies: the
first name, the company, the vertical, and the recent news that opens the hook.
Where one of those is missing the email is not written at all and the panel
says which — an email whose first line is "Saw ——'s ——" is worse than no
email.
"""
import re

# --------------------------------------------------------------- the blocks
#
# Verbatim from the template. Edits here change every future draft, which is
# the intent: this is the one place the wording lives.

VERTICALS = {
    "medical": {
        "label": "Medical / Life Sciences",
        # "— congratulations" only makes sense about something that just
        # happened, so it rides with the news opener rather than the insight.
        "congrats": True,
        "insight": (
            "Products like this usually have one problem: the science that "
            "makes them work is invisible. It's buried in a cell, a molecule, "
            "a mechanism no slide deck can really show."
        ),
        "solution": (
            "That's exactly what we solve. We make the invisible visible — the "
            "way a molecule binds, the way a device moves through the body, "
            "the way a therapy works, before a single word is spoken. "
            "Regulatory-aware, scientifically precise."
        ),
        "subjects": [
            "Making the invisible, visible for {company}",
            "A question about {company}'s MOA content",
        ],
    },
    "engineering": {
        "label": "Engineering / Industrial",
        "congrats": False,
        "insight": (
            "Products like yours usually face the same challenge — every unit "
            "looks the same on the outside, and the real advantage is sealed "
            "inside, hard to prove at a glance."
        ),
        "solution": (
            "That's exactly what we solve. We make the hidden obvious — the "
            "way the mechanism holds, the way the tool performs, the way it "
            "outlasts the one beside it. Technically accurate, true to spec."
        ),
        "subjects": [
            "Showing what's hidden inside {company}'s product",
            "A quick thought on {company}'s product demos",
        ],
    },
    "edtech": {
        "label": "EdTech",
        "congrats": False,
        "insight": (
            "Abstract concepts are always the hardest sell in edtech — "
            "learners scroll past what they can't quickly picture."
        ),
        "solution": (
            "That's exactly what we solve. We make the idea click — the way a "
            "formula comes alive, the way a concept lands and finally stays. "
            "Engaging, age-right, built to be remembered."
        ),
        "subjects": [
            "Making the hard concept the one students replay",
            "A question about {company}'s content engagement",
        ],
    },
}

# Common to every lead, and deliberately not parameterised beyond the company
# name the template itself merges.
ABOUT_US = (
    "We're Screwdriver — an animation, post-production and localisation "
    "studio, AI-accelerated, delivering global quality at Indian value. 15 "
    "years in production. We're not tied to any one tool or technique; we're "
    "wired to solve, in 2D, in 3D, in VFX, in post, whatever the story needs."
)

PORTFOLIO = (
    "A quick look at our work: screwdriverlabs.com Happy to share "
    "vertical-specific samples relevant to {company} if useful."
)

CLOSE = (
    "Would love a quick call, no pitch, just to understand where {company} is "
    "headed and whether there's a fit."
)

# The list's categories are broader than the three verticals the template
# covers. A contact in Finance or Retail has no approved wording, and inventing
# some would be the one thing this module exists to prevent.
CATEGORY_TO_VERTICAL = {
    "education": "edtech",
    "medical": "medical",
    "engineering": "engineering",
}


def vertical_for(person):
    """(key, spec) for this contact's vertical, or (None, None)."""
    from . import segments
    key = CATEGORY_TO_VERTICAL.get(segments.category_of(person)[0])
    return (key, VERTICALS[key]) if key else (None, None)


# ------------------------------------------------------------- recent news
#
# {{recent_news}} is described in the template as a "scraped/researched
# trigger", and that is exactly what a company web finding is: a headline about
# the employer, already confirmed as theirs and carrying its source. Using
# anything else would put an unsourced claim in the first line of a cold email.

# The hook reads "Saw {company}'s {news} — congratulations", so the slot needs
# an EVENT, phrased as a noun: "recent product launch", "new market entry". Two
# consequences, both enforced below rather than hoped for.
#
# Most company findings are not events. Of the seventeen on the current list,
# sixteen are About pages, careers pages and directory profiles — "Saw Red
# Nucleus's About us — congratulations" is the sentence that gets when anything
# is accepted. So only a finding that is actually news qualifies.
#
# And a headline is a sentence, not a noun phrase. "Axonify launches Checkpoint"
# has to become "launch of Checkpoint" or the hook is ungrammatical. That is a
# rewrite, so it is done by a fixed table of verb forms rather than by a model:
# a headline whose verb is not in the table produces nothing, which is the safe
# direction to fail in.

_EVENT_VERBS = [
    (r"launches\s+", "launch of "),
    (r"launched\s+", "launch of "),
    (r"unveils\s+", "unveiling of "),
    (r"announces\s+", "announcement of "),
    (r"announced\s+", "announcement of "),
    (r"introduces\s+", "introduction of "),
    (r"releases\s+", "release of "),
    (r"acquires\s+", "acquisition of "),
    (r"partners\s+with\s+", "partnership with "),
    (r"expands\s+into\s+", "expansion into "),
    (r"expands\s+to\s+", "expansion into "),
    (r"expands\s+", "expansion in "),
    (r"opens\s+", "opening of "),
    (r"raises\s+", "raise of "),
    (r"wins\s+", "win of "),
    (r"named\s+", "recognition as "),
]

# Angles that look for something happening, as opposed to who a company is.
_EVENT_ANGLE = ("announces", "news")


def _is_event(finding):
    """Whether this finding is an event we could congratulate someone on."""
    if (finding.kind or "") == "news":
        return True
    query = (finding.source_query or "").lower()
    if not any(a in query for a in _EVENT_ANGLE):
        return False
    return any(re.search(pat, (finding.title or ""), re.I)
               for pat, _ in _EVENT_VERBS)


def _news_phrase(title, company_name):
    """A headline as a noun phrase, or None if it is not an event.

    The company's own name is dropped where it opens the line — "Saw Axonify's
    Axonify launches..." reads as a mistake — and the headline is cut at the
    first clause break, because a hook wants the event and not the subtitle.
    """
    text = " ".join((title or "").split())
    text = re.split(r"\s*[;:|]\s*|\s+[-–—]\s+", text)[0]
    if company_name:
        text = re.sub(r"^%s(?:'s|’s)?\s+" % re.escape(company_name), "",
                      text, flags=re.I).strip()
    for pattern, noun in _EVENT_VERBS:
        m = re.match(pattern, text, re.I)
        if m:
            rest = text[m.end():].strip().rstrip(".")
            return (noun + rest).strip() if rest else None
    return None


def recent_news_for(person):
    """(phrase, url) from the company's own web research, or (None, None).

    Only a confirmed company finding is used: it is already tied to this
    employer and carries its source, which is what keeps an unsourced claim out
    of the first line of a cold email.
    """
    company = person.company
    if not company or not company.findings:
        return None, None
    for finding in company.findings:
        if not _is_event(finding):
            continue
        phrase = _news_phrase(finding.title, company.name)
        if phrase:
            return phrase, finding.url
    return None, None


# ------------------------------------------------------------- the opener
#
# The template describes the hook as a "personalized opener referencing lead's
# company/persona/recent news/pain point" — four possible sources, not one. The
# worked example uses recent news because that is the strongest, but insisting
# on it left thirty-four of thirty-seven contacts with no email at all, which
# is not what a skeleton meant to be filled per lead should do.
#
# So the opener is resolved down a chain, strongest first, and every rung is
# something the app already researched and can show a source for. The panel
# names which rung filled it, so a reader can see whether the email opens on an
# announcement or on the contact's own words.

OPENERS = {
    "news": "{lead} {company}'s {trigger}{congrats}.",
    # Their own sentence, quoted rather than summarised: a paraphrase of
    # someone's post is a small invention, and this is the first line they read.
    "post": '{lead} your post — "{trigger}".',
    "company": "{lead} {company} — {trigger}.",
}

OPENER_LABELS = {
    "news": "a company announcement",
    "post": "their own LinkedIn post",
    "company": "the company research",
}


def _first_sentence(text, limit=160):
    """The opening sentence, trimmed at a real boundary — never rewritten.

    It must end somewhere a sentence can end, because the opener puts a full
    stop after it: cutting at a character count produced "a pioneer in making
    life-changing higher.", which reads as a finished thought and is not one.
    A sentence break is preferred, a clause break accepted, and anything else
    refused so the chain falls through to the next rung instead.
    """
    flat = " ".join((text or "").split())
    if not flat:
        return None
    m = re.match(r"(.{20,%d}?[.!?])(?:\s|$)" % limit, flat)
    if m:
        return m.group(1).strip().rstrip(".").strip() or None
    if len(flat) <= limit:
        return flat.rstrip(".").strip() or None
    clause = flat[:limit].rsplit(",", 1)[0].strip()
    return clause.rstrip(",.").strip() if len(clause) >= 30 else None


def _names(text, person):
    """Whether this text carries the contact's own name."""
    low = " ".join((text or "").lower().split())
    parts = [p for p in ((person.first_name or ""), (person.last_name or ""))
             if len(p or "") > 2]
    return bool(parts) and all(p.lower() in low for p in parts)


def opener_for(person):
    """(kind, trigger phrase, source url) for the hook, or (None, None, None).

    Strongest first: an announcement about the company, then something the
    contact said themselves, then what research established the company does.
    """
    news, url = recent_news_for(person)
    if news:
        return "news", news, url

    # has_substance, not just substance_of: most indexed snippets are the
    # contact's own headline rather than the post, and quoting one back at them
    # reads as "Saw your post — [their job title]". The same floor the comment
    # drafting uses, for the same reason.
    from .messages import has_substance, substance_of
    for activity in person.activities:
        if not (activity.trusted and has_substance(activity.text)):
            continue
        said = _first_sentence(substance_of(activity.text))
        # A post does not open by naming its own author. Where the quote
        # carries the contact's name it is the profile block the index
        # captured, not the post — and that is true whatever language the
        # furniture is in, which a fragment list cannot promise.
        if said and not _names(said, person):
            return "post", said, activity.url

    company = person.company
    if company and company.description:
        what = _first_sentence(company.clean_description or company.description)
        if what:
            return "company", what, company.description_source_url
    return None, None, None


# ------------------------------------------------------- tone and length
#
# Both adjust only what the template says is per-lead. The brand blocks are
# never reworded — that is the one thing the template asks for explicitly — so
# tone moves the greeting, the verb the hook opens with, and the sign-off,
# while length decides which approved blocks are included at all.
#
# Deterministic, because a model asked to "make this warmer" rewrites whatever
# it likes, including the paragraph that must not change. Here the fixed blocks
# are string constants: they cannot drift however the controls are set.

TONES = {
    "warm": {
        "label": "Warm",
        "note": "peer to peer",
        "greeting": "Hi {first},",
        "leads": {"news": "Saw", "post": "Saw", "company": "Been reading about"},
        "signoff": "Best,",
    },
    "direct": {
        "label": "Direct",
        "note": "straight to the point",
        "greeting": "Hi {first},",
        "leads": {"news": "Saw", "post": "Saw", "company": "Been looking at"},
        "signoff": None,
    },
    "formal": {
        "label": "Formal",
        "note": "measured, for senior contacts",
        "greeting": "Dear {first},",
        "leads": {"news": "I noticed", "post": "I read", "company": "I have been reading about"},
        "signoff": "Kind regards,",
    },
}
DEFAULT_TONE = "warm"

# Which blocks a length includes. Dropping a block is not editing it: the
# wording of whatever survives is still exactly the approved wording.
LENGTHS = {
    "short": {
        "label": "Short",
        "note": "drops about us and portfolio — lands in the sweet spot",
        "blocks": ("hook", "solution", "close"),
    },
    "brief": {
        "label": "Brief",
        "note": "the hook and the ask, nothing else",
        "blocks": ("hook", "close"),
    },
    "full": {
        "label": "Full",
        "note": "all five blocks — past the length most replies stop at",
        "blocks": ("hook", "solution", "about", "portfolio", "close"),
    },
}

# Full by default. The widely quoted "50-125 words, peak at 75-100" comes from
# high-volume campaigns optimised for clicks, and it is sound for that. This app
# is the other case: one contact at a time, researched first, and the goal is a
# reply rather than a click. The guidance for that regime is 120-180 words, and
# explicitly so where the buyer faces regulation or a high switching cost —
# which is Medical and Life Sciences exactly. The approved skeleton assembles to
# about 155 words, inside that band by design.
#
# Short and Brief stay, because a lighter first touch is a real choice. What
# changed is which one is assumed: the band a draft lands in is now reported
# against the regime this tool actually operates in, not against a benchmark
# drawn from mass sending.
DEFAULT_LENGTH = "full"

# Two regimes, because one set of numbers cannot describe both. The verdicts
# below are for reply-goal outreach; the volume figures are kept in the reasons
# so the difference is visible rather than asserted.
WORD_BANDS = (
    (0, 49, "very short", "under 50 words — fine as a nudge, thin for a first "
                          "approach that has to explain anything"),
    (50, 74, "lean", "50-125 is where high-volume campaigns peak; short for a "
                     "researched first touch"),
    (75, 119, "lean", "the volume-campaign sweet spot at 75-100, and still "
                      "short of the 120-180 a reply-goal email supports"),
    (120, 180, "the reply-goal band", "120-180 words — what the guidance "
                                      "recommends when the goal is a reply "
                                      "rather than a click, and where a "
                                      "regulated or complex sale needs room"),
    (181, 250, "getting long", "past 180, where the value has to justify every "
                               "extra line"),
    (251, 10**6, "too long", "beyond 250 words a cold email is rarely read to "
                             "the end"),
)


def band_for(words):
    """(verdict, why) for a word count."""
    for low, high, verdict, why in WORD_BANDS:
        if low <= words <= high:
            return verdict, why
    return "unknown", ""


# Subject lines: 35-50 characters, and 4-7 words. Mobile clients cut at about
# 40, so a longer line is not merely wordy — the end of it is never seen.
SUBJECT_CHARS = (35, 50)
SUBJECT_MOBILE_CUT = 40


def subject_verdict(line):
    """(chars, words, ok, note) for one subject line."""
    n, w = len(line), len(line.split())
    if n > SUBJECT_CHARS[1]:
        return n, w, False, "longer than 50 characters — the end will be cut"
    if n > SUBJECT_MOBILE_CUT:
        return n, w, True, f"over {SUBJECT_MOBILE_CUT} characters, so some phones truncate it"
    if n < SUBJECT_CHARS[0]:
        return n, w, True, "short, which reads as an internal note rather than a pitch"
    return n, w, True, "inside the 35-50 character window"


# What makes a subject worth opening AND worth replying to. The evidence is
# consistent on three points: a question outperforms a statement by 10-15%;
# naming the company or a trigger beats a vague curiosity line, which "reads as
# marketing"; and a line engineered purely for opens can win the open and lose
# the reply. So the score rewards being specific and grounded, not intriguing.

def _grounded_subject(company, kind, trigger):
    """A question naming what actually happened, or None if it will not fit.

    Built rather than banked, because it is the one line that cannot be written
    in advance — it names this contact's own trigger. Refused when it runs past
    the length a phone shows, since a subject whose specific half is cut off is
    worse than a shorter generic one.
    """
    # Only an event. A quote does not shorten into a subject line, and a line
    # built from the company description collapses to "A question about
    # {company}" — which is the vague bait the evidence warns about, dressed as
    # specificity: identical for every contact there, and saying nothing the
    # bank does not already say better.
    if not trigger or kind != "news":
        return None
    # Question-framed first, because a question opens 10-15% better. If naming
    # the event pushes that past the length a phone shows, the shorter form
    # keeps the specific half and gives up the question — specificity is the
    # stronger signal of the two, and a subject cut off mid-event is worse than
    # either. If even that will not fit, the bank is left to it.
    for shape in ("A question about {c}'s {t}", "About {c}'s {t}"):
        line = shape.format(c=company, t=trigger)
        if len(line) <= SUBJECT_CHARS[1]:
            return line
    return None


def subject_score(line, company, trigger=None):
    """(score, reasons) — higher is better. Ranks the bank, never rewrites it."""
    low = line.lower()
    score, why = 0, []
    if low.startswith(("a question about", "question about")) or line.rstrip().endswith("?"):
        score += 3
        why.append("framed as a question")
    if company and company.lower() in low:
        score += 2
        why.append("names the company")
    if trigger and any(w in low for w in trigger.lower().split() if len(w) > 4):
        score += 3
        why.append("refers to something that actually happened")
    n = len(line)
    if SUBJECT_CHARS[0] <= n <= SUBJECT_MOBILE_CUT:
        score += 2
        why.append("survives a phone inbox")
    elif n <= SUBJECT_CHARS[1]:
        score += 1
    return score, why


SENIOR = ("c-suite", "cxo", "owner", "founder", "partner", "vp", "head",
          "director")


def suggested_tone(person):
    """(tone key, why) — advice for this contact, not an override."""
    seniority = (person.seniority or "").strip().lower()
    key, spec = vertical_for(person)
    if key == "medical":
        return "formal", ("medical and life sciences is a traditional buying "
                          "context, where the formal register tests better")
    if any(s in seniority for s in SENIOR):
        return "formal", (f"{person.seniority} is senior, and formal reads "
                          f"better above manager level")
    return "warm", ("a peer-level contact outside a traditional industry, "
                    "where the warmer register tests better")


# ------------------------------------------------------------------ compose

def missing_for(person):
    """What the template needs and this record does not have."""
    gaps = []
    if not (person.first_name or "").strip():
        gaps.append("a first name for the greeting")
    if not (person.company and (person.company.name or "").strip()):
        gaps.append("a company name")
    key, _ = vertical_for(person)
    if not key:
        from . import segments
        label = segments.category_of(person)[1]
        gaps.append(
            f"a vertical the template covers — this contact is {label}, and "
            f"the approved wording exists only for "
            f"{', '.join(v['label'] for v in VERTICALS.values())}"
        )
    kind, _, _ = opener_for(person)
    if not kind:
        gaps.append("anything researched to open on — an announcement about "
                    "the company, a post of theirs, or a description of what "
                    "the company does. Running research fills this in")
    return gaps


def compose(person, subject_index=0, tone=None, length=None, sender=None):
    """The assembled email, or a reason it cannot be assembled.

    `tone` and `length` adjust only the per-lead parts: the greeting, the
    verb the hook opens with, the sign-off, and which approved blocks are
    included. No block is ever reworded, whatever they are set to.

    Returns {"subject", "body", "vertical", "trigger", "tone", "length", ...}
    or {"missing": [...]}. No model call: every word is either from the
    template or from this contact's own record.
    """
    gaps = missing_for(person)
    if gaps:
        return {"missing": gaps}

    from .messages import sender as sender_fn
    who = sender or sender_fn()
    key, spec = vertical_for(person)
    tone_key = tone if tone in TONES else DEFAULT_TONE
    length_key = length if length in LENGTHS else DEFAULT_LENGTH
    voice = TONES[tone_key]
    shape = LENGTHS[length_key]

    company = person.company.name.strip()
    first = (person.first_name or "").strip().split()[0]
    kind, trigger, trigger_url = opener_for(person)

    # The bank, plus one line built from this contact's own trigger. The
    # evidence is blunt about which wins: "a specific reference to the
    # prospect's company, role, or trigger event outperforms vague bait", and a
    # grounded question "works because it sounds like a colleague, not a
    # campaign". So the trigger line is offered first where one exists — it is
    # the only option that can name something that actually happened.
    subjects = [t.format(company=company) for t in spec["subjects"]]
    grounded = _grounded_subject(company, kind, trigger)
    if grounded:
        subjects.insert(0, grounded)

    # Only an event counts as "something that happened" for scoring. A company
    # description shares ordinary words with the bank's lines by chance, and
    # crediting that would rank a coincidence above a real reference.
    event = trigger if kind == "news" else None

    # Ranked, never rewritten: the bank's wording is approved, the order is not.
    ranked = sorted(subjects,
                    key=lambda line: -subject_score(line, company, event)[0])
    subjects = ranked
    subject = subjects[subject_index % len(subjects)]

    # The opener carries the tone; the insight after it is the vertical's own
    # fixed wording, identical however the controls are set.
    congrats = " — congratulations" if (spec["congrats"] and kind == "news") else ""
    opener = OPENERS[kind].format(lead=voice["leads"][kind], company=company,
                                  trigger=trigger, congrats=congrats)
    hook = opener + " " + spec["insight"]

    available = {
        "hook": hook,
        "solution": spec["solution"],
        "about": ABOUT_US,
        "portfolio": PORTFOLIO.format(company=company),
        "close": CLOSE.format(company=company),
    }
    parts = [voice["greeting"].format(first=first)]
    parts += [available[b] for b in shape["blocks"]]
    if voice["signoff"]:
        parts.append(voice["signoff"])
    parts.append(who["name"])
    body = (chr(10) + chr(10)).join(parts)

    return {
        "subject": subject,
        "body": body,
        "vertical": key,
        "tone": tone_key,
        "tone_label": TONES[tone_key]["label"],
        "length": length_key,
        "length_label": LENGTHS[length_key]["label"],
        "blocks": list(LENGTHS[length_key]["blocks"]),
        "words": len(body.split()),
        "band": band_for(len(body.split()))[0],
        "band_why": band_for(len(body.split()))[1],
        "subject_notes": [subject_verdict(line) for line in subjects],
        "subject_reasons": [subject_score(line, company, event)[1]
                            for line in subjects],
        "suggested_tone": suggested_tone(person)[0],
        "suggested_tone_why": suggested_tone(person)[1],
        "vertical_label": spec["label"],
        "opener_kind": kind,
        "opener_label": OPENER_LABELS[kind],
        "trigger": trigger,
        "trigger_url": trigger_url,
        "subjects": subjects,
        "subject_index": subject_index % len(subjects),
        "basis": [
            f"vertical: {spec['label']}, from the company's category",
            f"opens on {OPENER_LABELS[kind]}: {trigger}",
            "about us and portfolio: fixed brand blocks, not regenerated",
        ],
    }
