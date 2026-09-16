# PeopleIntel

**A lead research dashboard for business development.** A CSV of contacts goes
in; a browsable set of researched profiles comes out — who each person is, what
they do, what they are currently interested in, what their employer does, and
what you could sensibly say to them.

Built around three pillars: **Company · Designation · Person.**

It is a tool for one team doing outbound by hand. It researches, it drafts, and
it keeps track of where each conversation has got to — but it never sends
anything. There is no send button anywhere in it, by design.

> **Python only.** Node is not used at runtime and is not needed to run this.
> There is roughly 100 lines of JavaScript in the whole app, in two places,
> each doing something a form cannot: the draggable divider on Home, and
> copy-to-clipboard. Everything else is server-rendered HTML and plain forms.

---

## Contents

| | |
|---|---|
| [Quick start](#quick-start) | get it running in two commands |
| [Configuration](#configuration) | the API keys, and what works without them |
| [What's on each screen](#whats-on-each-screen) | the eight pages and what they answer |
| [The 9 steps](#the-9-steps) | the research pipeline |
| [Design rules](#design-rules) | the three rules that explain most decisions |
| [Research](#research) | LinkedIn, the web, companies, CSV import |
| [Outreach](#outreach) | the sequence, rejecting, drafted comments |
| [The outreach email](#the-outreach-email) | composed from a template, not generated |
| [Segmenting the list](#segmenting-the-list) | country and category filters |
| [Project structure](#project-structure) | where everything lives |
| [Stack](#stack) | and why each piece |
| [Not built](#not-built) | deliberate omissions |

---

## Quick start

You need **Python 3.11+**. Nothing else — no Node, no database server, no build
step.

### Windows

```powershell
cd BD_Engine
python -m venv venvbd
.\venvbd\Scripts\python.exe -m pip install -r requirements.txt
.\run.ps1
```

`run.ps1` can be run from any directory — it moves to its own folder first,
calls the virtualenv's Python by full path, and picks the first free port from
8000 upward. That matters more than it sounds: `uvicorn` loads the app as
`app.main:app`, which only resolves when the working directory is `BD_Engine`,
and running it one folder up is the most common way to fail to start this
project.

```powershell
.\run.ps1 -Port 8080     # choose the port yourself
.\run.ps1 -NoReload      # don't restart on file changes
```

### macOS / Linux

```bash
cd BD_Engine
python3 -m venv venvbd
./venvbd/bin/pip install -r requirements.txt
./venvbd/bin/uvicorn app.main:app --reload --port 8000
```

Then open **<http://127.0.0.1:8000>** and upload a CSV from the **CSV Upload**
page. Contacts appear immediately; research is a separate step.

> A fresh clone starts with an empty database. The contact data is deliberately
> not in the repository — see [Not built](#not-built) — so `data/*.csv` and
> `data/*.db` are gitignored and you supply your own export.

---

## Configuration

Copy `.env.example` to `.env` and fill in what you need. Real environment
variables always win over the file, so anything exported in the shell or set by
a host is never overwritten by a stale `.env`.

| Variable | Needed for | Without it |
|---|---|---|
| `FIRECRAWL_API_KEY` | all web research | `run_pipeline.py` exits immediately; every research panel stays empty |
| `LLM_BASE_URL` | interest chips, drafted messages | defaults to Groq's endpoint |
| `LLM_MODEL` | the same | defaults to `openai/gpt-oss-120b` |
| `LLM_API_KEY` | the same | drafting and interest detection are skipped, with a reason on the page |
| `OUTREACH_SENDER_NAME` | signing drafted emails | defaults to the fixed user in the header |
| `OUTREACH_SENDER_ROLE` | the same | defaults to `Head of Marketing` |
| `OUTREACH_SENDER_COMPANY` | the same | defaults to `Screwdriver` |
| `PEOPLEINTEL_DATA` | where the SQLite file lives | defaults to `./data` |

**The dashboard runs with none of these set.** Upload, browse, segment and the
outreach sequence all work; only the research and drafting steps need keys.

Any OpenAI-compatible endpoint works for the model. Groq and a self-hosted
Ollama both speak the same chat-completions format, so moving between them is a
change to `LLM_BASE_URL` and nothing else.

### Running the research pipeline

```bash
python run_pipeline.py --limit 5           # try a few first — it costs credits
python run_pipeline.py                     # everything outstanding
python run_pipeline.py --slug jane-smith   # one contact
python run_pipeline.py --skip-interests    # research only, no LLM calls
python run_pipeline.py --skip-linkedin     # don't look for activity links
python run_pipeline.py --only-linkedin     # redo just the activity search
python run_pipeline.py --force             # re-research contacts that already have results
```

Roughly six searches per contact, plus four per employer the first time one is
seen. Start with `--limit 5` and look at what comes back before spending the
rest.

---

## What's on each screen

| Page | Answers |
|---|---|
| **Home** | who is worth looking at right now — stats, the people list, and one profile in full |
| **CSV Upload** | brings contacts in, and reports what it had to repair rather than doing it silently |
| **People** | the whole list, searchable and segmented by country and category |
| **Companies** | one row per employer, with what research found out about them |
| **Enrichment** | who is missing identity fields, and which ones |
| **Research** | who has been looked up, and what was found for them |
| **Outreach** | the day's tasks, and where every contact has got to |
| **Reports** | breakdowns, and the stale-designation list |

A contact's own page gathers all of it: identity, a composed summary, their
employer, what the web says about that employer, their LinkedIn activity, their
open outreach task, what is still missing, and what you could offer them.

---

## The 9 steps

| # | Step | How |
|---|------|-----|
| 1 | CSV Upload | `app/importer.py` — alias-matched columns, sniffed delimiter, preamble skipped, unusable header rows repaired |
| 2 | Parsing & Normalization | 75 Apollo columns → the ~25 fields the UI uses |
| 3 | Missing Data Enrichment | gap detection per contact; `Person.missing_fields()` |
| 4 | LinkedIn Activity | `app/research.py` — public post links from the search index, plus hand-pasted entries. LinkedIn is never crawled |
| 5 | Google / Web Research | `app/research.py` — two query angles about the person, each result confirmed or discarded. Feeds the interest chips; it has no panel of its own |
| 6 | Interest Detection | `app/interests.py` — OpenAI-compatible LLM call |
| 7 | Company Research | `app/research.py` — the description from the company's own site, plus what the web says about them (`research_company_web`), run once per company |
| 8 | Dashboard | FastAPI + Jinja2 templates |
| 9 | Reports & Export | `/reports` — breakdowns and the stale-designation list |

Segmenting and the outreach sequence sit outside this list: the nine steps
answer "who is this person", and the sequence answers "have we spoken to them
yet". Both have their own sections below.

---

## Design rules

Three rules explain most of the decisions in this codebase. Where a section
below justifies itself at length, it is usually one of these three being
applied.

**1. An empty answer beats a plausible one.** Panels say "not found" and stay
empty rather than padding to five. Interest chips return nothing rather than
inferring a focus from a job title. A result that cannot be tied to the person
is discarded, not shown behind a caveat — a labelled wrong row is still a wrong
row on someone's profile, and it moves the work of re-checking onto the reader.

**2. Nothing is asserted without a source.** Every fact the app fetched carries
the URL it came from, the query that surfaced it and the time it was fetched.
Facts from the uploaded CSV carry nothing, because the file is the source.

**3. What the prompt asks for, the code enforces.** Interest chips without
evidence are dropped after the model returns them. Drafted messages are checked
for flattery, filler, invented rapport and unfilled placeholders in Python, not
merely forbidden in the prompt.

---

### Two statuses, not one

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

### Precision over recall, and what it costs

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

### Provenance

Every fact the app fetched itself carries a clickable source and a fetch date:

> ✓ Verified — nelson.com, fetched 24 Aug 2026

Interest chips carry their evidence too — hover one to see which post or article
produced it. A chip whose evidence field is empty is dropped in
`app/interests.py` rather than displayed, so no chip on the page is unsourced.

Facts that came from the uploaded CSV don't carry a source tag; the file is the
source.

---

## Research

### LinkedIn

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

### What the web says about a company

`research_company_web` asks the company question directly instead of topping up
a thin person search with company news. Three angles — recent announcements,
sector news, who they are — and the rows live on the Company, so one search
serves everyone who works there rather than being paid for and stored once per
contact.

A result is kept only when it is on the company's own domain, or names the
company **alongside another fact we hold** — the domain or the industry.
Deliberately not the location: "Nelson" plus "Toronto" matched "Nelson Mandela
remembered in Toronto ceremony", which is research._corroborate's namesake
problem one level up. A row clearing neither test is not stored.

The panel distinguishes two empties that mean opposite things, which is why
`web_checked_at` is written whether or not anything was found:

- **Not searched yet** — nobody has asked. Not an empty result.
- **Nothing reliable found** — the search ran; it names the angles tried, the
  date, and why results were refused, and points at the company's own site.

### What the CSV importer tolerates

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

---

## Outreach

### The sequence

Steps 1–9 answer "who is this person". The sequence answers "have we spoken to
them yet", which is independent of it — a fully researched contact nobody has
approached and a thin contact mid-sequence are both real states.

Four steps, in `app/outreach.py`:

| Step | Waits | Why |
|---|---|---|
| Comment on their post | 0 | as soon as they enter the sequence |
| Comment again | 2 days | the gap, so the second isn't same-day |
| Send follow / connection request | 0 | while the name is fresh from the comment thread |
| Send email | 2 days | reference the posts rather than opening cold |

**Every step is done by hand.** Nothing here posts a comment, sends a
connection request or sends an email. The app tracks where each contact is and
what is due; a person does the work. That is deliberate twice over: automated
LinkedIn engagement gets accounts restricted, and an automated first touch is
exactly the thing this sequence is designed not to look like.

**Steps are not pre-created.** Only one is ever open per contact, and the next
one is created when the current one is completed, with its due date measured
from the day that actually happened. A manual sequence cannot schedule step
three in advance, because its due date depends on the day step two happened
rather than the day it was meant to — so a step done three days late pushes the
rest back three days, which is what someone running this by hand expects.
Marking a step done is therefore also what schedules the next one.

The waits are defaults, not rules — they are the `wait_days` key on each
entry of `SEQUENCE`. A due date can be moved, and a step can be
**skipped with a reason** when there is nothing to comment on — which is
necessary, because the sequence assumes things that aren't always true. A
skipped step is closed and recorded as considered, which is different from
still being open.

A step whose prerequisite is missing says so instead of presenting a task
nobody can action: "No email address on file — run enrichment, or skip this
step." See `outreach.blocked_reason`.

**The board.** `/outreach` splits into five sheets — Due today, New, In
progress, Complete, Rejected — rather than one long page, because the New column
alone filled a screen and pushed the in-progress list below the fold. Overdue
steps are counted in Due today rather than listed separately: a task that slipped
is still today's task, and the row shows how late it is. The header bell shows
the same count on every page, resolved once in `main.ctx()`.

**Research state is shown before Start, not after.** Every row on the New sheet
carries its research badge and a one-line note saying what the sequence would
actually have to work with — "4 posts to comment on", or "no posts of their own —
both comment steps open empty".

Both are needed, because they can disagree. `is_researched` is an **or** over
company findings and the contact's own posts, so a contact reads **Researched**
on company findings alone while the sequence opens on two comment steps that
need a post of theirs. The badge answers *has anyone looked at them*; the note
answers *will the first task have anything in it*. `Person.outreach_prep` decides
both in one place, and counts posts and reposts separately — a repost is not
something they wrote.

### Rejecting a contact

Some contacts should not be approached, and that is a different fact from a
finished sequence or a skipped step: those record work that was done, this
records a decision that none will be.

It is stored on the person, not as a step, for two reasons. It is true of them
whatever stage they had reached. And it has to survive a re-import — a rejected
contact reappearing in **New** after someone re-uploads the export is the exact
failure this prevents.

Available from the **New** sheet, from a contact's own page, and from inside the
task dialog, which is where you are when you read a task and decide it is not
worth doing. Rejected contacts get their own sheet on the board, and a **Not
contacting** badge in the status row of their profile.

Rejection takes precedence over every other stage, so a contact turned down
halfway through leaves **In progress** at once. Their open step is **parked, not
closed**: no fictional "skipped" entry enters the trail, and restoring them puts
them back exactly where they were. The exclusion lives in `outreach.open_steps`,
so today's list, the header bell and the board all inherit it rather than each
remembering to ask.

Reversible at any time, from the Rejected sheet or the contact's page.

### The task dialog

On the **In progress** sheet a contact is one line — name, step, timing, state —
and the task itself opens in a dialog when you click the name. Rendering every
contact's full task row inline meant six contacts produced six sets of three
forms on one page, and finding the one you meant to act on was the work.

Starting a sequence redirects to `/outreach?view=active#task-<slug>`, so the
contact moves to In progress and their first task opens in the dialog on
arrival.

The dialog is **CSS, not JavaScript**: the `:target` pseudo-class does the
showing, which is what lets a form POST open one by redirect and lets the
browser's back button close it. It works with scripts disabled.

It lays out its own body rather than reusing the list row. A list row is wide
and shallow and says as little as it can; a dialog is narrow and tall and is the
one place with room to explain. What the two share is behaviour —
`outreach_actions` and `outreach_comment_draft` are the same macros, asked for
in a stacked arrangement — so the routes and field names cannot diverge.

---

### What gets written for you

Three of the four steps end in something a person has to write, and a task that
says only "comment on their post" leaves the actual work undone. So the app
produces the text — by two deliberately different routes.

| Step | How the text is produced |
|---|---|
| Comment on their post | a model drafts one comment per post, from that post |
| Comment again | the same drafts; the second step takes the second post |
| Send email | **composed** from an approved template — no model involved |

Nothing is sent automatically. There is no send button anywhere in this app.

The split is not an accident. A comment has to respond to one specific post, so
it has to be written. An email is mostly brand boilerplate that must read
identically on every lead, so writing it fresh each time is the wrong tool — see
[The outreach email](#the-outreach-email).

### Drafted comments

**Draft comments** on a contact's LinkedIn panel drafts one comment for each post
on file. Re-running skips posts that already have one, so a stray double-click
costs nothing; **Redraft** forces them all.

The drafts are written as a marketing lead would write them: a peer who read the
post properly and has a view on it, not a fan and not a commenter-for-reach.

#### It refuses rather than padding

A post the app cannot actually read produces a stated reason, not a generic
comment. The most common case is an indexed snippet that turns out to be the
contact's own profile furniture rather than the post text — there is nothing
there to respond to, and a comment written from it would be about nothing.

What comes back is then checked in code, not merely asked for in the prompt:

- **Opening filler.** `BANNED_PHRASES` catches the sixteen openers that make a
  comment worthless on arrival — "great post", "well said", "couldn't agree
  more".
- **Length.** Over the word ceiling is rejected outright.
- **Links, hashtags and @mentions.** A drafted comment that tags someone or
  drops a URL reads as marketing, and is discarded.

A post that fails records why, so the panel explains itself instead of showing a
blank. Nothing half-valid is stored.

`LINKEDIN_COMMENT_MAX_CHARS` is 1250 — LinkedIn refuses a longer comment, so an
over-length draft is not a stylistic problem but an unpasteable one. Comments are
also collapsed behind "…see more" at roughly 250 characters, which is why the
length bands sit far under the hard limit rather than near it.

> **Dormant, on purpose.** `messages.suggest_comment_options`,
> `messages.suggest_email_options` and the whole of `app/personalisation.py` — a
> richer path producing **three graded options** per step behind an evidence
> gate, with a much wider validator set (`BANNED_COMMONALITY`, `GENERIC_FILLER`)
> — are complete and tested but currently reachable from no button, along with
> the `outreach_draft_block` macro that drove them. They are left intact pending
> a decision on which drafting model to keep. Nothing in the live app calls them.

---

### The outreach email

The email step does not ask a model to write an email. It **composes** one from
an approved template — `app/email_template.py` — in which the brand blocks are
string constants rather than prompts.

The reason is drift. Asked to write a whole email, a model paraphrases the
company description slightly differently every time, and fifty leads produce
fifty subtly different accounts of what Screwdriver is. Composition makes the
fixed parts byte-identical on every send, and confines variation to the parts the
record can actually justify.

**Five blocks, in template order:**

| Block | Comes from |
|---|---|
| Hook | research — an announcement, their own post, or what the company does |
| Solution | the vertical's stock paragraph |
| About us | one fixed constant, identical on every email |
| Portfolio | one fixed constant, company name merged in |
| Close | one fixed constant, company name merged in |

**Three verticals** — `medical`, `engineering`, `edtech` — chosen from the
company's category, each with its own congratulation, insight, solution paragraph
and subject bank. A company outside the three gets **no email** rather than a
generic one.

**The hook is the only researched part.** It tries three rungs in order: a real
company announcement, then a post of the contact's own, then what the company
does. Headlines become noun phrases first — "Axonify launches Checkpoint" turns
into "Axonify's launch of Checkpoint", because "Saw Axonify launches Checkpoint"
is not a sentence. An About page is not an announcement and is never
congratulated.

**It refuses rather than inventing.** With no vertical, or nothing researched to
open on, the panel names what is missing and links to the contact's page. No
skeleton is ever filled with a guess.

#### Tone and length change it, visibly

**Tone** — Warm, Direct or Formal — moves the greeting, the opening lead and the
sign-off. **Length** — Full (all five blocks), Short (three) or Brief (two) —
drops blocks from the end. Neither ever rewords a brand block; that is the whole
point of composing rather than generating.

The email is assembled **on read**, so the selection rides in the query string
and nothing is stored. Changing either is instant, costs nothing, and the
controls cannot drift out of step with the text beneath them — the panel states
what is in force, in words, under the selects.

Because the panel lives inside the `:target` task dialog, its Apply button
carries the `#task-<slug>` fragment in the form's `action` and re-sends the sheet
and filters as hidden fields. A GET submit replaces the query but keeps the
fragment, so the dialog stays open and the board stays where it was. See
`main.form_target`.

#### How long, and what the subject says

The defaults follow published benchmarks — and the first version of this had them
backwards. The widely-quoted "50–125 words" comes from high-volume,
click-optimised campaigns. For a researched first approach whose goal is a
**reply**, into a complex or regulated buyer, the guidance is **120–180 words**.
The approved skeleton is about 155 words, inside that band by design, so **Full
is the default**. Every length still reports which band it landed in.

Subject lines are **ranked, not fixed**. The vertical's bank is offered
unchanged, and where research found a real event one further line is built from
it — "A question about Axonify's launch of Checkpoint". Candidates are scored on
whether they ask a question (questions out-open statements), name the company,
refer to something that actually happened, and fit the 35–50 characters a phone
inbox shows. The best is selected; the rest stay one click away, each with its
character count and the reasoning behind its rank.

The grounded line is built from a **news event only**. Built from a company
description it produced "A question about Nelson" — vague, and scoring a false
"refers to something that happened" on nothing but coincidental word overlap.

#### Copy means the whole message

Subject and body are one `data-copy-block`, and the copy button joins every
`data-copy-part` in it: the chosen subject prefixed `Subject: `, a blank line,
then the body. It reads the fields' **current** values, so an edit made in the
box before copying is what lands on the clipboard — the textarea is a scratchpad,
and copying the pre-edit text would quietly undo the edit.

Where `navigator.clipboard` is unavailable — anything but localhost over plain
http — it falls back to a hidden textarea and `execCommand`, and if even that is
refused the button says **Press Ctrl+C** rather than silently doing nothing.

#### Who it is signed by

The email signs itself, and the sign-off is part of the composed template
rather than anything a model produces. It comes from the environment, because
auth is not implemented and the header shows a fixed user:

```bash
export OUTREACH_SENDER_NAME="Rahul Goenka"
export OUTREACH_SENDER_ROLE="Head of Marketing"
export OUTREACH_SENDER_COMPANY="Screwdriver"
```

Those are the defaults, so it works unset. When real accounts exist this reads
the session instead and nothing else changes.

---

## Segmenting the list

Two independent filters on the People list and the Outreach board, in
`app/segments.py`. Either narrows on its own; together they intersect —
"Education in Canada". Neither is stored on the record. Both are derived on
read, so a re-upload or an enrichment run can never leave a stale segment
behind.

**Country.** An Apollo export carries two, and they are not the same fact:
where the person is, and where their employer is. On the sample list the
person's country is Canada for 25 of 27 rows while the employer's spans seven
countries, so filtering on the person alone produces one useless bucket. A
contact therefore matches a country when *either* field does, and both are
shown on the row so a match is never mysterious.

**Category.** Nothing in the file says "education" or "medical" — the nearest
thing is the employer's industry, which arrives as free text ("e-learning",
"higher education", "electrical/electronic manufacturing"). Those are keyed
into seven broad buckets by keyword. First match wins, so the order of
`CATEGORIES` is precedence: "professional training & coaching" has to reach
Education before anything else claims it. Where the industry is blank the
company's keywords and then the person's own title are tried, because a row
with no industry often still says what it does — a title of "Filmmaker.
Creative Director. Founder" is clearly Media.

A row matching nothing lands in **Other** rather than being hidden. An
unmatched contact is still a contact, and silently dropping it from every
category view would quietly shrink the list.

---

## Project structure

```
BD_Engine/
├── app/
│   ├── main.py            FastAPI routes, template filters and globals
│   ├── models.py          the eight tables, and every derived status
│   ├── db.py              engine, session, and the hand-rolled column patcher
│   ├── importer.py        CSV → contacts: encodings, delimiters, header repair
│   ├── research.py        Firecrawl search: people, LinkedIn, companies
│   ├── resolve.py         missing email / LinkedIn URL, never guessed
│   ├── employer.py        who someone works for, when the CSV didn't say
│   ├── pipeline.py        one definition of "research a contact"
│   ├── interests.py       interest chips and the focus line
│   ├── messages.py        drafted comments, and their validators
│   ├── email_template.py  the outreach email, composed from fixed blocks
│   ├── personalisation.py whether we hold enough to write anything at all
│   ├── opportunities.py   what we could offer, matched against the record
│   ├── outreach.py        the sequence state machine
│   ├── segments.py        country and category filters
│   ├── summary.py         the profile paragraph, composed not generated
│   └── env.py             reads .env without a dependency
├── templates/             Jinja2; _partials.html holds every shared macro
├── static/app.css         prebuilt Tailwind — committed, so no build step
├── data/                  SQLite lives here (gitignored)
├── tools/                 the project-guide PDF builder
├── run_pipeline.py        the research CLI
├── run.ps1                start the app on Windows
└── requirements.txt       six direct dependencies
```

The eight tables: `people`, `companies`, `linkedin_activities`,
`web_findings`, `company_findings`, `interests`, `outreach_steps` and
`outreach_drafts`.

**Where to start reading.** `app/models.py` for the shape of the data,
`app/pipeline.py` for what research actually does, and `templates/_partials.html`
for the UI — every panel in the app is a macro in that one file.

---

## Stack

| Part | Choice |
|---|---|
| Web framework | FastAPI |
| Templates | Jinja2, server-rendered |
| Styling | Tailwind, prebuilt to `static/app.css` |
| Interactivity | plain forms, and two small scripts (see below) |
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

**JavaScript.** There is some, in two places, and both are things a form cannot
do: the draggable divider between the columns on Home, and the copy buttons on
drafted messages. The copy handler is one delegated listener for the whole app —
a block marked `data-copy-block` is copied whole, joining each `data-copy-part`
inside it, so "copy" on an email means subject *and* body rather than whichever
field the button happened to sit beside.

Both degrade to nothing: with scripts off the divider is inert at its default
width and every draft is still selectable text in a textarea. No framework, no
bundler, no CDN.

**CSS.** `static/app.css` is committed, so running the app needs no build step
and no Node. To change styles, edit `input.css` / `tailwind.config.js` and
rebuild — Tailwind ships a standalone binary that needs no Node either:

```bash
tailwindcss -i input.css -o static/app.css --minify
```

---

## Not built

- **Sending anything.** Drafting is built (see Suggested messages); sending is
  not, and is not planned. No mailbox is connected, nothing posts to LinkedIn,
  and there is no send button anywhere in the app. Every step of the sequence
  is completed by a person who copied the text and sent it themselves.
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
