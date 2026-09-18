"""
Interest detection — the chips and the "Focus:" line.

Runs through app/llm.py, which is the only place in this app that calls a
model — one request shape, one key, one model setting.

Two rules the prompt enforces, because the panel is the most trust-sensitive
part of the page:

  1. Every chip must cite the evidence it came from. A chip with no evidence is
     a guess dressed as a fact.
  2. Thin evidence returns nothing. Better an honest empty panel than four
     plausible chips invented from a job title.
"""
import json
import os
from datetime import datetime, timezone

import httpx

from . import llm as _llm

TIMEOUT = 60.0
MIN_EVIDENCE = 2  # fewer signals than this and we don't guess

SYSTEM = """You infer a professional's current interests from evidence about them.

Rules, in order of importance:
1. Ground every interest in the supplied evidence. Never infer an interest from
   a job title alone.
2. If the evidence is thin or generic, return an empty list. An empty answer is
   correct and expected; a plausible invention is a failure.
3. Prefer what the person themselves said (their posts and comments) over what
   others wrote about them.
4. Interests are 1-3 word noun phrases, title case. At most 5.
5. The focus line is one or two sentences, concrete and specific to this person.
   Say what they are actually working on or talking about. No marketing tone.

Return JSON only:
{"interests": [{"label": "...", "evidence": "which item supports this",
                "derived_from": "linkedin" | "web" | "both"}],
 "focus": "one or two sentences, or null if the evidence is too thin"}"""


# Kept under the old name so every `except LLMNotConfigured` in pipeline.py
# and main.py still catches it; the class itself now lives in app/llm.py,
# because "no key on file" is one condition, not one per module.
LLMNotConfigured = _llm.LLMNotConfigured


class _UnusedLLMNotConfigured(RuntimeError):
    pass


def _build_evidence(person):
    lines = []
    for a in person.activities:
        # A link with no indexed text carries no meaning; passing "None" to the
        # model would invent a focus out of nothing. The type_label is kept so
        # the model can see a Repost is someone else's words, not this
        # person's.
        if not (a.text or "").strip():
            continue
        # A search row we couldn't tie to this contact may be a namesake's, so
        # it is displayed but never inferred from. Pasted rows are trusted: a
        # person judged them.
        if not a.trusted:
            continue
        when = a.activity_date.isoformat() if a.activity_date else "date unknown"
        # Rule 3 tells the model to prefer the person's own words, so whose
        # words these are has to be on the line. A Repost or a Tagged row is
        # someone else writing about them — usable as evidence of what they
        # are involved in, not of how they talk about it.
        whose = ("someone else's words about them"
                 if a.activity_type in ("repost", "tagged")
                 else "their own words")
        lines.append(f"[LinkedIn {a.type_label}, {when}, {whose}] {a.text}")
    for f in person.findings:
        # Unconfirmed findings are excluded, not merely ranked lower. A search
        # for a common name returns strangers, and a chip derived from one is
        # sourced, clickable and about somebody else — the worst failure this
        # app can have, because it looks correct.
        if not f.corroborated:
            continue
        lines.append(f"[Web {f.kind}] {f.title} — {f.snippet or ''}".strip(" —"))
    return lines


def detect(person, model=None, base_url=None, api_key=None):
    """
    Returns {"interests": [...], "focus": str|None}.

    Raises LLMNotConfigured if no endpoint is reachable. Returns empty results
    (rather than raising) when the evidence itself is too thin.
    """
    evidence = _build_evidence(person)
    if len(evidence) < MIN_EVIDENCE:
        return {"interests": [], "focus": None,
                "note": f"only {len(evidence)} piece(s) of evidence — not enough to infer"}

    user = (
        f"Person: {person.full_name}\n"
        f"Title in our records: {person.title or 'unknown'}\n"
        f"Title observed on the web: {person.title_observed or 'not checked'}\n"
        f"Company: {person.company.name if person.company else 'unknown'}"
        f" — {person.company.description if person.company and person.company.description else 'no description'}\n\n"
        "Evidence:\n" + "\n".join(f"- {e}" for e in evidence)
    )

    # The shape is fixed, so it is declared rather than hoped for. Every
    # field is required and nullable instead of optional: a strict schema has
    # no "maybe absent", and the code below already treats an empty label or a
    # missing focus as nothing found.
    schema = {
        "type": "object",
        "properties": {
            "interests": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string"},
                        "evidence": {"type": "string"},
                    },
                    "required": ["label", "evidence"],
                    "additionalProperties": False,
                },
            },
            "focus": {"type": ["string", "null"]},
        },
        "required": ["interests", "focus"],
        "additionalProperties": False,
    }
    parsed, model = _llm.json_call(SYSTEM, user, schema=schema)
    if parsed is None:
        return {"interests": [], "focus": None,
                "note": "the model returned nothing usable"}

    interests = []
    for i, item in enumerate((parsed.get("interests") or [])[:5], start=1):
        label = (item.get("label") or "").strip()
        evidence_txt = (item.get("evidence") or "").strip()
        # Rule 1, enforced in code and not merely requested in the prompt.
        if not label or not evidence_txt:
            continue
        interests.append({
            "label": label,
            "evidence": evidence_txt,
            "derived_from": item.get("derived_from") or "both",
            "rank": i,
            "generated_at": datetime.now(timezone.utc),
        })

    return {"interests": interests, "focus": (parsed.get("focus") or None), "model": model}
