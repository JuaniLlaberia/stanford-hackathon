"""What the report did NOT check: figures in the analysed text that appear in no assessed claim.

Numbers are hard constraints (Punto Medio lesson): a figure is "checked" only if the same number
appears in an Ezio-assessed claim. Years and small list indices are not figures.
"""
from __future__ import annotations

import re

from agent.schema import Check, Claim, Report

# 17.1 | 4.27 | 2,763.17 | 70% | $97.7 — with an optional unit word kept for display
_FIG = re.compile(
    r"(?<![\w.])[$~]?(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)"
    r"(\s?(?:%|percent|million|billion|trillion|per ?kWh|/kWh|years?|x))?(?![\w.]*\d)",
    re.IGNORECASE,
)
_YEAR = re.compile(r"^(19|20)\d{2}$")
_MATH = re.compile(r"[\U0001D400-\U0001D7FF]")


def figures(text: str) -> list[str]:
    """Numeric figures in text, normalised ('1,000' -> '1000'), excluding years and bare small integers."""
    out = []
    for m in _FIG.finditer(text):
        num, unit = m.group(1).replace(",", ""), (m.group(2) or "").strip()
        if _YEAR.match(num) and not unit:
            continue
        if not unit and "." not in num and int(num) < 10:
            continue  # "Top 3", "1." list markers, "tier-1"
        if not unit and m.start() > 0 and text[m.start() - 1] == "-":
            continue  # "tier-2"
        out.append(num)
    return out


def units(text: str) -> list[str]:
    """Sentences / bullet lines of the analysed text (verbatim)."""
    parts = []
    for line in text.split("\n"):
        parts += [s.strip(" -") for s in re.split(r"(?<=[.!?])\s+(?=[A-Z])", line) if s.strip(" -")]
    return parts


def find_unchecked(report: Report) -> list[Claim]:
    checked: set[str] = set()
    for c in report.claims:
        checked.update(figures(c.text))
        for k in c.checks:
            checked.update(figures(k.span))
    out: list[Claim] = []
    for u in units(report.analyzed_text):
        missing = [f for f in figures(u) if f not in checked]
        if not missing:
            continue
        garbled = bool(_MATH.search(u))
        out.append(Claim(
            id=f"U{len(out) + 1}", text=u,
            verdict="Not checked by Ezio" + (" (text garbled)" if garbled else ""),
            checks=[Check(field="Amount", span=f, outcome="other", said="not checked by Ezio") for f in missing],
        ))
        checked.update(missing)  # one unchecked item per figure
    return out


def all_figures(report: Report) -> list[str]:
    seen: list[str] = []
    for f in figures(report.analyzed_text):
        if f not in seen:
            seen.append(f)
    return seen
