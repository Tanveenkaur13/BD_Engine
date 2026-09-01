"""
Loads a .env file from the project root into the environment.

The API keys are read from environment variables (see app/research.py and
app/interests.py) and nothing about that changes here. This only saves you
re-exporting them into every new terminal, which on Windows is otherwise the
whole game.

Deliberately not a dependency. python-dotenv does more than this and would be
the fourth thing to install to run a Python-only app; the file format we need
is KEY=value.

Real environment variables always win over the file, so a value exported in
the shell or set by the host (Railway, Render, Fly) is never overwritten by a
stale .env left in the directory.
"""
import os

ENV_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"
)


def parse(text):
    """KEY=value lines -> dict. Blank lines and # comments are skipped.

    Tolerates the two things people actually paste: a leading `export ` copied
    from a shell snippet, and quotes around the value copied from a dashboard.
    An inline # is left alone, because it is legal inside a key.
    """
    out = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        name, _, value = line.partition("=")
        name, value = name.strip(), value.strip()
        if not name:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[name] = value
    return out


def load(path=ENV_PATH):
    """Returns the names it set, or [] if there is no file to read."""
    try:
        # utf-8-sig: Notepad writes a BOM, which would otherwise end up glued
        # to the first key's name.
        with open(path, encoding="utf-8-sig") as fh:
            pairs = parse(fh.read())
    except (OSError, UnicodeDecodeError):
        return []

    applied = []
    for name, value in pairs.items():
        if name not in os.environ:      # the shell wins
            os.environ[name] = value
            applied.append(name)
    return applied


def status(path=ENV_PATH):
    """(exists, path) — for the Settings page to say where it looked."""
    return os.path.isfile(path), path
