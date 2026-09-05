import os
from datetime import date, datetime, timezone

from fastapi import FastAPI, Request, Depends, UploadFile, File, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from .db import init_db, get_session, SessionLocal
from .importer import import_csv
from .models import (
    Person, Company, LinkedInActivity, WebFinding, Interest, OutreachStep,
    STATUS_COMPLETE, STATUS_NEEDS_ENRICHMENT, STATUS_FAILED,
    RESEARCH_DONE, RESEARCH_FAILED,
    ACTIVITY_LABELS,
)
from .env import status as env_status
from . import pipeline
from . import outreach
from . import messages
from . import segments

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

app = FastAPI(title="PeopleIntel")
app.mount("/static", StaticFiles(directory=os.path.join(BASE, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE, "templates"))


# --------------------------------------------------------------- filters
def relative_time(value):
    """'2 days ago' — the mockup's timestamp style."""
    if not value:
        return None
    if isinstance(value, datetime):
        d = value.date()
    else:
        d = value
    days = (datetime.now(timezone.utc).date() - d).days
    if days < 0:
        return "just now"
    if days == 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 7:
        return f"{days} days ago"
    if days < 30:
        w = days // 7
        return f"{w} week{'s' if w > 1 else ''} ago"
    if days < 365:
        m = days // 30
        return f"{m} month{'s' if m > 1 else ''} ago"
    y = days // 365
    return f"{y} year{'s' if y > 1 else ''} ago"


def domain_of(url):
    if not url:
        return ""
    u = url.split("//")[-1]
    return u.split("/")[0].replace("www.", "")


def short_date(value):
    if not value:
        return ""
    return value.strftime("%d %b %Y")


def money(value):
    if not value:
        return None
    for cut, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if value >= cut:
            return f"${value / cut:.1f}".rstrip("0").rstrip(".") + suffix
    return f"${value:,.0f}"


templates.env.filters["relative_time"] = relative_time
templates.env.filters["domain"] = domain_of
templates.env.filters["short_date"] = short_date
templates.env.filters["money"] = money


# --------------------------------------------------------------- helpers
def stats(db):
    total = db.query(Person).count()
    return {
        "total": total,
        "imported": total,
        "needs_enrichment": db.query(Person)
                           .filter(Person.enrichment_status == STATUS_NEEDS_ENRICHMENT)
                           .count(),
        # Failure is a research outcome, not a statement about the record.
        "failed": db.query(Person)
                  .filter(Person.research_status == RESEARCH_FAILED).count(),
        # Was counting focus_line, which only exists after a successful LLM
        # call — so a contact with five findings and no chips read as
        # unresearched. It now counts what the word means.
        "researched": db.query(Person)
                      .filter(Person.research_status == RESEARCH_DONE).count(),
        "companies": db.query(Company).count(),
        "companies_described": db.query(Company).filter(Company.description.isnot(None)).count(),
        "drift": sum(1 for p in db.query(Person).all() if p.title_drift),
        "with_activities": db.query(func.count(func.distinct(LinkedInActivity.person_id))).scalar() or 0,
        "with_findings": db.query(func.count(func.distinct(WebFinding.person_id))).scalar() or 0,
        "no_direct_phone": db.query(Person).filter(
            or_(Person.phone_direct.is_(None), Person.phone_direct == "")).count(),
    }


def ctx(request, db, **kw):
    base = {"request": request, "stats": stats(db), "nav": kw.pop("nav", "home"),
            # The header bell is on every page, so its count is resolved here
            # rather than in each route. See outreach.due_steps: this is
            # "open and due today or earlier", overdue included.
            "pending_today": outreach.due_steps(db)}
    base.update(kw)
    return base


def _safe_back(back, fallback):
    """A redirect target inside this app, or the fallback.

    `back` arrives from a form, so anything that could leave the origin is
    discarded — same rule the research button uses.
    """
    return back if back.startswith("/") and not back.startswith("//") else fallback


def people_q(db):
    return db.query(Person).options(joinedload(Person.company)).order_by(Person.id)


@app.on_event("startup")
def startup():
    init_db()
    # A LinkedIn refresh runs in a daemon thread, so nothing survives a
    # restart. Anything still flagged as refreshing is a leftover from a
    # killed process, and would otherwise sit at "Refreshing…" for good.
    db = SessionLocal()
    try:
        pipeline.clear_orphaned_refreshes(db)
    finally:
        db.close()


# ----------------------------------------------------------------- routes
@app.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_session), selected: str = None):
    people = people_q(db).all()
    person = None
    if selected:
        person = db.query(Person).filter(Person.slug == selected).first()
    if person is None:
        # Default to the best-researched contact so the page isn't empty.
        person = max(
            people,
            key=lambda p: (len(p.activities) * 10 + len(p.findings), bool(p.focus_line)),
            default=None,
        )
    return templates.TemplateResponse(
        request, "home.html",
        ctx(request, db, nav="home", people=people[:8], person=person,
            total_people=len(people)),
    )


@app.get("/people", response_class=HTMLResponse)
def people_list(request: Request, db: Session = Depends(get_session),
                q: str = "", status: str = "",
                country: str = "", category: str = ""):
    """The contact list, narrowed by any combination of four filters.

    q and status are SQL; country and category are applied in Python because
    both are derived rather than stored (app/segments.py). Either segment filter
    works alone, and together they intersect.
    """
    query = people_q(db)
    if q:
        like = f"%{q}%"
        query = query.outerjoin(Company).filter(or_(
            Person.first_name.ilike(like), Person.last_name.ilike(like),
            Person.title.ilike(like), Person.email.ilike(like),
            Company.name.ilike(like),
        ))
    if status:
        query = query.filter(Person.enrichment_status == status)

    rows = [p for p in query.all() if segments.matches(p, country, category)]

    # Dropdown options are counted over every contact, not the filtered set, so
    # the options don't disappear the moment you pick one - a select that
    # empties itself when used cannot be undone.
    everyone = people_q(db).all()

    return templates.TemplateResponse(
        request, "people.html",
        ctx(request, db, nav="people", people=rows, q=q, status=status,
            country=country, category=category,
            country_options=segments.country_options(everyone),
            category_options=segments.category_options(everyone),
            total_people=len(everyone)),
    )


# Why an "add activity" post was turned away. Set as ?err= on the redirect;
# without these the form just silently did nothing.
ACTIVITY_ERRORS = {
    "max5": "Five activities is the cap — remove one before adding another.",
    "badtype": "That activity type isn't one of Post, Repost, Comment or Tagged.",
    "nores": "Research is already running or already done for this contact. "
             "Re-running costs API credits — use run_pipeline.py --force.",
    "nolirefresh": "LinkedIn is already refreshing, or this contact hasn't "
                   "been researched yet — run Research first.",
}


@app.get("/person/{slug}", response_class=HTMLResponse)
def person_page(slug: str, request: Request, err: str = "",
                db: Session = Depends(get_session)):
    person = db.query(Person).filter(Person.slug == slug).first()
    if not person:
        return RedirectResponse("/people")
    return templates.TemplateResponse(
        request, "person.html",
        ctx(request, db, nav="people", person=person, full_page=True,
            activity_error=ACTIVITY_ERRORS.get(err)),
    )


@app.get("/companies", response_class=HTMLResponse)
def companies(request: Request, db: Session = Depends(get_session), q: str = ""):
    query = db.query(Company).order_by(Company.name)
    if q:
        query = query.filter(or_(Company.name.ilike(f"%{q}%"),
                                 Company.industry.ilike(f"%{q}%")))
    return templates.TemplateResponse(
        request, "companies.html", ctx(request, db, nav="companies", companies=query.all(), q=q)
    )


@app.get("/company/{company_id}", response_class=HTMLResponse)
def company_page(company_id: int, request: Request, db: Session = Depends(get_session)):
    company = db.query(Company).filter(Company.id == company_id).first()
    if not company:
        return RedirectResponse("/companies")
    return templates.TemplateResponse(
        request, "company.html", ctx(request, db, nav="companies", company=company)
    )


@app.get("/enrichment", response_class=HTMLResponse)
def enrichment(request: Request, db: Session = Depends(get_session)):
    people = people_q(db).all()
    rows = [{"person": p,
             "gaps": p.missing_critical(),
             "optional": p.missing_optional()} for p in people]
    rows.sort(key=lambda r: -len(r["gaps"]))
    return templates.TemplateResponse(
        request, "enrichment.html", ctx(request, db, nav="enrichment", rows=rows)
    )


@app.get("/research", response_class=HTMLResponse)
def research(request: Request, db: Session = Depends(get_session)):
    people = people_q(db).all()
    rows = [{
        "person": p,
        "activities": len(p.activities),
        "findings": len(p.findings),
        "interests": len(p.interests),
    } for p in people]
    rows.sort(key=lambda r: -(r["activities"] * 10 + r["findings"]))
    return templates.TemplateResponse(
        request, "research.html", ctx(request, db, nav="research", rows=rows)
    )


@app.get("/reports", response_class=HTMLResponse)
def reports(request: Request, db: Session = Depends(get_session)):
    people = people_q(db).all()
    by_seniority, by_industry, by_country = {}, {}, {}
    for p in people:
        by_seniority[p.seniority or "Unknown"] = by_seniority.get(p.seniority or "Unknown", 0) + 1
        ind = (p.company.industry if p.company else None) or "Unknown"
        by_industry[ind] = by_industry.get(ind, 0) + 1
        ctry = (p.company.country if p.company else None) or "Unknown"
        by_country[ctry] = by_country.get(ctry, 0) + 1
    drift = [p for p in people if p.title_drift]
    return templates.TemplateResponse(
        request, "reports.html",
        ctx(request, db, nav="reports",
            by_seniority=sorted(by_seniority.items(), key=lambda x: -x[1]),
            by_industry=sorted(by_industry.items(), key=lambda x: -x[1]),
            by_country=sorted(by_country.items(), key=lambda x: -x[1]),
            drift=drift, people=people,
            researched_count=sum(1 for p in people if p.title_observed)),
    )


@app.get("/settings", response_class=HTMLResponse)
def settings(request: Request, db: Session = Depends(get_session)):
    return templates.TemplateResponse(
        request, "settings.html",
        ctx(request, db, nav="settings",
            firecrawl=bool(os.environ.get("FIRECRAWL_API_KEY")),
            llm_base=os.environ.get("LLM_BASE_URL", "https://api.groq.com/openai/v1"),
            llm_model=os.environ.get("LLM_MODEL", "openai/gpt-oss-120b"),
            llm_key=bool(os.environ.get("LLM_API_KEY")),
            env_file_found=env_status()[0],
            env_file_path=env_status()[1]),
    )


@app.get("/upload", response_class=HTMLResponse)
def upload_page(request: Request, db: Session = Depends(get_session)):
    return templates.TemplateResponse(
        request, "upload.html", ctx(request, db, nav="upload", result=None)
    )


@app.post("/upload", response_class=HTMLResponse)
async def do_upload(request: Request, file: UploadFile = File(...),
                    db: Session = Depends(get_session)):
    result = import_csv(db, await file.read(), file.filename or "upload.csv")
    return templates.TemplateResponse(
        request, "upload.html", ctx(request, db, nav="upload", result=result)
    )


# --- LinkedIn activity: pasted by hand, on screen. Never scraped.
@app.post("/person/{slug}/activity")
def add_activity(slug: str, activity_type: str = Form(...), url: str = Form(""),
                 text: str = Form(...), activity_date: str = Form(""),
                 db: Session = Depends(get_session)):
    person = db.query(Person).filter(Person.slug == slug).first()
    if not person:
        return RedirectResponse("/people", status_code=303)
    if len(person.activities) >= 5:
        return RedirectResponse(f"/person/{slug}?err=max5", status_code=303)
    # An unknown type would store a row the panel can't label or colour.
    if activity_type not in ACTIVITY_LABELS:
        return RedirectResponse(f"/person/{slug}?err=badtype", status_code=303)

    parsed = None
    if activity_date:
        try:
            parsed = datetime.strptime(activity_date, "%Y-%m-%d").date()
        except ValueError:
            parsed = None

    db.add(LinkedInActivity(
        person=person, activity_type=activity_type, url=url.strip() or None,
        text=text.strip(), activity_date=parsed,
        rank=len(person.activities) + 1, added_by="pasted",
    ))
    person.recompute_status()
    db.commit()
    return RedirectResponse(f"/person/{slug}#linkedin", status_code=303)


@app.post("/person/{slug}/research")
def research_person_now(slug: str, back: str = Form(""),
                        db: Session = Depends(get_session)):
    """Kick off research for one contact.

    Returns straight away: the work runs in a thread and the contact sits at
    "Researching…" until it lands. Refresh to see the result.
    """
    person = db.query(Person).filter(Person.slug == slug).first()
    if not person:
        return RedirectResponse("/people", status_code=303)

    # `back` comes from the form, so only a path within this app is honoured —
    # otherwise a crafted value turns this endpoint into an open redirect.
    target = back if back.startswith("/") and not back.startswith("//") else ""

    if not person.can_research:
        # Already running, or already has results. Re-running spends credits,
        # so it isn't something a stray double-click should do.
        return RedirectResponse(f"/person/{slug}?err=nores", status_code=303)

    pipeline.mark_running(db, person)
    pipeline.research_in_background(slug)
    return RedirectResponse(target or f"/person/{slug}", status_code=303)


@app.post("/person/{slug}/refresh-linkedin")
def refresh_linkedin_now(slug: str, back: str = Form(""),
                         db: Session = Depends(get_session)):
    """Re-run just the LinkedIn Activity search for one contact.

    Separate from /research on purpose: LinkedIn's own feed moves daily, so
    this one panel is offered again and again rather than the once-only
    Research button — see Person.can_refresh_linkedin.
    """
    person = db.query(Person).filter(Person.slug == slug).first()
    if not person:
        return RedirectResponse("/people", status_code=303)

    target = back if back.startswith("/") and not back.startswith("//") else ""

    if not person.can_refresh_linkedin:
        return RedirectResponse(f"/person/{slug}?err=nolirefresh", status_code=303)

    person.linkedin_refreshing = True
    db.commit()
    pipeline.refresh_linkedin_in_background(slug)
    return RedirectResponse(target or f"/person/{slug}#linkedin", status_code=303)


@app.get("/outreach", response_class=HTMLResponse)
def outreach_board(request: Request, db: Session = Depends(get_session),
                   view: str = "today", country: str = "", category: str = ""):
    """The sequence board, one sheet at a time.

    Split by outreach_stage rather than by research status — the question this
    page answers is "who have we contacted", which is independent of how well
    we know them.

    Each stage is its own sheet rather than a section of one long page: with 27
    contacts the New column alone ran to a full screen, which pushed the
    in-progress list — the one with work in it — below the fold. `view` is
    validated against the known sheets so a bad value lands on Today rather
    than rendering an empty page.
    """
    if view not in ("today", "new", "active", "done"):
        view = "today"

    everyone = people_q(db).all()
    people = [p for p in everyone if segments.matches(p, country, category)]

    new = [p for p in people if p.outreach_stage == outreach.STAGE_NEW]
    active = [p for p in people if p.outreach_stage == outreach.STAGE_IN_PROGRESS]
    done = [p for p in people if p.outreach_stage == outreach.STAGE_DONE]
    # Soonest first inside the active column, so the top of the list is the
    # work that matters next.
    active.sort(key=lambda p: (p.outreach_open.due_date if p.outreach_open
                               and p.outreach_open.due_date else date.max))

    # The Today sheet is a list of steps, not people, so the same filter is
    # applied through each step's contact. Kept separate from ctx's
    # `pending_today`, which the header bell reads: the bell is a count of
    # everything actually due and must not shrink because a segment is being
    # viewed, or you could filter your way into believing nothing is owed.
    due_here = [step for step in outreach.due_steps(db)
                if segments.matches(step.person, country, category)]

    return templates.TemplateResponse(
        request, "outreach.html",
        ctx(request, db, nav="outreach", view=view, new_people=new,
            active=active, done=done, sequence=outreach.SEQUENCE,
            due_here=due_here, country=country, category=category,
            country_options=segments.country_options(everyone),
            category_options=segments.category_options(everyone)),
    )


@app.post("/person/{slug}/outreach/start")
def outreach_start(slug: str, back: str = Form(""),
                   db: Session = Depends(get_session)):
    person = db.query(Person).filter(Person.slug == slug).first()
    if not person:
        return RedirectResponse("/people", status_code=303)
    outreach.start(db, person)
    return RedirectResponse(_safe_back(back, f"/person/{slug}#outreach"),
                            status_code=303)


@app.post("/outreach/{step_id}/done")
def outreach_done(step_id: int, note: str = Form(""), back: str = Form(""),
                  db: Session = Depends(get_session)):
    step = db.query(OutreachStep).filter(OutreachStep.id == step_id).first()
    if not step:
        return RedirectResponse("/outreach", status_code=303)
    slug = step.person.slug
    outreach.complete(db, step, note=note)
    return RedirectResponse(_safe_back(back, f"/person/{slug}#outreach"),
                            status_code=303)


@app.post("/outreach/{step_id}/skip")
def outreach_skip(step_id: int, reason: str = Form(""), back: str = Form(""),
                  db: Session = Depends(get_session)):
    step = db.query(OutreachStep).filter(OutreachStep.id == step_id).first()
    if not step:
        return RedirectResponse("/outreach", status_code=303)
    slug = step.person.slug
    outreach.skip(db, step, reason=reason)
    return RedirectResponse(_safe_back(back, f"/person/{slug}#outreach"),
                            status_code=303)


@app.post("/outreach/{step_id}/reschedule")
def outreach_reschedule(step_id: int, due: str = Form(""), back: str = Form(""),
                        db: Session = Depends(get_session)):
    step = db.query(OutreachStep).filter(OutreachStep.id == step_id).first()
    if not step:
        return RedirectResponse("/outreach", status_code=303)
    slug = step.person.slug
    try:
        parsed = datetime.strptime(due, "%Y-%m-%d").date()
    except ValueError:
        parsed = None
    if parsed:
        outreach.reschedule(db, step, parsed)
    return RedirectResponse(_safe_back(back, f"/person/{slug}#outreach"),
                            status_code=303)


@app.post("/person/{slug}/suggest-comments")
def suggest_comments(slug: str, back: str = Form(""), force: str = Form(""),
                     db: Session = Depends(get_session)):
    """Draft a recommended comment for each of this contact's posts.

    Runs inline rather than in a thread: the LLM answers in a second or two per
    post and there are at most five, so the page can just return the result.
    That is the opposite call from research, which takes tens of seconds.
    """
    person = db.query(Person).filter(Person.slug == slug).first()
    if not person:
        return RedirectResponse("/people", status_code=303)
    try:
        messages.draft_for_person(db, person, force=bool(force))
    except messages.LLMNotConfigured:
        return RedirectResponse(f"/person/{slug}?err=nollm#linkedin",
                                status_code=303)
    return RedirectResponse(_safe_back(back, f"/person/{slug}#linkedin"),
                            status_code=303)


@app.post("/activity/{activity_id}/delete")
def delete_activity(activity_id: int, db: Session = Depends(get_session)):
    a = db.query(LinkedInActivity).filter(LinkedInActivity.id == activity_id).first()
    if not a:
        return RedirectResponse("/people", status_code=303)
    slug = a.person.slug
    person = a.person
    db.delete(a)
    db.commit()
    for i, rest in enumerate(person.activities, start=1):
        rest.rank = i
    person.recompute_status()
    db.commit()
    return RedirectResponse(f"/person/{slug}#linkedin", status_code=303)
