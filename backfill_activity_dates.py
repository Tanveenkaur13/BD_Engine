#!/usr/bin/env python
"""
Backfill LinkedIn activity dates, then re-rank each panel newest first.

Rows written before recency ranking existed have activity_date = None, because
the search index returns no date and nothing decoded one from the URL. The date
was in the URL the whole time — see research.activity_moment. This fills it in
for existing rows so old data ranks the same way new data does.

Idempotent: a row that already has a date is left alone, and re-running changes
nothing. Pasted rows keep their position ahead of searched ones, matching what
pipeline.py does on a fresh run.

    python backfill_activity_dates.py --dry-run
    python backfill_activity_dates.py
"""
import argparse

from app.db import SessionLocal, init_db
from app.models import LinkedInActivity, Person
from app.research import activity_moment


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would change and write nothing")
    args = ap.parse_args()

    init_db()
    db = SessionLocal()
    dated = reranked = 0

    for a in db.query(LinkedInActivity).filter(
            LinkedInActivity.activity_date.is_(None)).all():
        moment = activity_moment(a.url)
        if moment:
            a.activity_date = moment.date()
            dated += 1

    for person in db.query(Person).all():
        rows = list(person.activities)
        if not rows:
            continue
        pasted = [a for a in rows if not a.from_search]
        found = [a for a in rows if a.from_search]
        # Newest first; an undated row can't be shown to be recent, so last.
        found.sort(key=lambda a: (a.activity_date is None,
                                  -a.activity_date.toordinal() if a.activity_date else 0))
        for i, a in enumerate(pasted + found, start=1):
            if a.rank != i:
                a.rank = i
                reranked += 1

    if args.dry_run:
        db.rollback()
        print(f"dry run - would date {dated} row(s), re-rank {reranked}")
        return
    db.commit()
    print(f"dated {dated} row(s), re-ranked {reranked}")


if __name__ == "__main__":
    main()
