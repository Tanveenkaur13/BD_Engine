# PeopleIntel

Lead research dashboard. A CSV of contacts goes in; a browsable set of
researched profiles comes out — who the person is, what they do, what they're
currently interested in, and what their company does.

Three pillars: **Company · Designation · Person.**

**Python only. Node is not used at runtime, and is not needed to run this.**

---

## Running it

```bash
pip install -r requirements.txt

python -c "from app.db import init_db; init_db()"
uvicorn app.main:app --reload --port 8000
```

Open <http://127.0.0.1:8000>, upload a CSV, and the contacts appear.

To load the sample data used in the screenshots:

```bash
python -c "
from app.db import init_db, SessionLocal
from app.importer import import_csv
init_db(); db = SessionLocal()
print(import_csv(db, open('data/apollo_contacts.csv','rb').read()))"
python seed_research.py
```

### Running the research pipeline

```bash
export FIRECRAWL_API_KEY=fc-...
export LLM_BASE_URL=https://api.groq.com/openai/v1
export LLM_MODEL=openai/gpt-oss-120b
export LLM_API_KEY=gsk_...

python run_pipeline.py --limit 5           # try a few first
python run_pipeline.py                     # everything outstanding
python run_pipeline.py --skip-interests    # research only, no LLM calls
python run_pipeline.py --skip-linkedin     # don't look for activity links
python run_pipeline.py --only-linkedin     # redo just the activity search
```

---

## The 9 steps

| # | Step | How |
|---|------|-----|
| 1 | CSV Upload | `app/importer.py` — alias-matched columns, sniffed delimiter, preamble skipped, unusable header rows repaired |
| 2 | Parsing & Normalization | 75 Apollo columns → the ~25 fields the UI uses |
| 3 | Missing Data Enrichment | gap detection per contact; `Person.missing_fields()` |
| 4 | LinkedIn Activity | `app/research.py` — public post links from the search index, plus hand-pasted entries. LinkedIn is never crawled |
| 5 | Google / Web Research | `app/research.py` — Firecrawl search, two query angles, each result marked confirmed or unconfirmed |
| 6 | Interest Detection | `app/interests.py` — OpenAI-compatible LLM call |
| 7 | Company Research | `app/research.py` — company's own site preferred |
| 8 | Dashboard | FastAPI + Jinja2 templates |
| 9 | Reports & Export | `/reports` — breakdowns and the stale-designation list |

---

## Two statuses, not one

`enrichment_status` and `research_status` answer different questions and are
tracked separately. Combining them was the bug: "Needs Enrichment" fired when
research hadn't run or a company lacked a description, so it flagged 16 of 25
contacts and told you nothing about any of them.

**enrichment_status** — do we know who this person is? Set from identity fields
only: email, LinkedIn URL, designation, and the company's website. Each is a
thing enrichment could go and fill. Nothing else counts.

**research_status** — has anyone looked them up? `Not researched` -> `Researching`
-> `Researched`, or `Research failed`. A failed search no longer overwrites the
enrichment verdict; the two are independent, so **Needs Enrichment +
Researched** is a real, expressible state, and so is Complete + Not researched.

Explicitly *not* enrichment blockers, each still shown as an optional gap:

| Gap | Why it doesn't block |
|---|---|
| Direct phone | The Apollo export has one for nobody, so it would hold every contact forever |
| Company description | A fact about the employer, and one research fills in |
| Web research / LinkedIn activity | Means nobody has looked yet - that's research status |

Both statuses are decided in one place, `Person.recompute_status()`. Five call
sites previously each ran their own copy of the rule.

Designation is treated as critical because it is one of the three pillars this
tool is built on and it comes from the file, so a blank one is a real hole.
Drop it from `missing_critical()` if you'd rather it were optional - nothing
else has to change.

### The Research button

A contact showing `Not researched` gets a **Research** button on the people list
and on their own page; a failed one gets **Retry**. It posts to
`/person/<slug>/research`, parks the contact at `Researching`, and returns
immediately - the work runs in a thread, because a run takes tens of seconds and
longer when the API rate-limits and the backoff waits. Refresh to see the result.

The button is not offered for a contact that already has results. Re-running
spends API credits, so that is a deliberate `run_pipeline.py --force`, not
something a stray double-click can do. The form's `back` field is restricted to
in-app paths so the endpoint can't be turned into an open redirect.

The button and the CLI both call `app/pipeline.py:research_contact`, so there is
one definition of what "research a contact" means.

## What the CSV importer tolerates

The 75-column Apollo export is the happy path, but the file that arrives is
often not that. Handled, each reported on the upload page rather than applied
silently:

- **Delimiters** — comma, semicolon (Excel under a European locale), tab, pipe.
  Sniffed from the file, not the extension.
- **Encodings** — UTF-8, UTF-8 with BOM, UTF-16 (Excel's "Unicode Text"),
  Latin-1.
- **A preamble** above the header row (`Exported from CRM on ...`).
- **Other exports** — LinkedIn Sales Navigator, HubSpot, Salesforce and a plain
  `Name,Email` file all import; column names are matched by alias, so
  `Associated Company`, `Account Name` and `Company` all mean the same field.
- **A broken header row.** An export sometimes arrives with its leading block
  of column names overwritten by one repeated label — 17 columns all called
  `First Name`. A repeated label names none of its columns, so those positions
  are recovered from the Apollo layout by aligning on the labels that *are*
  trustworthy. Below 60% agreement the repair is refused rather than guessed at,
  and where a label still repeats the first column with it is the one that
  counts.

Rows are read positionally, so a duplicated column name can never silently
swallow another column's data.

## LinkedIn

This app has no LinkedIn credentials, no scraper, and no third-party LinkedIn
data provider. It never logs into LinkedIn and never fetches a linkedin.com
page. Activities reach the panel two ways, and each row says which:

**found by search** — `run_pipeline.py`, and the Research button, ask the same
web search index used for everything else for public post URLs
(`site:linkedin.com/posts "Name"`). A post URL is
`/posts/<author-slug>_<words-from-the-post>-activity-<id>`, so the author
segment is the only thing in a search result that can prove authorship. Each
row keeps the query and the fetch time, the same provenance a web finding
carries.

A row is kept only when something ties it to *this* contact. Three things can:

1. **The author slug is a profile we hold for them.** Compared exactly — the
   short slug `anwar-chaudhry` is itself a substring of
   `dr-mumtaz-anwar-chaudhry-98231b11`, and a post's own text-slug contains the
   names of everyone it talks about, so substring-matching the URL credited a
   hospital's post *about* a Professor Anwar Chaudhry to ours.
2. **The author is a different profile carrying their name, and the post names
   their employer.** Two independent facts — the same pair `resolve.py` accepts
   as proof that a LinkedIn URL belongs to someone.
3. **Someone else's post that names them with their job title**, where the
   author is the employer's own page, or where name, employer and title all
   three appear. That is the three-fact test the web panel already uses.

Name alone is never enough. Matching on name tokens credited five different
Laura MacLeods' posts to ours; a middle version that showed name-matched rows
behind an "unverified" badge was tried and reverted — of 49 rows across 15
contacts, 7 were the actual contact, one panel showed four different Christopher
Carrolls, and another a recruiter called Daryl Daley, matched because "daryl"
and "speed" appear in order in "Great insight, Daryl! speed with clarity". A
labelled wrong row is still a wrong row on someone's profile.

**Why (2) exists.** Requiring the CSV's slug meant the panel was empty whenever
the export's LinkedIn URL was not the profile the person posts from — and vanity
URLs get changed, so exports go stale. Julie LeBrun posts weekly about OCA
training from `julie-lebrun-tumbaoju` while the file records
`julie-lebrun-45583110b`; her panel showed nothing, which read as "she doesn't
post" rather than "our URL for her is wrong". When (2) fires, the profile it
found is stored in `linkedin_observed` and the contact's page says the file's
URL points elsewhere — the header link and the outreach board's "Open their
LinkedIn" both go to the wrong profile until someone fixes it.

**Ordering.** Kind first, then recency inside a kind: the contact's own
readable posts, then anything else they wrote, then anything readable, then the
rest — newest first throughout, decoded from each URL's activity id. Recency
alone was right while every row was a post they wrote. Once posts that merely
name them are admitted it is not: those arrive in volume and are mostly
LinkedIn's attribution block with a fresh date on it, so recency alone ranked a
content-free February mention above Gordon Hirons' own announcement of the IB
Science Questionbanks.

**Snippets that say nothing.** When a contact comments under someone else's
post, what the index returns is often just LinkedIn's furniture — "Micaela Metz,
graphic · Micaela Metz. Senior Learning Content Manager @ Axonify. 1y. Report
this comment". Strip the chrome, their name, title and employer and nothing is
left. Those rows keep their link, type and date and lose their text, because
text is what interest chips are derived from and a chip derived from that one
would be a chip derived from a job title.

The cost is still real: a contact whose posts a search engine has never indexed
gets nothing, and nothing is padded to reach five. Tagged mentions are also
available by hand, where a person has judged them.

No `linkedin_url` in the CSV only narrows this to (2) and (3) — a contact with
no slug can still be confirmed by a post that names them with their employer.

`python run_pipeline.py --only-linkedin` re-runs just this step for every
contact, without paying again for company and web research that already
succeeded.

**pasted** — the form on the contact's page: pick the type, paste the link and
the text. A hand-pasted row is never overwritten by a later search run; the
search only fills the slots left over, up to five in total.

Two honest limits on the searched route. It sees only what a search engine has
already indexed, which for a mid-level contact is often nothing — the panel
says "Nothing indexed" rather than padding. And the text is the indexed
snippet, not the whole post; where no snippet exists the row is kept as a link
and labelled as one.

The text matters, not just the link. Interest chips are derived from what the
person actually wrote — a bare URL carries no meaning, so a link-only entry
gives the model nothing to work with. That is why a link-only row says so on
its face, and why pasting the text is still worth doing.

---

## Two things worth knowing before you trust the output

**1. Coverage is uneven, and that's the data, not a bug.**

A senior, conference-speaking contact returns four or five solid findings. A
mid-level Content Manager often returns only their own LinkedIn profile. Of the
25 contacts in the sample export, 13 are Manager-level — expect thin panels for
most of them.

Empty panels say "Not found" and stay empty. Nothing is padded to reach five,
and the Interests panel returns nothing rather than inferring a focus from a job
title. `MIN_EVIDENCE` in `app/interests.py` is the floor.

**A search for a name returns namesakes, and only what can be tied to the
contact is kept.**

Searching `"Laura Macleod" Nelson` returned her real LinkedIn profile — and a
Maine phone listing, a freelance creative strategist, a wedding-group singer,
and a 1994 SAGE paper on teaching performance appraisals. Four different people.

A result is kept only when one of three things is true:

1. It is on the employer's own domain (or a subdomain of it).
2. It is the contact's own LinkedIn profile, matched on the slug from the CSV.
3. Its text carries their name, their job title **and** their employer,
   together.

Signal 3 exists because 1 and 2 alone were too narrow: "Nora Mawla. Content
Marketing Manager, Corndel." is unmistakably her and was being discarded for the
sole reason that it wasn't hosted on corndel.com. Three CSV facts co-occurring
is not a coincidence a namesake produces. A name alone is not a signal, and
neither is a name plus an employer — "Nelson" matched a post written by a Sean
Nelson.

Anything that clears none of the three is **not stored**. An earlier version
kept them behind an "identity unconfirmed" badge, on the theory that a labelled
lead beats an empty panel. It doesn't: the label puts the work of re-checking
every row back on the reader, and a stranger's phone listing sitting on someone's
profile is wrong whatever it's wearing.

The cost is real. Of 85 findings re-scored under this rule, 18 were recovered by
signal 3 and 36 were dropped — and a few of those were genuinely relevant, like
a conference-talk listing that simply never repeats the speaker's title and
employer in its indexed snippet. Precision was chosen over recall deliberately;
17 of 25 contacts have at least one finding. Loosen signal 3 in
`research._corroborate` if you would rather trade back the other way.

**2. Designations in the CSV go stale fast.**

Of the four contacts researched in the sample, **three** had a different job
title on the web than in the file:

| Person | In the CSV | On the web |
|---|---|---|
| Laura Macleod | Executive Director, Content Services | **Vice President**, Content Services |
| Joshua Dyer | Director, Content & Community Engagement | Director, **Strategy & Engagement** |
| Angelina Attisano | Content Manager | Communications & Administrative Professional |

Designation is one of the three pillars and it ages faster than anything else in
the row. Mismatches are flagged on the contact's page and listed on `/reports`.
Both values are kept — the tool doesn't silently overwrite the file or silently
trust it.

---

## Provenance

Every fact the app fetched itself carries a clickable source and a fetch date:

> ✓ Verified — nelson.com, fetched 24 Aug 2026

Interest chips carry their evidence too — hover one to see which post or article
produced it. A chip whose evidence field is empty is dropped in
`app/interests.py` rather than displayed, so no chip on the page is unsourced.

Facts that came from the uploaded CSV don't carry a source tag; the file is the
source.

---

## Stack

| Part | Choice |
|---|---|
| Web framework | FastAPI |
| Templates | Jinja2, server-rendered |
| Styling | Tailwind, prebuilt to `static/app.css` |
| Interactivity | plain forms |
| Database | SQLite via SQLAlchemy |
| Web research | Firecrawl API (HTTP) |
| Language model | any OpenAI-compatible endpoint |

**Database.** SQLite is fine on a host with a persistent disk — Railway, Render,
Fly. It is *not* fine on a serverless platform like Vercel, where the filesystem
is ephemeral and writes vanish between requests. If you deploy serverless, switch
`app/db.py` to a Postgres URL (Neon or Supabase both have a free tier); nothing
else changes.

**Model.** Groq and Ollama both speak the OpenAI chat-completions format, so
moving from hosted to self-hosted is a change to `LLM_BASE_URL` and nothing else.

**CSS.** `static/app.css` is committed, so running the app needs no build step
and no Node. To change styles, edit `input.css` / `tailwind.config.js` and
rebuild — Tailwind ships a standalone binary that needs no Node either:

```bash
tailwindcss -i input.css -o static/app.css --minify
```

---

## Not built

- **Email generation.** Deliberately out of scope. The schema has room for it.
- **Direct phone numbers.** The Apollo export has none for any of the 25
  contacts — only company switchboards. Direct lines need a paid waterfall
  enrichment, so the field is shown as an optional gap and doesn't hold a
  contact at "Needs Enrichment".
- **Person photos.** Not in the CSV, and LinkedIn's images can't be hotlinked
  or reused. The avatar is initials. Company logos come from favicons.
- **Auth.** The header shows a fixed user. Real accounts are not implemented.
- **Refresh scheduling.** Research is fetched once. Nothing re-runs on a
  schedule yet, so a "2 days ago" activity will read "9 days ago" a week later
  and findings will drift out of date.
