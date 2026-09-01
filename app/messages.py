"""
Recommended comment drafts for the LinkedIn posts on a contact's page.

Steps one and two of the outreach sequence are "comment on their post", and a
task that says only that leaves the actual work undone. This drafts the comment,
grounded in the post it is replying to, for a person to edit and send. Nothing
is posted automatically.

The hard constraint is the source text. An activity row's `text` is the search
index's snippet of a LinkedIn page, and for most rows that snippet is page
furniture rather than the post: "Nora Mawla Content Marketing Manager
Videographer Content Creator 8mo". Of the 17 rows on hand, 7 carry something a
comment could reference and 10 carry the person's own headline and nothing else.

So substance is checked before anything is generated. A draft written from a
headline would be a confident comment about nothing — the exact failure the rest
of this app refuses elsewhere — and it would go out under the user's name. Rows
that fail the check say so, and point at the paste form, which is the actual
fix: pasting the real post text makes a good draft possible.

The model's output is then checked in code, not merely requested in the prompt.
Flattery openers, pitches, links and hashtags are rejected outright, because a
comment carrying any of them reads as automated and defeats the point of the
sequence.
"""
import json
import os
import re
from datetime import datetime, timezone

import httpx

TIMEOUT = 60.0

# Page furniture that survives into a snippet. Matched as fragments, since the
# snippet is a run-together of whatever the index captured.
CHROME_FRAGMENTS = (
    "view profile for", "report this post", "close menu", "see more",
    "like  reply", "like · reply", "s post.", "edited.", "sign in", "join now",
    "no more next content", "comments on linkedin", "view organization page for",
)

# Below this many words of non-furniture text there is nothing to reference.
MIN_SUBSTANCE_WORDS = 12

# Openers that make a comment read as automated. Rejected in code.
BANNED_PHRASES = (
    "great post", "great share", "well said", "couldn't agree more",
    "could not agree more", "so true", "spot on", "love this", "this is gold",
    "insightful", "thanks for sharing", "great insights", "totally agree",
    "well put", "amazing post", "very interesting",
)

MAX_WORDS = 60

SYSTEM = """You draft one short LinkedIn comment to leave on a prospect's post.

The comment's only job is to be noticed and remembered. It is a first touch in a
sequence, not a pitch.

Rules, in order of importance:
1. Ground it in the post. Reference a specific detail that is actually in the
   supplied text. If the text is too thin to reference anything specific,
   return null — that is the correct answer, not a failure.
2. Never invent anything about the person, their employer or their work. Use
   only what is supplied.
3. No flattery opener. Never "Great post", "Well said", "Insightful",
   "Thanks for sharing", "Couldn't agree more".
4. No pitch, no offer, no link, no mention of the commenter's own company or
   services.
5. One or two sentences, under 40 words, plain language. Ending on a genuine
   question is good but optional.
6. No hashtags, no emoji, no @mentions.

Return JSON only:
{"comment": "the comment text"} or {"comment": null, "why": "why not"}"""


class LLMNotConfigured(RuntimeError):
    pass


# Characters that survive a model round-trip and then paste badly: a
# non-breaking hyphen renders as a stray glyph in some clients, and zero-width
# characters are invisible here but not in the box the user pastes into.
_ODD_CHARS = {
    "‑": "-",      # non-breaking hyphen
    " ": " ",      # non-breaking space
    "​": "",       # zero-width space
    "﻿": "",       # BOM
    " ": " ",      # line separator
    " ": " ",      # paragraph separator
}


def _flatten(text):
    out = text or ""
    for bad, good in _ODD_CHARS.items():
        out = out.replace(bad, good)
    return " ".join(out.split())


def substance_of(text):
    """The part of a snippet that isn't LinkedIn page furniture.

    Splits on the separators the snippets use, drops any fragment that is
    boilerplate, and returns what is left. Empty or near-empty means the row
    carries a headline and nothing to comment on.
    """
    flat = _flatten(text)
    if not flat:
        return ""
    parts = re.split(r"[.·|]\s*", flat)
    keep = []
    for part in parts:
        p = part.strip()
        if not p:
            continue
        low = p.lower()
        if any(frag.strip(".·| ") in low for frag in CHROME_FRAGMENTS):
            continue
        keep.append(p)
    return " ".join(keep)


def has_substance(text):
    return len(substance_of(text).split()) >= MIN_SUBSTANCE_WORDS


def reject_reason(comment):
    """Why this draft is unusable, or None.

    Enforced here rather than trusted to the prompt, the same way interest chips
    require evidence in code.
    """
    if not comment or not comment.strip():
        return "the model returned nothing"
    text = _flatten(comment)
    low = text.lower()
    for phrase in BANNED_PHRASES:
        if phrase in low:
            return f"opens with filler ({phrase!r})"
    if len(text.split()) > MAX_WORDS:
        return f"too long ({len(text.split())} words)"
    if "http" in low or "www." in low:
        return "contains a link"
    if "#" in text:
        return "contains a hashtag"
    if "@" in text:
        return "contains an @mention"
    return None


def _post_context(person, activity):
    bits = [
        f"Person: {person.full_name}",
        f"Their title: {person.title or 'unknown'}",
        f"Their employer: {person.company.name if person.company else 'unknown'}",
        f"Post type: {activity.type_label}",
    ]
    if activity.activity_date:
        bits.append(f"Posted: {activity.activity_date.isoformat()}")
    if person.interests:
        labels = ", ".join(i.label for i in person.interests if i.label)
        if labels:
            bits.append(f"Known interests: {labels}")
    bits.append("")
    bits.append("Post text (this is all you may reference):")
    bits.append(substance_of(activity.text))
    return "\n".join(bits)


def suggest_comment(person, activity, model=None, base_url=None, api_key=None):
    """Returns {"comment", "model", "generated_at"} or {"reason": "..."}.

    Raises LLMNotConfigured when no endpoint is set. A thin post returns a
    reason rather than raising — it is an expected outcome, not an error.
    """
    if not has_substance(activity.text):
        return {"reason": "The indexed snippet for this post is the contact's "
                          "own headline, not the post — there is nothing "
                          "specific to reference. Paste the real post text and "
                          "a draft becomes possible."}

    base_url = base_url or os.environ.get("LLM_BASE_URL",
                                          "https://api.groq.com/openai/v1")
    model = model or os.environ.get("LLM_MODEL", "openai/gpt-oss-120b")
    api_key = api_key or os.environ.get("LLM_API_KEY", "")
    if not base_url:
        raise LLMNotConfigured("LLM_BASE_URL is not set — see the Settings page.")

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    response = httpx.post(
        f"{base_url.rstrip('/')}/chat/completions",
        headers=headers,
        json={
            "model": model,
            "temperature": 0.4,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": _post_context(person, activity)}],
        },
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return {"reason": "the model returned unparseable JSON"}

    comment = parsed.get("comment")
    if not comment:
        return {"reason": parsed.get("why")
                or "the model judged the post too thin to reference"}

    bad = reject_reason(comment)
    if bad:
        return {"reason": f"draft discarded — {bad}"}

    return {
        "comment": _flatten(comment),
        "model": model,
        "generated_at": datetime.now(timezone.utc),
    }


def draft_for_person(db, person, force=False, limit=None):
    """Draft a comment for each of this contact's posts. Returns counts.

    Skips rows that already carry a draft unless `force`, so re-running is cheap
    and a stray double-click doesn't re-spend on work already done. A row that
    can't be drafted records why, so the panel explains itself rather than
    showing a blank.
    """
    out = {"drafted": 0, "skipped": 0, "no_draft": 0}
    rows = [a for a in person.activities if force or not a.suggested_comment]
    if limit:
        rows = rows[:limit]
    out["skipped"] = len(person.activities) - len(rows)

    for activity in rows:
        try:
            result = suggest_comment(person, activity)
        except LLMNotConfigured:
            raise
        except Exception as exc:
            activity.suggested_note = f"drafting failed: {exc}"[:300]
            activity.suggested_comment = None
            out["no_draft"] += 1
            continue

        if "comment" in result:
            activity.suggested_comment = result["comment"]
            activity.suggested_note = None
            activity.suggested_at = result["generated_at"]
            activity.suggested_model = result["model"]
            out["drafted"] += 1
        else:
            activity.suggested_comment = None
            activity.suggested_note = result["reason"][:300]
            activity.suggested_at = datetime.now(timezone.utc)
            out["no_draft"] += 1
    db.commit()
    return out
