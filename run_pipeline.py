#!/usr/bin/env python3
"""
Runs steps 3-7 of the pipeline over the contacts in the database:
company research, web research, and interest detection.

    python run_pipeline.py                  # every contact needing work
    python run_pipeline.py --limit 5        # just the first five
    python run_pipeline.py --slug anwar-chaudhry
    python run_pipeline.py --skip-interests # research only, no LLM calls
    python run_pipeline.py --skip-linkedin  # don't look for activity links

Needs FIRECRAWL_API_KEY. Interest detection additionally needs LLM_BASE_URL /
LLM_MODEL / LLM_API_KEY (skip it with --skip-interests, or point it at a local
Ollama, which needs no key).

LinkedIn activity links are found through the same web search index as the
rest of the research — public posts a search engine has already indexed.
LinkedIn itself is never logged into or fetched. Anything found this way is
marked added_by="search"; hand-pasted rows are left alone.
"""
import argparse
import sys
from datetime import datetime, timezone

from app.db import SessionLocal, init_db
from app.models import (
    Person, WebFinding, Interest, LinkedInActivity, STATUS_COMPLETE,
    STATUS_NEEDS_ENRICHMENT, STATUS_FAILED,
)
from app import research, resolve, interests as interests_mod


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--slug")
    ap.add_argument("--skip-interests", action="store_true")
    ap.add_argument("--skip-linkedin", action="store_true",
                    help="don't search for LinkedIn activity links")
    ap.add_argument("--skip-resolve", action="store_true",
                    help="don't try to fill a missing email or LinkedIn URL")
    ap.add_argument("--force", action="store_true",
                    help="re-research contacts that already have findings")
    args = ap.parse_args()

    init_db()
    db = SessionLocal()

    q = db.query(Person).order_by(Person.id)
    if args.slug:
        q = q.filter(Person.slug == args.slug)
    people = q.all()
    if not args.force and not args.slug:
        people = [p for p in people if not p.findings]
    if args.limit:
        people = people[: args.limit]

    if not people:
        print("Nothing to do.")
        return 0

    print(f"{len(people)} contact(s) to process\n")
    done = companies_done = failed = resolved = 0

    for p in people:
        label = f"{p.full_name} ({p.company.name if p.company else 'no company'})"

        # ---- step 3: missing data enrichment
        # First, because step 4 attributes LinkedIn activity by profile slug —
        # filling the URL here means this same run can use it.
        if not args.skip_resolve and (not p.email or not p.linkedin_url):
            try:
                report = resolve.resolve_identity(db, p)
                for field in report["filled"]:
                    print(f"  + {label}: {field} found on the web")
                    resolved += 1
                for field, why in report["unresolved"].items():
                    print(f"  ~ {label}: {field} not resolved — {why}")
            except (research.FirecrawlNotConfigured, research.FirecrawlRejected) as e:
                print(f"  ! {e}")
                return 1
            except Exception as e:
                print(f"  ~ identity enrichment failed for {label}: {e}")

        # ---- step 7: company research (once per company)
        if p.company and not p.company.description:
            try:
                found = research.research_company(p.company)
                if found:
                    p.company.description = found["description"]
                    p.company.description_source_url = found["source_url"]
                    p.company.description_fetched_at = found["fetched_at"]
                    db.commit()
                    companies_done += 1
            except (research.FirecrawlNotConfigured, research.FirecrawlRejected) as e:
                print(f"  ! {e}")
                return 1
            except Exception as e:
                print(f"  ~ company research failed for {p.company.name}: {e}")

        # ---- step 5: web research
        try:
            rows = research.research_person(p, limit=5)
        except (research.FirecrawlNotConfigured, research.FirecrawlRejected) as e:
            print(f"  ! {e}")
            return 1
        except Exception as e:
            print(f"  x {label}: {e}")
            # A failed search says nothing about whether we know who they
            # are, so it no longer overwrites the enrichment answer.
            p.recompute_status(research_failed=True, note=str(e)[:200])
            db.commit()
            failed += 1
            continue

        if args.force:
            for old in list(p.findings):
                db.delete(old)
        for row in rows:
            db.add(WebFinding(person=p, **row))
        db.commit()

        # ---- step 4: LinkedIn activity links
        # Runs before interest detection because chips are derived from the
        # text of these, so they have to exist first.
        n_activities = 0
        if not args.skip_linkedin:
            try:
                pasted = [a for a in p.activities if not a.from_search]
                room = 5 - len(pasted)
                if room > 0:
                    found = research.find_linkedin_activity(p, limit=room)
                    # Never touch what a person typed in; replace only what a
                    # previous search run put there.
                    known = {(a.url or "").split("?")[0] for a in pasted}
                    found = [f for f in found if f["url"] not in known]
                    if found:
                        for old_row in [a for a in p.activities if a.from_search]:
                            db.delete(old_row)
                        db.flush()
                        for i, row in enumerate(found, start=len(pasted) + 1):
                            row = dict(row, rank=i)
                            db.add(LinkedInActivity(person=p, **row))
                        n_activities = len(found)
                        db.commit()
            except (research.FirecrawlNotConfigured, research.FirecrawlRejected) as e:
                print(f"  ! {e}")
                return 1
            except Exception as e:
                print(f"  ~ LinkedIn activity search failed for {p.full_name}: {e}")

        # ---- step 6: interest detection
        n_interests = 0
        if not args.skip_interests:
            try:
                out = interests_mod.detect(p)
                if out["interests"]:
                    for old in list(p.interests):
                        db.delete(old)
                    for item in out["interests"]:
                        db.add(Interest(person=p, **item))
                    n_interests = len(out["interests"])
                if out.get("focus"):
                    p.focus_line = out["focus"]
                    p.focus_generated_at = datetime.now(timezone.utc)
                    p.focus_model = out.get("model")
                db.commit()
            except interests_mod.LLMNotConfigured as e:
                print(f"  ! {e} — continuing without interest detection")
                args.skip_interests = True
            except Exception as e:
                print(f"  ~ interest detection failed for {p.full_name}: {e}")

        p.recompute_status()
        db.commit()
        done += 1

        note = "no public footprint found" if not rows else f"{len(rows)} finding(s)"
        print(f"  {done:>3}. {label}\n       {note}"
              f"{f', {n_activities} LinkedIn link(s)' if n_activities else ''}"
              f"{f', {n_interests} interest chip(s)' if n_interests else ''}"
              f"{'' if p.activities else ' · no LinkedIn activity found (paste it in the UI)'}")

    print(f"\ndone: {done} contact(s), {companies_done} company description(s), {resolved} identity field(s) resolved, {failed} failed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
