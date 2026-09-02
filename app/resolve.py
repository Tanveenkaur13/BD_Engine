"""
Step 3, Missing Data Enrichment: find a missing LinkedIn URL from an email, a
missing email from a LinkedIn URL, or both from a name and employer.

Nothing here guesses. In particular it never constructs an address from a
pattern like first.last@domain — that produces a plausible string, not a fact,
and a plausible string in an email field is worse than an empty one because
nothing downstream can tell them apart.

What "verified" means here, exactly:

  A LinkedIn URL is accepted when the profile slug carries the person's name
  and the indexed page text carries their employer. Two independent facts.

  An email address is accepted when it was found published in a page that also
  names the person, its domain is the employer's, and its local part carries
  their name. Three facts, none of them invented.

  In both cases, if two different candidates pass, nothing is accepted. Two
  answers is not a verified answer.

What it does NOT mean: that the mailbox exists. Proving deliverability needs a
verification provider (ZeroBounce, Apollo enrichment and the like) and a
separate API key. This module claims only that the address is published as
theirs — which is why what it writes lands in email_status as "found on the
web" rather than "valid".
"""
import re
from datetime import datetime, timezone

from .research import (
    FirecrawlNotConfigured, FirecrawlRejected, _domain_brand, _host, _in_order,
    _norm_text, _profile_slug, _search,
)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# Shared mailboxes. They belong to a company, not a person, and a search for
# someone's name will happily surface the one in the site footer.
ROLE_LOCALS = {
    "info", "contact", "hello", "sales", "admin", "support", "help", "press",
    "media", "enquiries", "inquiries", "office", "team", "marketing", "careers",
    "jobs", "hr", "billing", "accounts", "privacy", "legal", "webmaster",
    "noreply", "no-reply", "donotreply", "mail", "email", "general", "reception",
}

# Free mail hosts: an address here can't be tied to an employer, so the
# domain-match signal is unavailable and the address is not accepted.
CONSUMER_DOMAINS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com",
    "icloud.com", "me.com", "live.com", "msn.com", "protonmail.com", "gmx.com",
    "mail.com", "yandex.com", "zoho.com",
}

SOURCE_FOUND = "found on the web"


def _tokens(value):
    return [t for t in _norm_text(value).split() if len(t) > 1]


def name_tokens_for(person):
    """The person's name parts, from whichever field actually has them.

    A row that arrives with only an email still carries a name inside it, and a
    row with only a LinkedIn URL carries one in the slug. Both are good enough
    to search with and to check a candidate against; neither is written to the
    name fields, because a local part is a convention, not a statement.
    """
    if person.full_name and person.full_name.strip():
        return _tokens(person.full_name)
    if person.email and "@" in person.email:
        return _tokens(person.email.split("@")[0])
    slug = _profile_slug(person.linkedin_url)
    if slug:
        # Trailing hash like "laura-macleod-0a57a4a" is not part of the name.
        parts = [p for p in _tokens(slug) if not re.fullmatch(r"[0-9a-f]{5,}", p)]
        return parts
    return []


def company_terms(person, discovered=None):
    """Ways this employer might appear in text: its name and its domain's brand.

    `discovered` is an employer worked out by app/employer.py for a row whose
    company field is blank. It is a fallback, never an override: a company that
    came from the CSV is the better fact and is used in preference.
    """
    terms = []
    if person.company and person.company.name:
        terms.append(_norm_text(person.company.name))
    elif discovered and discovered.get("name"):
        terms.append(_norm_text(discovered["name"]))
    domain = ""
    if person.company and person.company.domain:
        domain = person.company.domain.lower()
    elif person.email and "@" in person.email:
        domain = person.email.split("@")[-1].lower()
    elif discovered and discovered.get("domain"):
        domain = discovered["domain"].lower()
    if domain and domain not in CONSUMER_DOMAINS:
        brand = _domain_brand(domain)
        if brand:
            terms.append(brand)
    return [t for t in terms if t], domain


def _mentions_employer(blob, terms):
    return any(t and t in blob for t in terms)


# ----------------------------------------------------------------- LinkedIn


def _profile_candidates(hits):
    """(url, slug, blob) for every linkedin.com/in/ result."""
    out = []
    for h in hits:
        url = (h.get("url") or "").split("?")[0]
        low = url.lower()
        if "linkedin.com" not in _host(url) or "/in/" not in low:
            continue
        slug = low.split("/in/")[-1].split("/")[0]
        if not slug:
            continue
        blob = _norm_text(f"{h.get('title') or ''} {h.get('description') or ''}")
        out.append((url, slug, blob))
    return out


def find_linkedin(person, discovered=None):
    """Locate this person's LinkedIn profile, or return why it couldn't be.

    Returns {"url", "evidence", "source_query", "fetched_at"} on success, or
    {"reason": "..."} explaining what was missing. Never a maybe.
    """
    names = name_tokens_for(person)
    if not names:
        return {"reason": "No name to search with — the row has no name, no "
                         "email to read one from, and no LinkedIn URL."}
    terms, domain = company_terms(person, discovered)
    if not terms:
        return {"reason": "No employer to check a profile against. A name alone "
                          "cannot separate this person from their namesakes, so "
                          "nothing is claimed."}

    display = " ".join(names)
    company = (person.company.name if person.company
               else (discovered or {}).get("name") or terms[0])
    queries = [
        f'site:linkedin.com/in "{display}" "{company}"',
        f'site:linkedin.com/in "{display}" {domain}'.strip(),
    ]

    accepted, seen_queries = {}, {}
    for q in queries:
        try:
            hits = _search(q, limit=8)
        except (FirecrawlNotConfigured, FirecrawlRejected):
            raise
        except Exception:
            continue
        for url, slug, blob in _profile_candidates(hits):
            # Two independent signals: the name is in the profile's own slug,
            # and the employer is in the page the search indexed.
            if not _in_order(names, slug):
                continue
            if not _mentions_employer(blob, terms):
                continue
            key = slug
            accepted.setdefault(key, (url, blob))
            seen_queries.setdefault(key, q)

    if not accepted:
        return {"reason": f"No LinkedIn profile found that carries both the name "
                          f"\"{display}\" and {company}."}
    if len(accepted) > 1:
        which = ", ".join(sorted(accepted)[:4])
        return {"reason": f"{len(accepted)} different profiles match the name and "
                          f"employer ({which}) — ambiguous, so none is claimed."}

    slug, (url, blob) = next(iter(accepted.items()))
    return {
        "url": url,
        "evidence": f"profile slug carries the name and the page names {company}",
        "source_query": seen_queries[slug],
        "fetched_at": datetime.now(timezone.utc),
    }


# -------------------------------------------------------------------- email


def _plausible_for(local, names):
    """Whether a local part carries this person's name.

    Deliberately permissive about shape — nora.mawla, n.mawla, nmawla, mawlan
    are all fine — and strict about substance: one of their actual name parts
    has to be in there. That is what rules out a colleague's address sitting on
    the same page.
    """
    flat = re.sub(r"[^a-z0-9]", "", local.lower())
    return any(t in flat for t in names if len(t) > 2)


def find_email(person, discovered=None):
    """Find this person's published email address, or say why not.

    Returns {"email", "evidence", "source_query", "fetched_at"} or
    {"reason": "..."}.
    """
    names = name_tokens_for(person)
    if not names:
        return {"reason": "No name to search with, so no address could be tied "
                          "to this person."}
    terms, domain = company_terms(person, discovered)
    if not domain or domain in CONSUMER_DOMAINS:
        return {"reason": "No company domain on file. An address can only be "
                          "confirmed as theirs when its domain is their "
                          "employer's, so nothing is claimed."}

    display = " ".join(names)
    company = (person.company.name if person.company
               else (discovered or {}).get("name") or domain)
    queries = [
        f'"{display}" "{company}" email contact',
        f'"{display}" "@{domain}"',
    ]

    accepted, evidence = {}, {}
    for q in queries:
        try:
            hits = _search(q, limit=8)
        except (FirecrawlNotConfigured, FirecrawlRejected):
            raise
        except Exception:
            continue
        for h in hits:
            page = h.get("url") or ""
            text = f"{h.get('title') or ''} {h.get('description') or ''}"
            # The page has to name the person independently of the address.
            # Normalising punctuation turns first.last@company.com into
            # "first last company com", so leaving the addresses in lets a
            # candidate vouch for itself — the page would "name" whoever the
            # local part happens to spell.
            without_addresses = EMAIL_RE.sub(" ", text)
            blob = _norm_text(without_addresses)
            if not _in_order(names, blob.replace(" ", "")):
                continue
            for raw in EMAIL_RE.findall(text):
                address = raw.strip(".,;:").lower()
                local, _, host = address.partition("@")
                if host != domain and not host.endswith("." + domain):
                    continue                      # not the employer's domain
                if local in ROLE_LOCALS:
                    continue                      # a shared mailbox
                if not _plausible_for(local, names):
                    continue                      # someone else at the company
                accepted.setdefault(address, page)
                evidence.setdefault(address, q)

    if not accepted:
        return {"reason": f"No address on {domain} was found published on a page "
                          f"that also names {display}."}
    if len(accepted) > 1:
        return {"reason": f"{len(accepted)} different addresses match "
                          f"({', '.join(sorted(accepted)[:3])}) — ambiguous, so "
                          "none is claimed."}

    address, page = next(iter(accepted.items()))
    return {
        "email": address,
        "evidence": f"published on {_host(page)} on a page naming {display}",
        "source_url": page,
        "source_query": evidence[address],
        "fetched_at": datetime.now(timezone.utc),
    }


# ------------------------------------------------------------------ combined


def resolve_identity(db, person):
    """Fill whichever of email / LinkedIn URL is missing, and commit.

    Returns a report: what was filled, and for anything still missing, the
    reason — so the page can say why rather than leaving a blank.
    """
    report = {"filled": [], "unresolved": {}, "employer": None}

    # A row with no company can't be enriched at all: both checks below need an
    # employer to verify against. When the company field is blank, work out who
    # they work for from the record and their profile page first — see
    # app/employer.py, and note that it needs two independent sources to agree
    # before it will say. Whatever it returns is only ever used to *search*; the
    # acceptance rules below are unchanged, so a wrong employer cannot write a
    # wrong value, only fail to find a right one.
    discovered = None
    employer_reason = None
    if not (person.company and person.company.domain):
        from . import employer as employer_mod
        try:
            if person.company and (person.company.name or "").strip():
                # The file named the employer but not its website — the common
                # shape for any CSV without a website column. Who they work for
                # is not in question, so don't run the discovery heuristic over
                # it; just find that company's domain. Stronger evidence than
                # anything inferred, and one search instead of two.
                name = person.company.name.strip()
                domain = employer_mod.find_domain(name)
                if domain:
                    found = {"name": name, "domain": domain,
                             "evidence": f"the file names {name} as the employer; "
                                         f"its domain {domain} was found on the web"}
                else:
                    found = {"reason": f"The file names {name} as the employer, "
                                       f"but no website for it could be confirmed, "
                                       f"so an address cannot be checked against "
                                       f"its domain."}
            else:
                found = employer_mod.discover(person, name_tokens_for(person))

            if found.get("name"):
                discovered = found
                report["employer"] = found
            else:
                # Say that discovery ran and why it declined. Without this the
                # page reports only "No company domain on file", which reads as
                # "nobody looked" when in fact we looked and would not guess.
                employer_reason = found.get("reason")
        except (FirecrawlNotConfigured, FirecrawlRejected):
            raise
        except Exception:
            pass   # discovery is best-effort; the reasons below still explain

    if not person.linkedin_url:
        found = find_linkedin(person, discovered)
        if "url" in found:
            person.linkedin_url = found["url"]
            person.linkedin_found_at = found["fetched_at"]
            person.linkedin_source = found["evidence"]
            report["filled"].append("LinkedIn URL")
        else:
            report["unresolved"]["LinkedIn URL"] = found["reason"]

    if not person.email:
        found = find_email(person, discovered)
        if "email" in found:
            person.email = found["email"]
            # Published as theirs — not the same as a verified mailbox, and the
            # status field says so rather than borrowing Apollo's "valid".
            person.email_status = SOURCE_FOUND
            person.email_found_at = found["fetched_at"]
            person.email_source = found["evidence"]
            person.email_source_url = found.get("source_url")
            report["filled"].append("email")
        else:
            report["unresolved"]["email"] = found["reason"]

    if report["unresolved"]:
        note = " ".join(f"{k}: {v}" for k, v in report["unresolved"].items())
        if employer_reason:
            note += f" (No employer could be established either: {employer_reason})"
        if discovered:
            note += (f" (Employer taken as {discovered['name']}"
                     + (f", domain {discovered['domain']}" if discovered.get("domain")
                        else ", no domain found")
                     + f" — {discovered['evidence']}.)")
        person.identity_note = note[:600]
    elif report["filled"]:
        person.identity_note = None

    person.recompute_status()
    db.commit()
    return report
