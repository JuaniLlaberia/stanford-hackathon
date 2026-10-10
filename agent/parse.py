"""Deterministic parser: Ezio trust-report PDF -> Report. No LLM.

pypdf emits one word per line, so we collapse all whitespace and slice on the report's stable
section markers. Anything we cannot parse goes to Report.degraded instead of being guessed.

    uv run python -m agent.parse reports/ev_market.pdf     # prints JSON
    uv run python -m agent.parse --all                     # writes reports/<name>.json for every PDF
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import get_args

from pypdf import PdfReader

from agent.schema import Check, Claim, Finding, Report, Severity, Source, SourceType

MODULES = ("Accuracy", "Bias", "Tone", "Agreement")
_MATH = re.compile(r"[\U0001D400-\U0001D7FF]")  # math alphanumerics: LaTeX-rendered text, i.e. garbled
_FINDING_HEAD = re.compile(r"([A-Z][A-Za-z&/\- ]{2,60}?) \((NOTE|WARNING|CRITICAL|N/A)\) ")
_SOURCE = re.compile(r"^(\d+) (\w+) · (.*?)(?: — Published: (\d{4}-\d{2}-\d{2}))?$")
_CHECK = re.compile(r"^([A-Z][A-Za-z ]{1,20}): (.*) — (.*)$")


def pdf_text(path: str | Path) -> str:
    pages = PdfReader(str(path)).pages
    return " ".join(" ".join(p.extract_text() or "" for p in pages).split())


def _between(t: str, start: str, end: str, frm: int = 0) -> tuple[str, int]:
    i = t.find(start, frm)
    if i < 0:
        return "", -1
    i += len(start)
    j = t.find(end, i)
    return (t[i:j] if j >= 0 else t[i:]).strip(), (j if j >= 0 else len(t))


def _clean_analyzed(s: str) -> str:
    s = s.replace("**", "")
    s = re.sub(r" - (?=\S)", "\n- ", s)  # markdown bullets lost by pypdf
    s = re.sub(r" (\d)\. (?=[A-Z])", r"\n  \1. ", s)  # numbered sub-items
    return s.strip()


def _parse_checks(block: str, degraded: list[str], cid: str) -> list[Check]:
    checks = []
    for item in [x.strip() for x in block.split("●") if x.strip()]:
        m = _CHECK.match(item)
        if not m:
            continue  # e.g. "Could not be checked against reliable sources."
        field, span, out = m.group(1), m.group(2).strip(), m.group(3).strip()
        src = re.search(r"\(Source (\d+)\)", out)
        n = int(src.group(1)) if src else None
        if out.startswith("matches"):
            checks.append(Check(field=field, span=span, outcome="matches", source_n=n))
        elif out.startswith("Not found"):
            checks.append(Check(field=field, span=span, outcome="not_found"))
        elif out.startswith("sources say"):
            said = re.sub(r"\s*\(Source \d+\)\s*$", "", out[len("sources say"):]).strip()
            checks.append(Check(field=field, span=span, outcome="sources_say", said=said, source_n=n))
        else:
            checks.append(Check(field=field, span=span, outcome="other", said=out, source_n=n))
            degraded.append(f"{cid}:unknown_check_outcome")
    return checks


def _parse_sources(block: str, degraded: list[str], cid: str) -> list[Source]:
    sources = []
    for item in [x.strip() for x in block.split("●") if x.strip()]:
        m = _SOURCE.match(item)
        if not m:
            degraded.append(f"{cid}:unparsed_source")
            continue
        typ = m.group(2)
        if typ not in get_args(SourceType):
            degraded.append(f"{cid}:unknown_source_type:{typ}")
            typ = "Other"
        sources.append(Source(n=int(m.group(1)), type=typ, title=m.group(3).strip(), date=m.group(4)))
    return sources


def _parse_claims(sec: str, degraded: list[str]) -> list[Claim]:
    claims = []
    parts = re.split(r"(?:^| )Claim (\d+) ", sec)
    for k in range(1, len(parts) - 1, 2):
        cid, body = f"C{parts[k]}", parts[k + 1]
        m = re.match(r"(.*?) Verdict: (.*?) Verification: (\S+)(.*)$", body)
        if not m:
            degraded.append(f"{cid}:unparsed_claim")
            continue
        text, verdict, rest = m.group(1).strip(), m.group(2).strip(), m.group(4)
        checks_block, _, sources_block = rest.partition(" Sources: ")
        claims.append(Claim(
            id=cid, text=text, verdict=verdict,
            checks=_parse_checks(checks_block, degraded, cid),
            sources=_parse_sources(sources_block, degraded, cid),
        ))
    if not claims:
        degraded.append("no_claims_parsed")
    return claims


def _parse_findings(sec: str, degraded: list[str]) -> list[Finding]:
    heads = list(_FINDING_HEAD.finditer(sec + " "))
    findings = []
    for i, h in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(sec)
        body = sec[h.end():end].strip()
        text, _, ev = body.partition("Evidence:")
        reasoning = ""
        if "Reasoning:" in ev:  # evidence before reasoning (rare); keep both
            ev, _, reasoning = ev.partition("Reasoning:")
        evidence = [e.strip().strip('"').strip() for e in ev.split("●") if e.strip().strip('"').strip()]
        sev = h.group(2)
        findings.append(Finding(
            id=f"F{i + 1}", kind=h.group(1).strip(), severity=sev if sev in get_args(Severity) else "NOTE",
            text=(text.strip() + (" Reasoning: " + reasoning.strip() if reasoning else "")).strip(),
            evidence=evidence,
        ))
    if not findings:
        degraded.append("no_findings_parsed")
    return findings


def parse_text(t: str, name: str = "") -> Report:
    degraded: list[str] = []

    issues_at = t.find("Issues found:")
    verdict_at = t.rfind(" Verdict ", 0, issues_at if issues_at >= 0 else len(t))
    a = t.find("Analyzed text ")
    analyzed = t[a + len("Analyzed text "):verdict_at] if a >= 0 and verdict_at > a else ""
    if not analyzed:
        degraded.append("analyzed_text_missing")
    if _MATH.search(analyzed):
        degraded.append("analyzed_text_garbled")

    verdict_summary = t[verdict_at + len(" Verdict "):issues_at].strip() if verdict_at >= 0 and issues_at >= 0 else ""
    if not verdict_summary:
        degraded.append("verdict_missing")
    m = re.search(r"Issues found: (\d+)", t)
    issue_count = int(m.group(1)) if m else None

    mod_sec, _ = _between(t, "Module Scores", "Claim analysis")
    modules: dict[str, int | None] = {}
    for name_ in MODULES:
        mm = re.search(rf"{name_}: (?:([█░]{{5}})|Not scored)", mod_sec)
        if mm is None:
            degraded.append(f"module_missing:{name_.lower()}")
            modules[name_.lower()] = None
        else:
            modules[name_.lower()] = mm.group(1).count("█") if mm.group(1) else None

    c0 = t.find("Claim analysis")
    f0 = t.find(" Findings ", c0 if c0 >= 0 else 0)
    claim_sec = t[c0:f0] if c0 >= 0 and f0 > c0 else ""
    claim_sec = claim_sec.split("may not have been checked.", 1)[-1]
    m0 = t.find(" Methodology ", f0 if f0 >= 0 else 0)
    finding_sec = t[f0 + len(" Findings "):m0 if m0 >= 0 else len(t)] if f0 >= 0 else ""

    return Report(
        name=name,
        analyzed_text=_clean_analyzed(analyzed),
        verdict_summary=verdict_summary,
        issue_count=issue_count,
        modules=modules,
        claims=_parse_claims(claim_sec, degraded),
        findings=_parse_findings(finding_sec, degraded),
        degraded=degraded,
    )


def parse_report(path: str | Path) -> Report:
    p = Path(path)
    return parse_text(pdf_text(p), name=p.stem)


if __name__ == "__main__":
    args = sys.argv[1:]
    if args == ["--all"]:
        for pdf in sorted(Path("reports").glob("*.pdf")):
            r = parse_report(pdf)
            pdf.with_suffix(".json").write_text(r.model_dump_json(indent=2))
            print(f"{pdf.stem}: {len(r.claims)} claims, {len(r.findings)} findings, modules={r.modules}, degraded={r.degraded}")
    else:
        for a in args:
            print(json.dumps(parse_report(a).model_dump(), indent=2, ensure_ascii=False))
