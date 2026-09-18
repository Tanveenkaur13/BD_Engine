"""
The one place this app talks to a language model.

Two providers, chosen on the Settings page:

  claude  Anthropic's Messages API, through the official `anthropic` SDK.
  groq    An OpenAI-compatible /chat/completions endpoint. The same shape any
          OpenAI-compatible host speaks, so a self-hosted Ollama or vLLM works
          by changing GROQ_BASE_URL.

Every call site passes a system prompt, a user prompt and an optional JSON
schema, and gets back parsed JSON. Which provider served it is not their
business — that is the point of this module. Previously each of the three call
sites built its own request, which meant three copies of the same thing and
three chances for them to drift.

The two APIs differ in ways that are errors rather than style, and this is
where those differences are absorbed:

  * `max_tokens` is required by Claude and optional for the OpenAI shape.
  * `temperature` does not exist on Claude Sonnet 5 — it is not in the SDK's
    signature and sending it is rejected. Groq still takes it.
  * JSON is guaranteed by `output_config.format` (a real schema) on Claude and
    by `response_format: {"type": "json_object"}` (shape-blind) on Groq.
"""
import json
import os

import httpx

CLAUDE = "claude"
GROQ = "groq"

DEFAULT_PROVIDER = CLAUDE

PROVIDERS = {
    CLAUDE: {
        "label": "Claude",
        "key_var": "ANTHROPIC_API_KEY",
        "model_var": "CLAUDE_MODEL",
        "default_model": "claude-sonnet-5",
        "key_hint": "sk-ant-...",
        "console": "https://console.anthropic.com/settings/keys",
        "note": "Anthropic's Messages API, via the official SDK.",
    },
    GROQ: {
        "label": "Groq",
        "key_var": "GROQ_API_KEY",
        "model_var": "GROQ_MODEL",
        "default_model": "openai/gpt-oss-120b",
        "key_hint": "gsk_...",
        "console": "https://console.groq.com/keys",
        "note": "OpenAI-compatible endpoint. Free tier is enough for this app.",
    },
}

GROQ_BASE_URL_DEFAULT = "https://api.groq.com/openai/v1"

# Generous for what these prompts ask for, and well under the point where the
# Anthropic SDK wants streaming to dodge an HTTP timeout.
DEFAULT_MAX_TOKENS = 4000

# Groq still has a temperature dial. One value for every call, rather than the
# old per-task 0.2 / 0.4 / 0.6: Claude has no such dial, and two providers that
# behave differently on the same prompt would be a worse problem than a blunt
# default. What each task wants is stated in its prompt either way.
GROQ_TEMPERATURE = 0.4

TIMEOUT = 60.0


class LLMNotConfigured(RuntimeError):
    """No API key on file for the chosen provider. Expected on a fresh
    install, not a bug."""


# The old name, kept so nothing that imported it breaks.
ClaudeNotConfigured = LLMNotConfigured


def chosen():
    """What the Settings page selected, regardless of whether it has a key."""
    name = (os.environ.get("LLM_PROVIDER") or "").strip().lower()
    return name if name in PROVIDERS else DEFAULT_PROVIDER


def provider():
    """Which provider actually gets called.

    The chosen one when it has a key. When it does not, whichever other
    provider does — "use whatever is attached" is what someone means when they
    paste one key and press a button, and failing with "no key" while a working
    key sits on the next row would be the app being pedantic at their expense.
    Falls back to the chosen one when nothing is configured at all, so the
    error names the provider they picked.
    """
    want = chosen()
    if api_key(want):
        return want
    for name in PROVIDERS:
        if name != want and api_key(name):
            return name
    return want


def spec(name=None):
    return PROVIDERS[name or provider()]


def api_key(name=None):
    name = name or provider()
    key = (os.environ.get(PROVIDERS[name]["key_var"]) or "").strip()
    # LLM_API_KEY is what this app called the Groq key before there was a
    # choice of provider. Read as a fallback so an existing .env keeps working
    # without anyone re-pasting a key they already had.
    if not key and name == GROQ:
        key = (os.environ.get("LLM_API_KEY") or "").strip()
    return key


def model_name(name=None):
    name = name or provider()
    s = PROVIDERS[name]
    chosen = (os.environ.get(s["model_var"]) or "").strip()
    if not chosen and name == GROQ:
        chosen = (os.environ.get("LLM_MODEL") or "").strip()   # same legacy
    return chosen or s["default_model"]


def configured(name=None):
    return bool(api_key(name))


def label(name=None):
    return PROVIDERS[name or provider()]["label"]


# --------------------------------------------------------------- Claude ----
def _claude_call(system, user, schema, max_tokens, key, model):
    import anthropic

    # The key is passed explicitly rather than left to the SDK's env lookup,
    # because the Settings page can change it while the process is running and
    # a client built once would hold the old one.
    client = anthropic.Anthropic(api_key=key, timeout=TIMEOUT)

    kwargs = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    if schema:
        kwargs["output_config"] = {"format": {"type": "json_schema",
                                              "schema": schema}}
    response = client.messages.create(**kwargs)

    # A safety decline is an HTTP 200 with no usable content, so it has to be
    # checked before reading the blocks rather than after.
    if getattr(response, "stop_reason", None) == "refusal":
        return None
    return "".join(b.text for b in response.content if b.type == "text").strip()


# ----------------------------------------------------------------- Groq ----
def _groq_call(system, user, max_tokens, key, model):
    base = (os.environ.get("GROQ_BASE_URL")
            or os.environ.get("LLM_BASE_URL")          # legacy name
            or GROQ_BASE_URL_DEFAULT)
    response = httpx.post(
        f"{base.rstrip('/')}/chat/completions",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"},
        json={
            "model": model,
            "temperature": GROQ_TEMPERATURE,
            "max_tokens": max_tokens,
            # Shape-blind next to Claude's schema: it promises valid JSON, not
            # the JSON this caller asked for. The prompts describe their own
            # skeleton, which is what this relied on before.
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
        },
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


# ------------------------------------------------------------ the front ----
def json_call(system, user, schema=None, max_tokens=DEFAULT_MAX_TOKENS):
    """Ask the configured model for JSON.

    Returns (parsed, model) — parsed is None if the reply would not parse, or
    if the model declined. Raises LLMNotConfigured when there is no key.

    `schema` is a JSON Schema object. Claude constrains the reply to it, so the
    shape cannot come back wrong; Groq ignores it and is trusted to follow the
    prompt, which is what every call did before this module existed.
    """
    name = provider()
    key, model = api_key(name), model_name(name)
    if not key:
        raise LLMNotConfigured(
            f"No {PROVIDERS[name]['label']} API key on file — add one on the "
            "Settings page."
        )

    if name == CLAUDE:
        text = _claude_call(system, user, schema, max_tokens, key, model)
    else:
        text = _groq_call(system, user, max_tokens, key, model)

    if not text:
        return None, model
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return None, model
    return (parsed if isinstance(parsed, dict) else None), model


def check_key(key=None, name=None):
    """Is this key usable? Returns (ok, message) for the Settings page.

    One real request, the smallest each API allows, because the only honest
    test of a key is whether the service accepts it. Costs a few tokens.
    """
    name = name or provider()
    key = (key if key is not None else api_key(name)).strip()
    model = model_name(name)
    if not key:
        return False, "No key given."

    try:
        if name == CLAUDE:
            import anthropic
            try:
                anthropic.Anthropic(api_key=key, timeout=20.0).messages.create(
                    model=model, max_tokens=1,
                    messages=[{"role": "user", "content": "hi"}],
                )
            except anthropic.AuthenticationError:
                return False, "Anthropic rejected that key."
            except anthropic.PermissionDeniedError:
                return False, "That key is valid but not permitted to use this model."
            except anthropic.NotFoundError:
                return False, f"The key works, but the model {model!r} was not found."
            except anthropic.RateLimitError:
                return True, "Key accepted (rate limited right now, which still proves it)."
        else:
            base = (os.environ.get("GROQ_BASE_URL")
                    or os.environ.get("LLM_BASE_URL")
                    or GROQ_BASE_URL_DEFAULT)
            r = httpx.post(
                f"{base.rstrip('/')}/chat/completions",
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {key}"},
                json={"model": model, "max_tokens": 1,
                      "messages": [{"role": "user", "content": "hi"}]},
                timeout=20.0,
            )
            if r.status_code in (401, 403):
                return False, "Groq rejected that key."
            if r.status_code == 404:
                return False, f"The key works, but the model {model!r} was not found."
            if r.status_code == 429:
                return True, "Key accepted (rate limited right now, which still proves it)."
            r.raise_for_status()
    except httpx.HTTPStatusError as exc:
        return False, f"The API returned {exc.response.status_code}."
    except httpx.HTTPError:
        return False, "Could not reach the API — check the network."
    except Exception as exc:                      # nothing here should 500
        return False, f"Could not verify: {exc}"
    return True, f"{PROVIDERS[name]['label']} key accepted."
