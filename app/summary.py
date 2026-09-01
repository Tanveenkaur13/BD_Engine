"""
The profile summary paragraph: who this person is and what they do.

Composed, not generated. Every clause restates a field the app already holds,
so the paragraph is available for all 25 contacts the moment a CSV lands, costs
nothing, and cannot invent. That matters more here than fluency: the fields it
leans on (title, seniority, department, company, industry, headcount) are
present for every contact, while focus_line — the one LLM-written sentence on
the page — is present for 9 of 25. A generated summary would leave most of the
dashboard blank, which is the failure this module exists to avoid.

Research is folded in when it exists and skipped silently when it doesn't, so
the paragraph gets richer as the pipeline runs rather than appearing late.

`basis` names the fields behind each sentence, so the panel can show its
sources the way every other panel in this app does.

No pronoun is ever guessed. A name does not tell you how someone refers to
themselves, so the subject is the person's name and then "they".
"""

import re

VOWELS = "aeiou"

# Some Apollo industry values are already a noun for the organisation itself
# ("civic & social organization", "management consulting services"). Appending
# "organisation" to those gives "a civic & social organization organisation".
INDUSTRY_IS_NOUN = ("organization", "organisation", "association", "institution",
                    "company", "agency", "services", "group", "nonprofit")

# Seniority as it reads in a sentence. Anything not listed falls through to the
# raw value, which is already a noun phrase in the Apollo export.
SENIORITY_PHRASE = {
    "c_suite": "C-suite",
    "c-suite": "C-suite",
    "vp": "VP",
    "svp": "SVP",
    "evp": "EVP",
}


def _article(phrase):
    """'a' or 'an' for the word that follows."""
    word = (phrase or "").strip().lower()
    return "an" if word[:1] in VOWELS else "a"


def _clean(value):
    v = (value or "").strip()
    return v or None


def _headcount(n):
    if not n:
        return None
    if n < 25:
        return f"{n} people"
    return f"about {n:,} people"


# Company descriptions come from whatever the research step scraped, and a
# scraped page brings its markup with it: headings, image tags, link syntax,
# non-breaking spaces, line separators. 12 of the 23 descriptions on hand carry
# at least one of those. Rendered raw they read as "# **Close the gap...**".
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_HEADING_RE = re.compile(r"^#{1,6}\s*")
_BULLET_RE = re.compile(r"^[-*+]\s+")
_EMPHASIS_RE = re.compile(r"(\*\*|__|\*|`)")
# _word_ is emphasis in prose; snake_case is not, so the underscores only go
# when they wrap a run with no word character on either outer side.
_UNDERSCORE_EM_RE = re.compile(r"(?<!\w)_([^_]+)_(?!\w)")
_ODD_SPACE = dict.fromkeys(map(ord, "   ​﻿"), " ")

# A scraped page is mostly navigation: "EN", "-", ".", "WHAT'S HAPPENING".
# Those survive markup stripping and read as noise in a sentence, so a line has
# to look like a clause before it counts as a description.
_MIN_WORDS = 4
_MIN_CHARS = 20


def _substantive(line):
    return len(line) >= _MIN_CHARS and len(line.split()) >= _MIN_WORDS


def _strip_markup(raw):
    """(is_heading, text) for one line, with scraped markup removed.

    Shared by both cleaning policies below so they can never disagree about
    what counts as markup — only about which lines are worth keeping.
    """
    line = _IMAGE_RE.sub("", raw)
    line = _LINK_RE.sub(r"\1", line)
    is_heading = bool(_HEADING_RE.match(line.strip()))
    line = _HEADING_RE.sub("", line.strip())
    line = _BULLET_RE.sub("", line)
    line = _UNDERSCORE_EM_RE.sub(r"\1", line)
    line = _EMPHASIS_RE.sub("", line)
    # Residue from links the regex couldn't pair up, e.g. "[Advisory \".
    line = line.replace("\\", " ").replace("[", " ").replace("]", " ")
    return is_heading, re.sub(r"\s+", " ", line).strip(" |")


def _joined(lines):
    """Lines as one string, each ended so a sentence splitter can tell them apart."""
    parts = [ln if ln[-1] in ".!?" else ln + "." for ln in lines if ln]
    return " ".join(parts).strip() or None


def clean_description(text):
    """A scraped company description as prose, or None.

    Prose lines are preferred over headings: a heading is a slogan fragment
    ("Think bigger."), while the line under it is usually the sentence that
    actually says what the company does. Headings are the fallback, not the
    first choice, so nothing is lost when prose is all there is.

    Short lines are dropped, because a scraped homepage is mostly navigation.
    That filter is wrong for a search snippet — see clean_snippet.
    """
    text = (text or "").translate(_ODD_SPACE)
    if not text.strip():
        return None
    prose, headings = [], []
    for raw in text.splitlines():
        is_heading, line = _strip_markup(raw)
        if not _substantive(line):
            continue
        (headings if is_heading else prose).append(line)
    return _joined(prose or headings)


def clean_snippet(text):
    """A search-result snippet with markup removed, or None.

    Every line is kept. A snippet is already a handful of words the index chose,
    so the navigation filter clean_description applies would throw away the
    content: "# The Assembly Canada / ## Speakers and Task Force / [Anwar
    Chaudhry](url)" reduces to just "Speakers and Task Force" under that policy,
    losing both the event and the person it names.
    """
    text = (text or "").translate(_ODD_SPACE)
    if not text.strip():
        return None
    lines = []
    for raw in text.splitlines():
        _, line = _strip_markup(raw)
        if line:
            lines.append(line)
    return _joined(lines)


def _first_sentences(text, limit=2, cap=280):
    """The opening of a researched company description, not all 1200 chars.

    Whole sentences only. Stopping mid-clause ("...combining interactive
    online content with…") reads as a truncation bug rather than a summary, so
    a sentence that would breach the cap is dropped instead of cut. The one
    exception is a first sentence already longer than the cap, which has to be
    cut somewhere.
    """
    text = clean_description(text)
    if not text:
        return None
    chunks = [c.strip() for c in
              text.replace("! ", ". ").replace("? ", ". ").split(". ")
              if c.strip()]
    if not chunks:
        return None

    out = []
    for chunk in chunks[:limit]:
        if out and sum(len(c) + 2 for c in out + [chunk]) > cap:
            break
        out.append(chunk)

    joined = ". ".join(out).rstrip(".")
    if len(joined) > cap:
        joined = joined[:cap].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"
        return joined
    return joined + "."


def _departments(person):
    """Department and sub-department, de-duplicated and readable.

    Apollo puts comma-separated lists in both, and the sub-department is often
    just the department repeated. Saying "in Marketing, specifically Marketing"
    would be worse than saying nothing.
    """
    dept = _clean(person.department)
    sub = _clean(person.sub_department)
    if not dept:
        return None, None
    if sub:
        d = {p.strip().lower() for p in dept.split(",") if p.strip()}
        s = [p.strip() for p in sub.split(",") if p.strip() and p.strip().lower() not in d]
        sub = ", ".join(s) or None
    return dept, sub


def compose(person):
    """Returns {"text": str, "basis": [str], "sentences": [str]}.

    Never returns an empty paragraph for a contact that has a name: the worst
    case is a single sentence saying the designation is unknown, which is
    itself the useful fact.
    """
    name = _clean(person.full_name) or "This contact"
    company = person.company
    company_name = _clean(company.name) if company else None
    title = _clean(person.title)
    sentences, basis = [], []

    # --- 1. the role, which is the whole point of the paragraph
    if title and company_name:
        sentences.append(f"{name} is {title} at {company_name}.")
    elif title:
        sentences.append(f"{name} is {title}.")
    elif company_name:
        sentences.append(f"{name} works at {company_name}, in a role we don't have on file.")
    else:
        sentences.append(f"{name} has no designation or employer on file yet.")
    if title or company_name:
        basis.append("CSV record")

    # --- 2. where that role sits, which is what "what do they actually do"
    #        usually means in practice
    seniority = _clean(person.seniority)
    seniority = SENIORITY_PHRASE.get((seniority or "").lower(), seniority)
    dept, sub = _departments(person)
    if seniority and dept:
        line = f"It is {_article(seniority)} {seniority}-level position in {dept}"
        line += f", specifically {sub}." if sub else "."
        sentences.append(line)
    elif seniority:
        sentences.append(f"It is {_article(seniority)} {seniority}-level position.")
    elif dept:
        line = f"The role sits in {dept}"
        line += f", specifically {sub}." if sub else "."
        sentences.append(line)

    # --- 3. the employer, so the role has a context
    if company:
        industry = _clean(company.industry)
        headcount = _headcount(company.employees)
        where = _clean(company.location)
        bits = []
        if industry:
            low = industry.lower()
            noun = "" if any(w in low for w in INDUSTRY_IS_NOUN) else " organisation"
            phrase = f"is {_article(industry)} {industry}{noun}"
            bits.append(f"{phrase} of {headcount}" if headcount else phrase)
        elif headcount:
            bits.append(f"employs {headcount}")
        if where:
            bits.append(f"based in {where}")
        if bits:
            sentences.append(f"{company_name} " + ", ".join(bits) + ".")
            basis.append("company record")

        # What the company does, in the words of the page research found.
        blurb = _first_sentences(company.description)
        if blurb:
            sentences.append(blurb)
            basis.append("company research")

    # --- 4. the person's own location, when it adds something the company
    #        line didn't already say
    person_where = _clean(person.location)
    if person_where and (not company or person_where != _clean(company.location)):
        sentences.append(f"They are based in {person_where}.")
        basis.append("CSV record") if "CSV record" not in basis else None

    # --- 5. the designation mismatch, because a summary that states a stale
    #        title as fact is worse than no summary
    if getattr(person, "title_drift", False) and _clean(person.title_observed):
        sentences.append(
            f"Note that the file says {person.title}, while the web shows "
            f"{person.title_observed} — the designation may be out of date."
        )
        basis.append("web research")

    # --- 6. what they are actually talking about, if anyone has looked.
    #        Reuses the existing evidence-cited focus line rather than making a
    #        second LLM call to say the same thing again.
    focus = _clean(person.focus_line)
    if focus:
        sentences.append(f"Lately: {focus[0].lower() + focus[1:]}"
                         if focus[0].isupper() and not focus.startswith("AI")
                         else f"Lately: {focus}")
        basis.append("LinkedIn and web research")

    # A company name that already ends in a full stop ("Rakuten Kobo Inc.")
    # would otherwise close its sentence with two.
    sentences = [re.sub(r"\.\.+$", ".", line) for line in sentences]

    seen, ordered_basis = set(), []
    for b in basis:
        if b and b not in seen:
            seen.add(b)
            ordered_basis.append(b)

    return {"text": " ".join(sentences), "basis": ordered_basis, "sentences": sentences}
