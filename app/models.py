"""
PeopleIntel data model.

Design rule carried over from the v1 plan: every fact that was fetched from the
web stores WHERE it came from and WHEN. Fields that come straight from the
uploaded CSV don't need provenance (the file is the source); anything we went
and found ourselves does.
"""
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column, Integer, String, Text, DateTime, ForeignKey, Date, Float,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


def utcnow():
    return datetime.now(timezone.utc)


def _title_key(t):
    """Loose comparison so 'Director, Content' == 'director content'."""
    import re
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())


# ---------------------------------------------------------------- statuses
# Two independent questions, previously answered by one field:
#
#   enrichment — do we know who this person is? Identity fields only.
#   research   — have we gone and looked them up yet?
#
# Conflating them made "Needs Enrichment" mean "something, somewhere, is
# missing", which is true of almost every contact and so told you nothing. A
# fully identified person who hasn't been researched is not missing data about
# themselves; a person with no email is, whether or not research has run. Both
# combinations are now expressible: Needs Enrichment + Researched is a real
# state, and so is Complete + Not researched.
STATUS_COMPLETE = "complete"
STATUS_NEEDS_ENRICHMENT = "needs_enrichment"
STATUS_FAILED = "failed"

STATUS_LABELS = {
    STATUS_COMPLETE: "Complete",
    STATUS_NEEDS_ENRICHMENT: "Needs Enrichment",
    STATUS_FAILED: "Failed",
}

RESEARCH_DONE = "researched"
RESEARCH_PENDING = "not_researched"
RESEARCH_RUNNING = "running"
RESEARCH_FAILED = "failed"

RESEARCH_LABELS = {
    RESEARCH_DONE: "Researched",
    RESEARCH_PENDING: "Not researched",
    RESEARCH_RUNNING: "Researching…",
    RESEARCH_FAILED: "Research failed",
}

# LinkedIn activity types. A repost counts as activity in its own right: it is
# something the person chose to put in front of their network, and it is not a
# post of theirs — conflating the two would overstate what they actually wrote.
ACTIVITY_POST = "post"
ACTIVITY_REPOST = "repost"
ACTIVITY_COMMENT = "comment"
ACTIVITY_TAGGED = "tagged"

ACTIVITY_LABELS = {
    ACTIVITY_POST: "Post",
    ACTIVITY_REPOST: "Repost",
    ACTIVITY_COMMENT: "Comment",
    ACTIVITY_TAGGED: "Tagged",
}


class Company(Base):
    __tablename__ = "companies"

    id = Column(Integer, primary_key=True)

    # --- from the CSV
    name = Column(String, nullable=False, index=True)
    website = Column(String)
    linkedin_url = Column(String)
    industry = Column(String)
    employees = Column(Integer)
    address = Column(String)
    city = Column(String)
    state = Column(String)
    country = Column(String)
    phone = Column(String)
    technologies = Column(Text)
    keywords = Column(Text)
    annual_revenue = Column(Float)
    total_funding = Column(Float)
    latest_funding = Column(String)
    sic_codes = Column(String)
    naics_codes = Column(String)
    apollo_account_id = Column(String)

    # --- researched: "what they do". Provenance required.
    description = Column(Text)
    description_source_url = Column(String)
    description_fetched_at = Column(DateTime)

    people = relationship("Person", back_populates="company")

    @property
    def location(self):
        parts = [p for p in (self.city, self.state, self.country) if p]
        return ", ".join(parts) if parts else None

    @property
    def domain(self):
        if not self.website:
            return None
        return (
            self.website.replace("https://", "")
            .replace("http://", "")
            .replace("www.", "")
            .rstrip("/")
        )

    @property
    def clean_description(self):
        """`description` as prose, with scraped markup removed.

        The research step stores whatever the source page gave it, and a
        scraped page brings headings, image tags and link syntax with it. Both
        the company card and the profile summary read this instead of the raw
        column so neither has to render "# **Close the gap...**".
        """
        from app.summary import clean_description
        return clean_description(self.description)

    @property
    def keyword_list(self):
        if not self.keywords:
            return []
        return [k.strip() for k in self.keywords.split(",") if k.strip()][:12]


class Person(Base):
    __tablename__ = "people"

    id = Column(Integer, primary_key=True)
    slug = Column(String, unique=True, index=True)

    # --- from the CSV
    first_name = Column(String)
    last_name = Column(String)
    title = Column(String)
    seniority = Column(String)
    department = Column(String)
    sub_department = Column(String)

    email = Column(String)
    email_status = Column(String)

    # --- step 3, Missing Data Enrichment. A value this app went and found
    #     carries where it came from and when, exactly like a web finding.
    #     A value from the CSV carries nothing: the file is the source.
    email_source = Column(String)          # why we believe it is theirs
    email_source_url = Column(String)      # the page it was published on
    email_found_at = Column(DateTime)
    linkedin_source = Column(String)
    linkedin_found_at = Column(DateTime)
    # Why a missing field is still missing, so the page can say so rather than
    # showing a blank the reader has to interpret.
    identity_note = Column(String)
    linkedin_url = Column(String)
    twitter_url = Column(String)
    facebook_url = Column(String)

    phone_direct = Column(String)   # 0/25 in the Apollo export — usually empty
    phone_corporate = Column(String)

    city = Column(String)
    state = Column(String)
    country = Column(String)

    apollo_contact_id = Column(String)

    company_id = Column(Integer, ForeignKey("companies.id"))
    company = relationship("Company", back_populates="people")

    # --- pipeline state, drives the badges and the stat tiles
    # Whether this person's own identity is complete. Set only by
    # recompute_status(); never assign it directly.
    enrichment_status = Column(String, default=STATUS_NEEDS_ENRICHMENT, index=True)
    # Whether research has run for them, and how it went.
    research_status = Column(String, default=RESEARCH_PENDING, index=True)

    # Legacy single field. Kept in step with enrichment_status so an older
    # query or export doesn't silently read a frozen value; nothing in the app
    # reads it any more.
    status = Column(String, default=STATUS_NEEDS_ENRICHMENT, index=True)
    status_note = Column(String)
    imported_at = Column(DateTime, default=utcnow)

    # --- designation is one of the three pillars, and the CSV's copy of it
    #     goes stale. When research turns up a different title, keep both and
    #     flag the mismatch rather than silently trusting either one.
    title_observed = Column(String)
    title_observed_source = Column(String)
    title_observed_at = Column(DateTime)

    # --- derived: the "Current Focus" sentence under the interest chips
    focus_line = Column(Text)
    focus_generated_at = Column(DateTime)
    focus_model = Column(String)

    activities = relationship(
        "LinkedInActivity",
        back_populates="person",
        cascade="all, delete-orphan",
        order_by="LinkedInActivity.rank",
    )
    findings = relationship(
        "WebFinding",
        back_populates="person",
        cascade="all, delete-orphan",
        order_by="WebFinding.rank",
    )
    outreach_steps = relationship(
        "OutreachStep",
        back_populates="person",
        cascade="all, delete-orphan",
        order_by="OutreachStep.order_index",
    )
    interests = relationship(
        "Interest",
        back_populates="person",
        cascade="all, delete-orphan",
        order_by="Interest.rank",
    )

    # ---- outreach sequence. See app/outreach.py for the sequence itself; the
    #      properties here answer only "where is this contact in it", which is
    #      what the list columns and the notification count are built on.

    @property
    def outreach_open(self):
        """The one step still open, or None. Never more than one by design."""
        for step in self.outreach_steps:
            if step.is_open:
                return step
        return None

    @property
    def outreach_stage(self):
        from app.outreach import STAGE_NEW, STAGE_IN_PROGRESS, STAGE_DONE
        if not self.outreach_steps:
            return STAGE_NEW
        return STAGE_IN_PROGRESS if self.outreach_open else STAGE_DONE

    @property
    def outreach_stage_label(self):
        from app.outreach import STAGE_LABELS
        return STAGE_LABELS.get(self.outreach_stage, self.outreach_stage)

    @property
    def outreach_done_count(self):
        return sum(1 for s in self.outreach_steps if s.done_at)

    @property
    def outreach_total(self):
        from app.outreach import SEQUENCE
        return len(SEQUENCE)

    # ---- segmenting. Derived on read from the CSV's own fields, never stored,
    #      so a re-upload or an enrichment run cannot leave a stale segment
    #      behind. See app/segments.py for how the mapping is decided.

    @property
    def category(self):
        """(key, label) - Education, Medical & Health, Engineering, ..."""
        from app.segments import category_of
        return category_of(self)

    @property
    def category_label(self):
        return self.category[1]

    @property
    def segment_countries(self):
        """Every country this contact can be filtered on: theirs, and their
        employer's. They are different facts and often differ."""
        from app.segments import countries_of
        return countries_of(self)

    @property
    def opportunities(self):
        """What Screwdriver can offer this contact's employer.

        Matched against fields already on the record — see app/opportunities.py.
        Computed on read, so it tracks the data rather than going stale, and it
        can be empty: a capability with nothing to point at is left out.
        """
        from app.opportunities import for_person
        return for_person(self)

    @property
    def profile_summary(self):
        """The narrative paragraph shown on the dashboard and the person page.

        Composed from fields already on the record rather than generated, so it
        exists for every contact and costs nothing — see app/summary.py for why
        that choice was made. Computed on read, so it never goes stale against
        the data it describes.
        """
        from app.summary import compose
        return compose(self)

    @property
    def full_name(self):
        return " ".join(p for p in (self.first_name, self.last_name) if p)

    @property
    def initials(self):
        """No photo in the CSV, and LinkedIn photos can't be hotlinked or
        reused — so the avatar is initials."""
        a = (self.first_name or " ")[:1].upper()
        b = (self.last_name or " ")[:1].upper()
        return (a + b).strip() or "?"

    @property
    def location(self):
        parts = [p for p in (self.city, self.state, self.country) if p]
        return ", ".join(parts) if parts else None

    @property
    def status_label(self):
        return STATUS_LABELS.get(self.enrichment_status, self.enrichment_status)

    @property
    def research_label(self):
        return RESEARCH_LABELS.get(self.research_status, self.research_status)

    @property
    def title_drift(self):
        """True when the web says a different job title than the file does."""
        if not (self.title and self.title_observed):
            return False
        return _title_key(self.title) != _title_key(self.title_observed)

    def missing_critical(self):
        """The identity gaps that hold a contact at Needs Enrichment.

        Only fields that say *who this person is* and how to reach them. Each
        one is a thing enrichment could actually go and fill.

        Designation is here because it is one of the three pillars this tool is
        built on (Company · Designation · Person) and it comes from the file, so
        a blank one is a real hole in the record. Drop it from this list if you'd
        rather treat it as optional — nothing else has to change.
        """
        gaps = []
        if not self.email:
            gaps.append("email")
        if not self.linkedin_url:
            gaps.append("LinkedIn URL")
        if not self.title:
            gaps.append("designation")
        if self.company and not self.company.website:
            gaps.append("company website")
        return gaps

    def missing_optional(self):
        """Gaps worth showing that must never set Needs Enrichment.

        None of these describe the person's identity:

        - Direct phone: the Apollo export carries one for nobody, so counting it
          would hold every contact at Needs Enrichment forever.
        - Company description: a fact about the employer, and one research
          fills in — not something missing from this person's record.
        - Research and LinkedIn activity: the absence of these means nobody has
          looked yet, or there was nothing public to find. That's research
          status, which is now tracked separately.
        """
        gaps = []
        if not self.phone_direct:
            gaps.append("direct phone (optional)")
        if self.company and not self.company.description:
            gaps.append("company description (optional)")
        if not self.activities:
            gaps.append("LinkedIn activity (optional)")
        if not self.findings:
            gaps.append("web research (optional)")
        return gaps

    def missing_fields(self, blocking_only=False):
        """Everything still outstanding; critical first."""
        if blocking_only:
            return self.missing_critical()
        return self.missing_critical() + self.missing_optional()

    @property
    def is_complete(self):
        """Identity complete. Says nothing about whether research has run."""
        return not self.missing_critical()

    @property
    def is_researched(self):
        return bool(self.findings or self.activities)

    @property
    def can_research(self):
        """Whether to offer the Research button.

        Offered when nothing has been found yet or the last attempt failed.
        Not offered while a run is in progress, and not for a contact that
        already has results — re-running costs API credits, so that is a
        deliberate act via the CLI's --force rather than a button anyone can
        hit twice.
        """
        return self.research_status in (RESEARCH_PENDING, RESEARCH_FAILED)

    def recompute_status(self, research_failed=False, note=None):
        """The single place either status is decided.

        Previously five call sites each ran their own `status = COMPLETE if
        is_complete else NEEDS_ENRICHMENT`, so a change to the rule had to be
        made five times and a research failure overwrote the enrichment answer.
        """
        self.enrichment_status = (
            STATUS_COMPLETE if self.is_complete else STATUS_NEEDS_ENRICHMENT
        )
        if research_failed:
            self.research_status = RESEARCH_FAILED
        elif self.is_researched:
            self.research_status = RESEARCH_DONE
        elif self.research_status != RESEARCH_RUNNING:
            # A run in progress stays visible. Without this guard an import or
            # an activity edit mid-run would reset the badge to "Not
            # researched" and the button would offer to start a second one.
            self.research_status = RESEARCH_PENDING
        if note is not None:
            self.status_note = note
        self.status = self.enrichment_status   # legacy mirror
        return self.enrichment_status


class LinkedInActivity(Base):
    """
    Either pasted by hand on the person's page, or found in the web search
    index by research.find_linkedin_activity. The tool never logs into
    LinkedIn or fetches a linkedin.com page for either route.

    added_by says which one produced the row: "pasted" or "search". A searched
    row carries the query and fetch time, the same provenance a WebFinding
    carries, because the app claims it for anything it fetched itself.
    """
    __tablename__ = "linkedin_activities"

    id = Column(Integer, primary_key=True)
    person_id = Column(Integer, ForeignKey("people.id"), index=True)
    person = relationship("Person", back_populates="activities")

    activity_type = Column(String, default=ACTIVITY_POST)
    url = Column(String)
    text = Column(Text)
    activity_date = Column(Date)      # the date shown on the post
    rank = Column(Integer, default=0)  # 1..5, the display order

    added_at = Column(DateTime, default=utcnow)
    added_by = Column(String, default="manual")

    fetched_at = Column(DateTime)      # set only for rows found by search
    source_query = Column(String)      # the query that surfaced it — auditable

    # ---- the recommended comment for this post. Steps 1 and 2 of the outreach
    #      sequence are "comment on their post", so the draft lives beside the
    #      post it replies to. suggested_note carries the reason when no draft
    #      was possible — usually that the indexed snippet is the contact's
    #      headline rather than the post. See app/messages.py.
    suggested_comment = Column(Text)
    suggested_note = Column(String)
    suggested_at = Column(DateTime)
    suggested_model = Column(String)

    # Same two tiers the web findings use. True only when the post URL's author
    # segment matches this contact's profile slug; False for anything found by
    # name, which may be a namesake. Hand-pasted rows are trusted outright — a
    # person judged them.
    corroborated = Column(Boolean, default=False)
    corroboration = Column(String)

    @property
    def from_search(self):
        return self.added_by == "search"

    @property
    def trusted(self):
        """Whether this row may be used to infer anything about the person."""
        return not self.from_search or bool(self.corroborated)

    @property
    def type_label(self):
        return ACTIVITY_LABELS.get(self.activity_type, self.activity_type)


class WebFinding(Base):
    """Automated: Google / web research. Provenance is mandatory here."""
    __tablename__ = "web_findings"

    id = Column(Integer, primary_key=True)
    person_id = Column(Integer, ForeignKey("people.id"), index=True)
    person = relationship("Person", back_populates="findings")

    title = Column(String)
    url = Column(String)
    snippet = Column(Text)
    kind = Column(String)             # article / interview / event / news / profile
    published_date = Column(Date)     # often unknown from search results
    rank = Column(Integer, default=0)

    fetched_at = Column(DateTime, default=utcnow)
    source_query = Column(String)     # the query that surfaced it — auditable

    # Whether this result is demonstrably about *this* person. A name in a
    # search result cannot be told apart from a namesake's, so anything not
    # corroborated is shown last and never used to derive an interest chip.
    corroborated = Column(Boolean, default=False)
    corroboration = Column(String)    # what tied it to them, for the tooltip

    @property
    def clean_snippet(self):
        """`snippet` with scraped markup removed.

        The index returns page content for a page it can fetch, so a snippet
        arrives carrying headings, image tags and link syntax. Rendered raw it
        reads as "# The Assembly Canada ## Speakers and Task Force [Anwar
        Chaudhry](https://...)".
        """
        from app.summary import clean_snippet
        return clean_snippet(self.snippet)


class OutreachStep(Base):
    """One step of the manual outreach sequence for one contact.

    Rows are created as the sequence progresses, not all at once: a step's due
    date is measured from the day the previous step was actually completed, so
    it cannot be known in advance. See app/outreach.py.

    A step is "open" until it is either done or skipped. Exactly one step per
    contact is open at a time, which is what makes "today's tasks" a simple
    query rather than a scheduling problem.
    """
    __tablename__ = "outreach_steps"

    id = Column(Integer, primary_key=True)
    person_id = Column(Integer, ForeignKey("people.id"), index=True)
    person = relationship("Person", back_populates="outreach_steps")

    step_key = Column(String, index=True)   # comment_1 / comment_2 / connect / email
    order_index = Column(Integer, default=0)

    due_date = Column(Date, index=True)
    done_at = Column(DateTime)
    skipped_at = Column(DateTime)
    skip_reason = Column(String)

    # What was actually done — which post was commented on, what the email said.
    # The sequence is only useful on the second pass if it records that.
    note = Column(Text)

    created_at = Column(DateTime, default=utcnow)

    @property
    def is_open(self):
        return self.done_at is None and self.skipped_at is None

    @property
    def label(self):
        from app.outreach import label_for
        return label_for(self.step_key)

    @property
    def hint(self):
        from app.outreach import step_def
        d = step_def(self.step_key)
        return d.get("hint") if d else None

    @property
    def days_until_due(self):
        """Negative when overdue, 0 today, positive when still to come."""
        if not self.due_date:
            return None
        return (self.due_date - utcnow().date()).days

    @property
    def is_due(self):
        """Due today or earlier — what "pending today" means."""
        d = self.days_until_due
        return self.is_open and d is not None and d <= 0

    @property
    def is_overdue(self):
        d = self.days_until_due
        return self.is_open and d is not None and d < 0

    @property
    def blocked_reason(self):
        """Why this step can't be actioned yet, or None.

        A property rather than a template filter so the board and the person
        page get the same answer without either registering anything.
        """
        from app.outreach import blocked_reason
        return blocked_reason(self.person, self.step_key) if self.person else None

    @property
    def state(self):
        if self.done_at:
            return "done"
        if self.skipped_at:
            return "skipped"
        if self.is_overdue:
            return "overdue"
        if self.is_due:
            return "due"
        return "waiting"


class Interest(Base):
    """The chips in the Interests / Current Focus panel."""
    __tablename__ = "interests"

    id = Column(Integer, primary_key=True)
    person_id = Column(Integer, ForeignKey("people.id"), index=True)
    person = relationship("Person", back_populates="interests")

    label = Column(String)
    # Which evidence produced this chip — so a chip is never an unsourced guess.
    evidence = Column(Text)
    derived_from = Column(String)     # "linkedin" | "web" | "both"
    rank = Column(Integer, default=0)
    generated_at = Column(DateTime, default=utcnow)
