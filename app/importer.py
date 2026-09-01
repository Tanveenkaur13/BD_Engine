"""
CSV import: Apollo export (75 columns) -> the ~25 fields the dashboard needs.

Column names are matched loosely, so a differently-shaped export (or a thin
name/title/company file) still imports rather than failing outright.
"""
import csv
import io
import re
import unicodedata

from .models import (
    Company, Person, STATUS_COMPLETE, STATUS_NEEDS_ENRICHMENT,
)

# Aliases per logical field, first match wins. Lowercased, non-alnum stripped.
FIELD_ALIASES = {
    "first_name": ["firstname", "first"],
    "last_name": ["lastname", "last", "surname"],
    "full_name": ["fullname", "name", "contactname", "personname",
                  "displayname", "leadname"],
    "title": ["title", "jobtitle", "designation", "role", "position",
              "currentposition", "currenttitle", "positiontitle"],
    "seniority": ["seniority", "level"],
    "department": ["departments", "department"],
    "sub_department": ["subdepartments", "subdepartment"],
    "email": ["email", "emailaddress", "primaryemail", "workemail"],
    "email_status": ["emailstatus"],
    "linkedin_url": ["personlinkedinurl", "linkedinurl", "linkedin", "personlinkedin",
                     "profileurl", "linkedinprofile", "linkedinprofileurl"],
    "twitter_url": ["twitterurl", "twitter"],
    "facebook_url": ["facebookurl", "facebook"],
    "phone_direct": ["mobilephone", "workdirectphone", "directphone", "mobile",
                     "personalphone", "mobilenumber", "cellphone", "cell"],
    "phone_corporate": ["corporatephone", "companyphone", "otherphone", "homephone", "phone", "phonenumber"],
    "city": ["city", "personcity", "contactcity"],
    "state": ["state", "region"],
    "country": ["country", "countryregion", "personcountry"],
    "apollo_contact_id": ["apollocontactid"],
    # company
    "company_name": ["companyname", "company", "organization", "account",
                     "companynameforemails", "associatedcompany", "accountname",
                     "organizationname", "employer", "currentcompany"],
    "company_website": ["website", "companywebsite", "companyurl", "domain"],
    "company_linkedin_url": ["companylinkedinurl", "companylinkedin"],
    "industry": ["industry"],
    "employees": ["employees", "numemployees", "employeecount", "companysize",
                  "headcount", "employeerange"],
    "company_address": ["companyaddress"],
    "company_city": ["companycity"],
    "company_state": ["companystate"],
    "company_country": ["companycountry"],
    "company_phone": ["companyphone", "corporatephone"],
    "technologies": ["technologies", "techstack"],
    "keywords": ["keywords"],
    "annual_revenue": ["annualrevenue", "revenue"],
    "total_funding": ["totalfunding"],
    "latest_funding": ["latestfunding"],
    "sic_codes": ["siccodes"],
    "naics_codes": ["naicscodes"],
    "apollo_account_id": ["apolloaccountid"],
}


# The Apollo export's column order. Used only to repair a header row whose
# labels are unusable (duplicated or blank) — exports in the wild sometimes
# arrive with the leading block of names overwritten by a single repeated
# label, which leaves the file's data intact but its header lying about it.
REFERENCE_LAYOUT = [
    "First Name", "Last Name", "Title", "Company Name",
    "Company Name for Emails", "Email", "Email Status", "Primary Email Source",
    "Primary Email Verification Source", "Email Confidence",
    "Primary Email Catch-all Status", "Primary Email Last Verified At",
    "Seniority", "Departments", "Sub Departments", "Contact Owner",
    "Work Direct Phone", "Home Phone", "Mobile Phone", "Corporate Phone",
    "Other Phone", "Do Not Call", "Stage", "Lists", "Last Contacted",
    "Account Owner", "# Employees", "Industry", "Keywords",
    "Person Linkedin Url", "Website", "Company Linkedin Url", "Facebook Url",
    "Twitter Url", "Address", "City", "State", "Country", "Company Address",
    "Company City", "Company State", "Company Country", "Company Phone",
    "Technologies", "Annual Revenue", "Total Funding", "Latest Funding",
    "Latest Funding Amount", "Last Raised At", "Parent company (Apollo data)",
    "Parent company (Apollo data - Organization ID)", "Email Sent",
    "Email Open", "Email Bounced", "Replied", "Demoed",
    "Number of Retail Locations", "SIC Codes", "NAICS Codes",
    "Apollo Contact Id", "Apollo Account Id", "Apollo Record Id",
    "Secondary Email", "Secondary Email Source", "Secondary Email Status",
    "Secondary Email Verification Source", "Tertiary Email",
    "Tertiary Email Source", "Tertiary Email Status",
    "Tertiary Email Verification Source", "Primary Intent Topic",
    "Primary Intent Score", "Secondary Intent Topic", "Secondary Intent Score",
    "Qualify Contact",
]

# Below this share of agreement between the file's trustworthy labels and the
# reference layout, the alignment is a coincidence and the repair is refused.
ALIGN_CONFIDENCE = 0.6


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _unusable(headers):
    """Positions whose label can't be trusted to name their column.

    A label repeated across several columns names none of them: the reader
    can only hand back one value for it and the rest of the data is dropped
    on the floor. Blank and spreadsheet-placeholder labels are no better.
    """
    counts = {}
    for h in headers:
        counts[_norm(h)] = counts.get(_norm(h), 0) + 1
    bad = set()
    for i, h in enumerate(headers):
        n = _norm(h)
        if not n or counts[n] > 1 or re.fullmatch(r"unnamed\d*", n):
            bad.add(i)
    return bad


def _align_offset(headers, bad):
    """Where this header sits in REFERENCE_LAYOUT, or None if it doesn't.

    Only the trustworthy labels get a vote, so a clobbered leading block
    doesn't stop the rest of the row from identifying the layout.
    """
    votes = [(i, _norm(h)) for i, h in enumerate(headers) if i not in bad]
    if not votes:
        return None
    ref = [_norm(c) for c in REFERENCE_LAYOUT]
    best, best_hits = None, 0
    for off in range(-len(headers) + 1, len(ref)):
        hits = comparable = 0
        for i, n in votes:
            j = i + off
            if 0 <= j < len(ref):
                comparable += 1
                hits += ref[j] == n
        if comparable and hits > best_hits and hits >= ALIGN_CONFIDENCE * comparable:
            best, best_hits = off, hits
    return best


def repair_headers(headers):
    """Give every column a usable, unique name.

    Returns (names, notes). Unusable labels are recovered from the reference
    layout where the file's own good labels pin down the alignment; anything
    still unnamed gets a synthetic name so it stays addressable instead of
    colliding with another column.
    """
    bad = _unusable(headers)
    if not bad:
        return list(headers), []

    names = list(headers)
    notes = []
    off = _align_offset(headers, bad)
    recovered, fixed = [], set()
    if off is not None:
        for i in sorted(bad):
            j = i + off
            if 0 <= j < len(REFERENCE_LAYOUT):
                names[i] = REFERENCE_LAYOUT[j]
                recovered.append(REFERENCE_LAYOUT[j])
                fixed.add(i)

    if recovered:
        notes.append(
            f"Header row was unusable in {len(bad)} of {len(headers)} columns "
            f"(repeated or blank labels). Recovered {len(recovered)} from the "
            f"Apollo layout: {', '.join(recovered[:6])}"
            + ("..." if len(recovered) > 6 else "")
        )
    if len(recovered) < len(bad):
        notes.append(
            f"{len(bad) - len(recovered)} column(s) still have no usable label "
            "and don't match the Apollo layout. Where a label repeats, the "
            "first column with it is the one the dashboard reads."
        )

    # Whatever is left must still be unique or the reader loses columns again.
    # First occurrence keeps the label; the rest stay addressable under a
    # numbered name, so no column's data is dropped on the floor.
    seen, out = set(), []
    for i, name in enumerate(names):
        n = _norm(name)
        if not n:
            name = f"column {i + 1}"
            n = _norm(name)
        if n in seen:
            name = f"{name} ({i + 1})"
            n = _norm(name)
        seen.add(n)
        out.append(name)
    return out, notes


# Delimiters seen in the wild: Excel writes ';' under a European locale, and
# the upload form accepts .tsv. Comma wins ties because it is the common case.
CANDIDATE_DELIMITERS = [",", ";", chr(9), "|"]

DELIMITER_NAMES = {",": "comma", ";": "semicolon", chr(9): "tab", "|": "pipe"}

# How far into the file to look for the real header. Exports sometimes open
# with a title or a "generated on ..." line before the column names.
HEADER_SEARCH_ROWS = 10


def _sniff_delimiter(text):
    """The delimiter that gives this file a consistent, plural column count.

    Judged over several lines rather than the first one, so a preamble line
    with no delimiter in it doesn't decide the whole file.
    """
    sample = chr(10).join(text.splitlines()[:20])
    best, best_score = ",", (0, 0)
    for delim in CANDIDATE_DELIMITERS:
        counts = {}
        for row in csv.reader(io.StringIO(sample), delimiter=delim):
            if any((v or "").strip() for v in row):
                counts[len(row)] = counts.get(len(row), 0) + 1
        if not counts:
            continue
        width = max(counts, key=lambda w: (counts[w], w))
        score = (width if width > 1 else 0, counts[width])
        if score > best_score:
            best, best_score = delim, score
    return best


def _header_index(rows):
    """Which row is the header, skipping any preamble above it.

    Scored by how many fields the dashboard can actually resolve from it — a
    real header names columns, a stray title line or a data row doesn't.
    """
    if not rows:
        return 0
    widths = {}
    for row in rows:
        widths[len(row)] = widths.get(len(row), 0) + 1
    full = max(widths, key=lambda w: (widths[w], w))

    best, best_score = 0, -1
    for i, row in enumerate(rows[:HEADER_SEARCH_ROWS]):
        if len(row) != full:
            continue
        score = len(build_column_map(repair_headers(row)[0]))
        if score > best_score:
            best, best_score = i, score
    return best


def read_rows(text):
    """(names, notes, rows) — rows keyed by repaired name, read positionally.

    Positional reading is the point: pairing values with names by index means
    a duplicated label can never silently swallow another column's data.
    """
    delimiter = _sniff_delimiter(text)
    parsed = [r for r in csv.reader(io.StringIO(text), delimiter=delimiter)
              if any((v or "").strip() for v in r)]
    if not parsed:
        return [], [], []

    start = _header_index(parsed)
    names, notes = repair_headers(parsed[start])
    if delimiter != ",":
        notes.append(f"Read as {DELIMITER_NAMES[delimiter]}-separated, not comma.")
    if start:
        notes.append(
            f"Skipped {start} line(s) above the header row — the column names "
            f"were on line {start + 1}."
        )

    rows = []
    for values in parsed[start + 1:]:
        row = dict(zip(names, values))
        for extra in names[len(values):]:
            row[extra] = ""
        rows.append(row)
    return names, notes, rows


def build_column_map(headers):
    """logical field -> actual header, resolved by alias."""
    lookup = {_norm(h): h for h in headers}
    mapping = {}
    for field, aliases in FIELD_ALIASES.items():
        for alias in aliases:
            if alias in lookup and lookup[alias] not in mapping.values():
                mapping[field] = lookup[alias]
                break
    return mapping


def _get(row, colmap, field):
    header = colmap.get(field)
    if not header:
        return None
    val = (row.get(header) or "").strip()
    # Excel/Apollo prefix phone numbers with an apostrophe to force text.
    val = val.lstrip("'").strip()
    return val or None


def _num(val):
    if not val:
        return None
    cleaned = re.sub(r"[^0-9.\-]", "", str(val))
    if not cleaned or cleaned in {"-", ".", "-."}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def slugify(name, existing):
    base = unicodedata.normalize("NFKD", name or "person")
    base = base.encode("ascii", "ignore").decode()
    base = re.sub(r"[^a-zA-Z0-9]+", "-", base).strip("-").lower() or "person"
    slug, n = base, 2
    while slug in existing:
        slug = f"{base}-{n}"
        n += 1
    existing.add(slug)
    return slug


def import_csv(db, file_bytes, filename="upload.csv"):
    """Returns a summary dict that feeds the stat tiles."""
    if file_bytes[:2] in (b"\xff\xfe", b"\xfe\xff"):
        attempts = ("utf-16", "utf-8-sig", "utf-8", "latin-1")
    else:
        attempts = ("utf-8-sig", "utf-8", "utf-16", "latin-1")
    for encoding in attempts:
        try:
            text = file_bytes.decode(encoding)
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
    else:
        return {"total": 0, "imported": 0, "needs_enrichment": 0, "failed": 0,
                "errors": ["Could not decode the file — is it a CSV?"]}

    fieldnames, notes, rows = read_rows(text)
    if not fieldnames:
        return {"total": 0, "imported": 0, "needs_enrichment": 0, "failed": 0,
                "errors": ["The file has no header row."]}

    colmap = build_column_map(fieldnames)
    if "company_name" not in colmap and "full_name" not in colmap and "first_name" not in colmap:
        return {"total": 0, "imported": 0, "needs_enrichment": 0, "failed": 0,
                "notes": notes,
                "errors": ["Couldn't find a name or company column in this file."]}

    existing_slugs = {s for (s,) in db.query(Person.slug).all() if s}
    companies = {c.name.lower(): c for c in db.query(Company).all() if c.name}

    # Apollo Contact Id is stable across exports, so a row that carries one we
    # already hold is the same person again — re-uploading a corrected export
    # should update them, not create a second copy. Without this, uploading the
    # same file three times leaves three of everybody, with -2 and -3 slugs, and
    # every later research run pays for each copy.
    by_apollo_id = {
        p.apollo_contact_id: p
        for p in db.query(Person).all() if p.apollo_contact_id
    }

    # Fields the CSV owns. Research lives elsewhere on the row and is untouched.
    CSV_FIELDS = (
        "first_name", "last_name", "title", "seniority", "department",
        "sub_department", "email", "email_status", "linkedin_url",
        "twitter_url", "facebook_url", "phone_direct", "phone_corporate",
        "city", "state", "country",
    )

    total = imported = updated = failed = 0
    errors = []
    added = []

    for row in rows:
        total += 1
        try:
            first = _get(row, colmap, "first_name")
            last = _get(row, colmap, "last_name")
            if not (first or last):
                full = _get(row, colmap, "full_name")
                if full:
                    bits = full.split()
                    first, last = bits[0], " ".join(bits[1:]) or None

            cname = _get(row, colmap, "company_name")
            if not (first or last or cname):
                failed += 1
                errors.append(f"Row {total}: no name and no company — skipped.")
                continue

            # ---- company (deduped by name)
            company = None
            if cname:
                company = companies.get(cname.lower())
                if not company:
                    company = Company(
                        name=cname,
                        website=_get(row, colmap, "company_website"),
                        linkedin_url=_get(row, colmap, "company_linkedin_url"),
                        industry=_get(row, colmap, "industry"),
                        employees=int(_num(_get(row, colmap, "employees")) or 0) or None,
                        address=_get(row, colmap, "company_address"),
                        city=_get(row, colmap, "company_city"),
                        state=_get(row, colmap, "company_state"),
                        country=_get(row, colmap, "company_country"),
                        phone=_get(row, colmap, "company_phone"),
                        technologies=_get(row, colmap, "technologies"),
                        keywords=_get(row, colmap, "keywords"),
                        annual_revenue=_num(_get(row, colmap, "annual_revenue")),
                        total_funding=_num(_get(row, colmap, "total_funding")),
                        latest_funding=_get(row, colmap, "latest_funding"),
                        sic_codes=_get(row, colmap, "sic_codes"),
                        naics_codes=_get(row, colmap, "naics_codes"),
                        apollo_account_id=_get(row, colmap, "apollo_account_id"),
                    )
                    db.add(company)
                    db.flush()
                    companies[cname.lower()] = company

            apollo_id = _get(row, colmap, "apollo_contact_id")
            known = by_apollo_id.get(apollo_id) if apollo_id else None
            if known is not None:
                # Same person, fresher row. Keep the slug so existing links and
                # any research attached to this contact survive.
                values = {
                    "first_name": first, "last_name": last,
                    "title": _get(row, colmap, "title"),
                    "seniority": _get(row, colmap, "seniority"),
                    "department": _get(row, colmap, "department"),
                    "sub_department": _get(row, colmap, "sub_department"),
                    "email": _get(row, colmap, "email"),
                    "email_status": _get(row, colmap, "email_status"),
                    "linkedin_url": _get(row, colmap, "linkedin_url"),
                    "twitter_url": _get(row, colmap, "twitter_url"),
                    "facebook_url": _get(row, colmap, "facebook_url"),
                    "phone_direct": _get(row, colmap, "phone_direct"),
                    "phone_corporate": _get(row, colmap, "phone_corporate"),
                    "city": _get(row, colmap, "city"),
                    "state": _get(row, colmap, "state"),
                    "country": _get(row, colmap, "country"),
                }
                for field in CSV_FIELDS:
                    new_value = values.get(field)
                    # A blank cell in a fresh export is not a statement that the
                    # value is gone, so it doesn't erase what we already have.
                    if new_value is not None:
                        setattr(known, field, new_value)
                if company is not None:
                    known.company = company
                updated += 1
                continue

            display = " ".join(p for p in (first, last) if p) or cname
            person = Person(
                slug=slugify(display, existing_slugs),
                first_name=first,
                last_name=last,
                title=_get(row, colmap, "title"),
                seniority=_get(row, colmap, "seniority"),
                department=_get(row, colmap, "department"),
                sub_department=_get(row, colmap, "sub_department"),
                email=_get(row, colmap, "email"),
                email_status=_get(row, colmap, "email_status"),
                linkedin_url=_get(row, colmap, "linkedin_url"),
                twitter_url=_get(row, colmap, "twitter_url"),
                facebook_url=_get(row, colmap, "facebook_url"),
                phone_direct=_get(row, colmap, "phone_direct"),
                phone_corporate=_get(row, colmap, "phone_corporate"),
                city=_get(row, colmap, "city"),
                state=_get(row, colmap, "state"),
                country=_get(row, colmap, "country"),
                apollo_contact_id=_get(row, colmap, "apollo_contact_id"),
                company=company,
                enrichment_status=STATUS_NEEDS_ENRICHMENT,
                status=STATUS_NEEDS_ENRICHMENT,
            )
            db.add(person)
            added.append(person)
            if person.apollo_contact_id:
                by_apollo_id[person.apollo_contact_id] = person
            imported += 1
        except Exception as exc:  # a bad row must not kill the import
            failed += 1
            errors.append(f"Row {total}: {exc}")

    db.commit()

    # Enrichment now depends only on the identity fields the CSV carries, so a
    # row can arrive Complete. Whether anyone has researched them is a separate
    # question, answered by research_status.
    for p in db.query(Person).all():
        p.recompute_status()
    db.commit()

    # Scoped to the rows this file contributed. A database-wide count here
    # would report rows the user didn't just upload, and read as though a
    # file that imported nothing had still queued work.
    needs = sum(1 for p in added
                if p.enrichment_status == STATUS_NEEDS_ENRICHMENT)
    if updated:
        notes.append(
            f"{updated} row(s) matched a contact already in the database (by "
            "Apollo Contact Id) and updated it instead of adding a duplicate. "
            "Research already attached to those contacts was kept."
        )
    return {
        "total": total,
        "imported": imported,
        "updated": updated,
        "needs_enrichment": needs,
        "failed": failed,
        "errors": errors[:10],
        "notes": notes,
        "columns_matched": len(colmap),
        "columns_seen": len(fieldnames),
        "filename": filename,
    }
