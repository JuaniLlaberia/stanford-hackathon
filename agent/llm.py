"""TokenRouter (OpenAI-compatible) client: JSON in, JSON out, hard deadline, one nudge retry.

Every caller must catch LLMError and fall back to deterministic code; the LLM never sets the action.
"""
from __future__ import annotations

import json
import os
import re
import time

from dotenv import load_dotenv

load_dotenv()

DEADLINE_S = 15.0


class LLMError(RuntimeError):
    """The LLM is unavailable, too slow, or returned unusable output."""


_client = None


def _get_client():
    global _client
    if _client is None:
        base_url = os.getenv("TOKENROUTER_BASE_URL")
        api_key = os.getenv("TOKENROUTER_API_KEY")
        if not (base_url and api_key and os.getenv("TOKENROUTER_MODEL")):
            raise LLMError("TokenRouter not configured (TOKENROUTER_BASE_URL / _API_KEY / _MODEL in .env)")
        from openai import OpenAI

        _client = OpenAI(base_url=base_url, api_key=api_key, timeout=DEADLINE_S, max_retries=0)
    return _client


def available() -> bool:
    return bool(os.getenv("TOKENROUTER_API_KEY") and os.getenv("TOKENROUTER_MODEL"))


def _parse_json(raw: str) -> dict:
    raw = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", raw, re.S)
    if fenced:
        raw = fenced.group(1).strip()
    try:
        out = json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end <= start:
            raise
        out = json.loads(raw[start : end + 1])
    if not isinstance(out, dict):
        raise json.JSONDecodeError("top-level JSON is not an object", raw, 0)
    return out


def chat_json(system: str, user: str, schema: dict | None = None, *, deadline_s: float = DEADLINE_S) -> dict:
    """Return the model's JSON object. `schema` (keys -> description) is described in the prompt.

    Raises LLMError on any failure: not configured, network, timeout (total, across the retry), bad JSON twice.
    """
    client = _get_client()
    model = os.environ["TOKENROUTER_MODEL"]
    sys_msg = system.strip() + "\n\nReply with ONE JSON object and nothing else."
    if schema:
        sys_msg += "\nJSON keys:\n" + json.dumps(schema, indent=1)
    messages = [{"role": "system", "content": sys_msg}, {"role": "user", "content": user}]

    t0 = time.monotonic()
    last_err: Exception | None = None
    for attempt in range(2):  # first try + one nudge retry on bad JSON
        remaining = deadline_s - (time.monotonic() - t0)
        if remaining <= 1:
            raise LLMError(f"deadline of {deadline_s:.0f}s exceeded") from last_err
        try:
            resp = client.chat.completions.create(
                model=model, messages=messages, timeout=remaining,
            )
            raw = resp.choices[0].message.content or ""
        except Exception as e:  # network, auth, timeout, rate limit...
            raise LLMError(f"{type(e).__name__}: {e}") from e
        try:
            return _parse_json(raw)
        except (json.JSONDecodeError, ValueError) as e:
            last_err = e
            messages += [
                {"role": "assistant", "content": raw},
                {"role": "user", "content": "That was not a valid JSON object. Reply again with only the JSON object."},
            ]
    raise LLMError("model returned invalid JSON twice") from last_err


if __name__ == "__main__":  # smoke test: uv run python -m agent.llm
    t = time.monotonic()
    try:
        out = chat_json("You are a ping service.", 'Return {"ok": true, "model": "<your model name>"}.')
        print(f"OK in {time.monotonic() - t:.1f}s:", out)
    except LLMError as e:
        print(f"LLMError after {time.monotonic() - t:.1f}s: {e}")
