"""
Automated research: company "what they do", and the Google/Web panel.

Both go through Firecrawl's search API. Every record written carries the URL it
came from, the query that surfaced it, and the time it was fetched.

LinkedIn is not crawled here. Nothing in this module logs into LinkedIn, fetches
a linkedin.com page, or calls a third-party LinkedIn provider. Activity links
come from the same web search index as everything else — public posts that a
search engine has already indexed — or by hand through the paste form on a
contact's page. Which of the two produced a row is recorded on it.
"""
import os
import re
import time
from datetime import datetime, timezone

import httpx

from urllib.parse import unquote

FIRECRAWL_URL = "https://api.firecrawl.dev/v2/search"
TIMEOUT = 45.0

# LinkedIn belongs to the LinkedIn Activity panel, not this one. The Google /
# Web panel exists to show what the rest of the internet says about a contact,
# so a linkedin.com result here is either a duplicate of the panel above it or,
# worse, noise: of the 30 linkedin.com rows this used to store across 25
# contacts, 20 were the person's own profile page (which the header already
# links to) and 8 were /pub/dir/ namesake directories — literally a page titled
# "50+ Anwar Chaudhry profiles", which is a list of other people.
LINKEDIN_HOST = "linkedin.com"


def _is_linkedin(url):
    """Whether this URL is on LinkedIn, including its country subdomains.

    Matched as a domain suffix rather than a substring: LinkedIn serves the same
    profile from ca./gt./uk. hosts, all of which must be caught, while a
    substring test would also catch an unrelated "mylinkedin.com".
    """
    host = _host(url)
    return host == LINKEDIN_HOST or host.endswith("." + LINKEDIN_HOST)


# Domains that only ever restate what the CSV already says.
LOW_VALUE = {"rocketreach.co", "zoominfo.com", "signalhire.com", "lusha.com",
             "apollo.io", "datanyze.com", "leadiq.com", "contactout.com"}

# Firecrawl's date-range filter, restricting a search to the last year.
# Unfiltered search ranks by engagement, not date, so "what's recent" needs
# asking for explicitly. Shared by the LinkedIn activity search and the
# company-news backfill in research_person, both of which care about lately
# over merely relevant.
RECENT_WINDOW = "qdr:y"


class FirecrawlNotConfigured(RuntimeError):
    pass


class FirecrawlRejected(RuntimeError):
    """A bad key, an expired one, or the plan's credits are gone.

    Kept separate from the per-query catch below on purpose. A rejected search
    returns no results, and "no results" is a legitimate answer here — so
    swallowing this would render an authentication problem as 25 contacts with
    nothing found, which reads as thin data rather than a broken setup.
    """


# A 429 means one of two very different things: too many requests this minute,
# which passes, or no credits left, which doesn't. Waiting is right for the
# first and pointless for the second, so they are told apart before retrying.
RETRY_WAITS = (5, 15, 30, 60)


def _search(query, limit=8, attempt=0, tbs=None):
    key = os.environ.get("FIRECRAWL_API_KEY")
    if not key:
        raise FirecrawlNotConfigured(
            "FIRECRAWL_API_KEY is not set — see the Settings page."
        )
    payload = {"query": query, "limit": limit}
    if tbs:
        payload["tbs"] = tbs
    r = httpx.post(
        FIRECRAWL_URL,
        headers={"Authorization": f"Bearer {key}"},
        json=payload,
        timeout=TIMEOUT,
    )
    if r.status_code in (401, 403):
        raise FirecrawlRejected(
            f"Firecrawl rejected the API key (HTTP {r.status_code}). "
            "Check FIRECRAWL_API_KEY — until it is valid, every search returns "
            "nothing and every panel will look empty."
        )
    if r.status_code == 429:
        body = (r.text or "").lower()
        out_of_credits = "credit" in body or "insufficient" in body
        if out_of_credits:
            raise FirecrawlRejected(
                "Firecrawl reports no credits left on the plan (HTTP 429). "
                "Waiting won't help; top up or wait for the billing period to "
                "roll over. Stopping rather than returning empty results."
            )
        wait = RETRY_WAITS[attempt] if attempt < len(RETRY_WAITS) else None
        if wait is None:
            raise FirecrawlRejected(
                "Firecrawl kept rate-limiting this run (HTTP 429) after "
                f"{len(RETRY_WAITS)} waits. The plan has credits, so this is "
                "requests-per-minute — rerun in a minute, or with --limit."
            )
        # Honour the server's own figure when it gives one.
        try:
            wait = max(wait, int(float(r.headers.get("retry-after", 0))))
        except (TypeError, ValueError):
            pass
        time.sleep(wait)
        return _search(query, limit=limit, attempt=attempt + 1, tbs=tbs)
    r.raise_for_status()
    return (r.json().get("data") or {}).get("web") or []


def classify(url, title):
    """Rough bucket for the coloured tag on each finding."""
    u, t = (url or "").lower(), (title or "").lower()
    if "linkedin.com/in/" in u or "/team" in u or "/leadership" in u or "/about-us" in u:
        return "profile"
    if any(w in t for w in ("interview", "q&a", "in conversation", "podcast")):
        return "interview"
    if any(w in t for w in ("webinar", "summit", "conference", "speaker",
                            "panel", "keynote", "session", "assembly")):
        return "event"
    if any(w in u for w in ("/news/", "/press", "/newsroom")) or "announces" in t:
        return "news"
    return "article"


def _host(url):
    return (url or "").split("//")[-1].split("/")[0].replace("www.", "").lower()


def _norm_text(value):
    """Lowercased, punctuation flattened to single spaces, for substring tests."""
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def _corroborate(url, person, text=""):
    """What ties this result to *this* person, or None.

    Two signals, either of which is enough:

    1. The page is on the employer's own domain.
    2. The text carries their name, their employer and their job title,
       together. Three independent facts from the CSV co-occurring is not a
       coincidence a namesake produces.

    There was a third — "it is this person's own LinkedIn profile", matched on
    the slug from the CSV. It was removed when linkedin.com was excluded from
    this panel: research_person now skips those URLs before reaching here, so
    the branch could never fire again and only looked like it could.

    A name alone is not a signal — there are many Laura MacLeods. Nor is a name
    plus an employer: "Nelson" matched a post written by a Sean Nelson. But all
    three at once pins it down, and without that a genuinely correct finding
    like "Nora Mawla. Content Marketing Manager, Corndel." is thrown away for
    the sole reason that it isn't hosted on corndel.com.
    """
    host = _host(url)
    if not host:
        return None

    company_domain = (person.company.domain or "").lower() if person.company else ""
    if company_domain and (host == company_domain or host.endswith("." + company_domain)):
        return "on the company's own site"

    blob = _norm_text(text)
    name = _norm_text(person.full_name)
    company = _norm_text(person.company.name if person.company else "")
    title = _norm_text(person.title)
    if blob and name and company and title:
        if name in blob and company in blob and title in blob:
            return "names them with their job title and employer"
    return None


def research_person(person, limit=5):
    """
    Returns up to `limit` findings as plain dicts, ranked.

    Two queries, both anchored on the company rather than just the person: the
    person with their company, and the person with their role at that company.
    The second used to drop the company entirely (name + title alone), which
    surfaced whoever else on the web shares a title, not this contact at this
    employer. Results are deduped by URL, and data-broker pages are pushed to
    the back.

    linkedin.com is excluded outright: this panel answers "what does the rest of
    the web say", and LinkedIn has its own panel that attributes posts properly.
    See _is_linkedin.

    When that leaves fewer than `limit` rows — a real, common outcome for a
    mid-level contact with no web footprint of their own — the remaining slots
    are filled with general company news (a launch, an expansion, a
    partnership) rather than left empty. This is exactly the kind of thing
    Screwdriver's pitch draws on, so a contact with nothing personal findable
    still leaves the panel useful instead of blank. Those rows are marked
    `about_company` and never `corroborated`: interests.py and employer.py
    already skip anything uncorroborated, so company filler can never be
    mistaken for evidence about the person or leak into their interest chips.
    """
    company = person.company.name if person.company else ""
    domain = (person.company.domain or "") if person.company else ""
    queries = [q for q in (
        f'"{person.full_name}" {company}'.strip(),
        f'"{person.full_name}" {person.title or ""} {company}'.strip(),
    ) if q.strip('" ')]

    seen, rows = set(), []
    for q in queries:
        try:
            hits = _search(q, limit=8)
        except (FirecrawlNotConfigured, FirecrawlRejected):
            raise
        except Exception:
            continue  # one failed query must not sink the contact
        for h in hits:
            url = h.get("url")
            if not url or url in seen:
                continue
            if _is_linkedin(url):
                continue   # the LinkedIn panel's job, not this one
            seen.add(url)
            host = _host(url)
            snippet = (h.get("description") or "")[:600] or None
            why = _corroborate(url, person, f"{h.get('title') or ''} {snippet or ''}")
            if why is None:
                # A search for a name returns strangers. An earlier version kept
                # these behind an "identity unconfirmed" badge; a labelled wrong
                # row is still a wrong row on someone's profile, so they are no
                # longer stored.
                continue
            rows.append({
                "title": h.get("title") or url,
                "url": url,
                "snippet": snippet,
                "kind": classify(url, h.get("title")),
                "source_query": q,
                "fetched_at": datetime.now(timezone.utc),
                "corroborated": True,
                "corroboration": why,
                "about_company": False,
                "_low": host in LOW_VALUE,
            })

    # Backfill with company news — one extra search, fired only when the
    # person-specific queries above didn't already fill the panel.
    if company and len(rows) < limit:
        q = f'"{company}" {domain} announces OR launches OR unveils OR expands OR partners'.strip()
        try:
            hits = _search(q, limit=8, tbs=RECENT_WINDOW)
        except (FirecrawlNotConfigured, FirecrawlRejected):
            raise
        except Exception:
            hits = []
        for h in hits:
            url = h.get("url")
            if not url or url in seen or _is_linkedin(url):
                continue
            seen.add(url)
            host = _host(url)
            rows.append({
                "title": h.get("title") or url,
                "url": url,
                "snippet": (h.get("description") or "")[:600] or None,
                "kind": classify(url, h.get("title")),
                "source_query": q,
                "fetched_at": datetime.now(timezone.utc),
                "corroborated": False,
                "corroboration": None,
                "about_company": True,
                "_low": host in LOW_VALUE,
            })

    # Evidence about the person always outranks company filler; within each
    # tier, data-broker pages are pushed to the back.
    rows.sort(key=lambda r: (r["about_company"], r["_low"]))
    for i, r in enumerate(rows[:limit], start=1):
        r["rank"] = i
        r.pop("_low", None)
    return [dict(r) for r in rows[:limit]]


# /posts/ carries its author in the URL, which is the only thing in a search
# result that can prove authorship directly. /pulse/ and /feed/update/ don't, so
# those depend on the post's own text naming the contact with their employer.
# The fourth query below re-asks the primary one restricted to RECENT_WINDOW,
# since a contact who posted last week can otherwise sit behind a 2023 post
# that ranks better. Costs one extra search per contact.

POSTS_MARKER = "/posts/"
ACTIVITY_PATHS = ("/posts/", "/pulse/", "/feed/update/")

# What a reposted item looks like in an indexed title or snippet. LinkedIn does
# not mark reposts in the URL, so this is a read of the indexed text and no
# stronger than that — hence reposts are only ever used as filler below.
REPOST_MARKERS = ("reposted this", "reposted", "reshared", "shared this",
                  "shared a post")

# What LinkedIn's comment-attribution block looks like once indexed. When a
# contact comments under someone else's post, the indexed text of that post
# carries their name and headline followed by this — so the row is theirs, and
# it is a comment rather than a mention of them.
COMMENT_MARKERS = ("report this comment", "report this reply",
                   "signaler ce commentaire")

# LinkedIn's own furniture, which the index returns as though it were the post.
# A snippet made of nothing but this and the contact's own name and title says
# only "they were on this page" — which the corroboration line already says
# better. Stripped before deciding whether a row carries anything to read.
CHROME_PHRASES = (
    "report this comment", "report this reply", "report this post",
    "close menu", "view profile for", "voir le profil de", "post de",
    "show translation", "signaler ce post", "signaler ce commentaire",
    "posted images on linkedin", "posted a video on linkedin",
    "posted on linkedin", "s post", "graphic", "linkedin",
    "like", "reply", "reaction", "reactions", "comment", "comments",
    "edited", "and others", "see more", "more",
)

# "1y", "7mo", "8 mois", "2 ans", "3 days ago" — relative dates the index
# includes, which are not content either.
_RELATIVE_DATE_RE = re.compile(
    r"\b\d+\s*(y|yr|yrs|year|years|mo|mos|month|months|w|wk|weeks?|d|days?|"
    r"h|hours?|ans?|mois|jours?|semaines?)\b")

# How many real words have to survive the strip for the row to be worth
# reading. Three or fewer is a fragment of a headline, not something said.
SUBSTANCE_MIN_WORDS = 4


def has_substance(text, identity):
    """Whether this snippet says anything beyond naming the contact.

    The identity parts are removed as well as LinkedIn's chrome, because a
    snippet that repeats "Micaela Metz. Senior Learning Content Manager @
    Axonify" three times is the platform's attribution block, not a post. Rows
    that fail keep their link, their type and their date — everything except a
    body of text they never had.
    """
    blob = _norm_text(text)
    if not blob:
        return False
    blob = _RELATIVE_DATE_RE.sub(" ", blob)
    # Whole words only. A two-letter name part — the "Ng" in Kevin Ng — removed
    # as a substring would cut "training" down to "trai" and "engineering" to
    # "engieeri", quietly changing what counts as a word.
    parts = (identity.tokens + identity.title_tokens
             + [term for term, _ in identity.employer]
             + list(CHROME_PHRASES))
    for part in sorted(parts, key=len, reverse=True):
        if part:
            blob = re.sub(rf"(?<![a-z0-9]){re.escape(part)}(?![a-z0-9])",
                          " ", blob)
    words = [w for w in blob.split() if len(w) > 2 and not w.isdigit()]
    return len(words) >= SUBSTANCE_MIN_WORDS


# --- when did it happen -----------------------------------------------------
#
# The search index returns url/title/description/position and no date at all,
# so "most recent" cannot be read off a result. It can be read off the URL.
#
# A LinkedIn activity id is a snowflake: the high bits are a Unix millisecond
# timestamp, so `id >> 22` is the moment the thing was published, to the second
# and without fetching the post. Verified against every hand-dated row already
# in the database — all three decode to exactly the date a human recorded.
#
# A comment permalink carries two ids: the parent post's and the comment's own.
# The comment's is the one that dates the activity, so it wins.
SNOWFLAKE_SHIFT = 22
_EPOCH_FLOOR = 1262304000.0          # 2010-01-01; LinkedIn ids predating this
                                     # are not activity ids, they are noise.

_COMMENT_ID_RE = re.compile(r"urn:li:comment:\(\s*activity:\d+\s*,\s*(\d{15,25})\s*\)", re.I)
_ACTIVITY_ID_RE = re.compile(r"(?:urn:li:activity:|[-_]activity[-:])(\d{15,25})", re.I)


def _snowflake_dt(raw):
    """UTC datetime encoded in a LinkedIn id, or None if it isn't one."""
    try:
        ts = (int(raw) >> SNOWFLAKE_SHIFT) / 1000.0
    except (TypeError, ValueError):
        return None
    # A malformed or non-activity number decodes to 1970 or to the far future;
    # either way it is not a date worth ranking on.
    if not _EPOCH_FLOOR <= ts <= time.time() + 86400:
        return None
    return datetime.fromtimestamp(ts, timezone.utc)


def activity_moment(url):
    """When the activity at `url` happened, decoded from the URL itself.

    None for anything without an id in it — a /pulse/ article, mainly. Those
    can't be proven recent, so they rank below everything that can.
    """
    u = unquote(url or "")
    m = _COMMENT_ID_RE.search(u)
    if m:
        dt = _snowflake_dt(m.group(1))
        if dt:
            return dt
    m = _ACTIVITY_ID_RE.search(u)
    return _snowflake_dt(m.group(1)) if m else None


def is_comment_url(url):
    """A comment permalink, not the post it hangs under."""
    return "commenturn=" in (url or "").lower()


def clean_activity_url(url):
    """Strip tracking parameters, keep commentUrn.

    The old code dropped the whole query string, which threw away the one
    parameter that distinguishes a comment from its parent post and carries the
    comment's own timestamp — so comments could never be identified or dated.
    """
    base, sep, query = (url or "").partition("?")
    if not sep:
        return base
    keep = [p for p in query.split("&") if p.lower().startswith("commenturn=")]
    return base + ("?" + "&".join(keep) if keep else "")


def _profile_slug(linkedin_url):
    """The 'laura-macleod-0a57a4a' part of a profile URL, if there is one."""
    u = (linkedin_url or "").lower().rstrip("/")
    if "/in/" not in u:
        return None
    return u.split("/in/")[-1].split("/")[0].split("?")[0] or None


def _author_slug(url):
    """The author's profile slug from a LinkedIn post URL, or None.

    A post URL is /posts/<author-slug>_<words-from-the-post>-activity-<id>. Only
    the first segment is the author; the rest is derived from the post's own
    text and will happily contain other people's names.
    """
    u = (url or "").lower()
    if POSTS_MARKER not in u:
        return None
    tail = u.split(POSTS_MARKER, 1)[1].strip("/")
    if not tail:
        return None
    return tail.split("_")[0] or None


def _name_tokens(name):
    return [t for t in re.split(r"[^a-z0-9]+", (name or "").lower()) if len(t) > 1]


def _in_order(tokens, text):
    """Whether every token appears in `text`, in sequence.

    Looser than a token-set match, which misses "laurakmacleod", and tighter
    than "any token appears", which would match half the platform.
    """
    pos = 0
    for t in tokens:
        i = text.find(t, pos)
        if i < 0:
            return False
        pos = i + len(t)
    return True


# --- naming the employer ----------------------------------------------------
#
# A company appears in text under more names than the CSV records. "OCA, the
# Odoo Community Association" is written "OCA" everywhere it actually appears;
# "Lillio (formerly HiMama)" appears as either half. Matching only the full
# string means the employer signal is unavailable for exactly the companies
# whose names are long, which is most of them.
#
# So three forms are tried: the full name, the short form the name itself leads
# with before a comma or bracket, and the brand inside the company's domain.
_SHORT_FORM_RE = re.compile(r"^([^,(\[]{2,40})\s*[,(\[]")

# Short forms are matched on word boundaries and the longer forms as
# substrings. A three-letter acronym as a substring hits "vocation" and
# "location", which would confirm a stranger's post as this contact's; the same
# acronym as a whole word does not.
_SHORT_FORM_MAX = 6


def _domain_brand(domain):
    """'odoo community' from 'odoo-community.org' — the brand inside a domain.

    Second-level label only, so a subdomain or a country suffix doesn't become
    the brand: 'careers.example.co.uk' gives 'example'.
    """
    if not domain:
        return ""
    return _norm_text(domain.rsplit(".", 2)[0].split(".")[-1])


def employer_terms(person):
    """[(term, whole_word)] — the ways this contact's employer names itself.

    Only the company on the record is used, never the email domain: a contact
    with a gmail address would otherwise contribute "gmail" as an employer
    term, and every post mentioning Gmail would confirm as theirs.
    """
    company = getattr(person, "company", None)
    raw = (company.name if company else None) or ""
    full = _norm_text(raw)

    terms = []
    if full:
        terms.append((full, len(full) <= _SHORT_FORM_MAX))

    match = _SHORT_FORM_RE.match(raw.strip())
    short = _norm_text(match.group(1)) if match else ""
    if short and short != full:
        terms.append((short, len(short) <= _SHORT_FORM_MAX))

    brand = _domain_brand((company.domain if company else "") or "")
    if brand and brand not in (full, short):
        terms.append((brand, len(brand) <= _SHORT_FORM_MAX))

    return terms


def employer_query(person):
    """The employer as a search term: the short form when the name has one.

    Distinct from employer_terms, which is for *matching* text and includes the
    domain brand. "terranovasecurity" is a fine thing to find in a page and a
    poor thing to search for; "OCA" is both.
    """
    company = getattr(person, "company", None)
    raw = ((company.name if company else None) or "").strip()
    if not raw:
        return ""
    match = _SHORT_FORM_RE.match(raw)
    return (match.group(1).strip() if match else raw)


def names_employer(blob, terms):
    """The employer name this text carries, or None."""
    for term, whole_word in terms:
        if not term:
            continue
        if whole_word:
            if re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", blob):
                return term
        elif term in blob:
            return term
    return None


# How much of a job title has to appear for the text to be naming it.
#
# Not the whole title, and not a prefix of it. Indexed snippets are cut
# mid-word — "Executive Director, Content Creator & Training Mana" — and a
# title grows over time: the OCA's post announcing Julie LeBrun's arrival calls
# her "Content Creator & Training Manager", which is the back half of the
# "Executive Director, Content Creator & Training Manager" the file now
# records. Anchoring on the front of the title rejects exactly the posts that
# describe how someone got where they are.
#
# So: any run of consecutive words from the title, as a phrase. Two words is
# enough because this test never stands alone — it is always the second or
# third fact beside the name and the employer.
TITLE_RUN = 2

# A run has to carry something specific. "of and", "at the" are consecutive
# title words that say nothing, so a run must include one real word.
TITLE_WORD_MIN = 4


def _names_title(blob, title_tokens):
    """Whether `blob` quotes a recognisable run of this job title."""
    for size in (3, TITLE_RUN):
        for i in range(len(title_tokens) - size + 1):
            run = title_tokens[i:i + size]
            if max((len(t) for t in run), default=0) < TITLE_WORD_MIN:
                continue
            if " ".join(run) in blob:
                return True
    return False


class Identity:
    """Everything about a contact that a candidate row can be checked against.

    Built once per contact rather than per result: `employer_terms` walks the
    company record and `_norm_text` runs a regex, and there are up to 32
    candidates per contact.
    """

    def __init__(self, person):
        self.slugs = {s for s in (_profile_slug(person.linkedin_url),
                                  _profile_slug(person.linkedin_observed)) if s}
        self.csv_slug = _profile_slug(person.linkedin_url)
        self.tokens = _name_tokens(person.full_name)
        self.employer = employer_terms(person)
        self.employer_query = employer_query(person)
        self.title_tokens = _norm_text(person.title).split()


def _attribute(url, identity, text):
    """(activity_type, corroborated, why) for a candidate row, or None to drop.

    Authorship is the question, and the URL alone answers it for only one case:

    1. The author segment is a profile slug we hold for this contact. Proof.
    2. The author is a *different* profile whose slug carries the contact's
       name. From the URL alone that is them on another account or a namesake,
       and the two are indistinguishable — so the post's own text is asked for
       a second, independent signal: does it name their employer? Slug-carries-
       the-name plus text-names-the-employer is exactly the pair of facts
       resolve.find_linkedin accepts as proof that a profile is theirs, so it
       is proof here too. Without that second signal the row is dropped.
    3. Someone else's post about them. Confirmed only when the author is the
       employer's own page and the text names the contact with their job title,
       or when name, employer and title all three appear — the same three-fact
       test _corroborate applies in the web panel. Otherwise dropped.

    Tier 2 exists because a CSV's LinkedIn URL is frequently not the profile
    the person posts from: vanity URLs get changed and exports go stale. It was
    previously computed, labelled and then discarded unconditionally, which
    made the whole panel depend on the CSV slug being current — so a contact
    who posts weekly showed nothing, and the panel's emptiness read as "this
    person doesn't post" rather than "our URL for them is out of date".

    Compared exactly at tier 1: the short slug "anwar-chaudhry" is a substring
    of "dr-mumtaz-anwar-chaudhry-98231b11", and a post's own text-slug contains
    the names of everyone it talks about.
    """
    blob = (text or "").lower()
    norm = _norm_text(text)
    is_repost = any(marker in blob for marker in REPOST_MARKERS)
    author = _author_slug(url)
    slugs, tokens = identity.slugs, identity.tokens

    if is_comment_url(url) and author not in slugs:
        # A comment permalink carries the parent post's author in the URL and
        # never the commenter's, so a search result cannot prove whose comment
        # it is. Typed correctly here so the row is labelled honestly; it stays
        # uncorroborated, which is what keeps it out of the panel and out of
        # interest chips.
        #
        # Excluded above: an anchor onto a post this contact demonstrably
        # wrote. That is still their post, and dropping the whole row because
        # the index appended a comment anchor would lose a confirmed result.
        return "comment", False,             "a comment under this post — the URL identifies the post's author, "             "not the commenter, so authorship cannot be confirmed from search"

    kind = "repost" if is_repost else "post"

    if author and author in slugs:
        return kind, True, "the post's author is this contact's profile"

    # Slugs come in every shape: "laura-macleod-lmsw-47766b23", but also
    # "laurakmacleod" with a middle initial and no separators. Requiring the
    # name parts in order handles both without matching "sean-nelson".
    if tokens and author and _in_order(tokens, author):
        employer = names_employer(norm, identity.employer)
        if employer:
            return kind, True, (
                f"posted by linkedin.com/in/{author}, whose slug carries this "
                f"contact's name, and the post names {employer} — the profile "
                "this contact actually posts from, which is not the URL on the "
                "record"
            )
        # Same name, no second signal. A labelled namesake is still a namesake.
        return None

    if tokens and all(t in blob for t in tokens):
        # Someone else's post carrying this contact's name. Whether they are
        # mentioned in it or commented under it is legible from the indexed
        # text: LinkedIn appends its comment-attribution block to the latter.
        third_party = "comment" if any(m in blob for m in COMMENT_MARKERS) \
            else "tagged"

        # The author is the employer's own LinkedIn page, so the employer is
        # established by authorship and the title is the second fact.
        if names_employer(_norm_text(author), identity.employer) \
                and _names_title(norm, identity.title_tokens):
            return third_party, True, (
                f"posted by the employer's own page linkedin.com/{author}, "
                "naming this contact with their job title"
            )
        # Anyone else's post: name, employer and title together, which is not a
        # coincidence a namesake produces. Same bar as the web panel.
        employer = names_employer(norm, identity.employer)
        if employer and _names_title(norm, identity.title_tokens):
            return third_party, True, (
                f"written by someone else, but names this contact with their "
                f"job title and {employer}"
            )
        return None

    return None


def find_linkedin_activity(person, limit=5):
    """
    Returns (rows, observed): up to `limit` public LinkedIn activities that
    demonstrably concern this contact, newest first, plus the profile the
    contact turned out to post from when that isn't the one on the record.

    Found through the web search index, never by fetching LinkedIn.

    Every row is corroborated — see _attribute for the three ways that is
    established. Rows that merely match the name are discarded, not shown with
    a caveat. Showing name-matched rows behind an "unverified" badge did fill
    the panels once — with strangers. Of 27 rows it found across 12 contacts, 2
    were actually the contact; one panel showed three different Chris Pachecos.
    A labelled wrong answer is still a wrong answer sitting on someone's
    profile, and the reader has to re-check every row by hand.

    What is no longer discarded is a row whose author is a different profile
    carrying this contact's name where the post also names their employer.
    That pair of facts is proof, and requiring the CSV slug instead meant an
    export with a stale vanity URL produced an empty panel indistinguishable
    from a contact who never posts.

    `observed` is {"url", "slug", "evidence", "fetched_at"} when tier 2 fired,
    and None otherwise. Recording it lets the next run match on the URL rather
    than re-deriving it from post text, and tells the reader the URL on the
    record points somewhere else.

    Nothing is padded to reach `limit`; an empty list is a real answer, and for
    a contact whose posts a search engine has never indexed it is the only
    honest one.

    Ordering is by date, newest first, decoded from each post's own URL — see
    activity_moment. This replaces an earlier rule that held reposts back as
    filler behind the contact's own posts regardless of age. Recency now wins:
    a repost from last month outranks an original post from 2023, because the
    question the panel answers is "what is this person doing lately", and a
    two-year-old post is a worse answer than a recent echo. Reposts still lose
    a tie at equal recency, which in practice means near never.

    A row whose URL carries no activity id — a /pulse/ article — cannot be
    dated and so cannot be shown to be recent. Those sort last rather than
    riding index position to the top.
    """
    name = (person.full_name or "").strip()
    if not name:
        return [], None

    identity = Identity(person)
    # The employer as it is actually written in a post, which for a long name
    # is the short form: nobody types "OCA, the Odoo Community Association"
    # into a post, they type "OCA". Searching the full name returns almost
    # nothing and the query is wasted, so the shortest term wins. employer
    # keeps its terms in specificity order, so the last one is the shortest.
    company = identity.employer_query
    base = f'site:linkedin.com/posts "{name}"'
    queries = [(q, tbs) for q, tbs in (
        (base, None),
        (f'{base} {company}'.strip(), None),
        (f'site:linkedin.com/pulse "{name}"', None),
        (base, RECENT_WINDOW),
    ) if q]

    seen, rows, observed = set(), [], None
    for q, tbs in queries:
        try:
            hits = _search(q, limit=8, tbs=tbs)
        except (FirecrawlNotConfigured, FirecrawlRejected):
            raise
        except Exception:
            continue  # one failed query must not sink the contact
        for h in hits:
            url = clean_activity_url(h.get("url"))
            low = url.lower()
            if not url or url in seen:
                continue
            if "linkedin.com" not in low or not any(p in low for p in ACTIVITY_PATHS):
                continue
            # The indexed snippet, not the whole post. Interest chips are
            # derived from text, so a link with no snippet carries nothing
            # and is kept only as a link.
            text = (h.get("description") or "").strip()[:900] or None
            verdict = _attribute(url, identity,
                                 f"{h.get('title') or ''} {text or ''}")
            if verdict is None:
                # Either nothing ties the row to this contact, or only the name
                # does — a namesake. Not evidence about this person, so it is
                # not written at all.
                continue
            kind, ok, why = verdict
            if ok and kind != "comment":
                # Confirmed as this contact's own activity, so the comment
                # anchor is incidental: strip it, and let the bare URL dedupe
                # against the same post found by another query.
                url = url.split("?")[0]
                if url in seen:
                    continue
            if not ok:
                continue
            author = _author_slug(url)
            if observed is None and author and author not in identity.slugs \
                    and kind in ("post", "repost"):
                # Tier 2: the contact posts from a profile the record doesn't
                # know about. Worth keeping — the "Open their LinkedIn" link
                # currently goes somewhere else.
                observed = {
                    "url": f"https://www.linkedin.com/in/{author}",
                    "slug": author,
                    "evidence": why,
                    "source_query": q,
                    "fetched_at": datetime.now(timezone.utc),
                }
            seen.add(url)
            moment = activity_moment(url)
            # A snippet that is only LinkedIn's attribution furniture is not
            # text this contact wrote, and passing it on would let an interest
            # chip be derived from a job title — the one thing the chips are
            # not allowed to do. The row keeps its link, type and date.
            substance = has_substance(text, identity)
            rows.append({
                "activity_type": kind,
                "url": url,
                "text": text if substance else None,
                "activity_date": moment.date() if moment else None,
                "added_by": "search",
                "corroborated": ok,
                "corroboration": why,
                "source_query": q,
                "fetched_at": datetime.now(timezone.utc),
                "_moment": moment,
                "_substance": substance,
            })

    # What kind of row it is comes before how recent it is, and recency decides
    # within a kind. Recency alone was right while every row was a post the
    # contact wrote; once posts that merely name them are admitted it is not,
    # because those arrive in volume and are mostly the platform's attribution
    # block with a fresh date on it. Ranking a content-free mention from last
    # month above a contact's own product-launch announcement answers "who has
    # been named lately" when the panel is asked "what is this person doing".
    #
    # Within a kind, newest first, decoded from each row's own URL — see
    # activity_moment. A row with no id to decode can't be shown to be recent,
    # so it sorts below every row that can rather than drifting to the top on
    # index position alone.
    def order(r):
        m = r["_moment"]
        authored = r["activity_type"] in ("post", "repost")
        return (
            not (authored and r["_substance"]),     # their own words, readable
            not authored,                           # then anything they wrote
            not r["_substance"],                    # then anything readable
            m is None,                              # dated rows before undated
            -m.timestamp() if m else 0,             # newest first
            r["activity_type"] == "repost",         # own words before echoes
        )

    chosen = sorted(rows, key=order)[:limit]
    for i, r in enumerate(chosen, start=1):
        r["rank"] = i
        r.pop("_moment", None)
        r.pop("_substance", None)
    return [dict(r) for r in chosen], observed


def research_company(company):
    """Returns {description, source_url, fetched_at} or None."""
    if not company or not company.name:
        return None
    q = f"{company.name} {company.domain or ''} about what they do".strip()
    try:
        hits = _search(q, limit=4)
    except (FirecrawlNotConfigured, FirecrawlRejected):
        raise
    except Exception:
        return None
    if not hits:
        return None

    # Prefer the company's own site — it's the authoritative description.
    best = None
    if company.domain:
        best = next((h for h in hits if company.domain in (h.get("url") or "")), None)
    best = best or hits[0]

    text = (best.get("description") or "").strip()
    if not text:
        return None
    return {
        "description": text[:1200],
        "source_url": best.get("url"),
        "fetched_at": datetime.now(timezone.utc),
    }
