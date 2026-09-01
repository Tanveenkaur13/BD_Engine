"""
Work out who someone works for, when the CSV didn't say.

resolve.py can only confirm an email or a LinkedIn profile by checking it
against an employer: the address has to be on the employer's domain, the
profile page has to name the employer. A row with no company therefore gets no
enrichment at all — it fails at the first gate with "No company domain on file"
and never runs a search. That is a real hole, because the employer is often
sitting in data the app already holds.

A worked example, and the reason this module exists: a contact imported with
title "Filmmaker. Creative Director. Founder" and no company. The employer was
in the row the whole time — the next words of that headline were "Screwdriver
Films", lost because the importer split the title on a comma — and it was in
three of their own LinkedIn posts. Nothing looked.

Confidence is lower here than anywhere else in resolve.py, and that is handled
by requiring agreement rather than by adding a caveat: a candidate has to turn
up in at least two independent pieces of evidence before it is used. One
mention is a guess. Two separate sources saying the same employer is a fact
worth searching with — and whatever it unlocks still has to pass resolve.py's
own checks unchanged, so a wrong employer here cannot by itself write a wrong
address. It can only cause a search that finds nothing.

Nothing in this module writes to the database. It answers "who do they work
for, and on what evidence", and resolve.py decides what to do with that.
"""
import re
from collections import defaultdict

from .research import _host, _search

# Employers show up after a role word in a LinkedIn headline, in one of a few
# shapes: "Founder, Screwdriver Films", "Director @ Screwdriver Films Pvt Ltd",
# "Head of AI and Content Technology at ACAMS".
ROLE_WORDS = (
    "founder", "co-founder", "cofounder", "owner", "partner", "director",
    "ceo", "cto", "coo", "cmo", "cfo", "president", "principal", "head",
    "manager", "lead", "chief", "vp", "vice president", "editor", "producer",
    "filmmaker", "consultant", "specialist", "strategist", "engineer",
)

# A capitalised run of up to five words: "Screwdriver Films Pvt Ltd".
_NAME_RUN = r"[A-Z][A-Za-z0-9&.'\-]*(?:\s+[A-Z][A-Za-z0-9&.'\-]*){0,4}"

_AT_RE = re.compile(r"(?:\bat\b|@)\s+(" + _NAME_RUN + r")")
_ROLE_COMMA_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(w) for w in ROLE_WORDS) + r")\b\s*[,:]\s*("
    + _NAME_RUN + r")", re.I)

# Legal suffixes are part of the registered name but not of the brand, and the
# brand is what has to match a domain.
_SUFFIX_RE = re.compile(
    r"\b(?:pvt|private|ltd|limited|llc|llp|inc|incorporated|corp|corporation|"
    r"gmbh|plc|co|company|holdings|group)\b\.?", re.I)

# Words that are never an employer, so a headline fragment can't become one.
_STOPWORDS = {
    "the", "and", "a", "an", "linkedin", "view", "profile", "post", "posts",
    "like", "reply", "comment", "share", "follow", "close", "menu", "see",
    "more", "connect", "message", "today", "years", "ago", "congratulations",
}

# Hosts that describe a company without being it.
_NOT_A_COMPANY_SITE = {
    "linkedin.com", "facebook.com", "twitter.com", "x.com", "instagram.com",
    "youtube.com", "wikipedia.org", "crunchbase.com", "bloomberg.com",
    "rocketreach.co", "zoominfo.com", "signalhire.com", "lusha.com",
    "apollo.io", "quickcompany.in", "zaubacorp.com", "tofler.in",
    "indiamart.com", "glassdoor.com", "indeed.com", "medium.com",
}


def _clean_candidate(raw):
    """A candidate employer name, or None if it isn't one."""
    name = " ".join((raw or "").split()).strip(" .,;:-&")
    if not name or len(name) < 3:
        return None
    words = [w for w in name.split() if w]
    # A run made only of stopwords or role words is a sentence fragment.
    meaningful = [w for w in words
                  if w.lower() not in _STOPWORDS
                  and w.lower() not in ROLE_WORDS]
    if not meaningful:
        return None
    return " ".join(words)


def brand_of(name):
    """The distinctive part of an employer name, lowercased and joined.

    "Screwdriver Films Pvt Ltd" -> "screwdriverfilms". This is what a domain is
    compared against, since a domain carries no spaces or legal suffix.
    """
    without_suffix = _SUFFIX_RE.sub(" ", name or "")
    words = [w for w in re.split(r"[^A-Za-z0-9]+", without_suffix) if w]
    words = [w for w in words if w.lower() not in _STOPWORDS]
    return "".join(w.lower() for w in words)


def candidates_from(text, exclude_names=()):
    """Employer names mentioned in `text`, best-effort.

    `exclude_names` drops the person's own name, which otherwise wins: "View
    profile for Rahul Goenka · Rahul Goenka" reads exactly like an employer to
    a pattern that only knows about capitalisation.
    """
    if not text:
        return []
    blocked = {brand_of(n) for n in exclude_names if n}
    out = []
    for pattern in (_ROLE_COMMA_RE, _AT_RE):
        for match in pattern.finditer(text):
            name = _clean_candidate(match.group(1))
            if not name:
                continue
            if brand_of(name) in blocked or not brand_of(name):
                continue
            out.append(name)
    return out


def _evidence_texts(person):
    """(label, text) for everything on the record that might name an employer."""
    out = []
    if (person.title or "").strip():
        out.append(("their designation", person.title))
    # Labelled per row, not as one bucket: two separate posts naming the same
    # employer is genuine agreement, and collapsing them under a single label
    # would hide that and force an avoidable search.
    for activity in getattr(person, "activities", []) or []:
        if getattr(activity, "trusted", False) and (activity.text or "").strip():
            out.append((f"their own LinkedIn {activity.type_label.lower()} "
                        f"#{activity.rank}", activity.text))
    for finding in getattr(person, "findings", []) or []:
        if getattr(finding, "corroborated", False) and (finding.snippet or "").strip():
            out.append(("a confirmed web finding", finding.snippet))
    return out


def discover(person, name_tokens=()):
    """Who this person works for, on the evidence, or why we can't say.

    Returns {"name", "brand", "domain"|None, "evidence", "sources"} or
    {"reason": "..."}.

    Costs at most two searches: one for their LinkedIn profile page (only when
    the record's own text isn't enough), and one to find the employer's website.
    """
    own_names = [person.full_name] + list(name_tokens or [])

    # Group candidates by brand so "Screwdriver Films" and "Screwdriver Films
    # Pvt Ltd" count as agreement rather than as two rival answers.
    by_brand = defaultdict(lambda: {"names": [], "sources": set()})

    def absorb(label, text):
        for name in candidates_from(text, exclude_names=own_names):
            slot = by_brand[brand_of(name)]
            slot["names"].append(name)
            slot["sources"].add(label)

    for label, text in _evidence_texts(person):
        absorb(label, text)

    # Their own profile page, when the record alone hasn't produced agreement.
    # The indexed title is "Name - Headline" and the headline names the employer.
    query = None
    if not any(len(v["sources"]) >= 2 for v in by_brand.values()):
        from .research import _profile_slug
        slug = _profile_slug(person.linkedin_url)
        if slug:
            query = f"site:linkedin.com/in {slug}"
            try:
                for hit in _search(query, limit=5):
                    # Labelled by the page it came from. Two different indexed
                    # pages naming the same employer is real agreement; filing
                    # them all under one label would hide that and refuse a
                    # well-evidenced answer.
                    where = _host(hit.get("url") or "") or "their profile page"
                    absorb(f"the indexed page at {where}",
                           f"{hit.get('title') or ''} {hit.get('description') or ''}")
            except Exception:
                pass   # discovery is best-effort; resolve.py still reports why

    if not by_brand:
        return {"reason": "No employer named anywhere on this record or on "
                          "their profile page."}

    # Agreement, then the fullest spelling of the winning name.
    brand, slot = max(by_brand.items(),
                      key=lambda kv: (len(kv[1]["sources"]), len(kv[1]["names"])))
    if len(slot["sources"]) < 2:
        only = sorted(slot["sources"])[0] if slot["sources"] else "one mention"
        return {"reason": f"\"{slot['names'][0]}\" looks like the employer but "
                          f"only {only} says so. One mention is a guess, so it "
                          "is not used."}

    name = max(slot["names"], key=len)
    sources = sorted(slot["sources"])
    return {
        "name": name,
        "brand": brand,
        "domain": find_domain(name, brand, person_name=person.full_name),
        "evidence": f"named as the employer by {len(sources)} independent "
                    f"sources on this record ({', '.join(sources)})",
        "sources": sources,
        "source_query": query,
    }


def find_domain(name, brand=None, person_name=None):
    """The employer's own domain, or None.

    Accepted only when the host actually carries the employer's brand. A search
    for a company returns its LinkedIn page, three data brokers and a registry
    listing; none of those is the company, and an address on any of them would
    not be the employer's.

    Two query angles, because "<company> official website" alone is unreliable
    for a small firm — for "Screwdriver Films" it returned a fandom wiki, a
    casting site and two Facebook pages, while pairing the company with the
    contact's name surfaced screwdriver.in in third place. The name is what
    disambiguates a company too small to own its own search results.
    """
    brand = brand or brand_of(name)
    if not brand:
        return None
    queries = [f'"{name}" official website']
    if person_name:
        queries.append(f'"{name}" {person_name}')

    hits = []
    for query in queries:
        try:
            hits.extend(_search(query, limit=6))
        except Exception:
            continue
    for hit in hits:
        host = _host(hit.get("url") or "")
        if not host or host in _NOT_A_COMPANY_SITE:
            continue
        if any(host.endswith(bad) for bad in _NOT_A_COMPANY_SITE):
            continue
        stem = "".join(re.split(r"[^a-z0-9]+", host.rsplit(".", 1)[0].lower()))
        # The brand has to be in the host, or the host in the brand — covers
        # "screwdriverfilms.com" for "Screwdriver Films Pvt Ltd", and
        # "acams.org" for "ACAMS".
        if brand in stem or stem in brand:
            return host
    return None
