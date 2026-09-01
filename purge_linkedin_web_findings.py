#!/usr/bin/env python
"""
Remove linkedin.com rows from the Google / Web panel, and re-rank what's left.

The panel answers "what does the rest of the web say about this contact".
LinkedIn has its own panel that attributes posts by author slug, so a
linkedin.com row here is either a duplicate of that panel or noise — the worst
being /pub/dir/ namesake directories, pages literally titled "50+ <Name>
profiles" that list other people.

research_person no longer stores them (see research._is_linkedin). This clears
the rows written before that rule existed, then closes the gaps in rank so the
panel still numbers 1..n with nothing missing.

Idempotent: a second run finds nothing and changes nothing.

    python purge_linkedin_web_findings.py --dry-run
    python purge_linkedin_web_findings.py
"""
import argparse

from app.db import SessionLocal, init_db
from app.models import Person, WebFinding
from app.research import _is_linkedin


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would change and write nothing")
    args = ap.parse_args()

    init_db()
    db = SessionLocal()

    doomed = [f for f in db.query(WebFinding).all() if _is_linkedin(f.url)]
    for finding in doomed:
        db.delete(finding)
    db.flush()

    reranked = 0
    for person in db.query(Person).all():
        for i, finding in enumerate(person.findings, start=1):
            if finding.rank != i:
                finding.rank = i
                reranked += 1

    if args.dry_run:
        db.rollback()
        print(f"dry run - would remove {len(doomed)} LinkedIn finding(s), "
              f"re-rank {reranked}")
        return
    db.commit()
    print(f"removed {len(doomed)} LinkedIn finding(s), re-ranked {reranked}")


if __name__ == "__main__":
    main()
