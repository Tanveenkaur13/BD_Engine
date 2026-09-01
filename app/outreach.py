"""
The outreach sequence: comment, wait, comment, connect, email.

Every step is done by hand. Nothing here posts a comment, sends a connection
request or sends an email — the app tracks where each contact is in the
sequence and what is due today, and a person does the work. That is deliberate:
automated LinkedIn engagement gets accounts restricted, and an automated first
touch is exactly the thing this sequence is designed to avoid looking like.

Only one step is ever open per contact. A manual sequence cannot schedule step
three in advance, because its due date depends on the day step two actually
happened rather than the day it was supposed to. So a step is created when the
one before it is completed, with its due date measured from that completion.
Marking a step done is therefore also what schedules the next one.

WAIT_DAYS is the gap before a step becomes due, counted from the previous
step's completion:

    comment_1   0   as soon as the contact enters the sequence
    comment_2   2   the two-day gap, so the second comment isn't same-day
    connect     0   straight after the second comment, while they may still
                    recognise the name from the comment thread
    email       2   two days after the connection request

Those are defaults, not rules — a due date can be moved, and a step can be
skipped with a reason when there is nothing to comment on.
"""
from datetime import date, datetime, time, timedelta, timezone

STEP_COMMENT_1 = "comment_1"
STEP_COMMENT_2 = "comment_2"
STEP_CONNECT = "connect"
STEP_EMAIL = "email"

SEQUENCE = (
    {
        "key": STEP_COMMENT_1,
        "label": "Comment on their post",
        "wait_days": 0,
        "hint": "Pick a recent post from the LinkedIn Activity panel and leave "
                "something specific. The panel is ordered newest first.",
        "needs": "linkedin",
    },
    {
        "key": STEP_COMMENT_2,
        "label": "Comment again",
        "wait_days": 2,
        "hint": "A second comment two days later, on a different post if there "
                "is one. This is the step that makes the name familiar.",
        "needs": "linkedin",
    },
    {
        "key": STEP_CONNECT,
        "label": "Send follow / connection request",
        "wait_days": 0,
        "hint": "Straight after the second comment, while the name is still "
                "fresh from the comment thread.",
        "needs": "linkedin",
    },
    {
        "key": STEP_EMAIL,
        "label": "Send email",
        "wait_days": 2,
        "hint": "Two days after the request. Reference the posts you commented "
                "on rather than opening cold.",
        "needs": "email",
    },
)

BY_KEY = {s["key"]: s for s in SEQUENCE}
ORDER = [s["key"] for s in SEQUENCE]

# What a contact's outreach looks like from the outside. Drives the two columns
# the list view is split into.
STAGE_NEW = "new"                  # nobody has started
STAGE_IN_PROGRESS = "in_progress"  # started, steps still open
STAGE_DONE = "done"                # every step closed

STAGE_LABELS = {
    STAGE_NEW: "New",
    STAGE_IN_PROGRESS: "In progress",
    STAGE_DONE: "Sequence complete",
}


def step_def(key):
    return BY_KEY.get(key)


def label_for(key):
    d = BY_KEY.get(key)
    return d["label"] if d else key


def next_key(key):
    """The step after `key`, or None at the end of the sequence."""
    try:
        i = ORDER.index(key)
    except ValueError:
        return None
    return ORDER[i + 1] if i + 1 < len(ORDER) else None


def due_after(key, completed_on=None):
    """When the step `key` falls due, given the previous step's completion."""
    base = completed_on or date.today()
    d = BY_KEY.get(key)
    return base + timedelta(days=d["wait_days"] if d else 0)


def blocked_reason(person, key):
    """Why this step can't be done yet, or None.

    A step that needs an email address cannot be done for a contact who has
    none. Saying so is more useful than presenting a task that can't be
    actioned — and it points at the enrichment that would unblock it.
    """
    d = BY_KEY.get(key)
    if not d:
        return None
    if d.get("needs") == "email" and not (person.email or "").strip():
        return ("No email address on file — run enrichment, or skip this step.")
    if d.get("needs") == "linkedin" and not (person.linkedin_url or "").strip():
        return ("No LinkedIn URL on file — run enrichment, or skip this step.")
    return None


# --------------------------------------------------------------- actions
#
# Each of these commits. They are the only places outreach state changes, so
# the invariant "exactly one open step per contact" is enforceable by reading
# this section alone.


def start(db, person):
    """Put a contact into the sequence at step one. Idempotent.

    Returns the open step. Calling this on a contact already in the sequence
    returns their current step rather than restarting them — a double-clicked
    button must not wipe progress.
    """
    from .models import OutreachStep
    existing = person.outreach_open
    if existing:
        return existing
    if person.outreach_steps:
        return None          # sequence finished; use restart() to go again
    step = OutreachStep(
        person=person, step_key=ORDER[0], order_index=0,
        due_date=due_after(ORDER[0]),
    )
    db.add(step)
    db.commit()
    return step


def complete(db, step, note=None, when=None):
    """Mark a step done and schedule the next one.

    The next step's due date is measured from the day this one was actually
    completed, not from when it was due — a step done three days late pushes
    the rest of the sequence back by three days, which is the behaviour a human
    running this by hand expects.
    """
    from .models import OutreachStep
    if not step.is_open:
        return None
    done_on = when or date.today()
    step.done_at = datetime.combine(done_on, time.min).replace(tzinfo=timezone.utc)
    if note and note.strip():
        step.note = note.strip()

    nxt = next_key(step.step_key)
    created = None
    if nxt:
        created = OutreachStep(
            person=step.person, step_key=nxt,
            order_index=ORDER.index(nxt),
            due_date=due_after(nxt, done_on),
        )
        db.add(created)
    db.commit()
    return created


def skip(db, step, reason=None):
    """Close a step without doing it, and schedule the next one.

    Needed because the sequence assumes things that aren't always true — a
    contact with no indexed posts has nothing to comment on. Skipping records
    that the step was considered and passed over, which is different from it
    still being open.
    """
    if not step.is_open:
        return None
    step.skipped_at = datetime.now(timezone.utc)
    step.skip_reason = (reason or "").strip() or "no reason given"
    nxt = next_key(step.step_key)
    created = None
    if nxt:
        from .models import OutreachStep
        created = OutreachStep(
            person=step.person, step_key=nxt,
            order_index=ORDER.index(nxt),
            due_date=due_after(nxt, date.today()),
        )
        db.add(created)
    db.commit()
    return created


def reschedule(db, step, new_date):
    """Move an open step's due date. The rest of the sequence still follows
    from actual completion, so only this step moves."""
    if not step.is_open or not new_date:
        return False
    step.due_date = new_date
    db.commit()
    return True


# --------------------------------------------------------------- queries


def open_steps(db):
    """Every open step, soonest due first."""
    from .models import OutreachStep
    return (db.query(OutreachStep)
            .filter(OutreachStep.done_at.is_(None),
                    OutreachStep.skipped_at.is_(None))
            .order_by(OutreachStep.due_date.asc())
            .all())


def due_steps(db, on=None):
    """Open steps due on or before `on` (default today) — the day's task list.

    Overdue steps are included rather than listed separately, because a task
    that slipped is still a task for today; the row shows how late it is.
    """
    on = on or date.today()
    return [s for s in open_steps(db) if s.due_date and s.due_date <= on]


def pending_today_count(db):
    """What the header bell shows."""
    return len(due_steps(db))
