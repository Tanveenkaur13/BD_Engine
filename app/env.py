"""
Loads a .env file from the project root into the environment.

The API keys are read from environment variables (see app/research.py and
app/llm.py), so this saves you re-exporting them into every new terminal,
which on Windows is otherwise the whole game. save() writes back to the file
and to os.environ together, which is what lets the Settings page take a key and
have it work on the next click rather than after a restart.

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


def load(path=None):
    """Returns the names it set, or [] if there is no file to read."""
    path = path or ENV_PATH
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


def status(path=None):
    """(exists, path) — for the Settings page to say where it looked."""
    path = path or ENV_PATH
    return os.path.isfile(path), path


def save(values, path=None):
    """Write these KEY=value pairs into .env and into this process.

    Both halves matter. The file is what survives a restart; os.environ is what
    the call sites actually read, and without updating it a key entered on the
    Settings page would appear saved and still not work until the server was
    bounced — which is the whole problem the page exists to solve.

    Rewrites the file line by line rather than dumping a fresh dict, so
    comments, ordering and any key this app doesn't know about are preserved.
    A value of None means "leave whatever is there alone"; an empty string
    means "remove it".

    Note the interaction with load(): a variable exported in the shell wins
    over the file at startup. Setting os.environ here overrides that for the
    running process, which is the intent — you changed it deliberately — but
    the shell will win again after a restart, so the page says so.

    Returns the names it changed.
    """
    # Resolved here, not as a default argument: a default binds ENV_PATH once
    # at import, so a test or a relocated project reassigning env.ENV_PATH
    # would silently keep writing to the original file.
    path = path or ENV_PATH
    values = {k: v for k, v in (values or {}).items() if v is not None}
    if not values:
        return []

    try:
        with open(path, encoding="utf-8-sig") as fh:
            lines = fh.read().splitlines()
    except (OSError, UnicodeDecodeError):
        lines = []

    changed, seen = [], set()
    out = []
    for raw in lines:
        stripped = raw.strip()
        bare = stripped[len("export "):].lstrip() if stripped.startswith("export ") else stripped
        name = bare.partition("=")[0].strip() if "=" in bare else ""
        if name and name in values and not stripped.startswith("#"):
            seen.add(name)
            new = values[name]
            if new == "":
                changed.append(name)
                continue                      # drop the line entirely
            out.append(f"{name}={new}")
            changed.append(name)
        else:
            out.append(raw)

    for name, new in values.items():
        if name not in seen and new != "":
            out.append(f"{name}={new}")
            changed.append(name)

    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out).rstrip("\n") + "\n")

    for name, new in values.items():
        if new == "":
            os.environ.pop(name, None)
        else:
            os.environ[name] = new

    return changed
