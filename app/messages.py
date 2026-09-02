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


# ===========================================================================
# Options for an outreach step: several comments, or several emails.
#
# The single draft above answers "what could I say on this post". A step on the
# board asks something narrower and harder — "what do I send this person today"
# — and one answer is the wrong shape for it. A reader who dislikes the single
# draft has nothing to fall back on but writing it themselves, which is the work
# this is supposed to remove; three answers side by side also make the weakest
# one visibly weak, which one answer never does.
#
# Each option leads with a different angle, and every option names the signals
# it was written from. An option whose grounding is empty is discarded rather
# than shown, the same rule interests.py applies to a chip with no evidence.
# ===========================================================================

from . import personalisation

OPTION_COUNT = 3

# LinkedIn refuses a comment longer than this, so a draft over it is not a
# stylistic problem but an unpasteable one. Comments are also collapsed behind
# "…see more" after roughly 250 characters in the feed, which is why the length
# bands below sit far under the hard limit rather than near it.
LINKEDIN_COMMENT_MAX_CHARS = 1250
LINKEDIN_COMMENT_VISIBLE_CHARS = 250

# Word bands per length. The panel shows the real count, so these steer the
# prompt and bound the check; they are not presented to the reader as promises.
COMMENT_LENGTHS = {
    "brief": {
        "label": "Brief",
        "words": (15, 30),
        "note": "one sentence — stays visible in the feed without expanding",
    },
    "standard": {
        "label": "Standard",
        "words": (30, 55),
        "note": "two sentences, room for a specific reference and a question",
    },
}
DEFAULT_COMMENT_LENGTH = "brief"

EMAIL_LENGTHS = {
    "short": {"label": "Short", "words": (60, 90),
              "note": "three or four sentences — reads fully on a phone"},
    "medium": {"label": "Medium", "words": (100, 140),
               "note": "room to connect two things about them"},
    "long": {"label": "Long", "words": (150, 200),
             "note": "for a contact we hold a lot on; risks being skimmed"},
}
DEFAULT_EMAIL_LENGTH = "short"

# Tone changes register and nothing else. None of them licenses a claim the
# evidence doesn't carry, which is why the instruction is about wording rather
# than about how enthusiastic to sound.
EMAIL_TONES = {
    "warm": {
        "label": "Warm",
        "note": "peer to peer, contractions, plain words",
        "prompt": "Warm and human. Write as one working person to another: "
                  "contractions, short words, no corporate register. Warmth "
                  "comes from specificity about them, never from adjectives.",
    },
    "direct": {
        "label": "Direct",
        "note": "gets to the point in the first line",
        "prompt": "Direct and brief. Say why you are writing in the first "
                  "sentence. No preamble, no scene-setting, no throat-clearing. "
                  "Respectful but efficient.",
    },
    "formal": {
        "label": "Formal",
        "note": "for senior or public-sector contacts",
        "prompt": "Professional and measured. Full words rather than "
                  "contractions, complete sentences, no slang. Courteous "
                  "without being deferential or florid.",
    },
}
DEFAULT_EMAIL_TONE = "warm"

# Claims a cold email cannot honestly make. A first email that says "following
# up on our call" is not a tone problem, it is a false statement about a
# relationship that does not exist, and it is the fastest way to lose one.
BANNED_EMAIL_PHRASES = (
    "as we discussed", "as discussed", "following up on our call",
    "following up on my last", "per our conversation", "as promised",
    "great to meet you", "great speaking with you", "great chatting",
    "thanks for your time yesterday", "circling back", "just bumping this",
    "as i mentioned", "further to our",
)

# An unfilled template is worse than no draft: it is a draft that looks ready.
PLACEHOLDER_MARKERS = ("[", "]", "{{", "}}", "<name>", "xxx", "your company here")

# ---- the two failures that make a draft unusable without editing it
#
# Both are things a model reaches for when it has nothing specific to say, and
# both are invisible to a reader skimming their own draft — which is exactly
# why they are checked here rather than only asked for in the prompt.

# Claimed common ground. "We both know how hard content is" is not warmth, it
# is a guess about the sender dressed as rapport: nothing on this record says
# anything about the sender's own work, so any shared experience is invented.
# It is also the single most recognisable tell of an automated first touch.
BANNED_COMMONALITY = (
    "we both", "as fellow", "as a fellow", "like you, i", "like you i",
    "in our world", "in our line of work", "in our industry", "us marketers",
    "we marketers", "as marketers", "people like us", "those of us",
    "speaks to me", "so much of this", "i felt this", "all too familiar",
    "story of my life", "same boat", "preaching to the choir",
    # Stems rather than phrases: "this resonates" did not catch "this really
    # resonates", and an adverb dropped between two words defeats any bigram.
    # These words carry the claim on their own, whatever is put in front.
    "resonate", "hits home", "hit home", "felt seen", "spoke to me",
)

# Marketing filler. Every one of these says nothing and signals that the rest
# of the sentence says nothing either. A draft carrying one is not "a bit
# generic", it is a draft the reader would have to rewrite before sending,
# which defeats the point of drafting it.
GENERIC_FILLER = (
    "in today's", "in todays", "in this day and age", "now more than ever",
    "at the end of the day", "game changer", "game-changer",
    "move the needle", "needle-moving", "synergy", "synergies",
    "circle back", "touch base", "leverage", "leveraging", "unlock",
    "unlocking", "supercharge", "revolutionize", "revolutionise",
    "seamless", "seamlessly", "cutting-edge", "cutting edge",
    "best-in-class", "best in class", "world-class", "world class",
    "thought leader", "thought leadership", "deep dive", "low-hanging fruit",
    "win-win", "value-add", "value add", "holistic", "robust solution",
    "innovative solution", "digital-first", "at scale and speed",
    "exciting to see", "the future of work", "space is evolving",
)


def sender():
    """Who the drafts are signed by.

    An email ending in a dash is not copy-and-paste ready: it is copy, paste,
    then remember to sign it. So the signature is part of what gets drafted,
    and the validator checks it arrived.

    From the environment because auth is not implemented and the header shows a
    fixed user — see templates/base.html. When real accounts exist this reads
    the session instead and nothing else here changes.
    """
    return {
        "name": os.environ.get("OUTREACH_SENDER_NAME", "Rahul Goenka"),
        "role": os.environ.get("OUTREACH_SENDER_ROLE", "Head of Marketing"),
        "company": os.environ.get("OUTREACH_SENDER_COMPANY", "Screwdriver"),
    }


def signature_block():
    who = sender()
    return f"{who['name']}\n{who['role']}, {who['company']}"


def _voice_reason(text):
    """Why this draft reads as automated rather than written, or None.

    Checked for both kinds, because the tell is the same in a comment and in an
    email: language that could have been produced without reading anything.
    """
    low = " ".join((text or "").lower().split())
    for phrase in BANNED_COMMONALITY:
        if phrase in low:
            return (f"claims common ground it cannot support ({phrase!r}) — "
                    "nothing on this record says anything about the sender")
    for phrase in GENERIC_FILLER:
        if phrase in low:
            return f"marketing filler ({phrase!r}) — it would have to be rewritten"
    return None


# Words too common to prove a draft actually used the evidence.
_GROUNDING_STOPWORDS = {
    "about", "after", "again", "their", "there", "these", "those", "which",
    "while", "would", "could", "should", "where", "being", "other", "under",
    "between", "through", "because", "however", "content", "company",
    "business", "people", "really", "thing", "things", "great", "using",
}


def _content_tokens(text):
    """Distinctive words in `text`, for checking a draft used its evidence."""
    words = re.findall(r"[a-z][a-z0-9'-]{4,}", (text or "").lower())
    return {w for w in words if w not in _GROUNDING_STOPWORDS}


def grounded_in(draft, evidence):
    """Words the draft and the evidence share, as proof it used it.

    A draft can pass every style rule and still be about nothing in
    particular — the failure this whole feature exists to avoid. Requiring an
    overlap of distinctive words is a blunt test, but it is a test in code
    rather than a hope about the prompt, and it catches the generic paragraph
    that mentions no fact from the record.
    """
    return sorted(_content_tokens(draft) & _content_tokens(evidence))[:6]


def _band_reason(text, band, what):
    """Why this draft's length is wrong, or None.

    Tolerance is deliberately loose: the band steers the model, and the panel
    shows the real count, so the check is here to catch a draft that ignored
    the length entirely rather than to police a word or two.
    """
    words = len((text or "").split())
    low, high = band
    if words > high * 1.25:
        return f"{what} ran to {words} words against a {low}-{high} target"
    if words < low * 0.6:
        return f"{what} came back at {words} words, too thin for {low}-{high}"
    return None


def reject_comment_option(text, evidence, band=None):
    """Why this comment option is unusable, or None.

    `band` is the word range the reader asked for. Passing it makes the choice
    binding rather than a hint to the model: emails were checked against their
    band from the start and comments were not, so "Brief" quietly accepted a
    forty-word comment.
    """
    bad = reject_reason(text)          # the existing style and content rules
    if bad:
        return bad
    flat = _flatten(text)
    if len(flat) > LINKEDIN_COMMENT_MAX_CHARS:
        return (f"{len(flat)} characters — LinkedIn refuses a comment over "
                f"{LINKEDIN_COMMENT_MAX_CHARS}")
    low = flat.lower()
    for marker in PLACEHOLDER_MARKERS:
        if marker in low:
            return f"left a placeholder in it ({marker!r})"
    voice = _voice_reason(flat)
    if voice:
        return voice
    if band:
        over = _band_reason(flat, band, "the comment")
        if over:
            return over
    if not grounded_in(flat, evidence):
        return ("shares no specific word with the post it replies to, so it "
                "would read as generic")
    return None


def reject_email_option(subject, body, evidence, band):
    """Why this email option is unusable, or None."""
    if not (body or "").strip():
        return "the model returned no body"
    if not (subject or "").strip():
        return "the model returned no subject"
    flat = _flatten(body)
    low = flat.lower()
    subj = _flatten(subject)

    for phrase in BANNED_PHRASES:
        if phrase in low:
            return f"contains filler ({phrase!r})"
    for phrase in BANNED_EMAIL_PHRASES:
        if phrase in low:
            return f"claims a relationship that doesn't exist ({phrase!r})"
    for marker in PLACEHOLDER_MARKERS:
        if marker in low or marker in subj.lower():
            return f"left a placeholder in it ({marker!r})"
    if "http" in low or "www." in low:
        return "contains a link"
    if len(subj) > 78:
        return f"subject is {len(subj)} characters — it will be truncated"
    voice = _voice_reason(flat + " " + subj)
    if voice:
        return voice
    # Curiosity about their company is the brief, and a mail that asks nothing
    # is a statement about us rather than an interest in them.
    if "?" not in flat:
        return "asks them nothing — a first email with no question is a pitch"
    # Copy-and-paste ready means signed. An unsigned draft is one the reader
    # has to remember to finish, which is the failure this feature exists to
    # remove.
    if sender()["name"].lower() not in low:
        return f"unsigned — it must end with {sender()['name']}"
    over = _band_reason(flat, band, "the body")
    if over:
        return over
    if not grounded_in(flat, evidence):
        return ("names nothing from the record, so it would read as a "
                "template with their name at the top")
    return None


def _signal_block(signals, cap=6):
    """The evidence, labelled, as the model sees it.

    Labelled rather than merged, for the reason opportunities.py matches per
    field: a draft has to be able to say which fact it used, and a single blob
    makes every citation unverifiable.
    """
    lines = []
    for signal in signals[:cap]:
        text = " ".join((signal["text"] or "").split())[:700]
        if not text:
            continue
        tier = personalisation.TIER_LABELS.get(signal["tier"], signal["tier"])
        lines.append(f"- [{signal['label']} — {tier}] {text}")
    return "\n".join(lines)


def _evidence_text(signals):
    """Every signal's words, for the grounding check."""
    return " ".join((s["text"] or "") for s in signals)


def _call(system, user, temperature=0.6, model=None, base_url=None, api_key=None):
    """One chat-completions call returning parsed JSON, or None."""
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
            # Higher than the single draft's 0.4: three options that read the
            # same are one option, and the checks below catch what drifts.
            "temperature": temperature,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
        },
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    try:
        return json.loads(content), model
    except json.JSONDecodeError:
        return None, model


COMMENT_OPTIONS_SYSTEM = """You are the head of marketing at Screwdriver, a \
studio doing video and multimedia production, custom software, learning design \
and AI-assisted content work. You are leaving a comment on a prospect's \
LinkedIn post.

You are not a fan and not a commenter-for-reach. You are a peer who runs a \
marketing function, read this post properly, and has something to say about it \
that only someone doing this work would say. That is the whole reason a \
stranger's comment gets a reply.

WRITE IT READY TO PASTE. The text you return is what goes in the comment box \
exactly as written. No placeholders, no square brackets, no "[insert x]", no \
notes to the reader, no two options separated by a slash, nothing to fill in.

Rules, in order of importance:

1. Name something specific from the post. A number they gave, a tool they \
named, a decision they described, a phrase they used. Not the subject in \
general — the actual detail. If the post text is too thin to name anything \
specific, return an empty list. That is the correct answer.

2. Add one thought of your own that a marketing lead would have: what their \
point implies, what usually breaks when people try it, the trade-off they did \
not mention, or what you would want to know next. The comment must carry a \
view, not a summary of what they already said.

3. NEVER claim shared experience. You know nothing about your own team's work \
from the evidence supplied, so "we both", "as fellow marketers", "like you, \
I", "in our world", "this resonates" are all inventions. Interest in what THEY \
did is the register, not kinship.

4. No marketing filler of any kind. Never "in today's landscape", "move the \
needle", "game changer", "leverage", "unlock", "seamless", "thought \
leadership", "deep dive", "at scale". If a sentence would survive being \
pasted under a different post, delete it and write a real one.

5. No flattery opener. Never "Great post", "Well said", "Insightful", "Thanks \
for sharing", "Couldn't agree more", "Spot on", "Love this".

6. No pitch, no offer, no mention of Screwdriver or what it sells, no link.

7. Each option must take a genuinely DIFFERENT angle — agree and extend, \
challenge one specific part, ask about the mechanics of what they did, add a \
comparable case from the same field. Three rephrasings of one thought are one \
option.

8. Ending on a question is strongest, and the best question is one only \
someone who actually did what they described could answer.

9. No hashtags, no emoji, no @mentions. Plain sentences, the way a competent \
person types into a comment box.

For each option give:
  angle       — three or four words naming what it leads with
  comment     — the exact text to paste
  grounded_in — the specific detail from the post it uses

Return JSON only:
{"options": [{"angle": "...", "comment": "...", "grounded_in": "..."}]}
or {"options": [], "why": "why none are possible"}"""


EMAIL_OPTIONS_SYSTEM = """You are the head of marketing at Screwdriver, a \
studio doing video and multimedia production, custom software, learning design \
and AI-assisted content work. You are writing a first email to someone who has \
never heard from you.

Write the email a busy person actually replies to. That email does three \
things: it shows in its first two lines that you looked at their specific work, \
it says something about that work worth reading, and it asks one real question \
about how their company does it. You are trying to understand what they are \
building and whether there is a reason to work together — not to present a \
deck.

Genuine curiosity about their company is the point. The question you ask should \
be one you could only ask having read what is in front of you, and one they \
would enjoy answering because it is about their own work.

WRITE IT READY TO SEND. Greeting, body and signature, complete, in the sender's \
own voice, pasteable exactly as written. No placeholders, no square brackets, \
no "[Your Name]", no notes to the reader, no alternatives separated by slashes, \
nothing to fill in.

Rules, in order of importance:

1. Ground it in the supplied evidence. Every option must reference something \
specific from it in the first two sentences — what they said, what their \
employer does, what a source says about them. If the evidence is too thin to \
say anything specific, return an empty list. That is the correct answer.

2. Invent nothing. No claimed prior contact, no invented mutual connection, no \
guessed metric, headcount, budget or plan. Never "as we discussed", "following \
up", "great to meet you", "I saw you were looking for" — you have never spoken \
and you do not know what they are looking for.

3. NEVER claim shared experience or common ground. Nothing here tells you about \
your own team's work, so "we both", "as fellow marketers", "like you, I", "in \
our industry", "this resonates" are guesses about yourself. Curiosity about \
them, not kinship with them.

4. No marketing filler. Never "in today's landscape", "move the needle", "game \
changer", "leverage", "unlock", "supercharge", "seamless", "best-in-class", \
"thought leadership", "deep dive", "synergies", "at scale", "circle back", "I \
hope this finds you well". A sentence that would work in an email to anyone \
else must be cut.

5. At most ONE sentence about what Screwdriver could do, and only where the \
evidence shows the matching need. Say it plainly, as what you do, not as a \
benefit claim. If nothing in the evidence supports an offer, leave it out \
entirely — an email that only asks a good question outperforms one with an \
unfounded pitch.

6. Ask for a reply or a short conversation. Never a 30-minute demo, never a \
calendar link, never "let me know a time that works".

7. Subject line: under 60 characters, specific to them and their company, \
readable as something a human typed. No "Quick question", no "Following up", \
no clickbait, no colons stacking two phrases.

8. Sign off with exactly this, on its own lines, and nothing after it:
{signature}

For each option give:
  angle       — three or four words naming its approach
  subject     — the subject line
  body        — the complete email: "Hi <first name>," through to the \
signature above
  grounded_in — the specific facts from the evidence it uses

Return JSON only:
{"options": [{"angle": "...", "subject": "...", "body": "...", \
"grounded_in": "..."}]}
or {"options": [], "why": "why none are possible"}"""


def suggest_comment_options(person, target, length=DEFAULT_COMMENT_LENGTH,
                            count=OPTION_COUNT, **llm):
    """Alternative comments for one post.

    Returns {"options": [...]} — possibly empty — or {"reason": "..."}. One
    call for all of them: three separate calls cost three times as much and
    give the model no way to make the options differ from each other.
    """
    spec = COMMENT_LENGTHS.get(length) or COMMENT_LENGTHS[DEFAULT_COMMENT_LENGTH]
    low, high = spec["words"]
    post = target["text"]

    company = person.company.name if person.company else "unknown"
    extra = [s for s in personalisation.signals(person)
             if s.get("activity_id") != target.get("activity_id")]

    user = (
        f"Person: {person.full_name}\n"
        f"Their title: {person.title or 'unknown'}\n"
        f"Their employer: {company}\n"
        f"Give exactly {count} options, each {low}-{high} words.\n\n"
        f"THE POST TO COMMENT ON ({target['label']}) — this is the only thing "
        f"an option may reference directly:\n{post}\n\n"
        "Background, for judgement only. Do not quote from it; the comment must "
        "read as a reply to the post above:\n"
        + (_signal_block(extra, cap=4) or "- nothing else on file")
    )

    parsed, model = _call(COMMENT_OPTIONS_SYSTEM, user, **llm)
    if parsed is None:
        return {"reason": "the model returned unparseable JSON"}

    raw = parsed.get("options") or []
    if not raw:
        return {"reason": parsed.get("why")
                or "the model judged the post too thin to reference"}

    kept, discarded = [], []
    for item in raw[:count]:
        text = _flatten(item.get("comment") or "")
        grounding = (item.get("grounded_in") or "").strip()
        # Rule 1 in code, not merely in the prompt — the same enforcement
        # interests.py applies to a chip that arrives with no evidence.
        if not grounding:
            discarded.append("an option arrived with no grounding stated")
            continue
        bad = reject_comment_option(text, post, band=(low, high))
        if bad:
            discarded.append(bad)
            continue
        kept.append({
            "angle": (item.get("angle") or "alternative").strip()[:60],
            "body": text,
            "subject": None,
            "length": length,
            "tone": None,
            "basis": f"{target['label']}; grounded in: {grounding}"[:600],
            "activity_id": target.get("activity_id"),
            "model": model,
        })

    if not kept:
        return {"reason": "every draft was discarded — "
                          + "; ".join(discarded[:3])}
    return {"options": kept, "discarded": discarded, "model": model}


def suggest_email_options(person, tone=DEFAULT_EMAIL_TONE,
                          length=DEFAULT_EMAIL_LENGTH, count=OPTION_COUNT,
                          **llm):
    """Alternative first emails, at the chosen tone and length."""
    from . import opportunities

    tone_spec = EMAIL_TONES.get(tone) or EMAIL_TONES[DEFAULT_EMAIL_TONE]
    len_spec = EMAIL_LENGTHS.get(length) or EMAIL_LENGTHS[DEFAULT_EMAIL_LENGTH]
    low, high = len_spec["words"]

    signals = personalisation.signals(person)
    evidence = _signal_block(signals)
    if not evidence:
        return {"reason": "nothing on this record can be written from"}

    # The offer, and only where it already matched something on the record.
    # opportunities.for_person returns an empty list rather than padding, so a
    # contact with no match gives the model no capability to reach for — which
    # is what rule 4 in the prompt then has nothing to break.
    matched = opportunities.for_person(person)
    offer_block = "\n".join(
        f"- {row['label']}: {row['offer']} (matched because {row['because']})"
        for row in matched[:3]
    ) or "- nothing on this record supports an offer; leave it out"

    first = (person.first_name or person.full_name or "there").split()[0]
    company = person.company.name if person.company else "unknown"

    user = (
        f"Person: {person.full_name} (use \"{first}\" in the greeting)\n"
        f"Their title: {person.title or 'unknown'}\n"
        f"Their employer: {company}\n\n"
        f"TONE: {tone_spec['prompt']}\n"
        f"LENGTH: body of {low}-{high} words.\n"
        f"Give exactly {count} options, each on a different angle.\n\n"
        f"EVIDENCE about them — everything an option may reference:\n{evidence}\n\n"
        f"What Screwdriver could offer, already matched against this record:\n"
        f"{offer_block}"
    )

    # .replace rather than .format: the prompt shows a JSON skeleton, and
    # str.format would read every brace in it as a field.
    system = EMAIL_OPTIONS_SYSTEM.replace("{signature}", signature_block())
    parsed, model = _call(system, user, **llm)
    if parsed is None:
        return {"reason": "the model returned unparseable JSON"}

    raw = parsed.get("options") or []
    if not raw:
        return {"reason": parsed.get("why")
                or "the model judged the evidence too thin to write from"}

    evidence_text = _evidence_text(signals)
    kept, discarded = [], []
    for item in raw[:count]:
        body = _flatten(item.get("body") or "")
        subject = _flatten(item.get("subject") or "")
        grounding = (item.get("grounded_in") or "").strip()
        if not grounding:
            discarded.append("an option arrived with no grounding stated")
            continue
        bad = reject_email_option(subject, body, evidence_text, (low, high))
        if bad:
            discarded.append(bad)
            continue
        kept.append({
            "angle": (item.get("angle") or "alternative").strip()[:60],
            "subject": subject,
            "body": body,
            "tone": tone,
            "length": length,
            "basis": f"grounded in: {grounding}"[:600],
            "activity_id": None,
            "model": model,
        })

    if not kept:
        return {"reason": "every draft was discarded — "
                          + "; ".join(discarded[:3])}
    return {"options": kept, "discarded": discarded, "model": model}


def clear_options(db, person, step_key):
    """Drop this contact's options for one step. Regenerating replaces the set.

    Removed from person.drafts rather than deleted with a query, and that
    detail matters twice over.

    The session is built with expire_on_commit=False (see app/db.py), so a
    loaded collection is never refreshed after a commit. A bulk delete, or even
    a db.delete() on each row, therefore leaves the deleted options sitting in
    person.drafts for the rest of the request — options_for read them back
    alongside the new ones and reported two sets where one had been written.
    Removing from the collection keeps the in-memory picture true, and the
    delete-orphan cascade turns each removal into the DELETE.

    Then it commits rather than flushing. SQLite hands the next INSERT the
    rowid the DELETE just freed, so deleting and inserting inside one
    transaction collides in the identity map: the new row claims id 1 while the
    deleted object still holds id 1, and SQLAlchemy refuses with "another
    instance with key ... is already present in this session". Committing
    evicts the deleted objects, so the reused id is free.

    The cost is that a failed generation leaves the old options gone rather
    than restoring them. That is what we want anyway: the panel treats a
    failure as "no options, and here is why", and keeping a set the reader
    explicitly asked to replace would be worse than showing none.
    """
    doomed = [d for d in person.drafts if d.step_key == step_key]
    if not doomed:
        return
    for draft in doomed:
        person.drafts.remove(draft)
    db.commit()


def store_options(db, person, kind, step_key, options):
    """Replace this contact's options for one step. Returns how many were kept.

    Keyed on the step, not on the kind. comment_1 and comment_2 reply to
    different posts — see personalisation.comment_target — so one set shared
    between them put options written about one post under a header naming
    another.
    """
    from .models import OutreachDraft

    clear_options(db, person, step_key)
    for i, option in enumerate(options, start=1):
        db.add(OutreachDraft(
            person=person, kind=kind, step_key=step_key, variant=i,
            angle=option["angle"], subject=option.get("subject"),
            body=option["body"], tone=option.get("tone"),
            length=option.get("length"), basis=option.get("basis"),
            activity_id=option.get("activity_id"), model=option.get("model"),
            generated_at=datetime.now(timezone.utc),
        ))
    db.commit()
    return len(options)


def options_for(person, step_key):
    """This contact's stored options for one step, in display order."""
    return sorted((d for d in person.drafts if d.step_key == step_key),
                  key=lambda d: d.variant or 0)


def suggest_for_step(db, person, step, tone=None, length=None):
    """Draft options for whichever kind of message this step needs.

    The one entry point the route calls, so the board and the person page
    cannot disagree about what a step offers. Returns the verdict from
    personalisation.assess either way: an unpersonalisable contact is reported,
    not drafted for, and nothing is spent on the model.
    """
    kind = "email" if step.step_key == "email" else "comment"
    # The second comment step should not comment twice on the same post.
    prefer = 1 if step.step_key == "comment_2" else 0

    verdict = personalisation.assess(person, kind, prefer_index=prefer)
    if not verdict["ok"]:
        return {"verdict": verdict, "stored": 0, "reason": verdict["reason"]}

    if kind == "comment":
        result = suggest_comment_options(
            person, verdict["target"],
            length=length or DEFAULT_COMMENT_LENGTH,
        )
    else:
        result = suggest_email_options(
            person,
            tone=tone or DEFAULT_EMAIL_TONE,
            length=length or DEFAULT_EMAIL_LENGTH,
        )

    if "options" not in result:
        return {"verdict": verdict, "stored": 0, "reason": result["reason"]}

    stored = store_options(db, person, kind, step.step_key, result["options"])
    return {"verdict": verdict, "stored": stored,
            "discarded": result.get("discarded", []), "reason": None}
