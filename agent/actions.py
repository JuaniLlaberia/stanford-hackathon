"""Actions the agent can take after assess(): rewrite, pull_source, escalation_note, execute.

Each LLM-backed action has a deterministic fallback, so the demo works with TokenRouter down.
The LLM only drafts text; it never changes the decision, and its rewrites may not add new figures.
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from urllib.parse import quote_plus

from pydantic import BaseModel

from agent.coverage import figures
from agent.llm import LLMError, chat_json
from agent.schema import Claim, ClaimAssessment, Decision, Persona, Report

LEAD_LABEL = "lead, not evidence"
NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")
MATH_ALNUM = re.compile(r"[\U0001D400-\U0001D7FF]")


class Draft(BaseModel):
    text: str
    via: str  # "llm" | "fallback"
    note: str = ""  # why we fell back, or what the LLM output was checked against


class Lead(BaseModel):
    claim_id: str
    label: str = LEAD_LABEL
    source_name: str
    url: str | None = None
    query: str
    expected_value: str | None = None
    why: str = ""
    via: str = "fallback"


# ---- text helpers -----------------------------------------------------------------

def clean_text(text: str, normalize: bool = True) -> str:
    """Undo Unicode math-alphanumeric garbling (𝑝𝑒𝑟𝑘𝑊ℎ -> perkWh) and put bullets on their own lines."""
    if normalize and MATH_ALNUM.search(text):
        text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"\s+(?=- \*\*)", "\n", text)  # "- **Heading:**" bullets
    text = re.sub(r"\s+(?=\d\. \*\*)", "\n  ", text)  # "1. **BYD:**" sub-items
    return text


def _units(text: str) -> list[str]:
    """Split cleaned text into editable units: bullet lines, then sentences."""
    out: list[str] = []
    for line in text.split("\n"):
        out += [s for s in re.split(r"(?<=[.!?])(?<!\s\d\.)\s+(?=[A-Z*])", line) if s.strip()]
    return out


def _numbers(s: str) -> set[str]:
    return {n.replace(",", "") for n in NUM_RE.findall(s)}


def _find_unit(units: list[str], ca: ClaimAssessment) -> int | None:
    plain = [u.replace("**", "") for u in units]
    for needle in (ca.span, ca.text):
        if needle:
            needle = unicodedata.normalize("NFKC", needle).replace("**", "").strip()
            for i, u in enumerate(plain):
                if needle and needle in u:
                    return i
    nums = _numbers(ca.span or ca.text)
    if nums:  # fall back to the unit containing all of the claim's figures
        for i, u in enumerate(plain):
            if nums <= _numbers(u):
                return i
    return None


def _instruction(ca: ClaimAssessment) -> tuple[str, str | None]:
    """(op, arg) for one claim: remove | replace | cite | caveat | keep."""
    res = ca.human_resolution or ""
    if res == "remove":
        return "remove", None
    if res.startswith("replace:"):
        return "replace", res.split(":", 1)[1].strip()
    if res.startswith("source:"):
        return "cite", f"source: {res.split(':', 1)[1].strip()}, human-attested"
    if res == "caveat":
        return "caveat", None
    if res == "keep":
        return "cite", ca.best_source or "kept by analyst"
    if ca.status == "strong_support":
        return "cite", ca.best_source
    if ca.status == "contradicted" or ca.next_step == "BLOCK":
        return "remove", None
    if ca.status in ("unverified", "unchecked", "weak_support", "contradiction_questionable", "unknown"):
        return "caveat", None
    return "keep", None


def _plan(decision: Decision) -> list[tuple[ClaimAssessment, str, str | None]]:
    return [(ca, *_instruction(ca)) for ca in decision.claims]


# ---- rewrite ----------------------------------------------------------------------

def _figure_rules(decision: Decision) -> tuple[set[str], set[str]]:
    """(must_keep, must_drop) figures: kept/cited claims' figures stay; removed claims' figures go."""
    keep, drop = set(), set()
    for ca, op, arg in _plan(decision):
        figs = set(figures(ca.text))
        if op == "remove":
            drop |= figs
        elif op == "replace":
            keep |= set(figures(arg or ""))
            drop |= figs - set(figures(arg or ""))
        elif op == "cite":
            keep |= figs
    return keep, drop - keep


def rewrite_fallback(report: Report, decision: Decision) -> Draft:
    raw_units = _units(clean_text(report.analyzed_text, normalize=False))
    garbled = {i for i, u in enumerate(raw_units) if MATH_ALNUM.search(u)}
    units = [unicodedata.normalize("NFKC", u) if i in garbled else u for i, u in enumerate(raw_units)]
    ops: dict[int, list[tuple[str, str | None, ClaimAssessment]]] = {}
    missed = []
    for ca, op, arg in _plan(decision):
        if op == "keep":
            continue
        i = _find_unit(units, ca)
        if i is None:
            missed.append(ca.claim_id)
            continue
        ops.setdefault(i, []).append((op, arg, ca))
    out = []
    for i, u in enumerate(units):
        u_ops = ops.get(i, [])
        if any(op == "remove" for op, _, _ in u_ops):
            survivors = [(op, arg, ca) for op, arg, ca in u_ops if op != "remove"]
            if not survivors:
                continue
            # the sentence mixes a removed claim with kept ones: keep only the kept claims' own (checked) text
            u = " ".join(dict.fromkeys(ca.text for _, _, ca in survivors))
            u_ops = survivors
        tags = []
        for op, arg, ca in u_ops:
            if op == "replace":
                tags.append(f"[{ca.claim_id} corrected by analyst: {arg}]")
            elif op == "cite" and arg:
                tags.append(f"[{ca.claim_id}: {arg}]")
            elif op == "caveat":
                tags.append(f"[{ca.claim_id}: unverified]")
        if i in garbled and all(op != "cite" for op, _, _ in u_ops):
            tags.append("[garbled in the source answer: verify before use]")
        out.append(u + (" " + " ".join(dict.fromkeys(tags)) if tags else ""))
    note = "deterministic: removed/caveated/cited flagged sentences"
    if garbled:
        note += "; garbled math text normalised and flagged"
    if missed:
        note += f"; could not locate {', '.join(missed)} in the text"
    return Draft(text="\n".join(out), via="fallback", note=note)


def check_rewrite(text: str, report: Report, decision: Decision) -> list[str]:
    """Code-side check of an LLM rewrite. Returns the problems (empty = accepted)."""
    keep, drop = _figure_rules(decision)
    allowed = set(figures(unicodedata.normalize("NFKC", report.analyzed_text))) | keep
    got = set(figures(text))
    problems = []
    if new := got - allowed:
        problems.append(f"introduced figures not in the report: {', '.join(sorted(new))}")
    if kept := got & drop:
        problems.append(f"kept removed figures: {', '.join(sorted(kept))}")
    if lost := keep - got:
        problems.append(f"dropped backed/kept figures: {', '.join(sorted(lost))}")
    return problems


def rewrite(report: Report, decision: Decision, persona: Persona | None = None) -> Draft:
    """Rewrite the analysed text per the per-claim plan. LLM first; code checks the figures, else fallback."""
    plan = [
        f"- {ca.claim_id} ({ca.status}): \"{ca.text}\" -> {op.upper()}" + (f" ({arg})" if arg else "")
        for ca, op, arg in _plan(decision)
    ]
    keep, drop = _figure_rules(decision)
    audience = f"for a {persona.who} who will {persona.action}" if persona else ""
    system = (
        "You edit LLM answers so they are safe to rely on. Apply every instruction exactly: REMOVE deletes that "
        "statement and its figure entirely, REPLACE swaps in the given value, CITE keeps the statement and appends "
        "the given source in brackets, CAVEAT keeps it marked [unverified], KEEP leaves it. A sentence can hold "
        "several claims: remove only the removed claim's part. Fix garbled characters. Do NOT add any new facts, "
        "figures, dates or sources. Keep the markdown bullet structure."
    )
    user = (
        f"Audience: {audience}\n\nInstructions:\n" + "\n".join(plan)
        + (f"\n\nThese figures must NOT appear: {', '.join(sorted(drop))}" if drop else "")
        + (f"\nThese figures MUST remain: {', '.join(sorted(keep))}" if keep else "")
        + f"\n\nText:\n{clean_text(report.analyzed_text)}"
    )
    try:
        out = chat_json(system, user, {"text": "the rewritten text (markdown)"})
        text = str(out.get("text", "")).strip()
        if not text:
            raise LLMError("empty rewrite")
        if problems := check_rewrite(text, report, decision):
            fb = rewrite_fallback(report, decision)
            fb.note = f"LLM rewrite rejected ({'; '.join(problems)}). " + fb.note
            return fb
        return Draft(text=text, via="llm", note="checked by code: removed figures gone, backed figures kept, nothing new")
    except LLMError as e:
        fb = rewrite_fallback(report, decision)
        fb.note = f"LLM unavailable ({e}). " + fb.note
        return fb


# ---- pull_source ------------------------------------------------------------------

def _ezio_claim(report: Report | None, claim_id: str) -> Claim | None:
    if report:
        for c in report.claims:
            if c.id == claim_id:
                return c
    return None


def pull_source_fallback(ca: ClaimAssessment, report: Report | None = None) -> Lead:
    text = (ca.span or ca.text).replace("**", "")
    years = re.findall(r"\b(?:19|20)\d{2}\b", text)
    query = " ".join(dict.fromkeys(re.sub(r"[^\w%$.\s-]", " ", text).split()))
    if years and years[-1] not in query:
        query += " " + years[-1]
    name, why = "Primary or official publisher of this figure", "No Ezio source covered this figure."
    c = _ezio_claim(report, ca.claim_id)
    if c:
        missed = {ch.source_n for ch in c.checks if ch.outcome == "not_found" and ch.source_n}
        official = [s for s in c.sources if s.type == "Official"]
        pick = next((s for s in official if s.n in missed), official[0] if official else None)
        if pick:
            name = pick.title
            why = f"Ezio listed this Official source (Source {pick.n}) but did not confirm the figure in it: check it directly."
    elif re.search(r"revenue|earnings|profit|10-K|annual report", text, re.I):
        name, why = "Company annual report / SEC filing", "Company financials are best checked against the filing."
    return Lead(
        claim_id=ca.claim_id, source_name=name, query=query[:200],
        url="https://www.google.com/search?q=" + quote_plus(query[:200]), why=why, via="fallback",
    )


def pull_source(ca: ClaimAssessment, report: Report | None = None) -> Lead:
    """Propose where to verify a claim. Always a LEAD (weight 0) until a human confirms it."""
    c = _ezio_claim(report, ca.claim_id)
    listed = "\n".join(f"- Source {s.n} · {s.type} · {s.title} ({s.date or 'no date'})" for s in (c.sources if c else []))
    system = (
        "You suggest the single most authoritative primary source to verify a factual claim (statistics agency, "
        "regulator filing, official company report, international body). Never assert the claim is true. If you "
        "are not sure of an exact URL, give the publisher's homepage or null."
    )
    user = f"Claim: {ca.span or ca.text}\nSources the checker already tried:\n{listed or '- none'}"
    schema = {
        "source_name": "publisher + document title",
        "url": "URL or null",
        "query": "search query to find the figure",
        "expected_value": "what value that source should show for this claim, or null",
        "why": "one sentence: why this source is authoritative",
    }
    try:
        out = chat_json(system, user, schema)
        if not out.get("source_name") or not out.get("query"):
            raise LLMError("lead missing source_name/query")
        url = out.get("url") or None
        if url and not re.match(r"https?://", str(url)):
            url = None
        return Lead(
            claim_id=ca.claim_id, source_name=str(out["source_name"]), url=url, query=str(out["query"]),
            expected_value=(str(out["expected_value"]) if out.get("expected_value") else None),
            why=str(out.get("why", "")), via="llm",
        )
    except LLMError:
        return pull_source_fallback(ca, report)


# ---- escalation note --------------------------------------------------------------

def escalation_note_fallback(decision: Decision, persona: Persona | None = None, report: Report | None = None) -> str:
    to = persona.sign_off if persona else "Accountable reviewer"
    task = f" before we {persona.action}" if persona else ""
    lines = [
        f"**To:** {to}",
        f"**Re:** {decision.action}: {report.name if report else 'trust report'}{task}",
        "",
        f"{decision.headline}",
        f"Coverage: {decision.coverage}. Confidence (shown, not used to decide): {decision.confidence:.0%}.",
        "",
        "**Why this needs you:**",
        *[f"- `{r.ref}` → {r.pushes}: {r.text}" for r in decision.reasons if r.pushes != "APPROVE"],
    ]
    if decision.questions:
        lines += ["", "**Decisions needed:**"]
        lines += [f"- `{q.ref}`: {q.question} ({' / '.join(q.options)})" for q in decision.questions]
    if decision.not_checked_notes:
        lines += ["", "**What the report could not check:**", *[f"- {n}" for n in decision.not_checked_notes]]
    return "\n".join(lines)


def escalation_note(decision: Decision, persona: Persona | None = None, report: Report | None = None) -> Draft:
    """Template note (always correct refs) + an LLM summary paragraph on top when available."""
    body = escalation_note_fallback(decision, persona, report)
    system = (
        "Write a 2-3 sentence plain-English summary for a busy reviewer explaining why this AI-generated answer "
        "needs their decision. Use only the facts given; mention claim refs like C1 or U2 in brackets."
    )
    try:
        out = chat_json(system, body, {"summary": "2-3 sentences"})
        summary = str(out.get("summary", "")).strip()
        if not summary:
            raise LLMError("empty summary")
        return Draft(text=f"{summary}\n\n---\n{body}", via="llm", note="summary by LLM, details from code")
    except LLMError as e:
        return Draft(text=body, via="fallback", note=f"LLM unavailable ({e}); template only")


# ---- execute (only after APPROVE + human sign-off) --------------------------------

def execute(report: Report, decision: Decision, persona: Persona, text: str, signed_by: str) -> Path:
    """Perform the gated action: write the final note. Refuses unless APPROVE and signed off."""
    if decision.action != "APPROVE":
        raise PermissionError(f"action is {decision.action}, not APPROVE")
    if not (decision.signed_off and signed_by.strip()):
        raise PermissionError("needs a human sign-off")
    path = Path("runs") / f"{report.name or 'report'}_{persona.key}_final.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    cites = [f"- {ca.claim_id}: {ca.best_source}" for ca in decision.claims if ca.best_source and ca.status == "strong_support"]
    path.write_text(
        f"# {persona.action.capitalize()}\n\n{text}\n\n---\nSources:\n" + ("\n".join(cites) or "- none")
        + f"\n\nCoverage: {decision.coverage}\nSigned off by: {signed_by} ({persona.sign_off})\n",
        encoding="utf-8",
    )
    return path
