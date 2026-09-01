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

    Two queries, because one angle misses things: the person with their company,
    and the person with their role. Results are deduped by URL, data-broker
    pages are pushed to the back, and nothing is padded to reach `limit` — an
    empty list is a real answer.

    linkedin.com is excluded outright: this panel answers "what does the rest of
    the web say", and LinkedIn has its own panel that attributes posts properly.
    See _is_linkedin.
    """
    company = person.company.name if person.company else ""
    queries = [q for q in (
        f'"{person.full_name}" {company}'.strip(),
        f'"{person.full_name}" {person.title or ""}'.strip(),
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
                "corroborated": why is not None,
                "corroboration": why,
                "_low": host in LOW_VALUE,
            })

    # Everything here is tied to the contact, so only the data-broker demotion
    # is left to do.
    rows.sort(key=lambda r: r["_low"])
    for i, r in enumerate(rows[:limit], start=1):
        r["rank"] = i
        r.pop("_low", None)
    return [dict(r) for r in rows[:limit]]


# /posts/ carries its author in the URL, which is the only thing that can prove
# whose activity a row is. /pulse/ and /feed/update/ don't, so they can be shown
# but never confirmed.
# Re-ask the primary query restricted to the last year. The unfiltered passes
# return whatever the index ranks highest, which tracks engagement rather than
# date, so a contact who posted last week can sit behind a 2023 post that
# happens to rank well. Costs one extra search per contact.
RECENT_WINDOW = "qdr:y"

POSTS_MARKER = "/posts/"
ACTIVITY_PATHS = ("/posts/", "/pulse/", "/feed/update/")

# What a reposted item looks like in an indexed title or snippet. LinkedIn does
# not mark reposts in the URL, so this is a read of the indexed text and no
# stronger than that — hence reposts are only ever used as filler below.
REPOST_MARKERS = ("reposted this", "reposted", "reshared", "shared this",
                  "shared a post")


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


def _attribute(url, slug, tokens, text):
    """(activity_type, corroborated, why) for a candidate row, or None to drop.

    Three tiers, because showing nothing for most contacts is its own failure:

    1. The author segment equals the contact's profile slug. This is proof, and
       only this tier is allowed to feed an interest chip.
    2. The author is a different profile whose slug carries the contact's name.
       That is either them on a second account or a namesake, and the two can't
       be told apart from a URL — shown, labelled, never inferred from.
    3. Someone else's post that names the contact in its indexed text. A
       mention, equally possibly of a namesake. Same treatment.

    Compared exactly at tier 1: the short slug "anwar-chaudhry" is a substring
    of "dr-mumtaz-anwar-chaudhry-98231b11", and a post's own text-slug contains
    the names of everyone it talks about.
    """
    blob = (text or "").lower()
    is_repost = any(marker in blob for marker in REPOST_MARKERS)
    author = _author_slug(url)

    if is_comment_url(url) and not (slug and author == slug):
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

    if slug and author == slug:
        return ("repost" if is_repost else "post"), True, \
            "the post's author is this contact's profile"

    # Slugs come in every shape: "laura-macleod-lmsw-47766b23", but also
    # "laurakmacleod" with a middle initial and no separators. Requiring the
    # name parts in order handles both without matching "sean-nelson".
    if tokens and author and _in_order(tokens, author):
        return ("repost" if is_repost else "post"), False, \
            f"posted by linkedin.com/in/{author} — same name, different profile, " \
            "so this may be a namesake"

    if tokens and all(t in blob for t in tokens):
        return "tagged", False, \
            "names this contact but was written by someone else, who may mean a namesake"

    return None


def find_linkedin_activity(person, limit=5):
    """
    Up to `limit` public LinkedIn posts written by this person, newest first.

    Found through the web search index, never by fetching LinkedIn.

    Only posts the contact demonstrably wrote. The author's profile slug is in
    the post URL and is compared, exactly, against the slug from the CSV. A
    post that merely matches the name is discarded, not shown with a caveat.

    That is a deliberate reversal of an earlier, looser version. Showing
    name-matched rows behind an "unverified" badge did fill the panels — with
    strangers. Of 27 rows it found across 12 contacts, 2 were actually the
    contact; one panel showed three different Chris Pachecos. A labelled wrong
    answer is still a wrong answer sitting on someone's profile, and the person
    reading it has to re-check every row by hand. An empty panel costs less.

    The consequence is real and intended: most contacts get nothing here,
    because most people's posts aren't in a search index under a name a
    stranger can match. Nothing is padded to reach five.

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

    Needs person.linkedin_url — without a profile slug there is nothing to
    check authorship against, and the result is empty by design.
    """
    name = (person.full_name or "").strip()
    if not name:
        return []

    slug = _profile_slug(person.linkedin_url)
    tokens = _name_tokens(name)
    company = person.company.name if person.company else ""
    base = f'site:linkedin.com/posts "{name}"'
    queries = [(q, tbs) for q, tbs in (
        (base, None),
        (f'{base} {company}'.strip(), None),
        (f'site:linkedin.com/pulse "{name}"', None),
        (base, RECENT_WINDOW),
    ) if q]

    seen, rows = set(), []
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
            verdict = _attribute(url, slug, tokens,
                                 f"{h.get('title') or ''} {text or ''}")
            if verdict is None:
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
                # Name-matched but not author-matched: a namesake, or the
                # contact on an account we can't tie to them. Either way it is
                # not evidence about this person, so it isn't written at all.
                continue
            seen.add(url)
            moment = activity_moment(url)
            rows.append({
                "activity_type": kind,
                "url": url,
                "text": text,
                "activity_date": moment.date() if moment else None,
                "added_by": "search",
                "corroborated": ok,
                "corroboration": why,
                "source_query": q,
                "fetched_at": datetime.now(timezone.utc),
                "_moment": moment,
            })

    # Newest first. Every row's exact moment came out of its own URL, so this
    # is a real ordering rather than a guess at what the index meant. A row
    # with no id to decode can't be shown to be recent, so it sorts below every
    # row that can, instead of drifting to the top on index position alone.
    def order(r):
        m = r["_moment"]
        return (
            m is None,                              # dated rows first
            -m.timestamp() if m else 0,             # newest first
            r["activity_type"] == "repost",         # own words before echoes
            not r["text"],                          # text before a bare link
        )

    chosen = sorted(rows, key=order)[:limit]
    for i, r in enumerate(chosen, start=1):
        r["rank"] = i
        r.pop("_moment", None)
    return [dict(r) for r in chosen]


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
