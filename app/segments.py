"""
Segmenting an uploaded list two ways: by country, and by category.

Both filters are independent. Either one alone narrows the list, and together
they intersect - "Education in Canada". Neither is stored on the record; both
are derived on read from what the CSV already gave us, so a re-upload or an
enrichment run never leaves a stale segment behind.

COUNTRY
-------
An Apollo export carries two countries, and they are not the same fact: where
the person is, and where their employer is. On the list in hand the person's
country is Canada for 25 of 27 rows while the employer's spans seven countries,
so filtering on the person alone would produce one useless bucket.

So a contact matches a country when *either* field matches. That keeps the
filter useful on a list of local people working for companies abroad, and on a
list of scattered people working for one company. Both countries are shown on
the row, so a match is never mysterious.

CATEGORY
--------
Nothing in the file says "education" or "medical" - the nearest thing is the
employer's industry, which arrives as free text ("e-learning", "higher
education", "pharmaceuticals", "electrical/electronic manufacturing"). Those are
mapped into a handful of broad buckets by keyword.

First match wins, so the order of CATEGORIES matters: "professional training &
coaching" has to reach Education before anything else claims it. When the
industry is blank the company's keywords and then the person's own title are
tried, because a row with no industry still often says what it does - a title of
"Filmmaker. Creative Director. Founder" is clearly Media.

A row that matches nothing lands in "Other" rather than being hidden. An
unmatched contact is still a contact, and silently dropping it from every
category view would quietly shrink the list.
"""
import re

# (key, label, keywords). Order is precedence - see the module docstring.
CATEGORIES = (
    ("education", "Education", (
        "e-learning", "elearning", "education", "edtech", "school",
        "university", "college", "academic", "curriculum", "teaching",
        "training", "tutoring", "library", "libraries", "learning",
        "pedagog", "student", "courseware",
    )),
    ("medical", "Medical & Health", (
        "medical", "health", "hospital", "pharmaceutic", "pharma", "biotech",
        "clinical", "dental", "nursing", "wellness", "therapy", "life science",
        "veterinar", "patient", "diagnostic", "medicine",
    )),
    ("engineering", "Engineering & Technology", (
        "engineering", "information technology", "software", "computer",
        "semiconductor", "electrical", "electronic", "manufactur", "industrial",
        "construction", "architect", "aerospace", "automotive", "mechanical",
        "civil engineering", "robotics", "saas", "internet", "telecommunic",
        "hardware", "cloud", "cyber",
    )),
    ("finance", "Finance & Insurance", (
        "financial", "finance", "bank", "insurance", "investment", "accounting",
        "venture capital", "fintech", "credit", "wealth",
    )),
    ("nonprofit", "Nonprofit & Public", (
        "nonprofit", "non-profit", "charit", "civic", "social organization",
        "government", "public administration", "ngo", "foundation",
        "philanthrop",
    )),
    ("media", "Media & Creative", (
        "media", "entertainment", "publishing", "publisher", "film", "music",
        "broadcast", "advertis", "marketing", "design", "photograph", "gaming",
        "animation", "journalism", "newspaper",
    )),
    ("retail", "Retail & Consumer", (
        "retail", "consumer", "luxury", "jewelry", "jewellery", "apparel",
        "fashion", "food", "beverage", "hospitality", "restaurant", "travel",
        "tourism", "e-commerce", "ecommerce", "cosmetic",
    )),
)

OTHER_KEY = "other"
OTHER_LABEL = "Other"

LABELS = dict((key, label) for key, label, _ in CATEGORIES)
LABELS[OTHER_KEY] = OTHER_LABEL

# The order buckets appear in a dropdown: the defined order, then Other last.
ORDER = [key for key, _, _ in CATEGORIES] + [OTHER_KEY]


def _norm(value):
    """Lower-cased, punctuation flattened, so 'Electrical/Electronic' matches."""
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def _bucket(text):
    """The first category whose keyword appears in `text`, or None."""
    blob = _norm(text)
    if not blob:
        return None
    for key, _, keywords in CATEGORIES:
        for word in keywords:
            if _norm(word) in blob:
                return key
    return None


def category_of(person):
    """(key, label) for this contact. Never None - falls back to Other.

    Tried in order of how directly each source states what the employer does:
    the industry, then the company's CSV keywords, then the person's own title.
    """
    company = getattr(person, "company", None)

    for source in (
        company.industry if company else None,
        company.keywords if company else None,
        getattr(person, "title", None),
    ):
        key = _bucket(source)
        if key:
            return key, LABELS[key]

    return OTHER_KEY, OTHER_LABEL


def person_country(person):
    return (getattr(person, "country", None) or "").strip() or None


def company_country(person):
    company = getattr(person, "company", None)
    return ((company.country if company else None) or "").strip() or None


def countries_of(person):
    """Every country this contact can be matched on, de-duplicated.

    Case-insensitively de-duplicated but returned in original casing, so a file
    mixing "canada" and "Canada" produces one option rather than two.
    """
    out, seen = [], set()
    for value in (person_country(person), company_country(person)):
        if value and value.lower() not in seen:
            seen.add(value.lower())
            out.append(value)
    return out


def matches(person, country="", category=""):
    """Whether this contact passes the filters. An empty filter passes."""
    if country:
        wanted = country.strip().lower()
        if wanted not in [c.lower() for c in countries_of(person)]:
            return False
    if category:
        if category_of(person)[0] != category.strip().lower():
            return False
    return True


def country_options(people):
    """[(country, count)] over the whole list, most common first then A-Z.

    Counted before filtering, so the dropdown does not shrink as you use it -
    a select whose own options vanish when chosen is impossible to undo.
    """
    counts, display = {}, {}
    for person in people:
        for country in countries_of(person):
            key = country.lower()
            counts[key] = counts.get(key, 0) + 1
            display.setdefault(key, country)
    return sorted(((display[k], n) for k, n in counts.items()),
                  key=lambda row: (-row[1], row[0].lower()))


def category_options(people):
    """[(key, label, count)] in CATEGORIES order, skipping empty buckets."""
    counts = {}
    for person in people:
        key = category_of(person)[0]
        counts[key] = counts.get(key, 0) + 1
    return [(key, LABELS[key], counts[key]) for key in ORDER if counts.get(key)]


def summarise(people):
    """Counts for the segment overview: totals per country and per category."""
    return {
        "countries": country_options(people),
        "categories": category_options(people),
        "total": len(people),
    }
