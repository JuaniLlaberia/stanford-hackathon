"""Append-only audit log: runs/audit.jsonl (one JSON event per line)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

AUDIT_PATH = Path("runs/audit.jsonl")


def log(event: str, **data) -> dict:
    """Append {ts, event, **data}. Pydantic models are dumped; anything else falls back to str()."""
    rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "event": event, **data}
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, default=_default, ensure_ascii=False) + "\n")
    return rec


def tail(n: int = 20) -> list[dict]:
    if not AUDIT_PATH.exists():
        return []
    lines = AUDIT_PATH.read_text(encoding="utf-8").splitlines()[-n:]
    return [json.loads(line) for line in lines if line.strip()]


def _default(o):
    if hasattr(o, "model_dump"):
        return o.model_dump()
    return str(o)
