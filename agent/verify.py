"""Check a pulled source ("lead") before it counts as evidence. Deterministic; the model only finds leads.

A lead is (url, excerpt[, page_text]). It supports a claim only if:
  1. the excerpt is really in the page we fetched (when we have the page) — the model's copy is a locator, and
  2. every figure in the claim appears in the excerpt with the same scale (million/billion/%), within tolerance.
Numbers are hard constraints: a different figure of the same kind is evidence AGAINST, not "close enough".
Non-numeric claims return "insufficient": a human has to read the source.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, Field

PCT_TOL = 0.5  # percentage points
REL_TOL = 0.005  # 0.5% relative on amounts

_SCALE = {"thousand": 1e3, "k": 1e3, "million": 1e6, "m": 1e6, "mn": 1e6, "billion": 1e9, "bn": 1e9, "b": 1e9,
          "trillion": 1e12, "tn": 1e12}
_QTY = re.compile(
    r"(?<![\w.])[$€£~]?(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s?"
    r"(%|percent|per ?cent|thousand|million|billion|trillion|mn|bn|tn|k|m|b)?(?![\w])",
    re.IGNORECASE,
)
_YEAR = re.compile(r"^(19|20)\d{2}$")
_WS = re.compile(r"\s+")


class Quantity(BaseModel):
    raw: str
    value: float
    kind: Literal["percent", "amount"]


class LeadCheck(BaseModel):
    url: str
    verdict: Literal["supports", "contradicts", "insufficient", "excerpt_not_in_source"]
    matched: list[str] = Field(default_factory=list)  # claim figures found in the excerpt
    missing: list[str] = Field(default_factory=list)  # claim figures not found
    conflicting: list[str] = Field(default_factory=list)  # excerpt figures of the same kind that differ
    reason: str
    counts_as_evidence: bool = False


def _norm(t: str) -> str:
    return _WS.sub(" ", unicodedata.normalize("NFC", t)).strip().lower()


def quantities(text: str) -> list[Quantity]:
    out = []
    for m in _QTY.finditer(text):
        num, unit = m.group(1).replace(",", ""), (m.group(2) or "").lower()
        if _YEAR.match(num) and not unit:
            continue
        if not unit and "." not in num and int(num) < 10:
            continue
        v = float(num)
        if unit in ("%", "percent", "per cent", "percent"):
            out.append(Quantity(raw=m.group(0).strip(), value=v, kind="percent"))
        else:
            out.append(Quantity(raw=m.group(0).strip(), value=v * _SCALE.get(unit, 1.0), kind="amount"))
    return out


def _same(a: Quantity, b: Quantity) -> bool:
    if a.kind != b.kind:
        return False
    if a.kind == "percent":
        return abs(a.value - b.value) <= PCT_TOL
    return abs(a.value - b.value) <= REL_TOL * max(abs(a.value), abs(b.value), 1e-9)


def verify_lead(claim_text: str, url: str, excerpt: str, page_text: str | None = None) -> LeadCheck:
    if page_text is not None and _norm(excerpt) not in _norm(page_text):
        return LeadCheck(url=url, verdict="excerpt_not_in_source",
                         reason="the quoted excerpt is not in the fetched page; the lead weighs 0")
    want, have = quantities(claim_text), quantities(excerpt)
    if not want:
        return LeadCheck(url=url, verdict="insufficient",
                         reason="the claim has no figure to match; a human must read the source")
    matched = [w.raw for w in want if any(_same(w, h) for h in have)]
    missing = [w.raw for w in want if w.raw not in matched]
    kinds = {w.kind for w in want}
    conflicting = [h.raw for h in have if h.kind in kinds and not any(_same(h, w) for w in want)]
    if not missing:
        return LeadCheck(url=url, verdict="supports", matched=matched, conflicting=conflicting,
                         reason=f"the source states {', '.join(matched)}", counts_as_evidence=True)
    if conflicting and not matched:
        return LeadCheck(url=url, verdict="contradicts", missing=missing, conflicting=conflicting,
                         reason=f"the source gives {', '.join(conflicting)} where the claim says {', '.join(missing)}",
                         counts_as_evidence=True)
    return LeadCheck(url=url, verdict="insufficient", matched=matched, missing=missing, conflicting=conflicting,
                     reason=f"the source does not state {', '.join(missing)}")
