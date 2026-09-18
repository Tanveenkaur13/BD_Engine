"""
Loads the outbound-copy skill from `skills/` and turns it into prompts.

The skill is authored as Markdown, by whoever owns the messaging rather than
whoever owns the code: `SKILL.md` for the process and the guardrails,
`voice-and-format.md` for tone, banned phrases and the word counts,
`screwdriver-knowledge.md` for what Screwdriver actually does. Editing any of
those changes what the app writes, with no deploy and no Python.

The files are the source of truth for *content*. Two things this module adds
on top, because an app needs them and a human-readable brief does not:

  * **JSON, not the Markdown report.** SKILL.md ends with an output format
    aimed at a person reading a document. The app needs fields it can put in a
    textarea, so the prompts here ask for the same information as JSON. Every
    rule about what goes *in* those fields comes from the files unchanged.

  * **One lead, one task.** SKILL.md covers a whole lead — an email and a set
    of comments — in one pass. This app drafts a comment per post, on its own
    screen, at its own moment. So the skill is split into an email prompt and
    a comment prompt, each carrying the shared voice rules.

Cached by modification time, so an edit to a Markdown file shows up on the next
draft without restarting the server.
"""
import os
import re

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Overridable so a test can point at a fixture directory.
SKILLS_DIR = os.environ.get("SKILLS_DIR") or os.path.join(BASE, "skills")

SKILL_FILE = "SKILL.md"
REFERENCES = ("voice-and-format.md", "screwdriver-knowledge.md")

_cache = {"stamp": None, "data": None}


class SkillMissing(RuntimeError):
    """The skill files are not on disk. The app can still run; it just has
    nothing to write from."""


def _find(name, root):
    """A reference lives next to SKILL.md or under references/.

    The folder ships both ways — flat at the top level, and nested under
    `references/` in the packaged copy — so both are accepted rather than
    making the layout a thing anyone has to get right.
    """
    for candidate in (os.path.join(root, name),
                      os.path.join(root, "references", name)):
        if os.path.isfile(candidate):
            return candidate
    return None


def _strip_front_matter(text):
    """Remove the --- name/description --- block a skill carries for its own
    discovery. It tells an agent when to use the skill; inside the prompt it
    would just be noise."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4:].lstrip("\n")
    return text


def _read(path):
    with open(path, encoding="utf-8-sig") as fh:
        return fh.read()


def load(directory=None, force=False):
    """Returns {"skill": str, "voice": str, "knowledge": str, "files": [...]}.

    Raises SkillMissing when SKILL.md is not there.
    """
    root = directory or SKILLS_DIR
    skill_path = _find(SKILL_FILE, root)
    if not skill_path:
        raise SkillMissing(
            f"No {SKILL_FILE} under {root} — the outbound copy rules live "
            "there, and nothing can be drafted without them."
        )

    paths = [skill_path] + [p for p in (_find(n, root) for n in REFERENCES) if p]
    stamp = tuple((p, os.path.getmtime(p)) for p in paths)
    if not force and _cache["stamp"] == stamp:
        return _cache["data"]

    data = {
        "skill": _strip_front_matter(_read(skill_path)).strip(),
        "voice": "",
        "knowledge": "",
        "files": paths,
    }
    for name, key in zip(REFERENCES, ("voice", "knowledge")):
        path = _find(name, root)
        if path:
            data[key] = _read(path).strip()

    _cache["stamp"], _cache["data"] = stamp, data
    return data


def available(directory=None):
    try:
        load(directory)
        return True
    except (SkillMissing, OSError):
        return False


def summary(directory=None):
    """What the Settings page shows: which files are in play."""
    try:
        data = load(directory)
    except (SkillMissing, OSError) as exc:
        return {"ok": False, "note": str(exc), "files": []}
    return {
        "ok": True,
        "note": "",
        "files": [os.path.relpath(p, BASE).replace("\\", "/")
                  for p in data["files"]],
    }


# --------------------------------------------------------------- prompts ----
_JSON_TAIL_EMAIL = """
OUTPUT
Reply with JSON only, no prose around it:

{"subject": "...",
 "body": "...",
 "signal": "the exact supplied fact this was built on",
 "opportunity": "which Screwdriver capability or proof point, and why it fits",
 "confidence": "High" | "Medium" | "Low",
 "why": "one line explaining the confidence"}

If the research will not support a genuine personalised email, do not write one
anyway. Return confidence "Low", leave subject and body empty strings, and put
the reason in "why". That is a correct outcome, not a failure.
"""

_JSON_TAIL_COMMENT = """
OUTPUT
Reply with JSON only, no prose around it:

{"comment": "the 1-2 sentence comment, or null if the post has nothing to
             respond to",
 "insight": "the specific thing in the post the comment answers",
 "confidence": "High" | "Medium" | "Low",
 "why": "one line; if comment is null, why the post was skipped"}

Skipping is a correct outcome. A post with no real idea, announcement, insight
or result in it gets comment: null, not a comment written to fill the slot.
"""


def email_system(directory=None):
    """The system prompt for drafting one cold email."""
    d = load(directory)
    parts = ["You are drafting outbound copy for Screwdriver Films. The rules "
             "below are the brief; follow them literally.",
             "", "=== THE BRIEF ===", d["skill"]]
    if d["voice"]:
        parts += ["", "=== VOICE AND FORMAT ===", d["voice"]]
    if d["knowledge"]:
        parts += ["", "=== WHAT SCREWDRIVER DOES ===", d["knowledge"]]
    parts += ["",
              "You are doing Task 1 (the email) only. Ignore the LinkedIn "
              "comment task and the Markdown report format in the brief — the "
              "output schema below replaces it.",
              _JSON_TAIL_EMAIL]
    return "\n".join(parts)


def comment_system(directory=None):
    """The system prompt for drafting one LinkedIn comment on one post."""
    d = load(directory)
    parts = ["You are drafting outbound copy for Screwdriver Films. The rules "
             "below are the brief; follow them literally.",
             "", "=== THE BRIEF ===", d["skill"]]
    if d["voice"]:
        parts += ["", "=== VOICE AND FORMAT ===", d["voice"]]
    parts += ["",
              "You are doing Task 2 (comments) only, for ONE post, and the "
              "brief's Markdown report format is replaced by the schema below. "
              "Screwdriver is never mentioned in a comment, so the capability "
              "reference is not included here.",
              _JSON_TAIL_COMMENT]
    return "\n".join(parts)


# ------------------------------------------------------- rules in code -----
# The brief states these; they are restated here because a rule only asked for
# in a prompt is a rule that holds most of the time. Same principle the rest of
# this app already applies to drafted copy.
EMAIL_WORDS = (80, 120)         # voice-and-format.md, "Email structure"
COMMENT_SENTENCES = 2           # voice-and-format.md, "Comment structure"
SUBJECT_WORDS_MAX = 8           # voice-and-format.md, "Subject line rules"


def word_count(text):
    return len((text or "").split())


def banned_hits(text, section="email"):
    """Which banned phrases from voice-and-format.md appear in `text`.

    Read out of the Markdown rather than duplicated into Python, so editing the
    list in the file is enough — the checker cannot fall behind the brief.
    """
    try:
        voice = load()["voice"]
    except (SkillMissing, OSError):
        return []
    heading = ("Banned phrases and patterns (email)" if section == "email"
               else "Banned phrases and patterns (LinkedIn comments)")
    start = voice.find(heading)
    if start == -1:
        return []
    end = voice.find("\n## ", start)
    block = voice[start:end if end != -1 else len(voice)]

    phrases = []
    for line in block.splitlines():
        line = line.strip()
        if not line.startswith("- "):
            continue
        # Bullets quote their phrases; anything outside quotes is prose
        # describing a pattern, which code cannot check for.
        for quoted in re.findall(r'"([^"]+)"', line):
            quoted = quoted.strip().strip(".!")
            if len(quoted) >= 4:
                phrases.append(quoted.lower())

    low = (text or "").lower()
    return sorted({p for p in phrases if p in low})
