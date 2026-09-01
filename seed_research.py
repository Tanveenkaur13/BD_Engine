"""
Seeds the DB with REAL research gathered via Firecrawl for four contacts.

Nothing here is invented. Every web finding carries the URL it came from and
the query that surfaced it. The LinkedIn activities are the kind of thing a
person pastes in on the person's page — link plus text — which is the only way
LinkedIn data enters this tool.
"""
from datetime import datetime, timezone, date

from app.db import SessionLocal, init_db
from app.research import _is_linkedin
from app.models import (
    Person, Company, LinkedInActivity, WebFinding, Interest,
    STATUS_COMPLETE, STATUS_NEEDS_ENRICHMENT,
    ACTIVITY_POST, ACTIVITY_COMMENT, ACTIVITY_TAGGED,
)

NOW = datetime.now(timezone.utc)


def li_date(activity_id):
    """LinkedIn activity IDs embed their creation time in the high bits."""
    try:
        ms = int(activity_id) >> 22
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date()
    except Exception:
        return None


# --------------------------------------------------------------- companies
COMPANY_RESEARCH = {
    "Hibernia College": dict(
        description=(
            "Irish online and blended-learning college, delivering blended programmes "
            "since 2000. Describes itself as a leader in flexible learning and the "
            "largest provider of school teachers in Ireland, with an award-winning "
            "blended learning model combining interactive online content with "
            "offline practice."
        ),
        source="https://hiberniacollege.com/",
    ),
    "ACAMS": dict(
        description=(
            "The Association of Certified Anti-Money Laundering Specialists — the "
            "largest international membership body for anti-financial-crime "
            "professionals. Provides training, runs the globally recognised CAMS "
            "certification, and hosts conferences such as The Assembly."
        ),
        source="https://www.acams.org/en",
    ),
    "Nelson": dict(
        description=(
            "Canada's leading K-12 educational publisher, over a century old and the "
            "country's only heritage education content provider. In 2017 launched "
            "Edwin, its digital learning platform, as the centre of a company-wide "
            "digital transformation."
        ),
        source="https://www.nelson.com/aboutus",
    ),
    "Richmond Hill Public Library": dict(
        description=(
            "The public library system for Richmond Hill, Ontario — books, programmes, "
            "digital resources and community services. Recently completed an "
            "award-winning rebrand and was recognised at the 2023 Innovations "
            "Celebration for a programming partnership."
        ),
        source="https://www.rhpl.ca/about-the-library/leadership",
    ),
}

# ------------------------------------------------------------------ people
RESEARCH = {
    # ---- Rich case: senior, publicly active, speaks at conferences.
    "anwar-chaudhry": dict(
        activities=[
            dict(
                type=ACTIVITY_COMMENT,
                url="https://www.linkedin.com/posts/example-author_example-post-activity-7374203047251660800",
                text=("On how financial professionals can start with AI: \"I got into AI about "
                      "10 years ago [when] I was in software... YouTube was amazing for\" "
                      "self-teaching. Recommends starting with AI Fundamentals — understanding "
                      "the basics of how AI creates new content."),
                aid="7374203047251660800",
            ),
        ],
        findings=[
            dict(title="Leading AFC topics addressed at The Assembly Las Vegas",
                 url="https://www.acams.org/en/opinion/leading-afc-topics-addressed-at-the-assembly-las-vegas",
                 snippet=("Panelist Anwar Chaudhry, head of AI and Content Technology at ACAMS, "
                          "was asked how financial professionals could learn how to begin using AI."),
                 kind="article"),
            dict(title="Speaking at The Assembly Canada — \"Details are Everything: Data's Role in Agentic Artificial Intelligence\"",
                 url="https://www.acams.org/en/events/the-assembly/the-assembly-canada",
                 snippet="Anwar Chaudhry, Head of AI and Content Technology, ACAMS — conference programme listing.",
                 kind="event"),
            dict(title="LinkedIn profile — Head of AI and Content Technology",
                 url="https://www.linkedin.com/in/example-contact-1",
                 snippet=("\"Advancing AI for unstructured digital content; audio/video, images, "
                          "documents, text.\" Experience: ACAMS."),
                 kind="profile"),
            dict(title="ACAMS session listing with Kathy Nugent (LexisNexis Risk)",
                 url="https://www.acams.org/en/print/pdf/node/690",
                 snippet=("Anwar Chaudhry, Head of AI and Content Technology, ACAMS. Presenter: "
                          "Kathy Nugent, CAMS, Director, Financial Crimes Compliance Strategy, LexisNexis Risk."),
                 kind="event"),
        ],
        interests=[
            ("AI & Automation", "LinkedIn comment on learning AI; job title; conference sessions", "both"),
            ("Agentic AI", "Speaking on \"Data's Role in Agentic Artificial Intelligence\" at The Assembly Canada", "web"),
            ("Unstructured Content", "Profile headline: advancing AI for audio/video, images, documents, text", "web"),
            ("Anti-Financial Crime", "Employer is ACAMS; sessions target AFC professionals", "web"),
        ],
        focus=("Bringing AI to unstructured content — audio, video, images, documents — and "
               "teaching anti-financial-crime professionals how to actually adopt it. Currently "
               "speaking on agentic AI and the role of data."),
    ),

    # ---- Rich case, different flavour: brand/content, award-winning rebrand.
    "joshua-dyer": dict(
        activities=[
            dict(
                type=ACTIVITY_POST,
                url="https://www.linkedin.com/posts/example-author_example-post-activity-7214705553829158913",
                text=("\"I have always been driven by the belief that...\" — post reflecting on his "
                      "work, referenced as Josh Dyer, Director of Content and Community Engagement."),
                aid="7214705553829158913",
            ),
            dict(
                type=ACTIVITY_TAGGED,
                url="https://www.linkedin.com/posts/example-author_example-post-activity-7242595683495518208",
                text=("Tagged by Richmond Hill Public Library: \"We're thrilled to be part of this "
                      "year's BiblioCon! Join Joshua Dyer on October 9th as he shares our "
                      "award-winning rebrand journey.\" #bibliocon #rhpl #libraryinnovation"),
                aid="7242595683495518208",
            ),
        ],
        findings=[
            dict(title="RHPL Leadership page — listed as Director, Strategy & Engagement",
                 url="https://www.rhpl.ca/about-the-library/leadership",
                 snippet="Joshua Dyer | Director, Strategy & Engagement | first.last@example.org",
                 kind="profile"),
            dict(title="Richmond Hill library awarded for programming partnership",
                 url="https://www.yorkregion.com/news/richmond-hill-public-library-receives-honourable-mention-award-at-2023-innovations-celebration/article_c07bb5df-0f8f-51ca-a6c1-65f188c4874b.html",
                 snippet=("Honourable mention at the 2023 Innovations Celebration. Pictured from the "
                          "library are Joshua Dyer, Leah Fiore and Darren Solomon."),
                 kind="news"),
            dict(title="BiblioCon speaker — award-winning rebrand journey",
                 url="https://www.linkedin.com/posts/example-author_example-post-activity-7242595683495518208",
                 snippet="Speaking on Oct 9 about RHPL's award-winning rebrand.",
                 kind="event"),
            dict(title="RHPL Board Meeting Agenda, June 19 2025 — Director, Collections & Community Engagement",
                 url="https://www.rhpl.ca/Media/library-sites/rhpl/files/rhpl_06_19_board-agenda-and-package",
                 snippet="Joshua Dyer, Director, Collections & Community Engagement.",
                 kind="article"),
            dict(title="LinkedIn profile — brand and content strategy",
                 url="https://www.linkedin.com/in/example-contact-2",
                 snippet=("\"Brand Strategy, Content Strategy, Digital Marketing, Communications, and "
                          "Program Development.\" Richmond Hill Public Library; San Francisco State."),
                 kind="profile"),
        ],
        interests=[
            ("Brand Strategy", "Profile headline; speaking on RHPL's award-winning rebrand", "both"),
            ("Content Strategy", "Profile headline; title across RHPL documents", "both"),
            ("Library Innovation", "#libraryinnovation on the BiblioCon post; 2023 Innovations award", "both"),
            ("Community Engagement", "Title in RHPL board documents; award was for a programming partnership", "web"),
        ],
        focus=("Ran a rebrand at Richmond Hill Public Library that won recognition and is now on "
               "the conference circuit talking about it. Sits where brand, content and community "
               "programming meet."),
    ),

    # ---- Medium case: findable, but mostly through data-broker pages.
    "laura-macleod": dict(
        activities=[],
        findings=[
            dict(title="LinkedIn profile — now Vice President, Content Services at Nelson",
                 url="https://www.linkedin.com/in/example-contact-3",
                 snippet=("\"Vice President, Content Services at Nelson.\" Education: The University "
                          "of British Columbia. Location: Toronto."),
                 kind="profile"),
            dict(title="ZoomInfo profile — oversees content creation at Nelson",
                 url="https://www.zoominfo.com/p/Laura-Macleod/8269868104",
                 snippet=("\"Executive Director of Content Services at Nelson, where they are "
                          "responsible for overseeing the company's content creation.\""),
                 kind="profile"),
            dict(title="Credited as Executive Editor — \"Method and Meaning in Canadian Environmental History\"",
                 url="https://niche-canada.org/wp-content/uploads/method-and-meaning/chapter0-FrontMatter.pdf",
                 snippet="Higher Education: Anne Williams. Executive Editor: Laura Macleod.",
                 kind="article"),
            dict(title="Listed on Nelson Education's leadership roster",
                 url="https://growjo.com/company/Nelson_Education",
                 snippet="Laura Macleod — Executive Director, Content Services.",
                 kind="profile"),
        ],
        interests=[
            ("Educational Content", "Runs Content Services at a K-12 publisher; editor credit on an academic title", "web"),
            ("K-12 Publishing", "Nelson is Canada's leading K-12 educational publisher", "web"),
            ("Academic Publishing", "Executive Editor credit on a Canadian environmental history volume", "web"),
        ],
        focus=("Leads content services at Canada's largest K-12 publisher, through the company's "
               "shift to its Edwin digital platform. Publishing-editorial background rather than "
               "a public-speaking profile."),
    ),

    # ---- The sparse case. Deliberately left thin: this is what most
    #      mid-level contacts actually look like.
    "angelina-attisano": dict(
        activities=[],
        findings=[
            dict(title="LinkedIn profile — Communications & Administrative Professional",
                 url="https://www.linkedin.com/in/example-contact-4",
                 snippet=("\"Communications & Administrative Professional. Hibernia College / "
                          "Canadian College of Educators. Canada. 196 followers.\""),
                 kind="profile"),
        ],
        interests=[],
        focus=None,
    ),
}


# Titles actually observed on the web, vs. what the CSV says. All three of
# these disagree with the file — which is the point.
TITLE_OBSERVED = {
    "laura-macleod": ("Vice President, Content Services",
                      "https://www.linkedin.com/in/example-contact-3"),
    "joshua-dyer": ("Director, Strategy & Engagement",
                    "https://www.rhpl.ca/about-the-library/leadership"),
    "angelina-attisano": ("Communications & Administrative Professional",
                          "https://www.linkedin.com/in/example-contact-4"),
    "anwar-chaudhry": ("Head of AI and Content Technology",
                       "https://www.linkedin.com/in/example-contact-1"),
}


def main():
    init_db()
    db = SessionLocal()

    for slug, (observed, src) in TITLE_OBSERVED.items():
        p = db.query(Person).filter(Person.slug == slug).first()
        if p:
            p.title_observed = observed
            p.title_observed_source = src
            p.title_observed_at = NOW
    db.commit()

    # wipe research so the script is re-runnable
    db.query(LinkedInActivity).delete()
    db.query(WebFinding).delete()
    db.query(Interest).delete()
    db.commit()

    for name, data in COMPANY_RESEARCH.items():
        c = db.query(Company).filter(Company.name == name).first()
        if not c:
            print(f"  ! company not found: {name}")
            continue
        c.description = data["description"]
        c.description_source_url = data["source"]
        c.description_fetched_at = NOW
    db.commit()

    for slug, data in RESEARCH.items():
        p = db.query(Person).filter(Person.slug == slug).first()
        if not p:
            print(f"  ! person not found: {slug}")
            continue

        for i, a in enumerate(data["activities"], start=1):
            db.add(LinkedInActivity(
                person=p, activity_type=a["type"], url=a["url"], text=a["text"],
                activity_date=li_date(a["aid"]), rank=i, added_at=NOW,
                added_by="pasted",
            ))

        # linkedin.com is excluded from the Google / Web panel — it has its own
        # panel, and a profile link there is either a duplicate or a namesake
        # directory. research_person applies the same rule when collecting, so
        # seeding rows it would have skipped would reintroduce exactly what the
        # panel is meant to keep out. Ranks are assigned after the filter so the
        # panel still numbers 1..n with no gaps.
        kept = [f for f in data["findings"] if not _is_linkedin(f["url"])]
        for i, f in enumerate(kept, start=1):
            db.add(WebFinding(
                person=p, title=f["title"], url=f["url"], snippet=f["snippet"],
                kind=f["kind"], rank=i, fetched_at=NOW,
                source_query=f'"{p.full_name}" {p.company.name if p.company else ""}'.strip(),
            ))

        for i, (label, evidence, src) in enumerate(data["interests"], start=1):
            db.add(Interest(person=p, label=label, evidence=evidence,
                            derived_from=src, rank=i, generated_at=NOW))

        if data["focus"]:
            p.focus_line = data["focus"]
            p.focus_generated_at = NOW
            p.focus_model = "claude-opus-5 (stand-in for the Groq/Ollama call)"

    db.commit()

    for p in db.query(Person).all():
        p.recompute_status()
    db.commit()

    researched = db.query(Person).filter(Person.focus_line.isnot(None)).count()
    print(f"seeded: {db.query(WebFinding).count()} web findings, "
          f"{db.query(LinkedInActivity).count()} LinkedIn activities, "
          f"{db.query(Interest).count()} interest chips, "
          f"{researched} focus lines")
    print(f"companies described: {db.query(Company).filter(Company.description.isnot(None)).count()}/"
          f"{db.query(Company).count()}")


if __name__ == "__main__":
    main()
