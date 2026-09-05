"""
Research one contact: company, web, LinkedIn links, interests.

Extracted so the CLI (run_pipeline.py) and the Research button in the UI run
exactly the same steps. Two copies of this would drift, and the one used less
often would be the one that rotted.

Nothing here prints. The caller decides how to report — the CLI writes lines,
the web request updates a status the page can show.
"""
import threading
from datetime import datetime, timezone

from . import interests as interests_mod
from . import research
from . import resolve
from . import messages as messages_mod
from .db import SessionLocal
from .models import (
    Interest, LinkedInActivity, Person, WebFinding, RESEARCH_RUNNING,
)

MAX_ACTIVITIES = 5


class Blocked(RuntimeError):
    """The whole run can't proceed: no key, bad key, or no credits.

    Distinct from a per-step failure. One contact's search failing is that
    contact's problem; this is everybody's, so the caller should stop rather
    than mark 25 people as failed in turn.
    """


def refresh_linkedin(db, person):
    """Re-run just the LinkedIn Activity search for one contact.

    Extracted out of research_contact so the full pipeline and the standalone
    Refresh button run exactly this, and only this, step — the module
    docstring's reason for research_contact existing at all applies just as
    much to a second copy of its LinkedIn block.

    Only search-found rows are ever touched; anything pasted by hand is never
    replaced (see the `pasted` filter below). A previous search run's rows are
    replaced only when this one actually turns something up, so a transient
    empty result can't wipe out what's already shown — the same guard
    research_contact's own LinkedIn step already relied on.

    Returns the number of activities found this run. Raises Blocked when the
    API refuses the run outright (no key, bad key, no credits).
    """
    pasted = [a for a in person.activities if not a.from_search]
    room = MAX_ACTIVITIES - len(pasted)
    if room <= 0:
        return 0
    found, observed = research.find_linkedin_activity(person, limit=room)
    if observed:
        person.linkedin_observed = observed["url"]
        person.linkedin_observed_source = observed["evidence"][:600]
        person.linkedin_observed_at = observed["fetched_at"]
        db.commit()
    known = {research.clean_activity_url(a.url) for a in pasted}
    found = [f for f in found if f["url"] not in known]
    if found:
        for old_row in [a for a in person.activities if a.from_search]:
            db.delete(old_row)
        db.flush()
        for i, row in enumerate(found, start=len(pasted) + 1):
            db.add(LinkedInActivity(person=person, **dict(row, rank=i)))
        db.commit()
    return len(found)


def research_contact(db, person, skip_interests=False, skip_linkedin=False,
                     skip_resolve=False, force=False):
    """Run every research step for one contact and commit as it goes.

    Returns a dict of counts and a list of non-fatal step failures. Raises
    Blocked when the API refuses the whole run.
    """
    out = {"findings": 0, "activities": 0, "interests": 0,
           "comments_drafted": 0, "company_described": False,
           "resolved": [], "problems": []}

    # ---- step 3: missing data enrichment, first
    #
    # Before everything else, because the rest of the pipeline reads these two
    # fields: the LinkedIn slug is what attributes activity to this contact
    # rather than a namesake, so filling it here means step 4 can actually use
    # it on the same run instead of the next one.
    #
    # Only ever fills a blank. A value that came from the CSV is left alone —
    # the file is the source for what it contains, and overwriting it with a
    # web guess would lose the better fact.
    if not skip_resolve and (not person.email or not person.linkedin_url):
        try:
            report = resolve.resolve_identity(db, person)
            out["resolved"] = report["filled"]
        except (research.FirecrawlNotConfigured, research.FirecrawlRejected) as e:
            raise Blocked(str(e)) from e
        except Exception as e:
            out["problems"].append(f"identity enrichment: {e}")

    # ---- step 7: company research (once per company)
    if person.company and not person.company.description:
        try:
            found = research.research_company(person.company)
            if found:
                person.company.description = found["description"]
                person.company.description_source_url = found["source_url"]
                person.company.description_fetched_at = found["fetched_at"]
                db.commit()
                out["company_described"] = True
        except (research.FirecrawlNotConfigured, research.FirecrawlRejected) as e:
            raise Blocked(str(e)) from e
        except Exception as e:
            out["problems"].append(f"company research: {e}")

    # ---- step 5: web research
    try:
        rows = research.research_person(person, limit=5)
    except (research.FirecrawlNotConfigured, research.FirecrawlRejected) as e:
        raise Blocked(str(e)) from e
    except Exception as e:
        person.recompute_status(research_failed=True, note=str(e)[:200])
        db.commit()
        out["problems"].append(f"web research: {e}")
        return out

    if force:
        for old in list(person.findings):
            db.delete(old)
        db.flush()
    for row in rows:
        db.add(WebFinding(person=person, **row))
    out["findings"] = len(rows)
    db.commit()

    # ---- step 4: LinkedIn activity links
    # Before interest detection, because chips are derived from the text of
    # these, so they have to exist first.
    if not skip_linkedin:
        try:
            out["activities"] = refresh_linkedin(db, person)
        except (research.FirecrawlNotConfigured, research.FirecrawlRejected) as e:
            raise Blocked(str(e)) from e
        except Exception as e:
            out["problems"].append(f"LinkedIn activity: {e}")

    # ---- step 6: interest detection
    if not skip_interests:
        try:
            detected = interests_mod.detect(person)
            if detected["interests"]:
                for old in list(person.interests):
                    db.delete(old)
                db.flush()
                for item in detected["interests"]:
                    db.add(Interest(person=person, **item))
                out["interests"] = len(detected["interests"])
            if detected.get("focus"):
                person.focus_line = detected["focus"]
                person.focus_generated_at = datetime.now(timezone.utc)
                person.focus_model = detected.get("model")
            db.commit()
        except interests_mod.LLMNotConfigured as e:
            out["problems"].append(f"interest detection skipped: {e}")
        except Exception as e:
            out["problems"].append(f"interest detection: {e}")

        # ---- recommended comment per post
        # After interest detection, because the draft is given the interest
        # chips as context — the same reason activities come before interests.
        # Uses the same LLM endpoint, so it is gated on the same flag.
        try:
            counts = messages_mod.draft_for_person(db, person)
            out["comments_drafted"] = counts["drafted"]
        except messages_mod.LLMNotConfigured as e:
            out["problems"].append(f"comment drafting skipped: {e}")
        except Exception as e:
            out["problems"].append(f"comment drafting: {e}")

    person.recompute_status()
    db.commit()
    return out


def research_in_background(slug, skip_interests=False, skip_linkedin=False,
                           skip_resolve=False, force=False):
    """Start a research run for one contact and return immediately.

    Research takes tens of seconds — longer when the API rate-limits and the
    backoff waits — so holding an HTTP request open for it would look like a
    hung page. The contact is parked at "Researching" first, so a refresh shows
    what's happening, and the thread opens its own session because the
    request's is closed the moment the response goes out.
    """
    def run():
        db = SessionLocal()
        try:
            person = db.query(Person).filter(Person.slug == slug).first()
            if person is None:
                return
            try:
                research_contact(db, person, skip_interests=skip_interests,
                                 skip_linkedin=skip_linkedin,
                                 skip_resolve=skip_resolve, force=force)
            except Blocked as e:
                person.recompute_status(research_failed=True, note=str(e)[:200])
                db.commit()
            except Exception as e:                     # never leave it stuck
                person.recompute_status(research_failed=True, note=str(e)[:200])
                db.commit()
        finally:
            db.close()

    thread = threading.Thread(target=run, name=f"research:{slug}", daemon=True)
    thread.start()
    return thread


def mark_running(db, person):
    """Park the contact at Researching so the page can say so."""
    person.research_status = RESEARCH_RUNNING
    person.status_note = None
    db.commit()


def clear_orphaned_refreshes(db):
    """Reset any linkedin_refreshing flag left set by a killed process.

    The refresh runs in a daemon thread, so it cannot outlive the process that
    started it: a restart, a crash or a --reload mid-run leaves the flag set
    with nothing still working on it. The panel would then show "Refreshing…"
    forever and never offer the button again, with no way back from the UI.
    Anything still marked refreshing at startup is by definition an orphan.

    Returns how many were cleared.
    """
    stuck = db.query(Person).filter(Person.linkedin_refreshing == True).all()  # noqa: E712
    for person in stuck:
        person.linkedin_refreshing = False
        person.linkedin_refresh_error = (
            "Interrupted — the server restarted while this refresh was running."
        )
    if stuck:
        db.commit()
    return len(stuck)


def refresh_linkedin_in_background(slug):
    """Start a standalone LinkedIn refresh for one contact and return.

    Mirrors research_in_background, but tracks its own flag
    (linkedin_refreshing) rather than research_status — this is a re-check of
    one panel, not a re-run of the whole record, so it shouldn't flip the
    contact back to "Researching…" or block the (separate) Research button.
    """
    def run():
        db = SessionLocal()
        try:
            person = db.query(Person).filter(Person.slug == slug).first()
            if person is None:
                return
            try:
                refresh_linkedin(db, person)
                person.linkedin_refresh_error = None
            except Exception as e:                     # never leave it stuck
                # research.FirecrawlNotConfigured/FirecrawlRejected included:
                # refresh_linkedin doesn't wrap them in Blocked the way
                # research_contact's caller does, since this run has no
                # further steps for a Blocked/Exception split to matter to.
                person.linkedin_refresh_error = str(e)[:200]
            finally:
                person.linkedin_refreshing = False
                person.linkedin_refreshed_at = datetime.now(timezone.utc)
                db.commit()
        finally:
            db.close()

    thread = threading.Thread(target=run, name=f"linkedin-refresh:{slug}",
                              daemon=True)
    thread.start()
    return thread
