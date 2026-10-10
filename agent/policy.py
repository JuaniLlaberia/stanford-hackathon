"""Deterministic decision policy: Report (+ persona, + human resolutions) -> Decision. No LLM.

assess() is a pure function. Call it again with the human's answers (resolutions) to re-decide.
Precedence: BLOCK > ESCALATE > PULL_SOURCE > REWRITE > APPROVE. See PLAN.md §3.
"""
from __future__ import annotations

import re

from agent.coverage import _MATH, all_figures, figures, find_unchecked, units
from agent.schema import (
    SEVERITY_ORDER, Action, Claim, ClaimAssessment, Decision, HumanQuestion, Persona, Reason, Report, Source,
)

TYPE_WEIGHT = {"Official": 1.0, "News": 0.7, "Reference": 0.5, "Other": 0.3}
NO_DATE = 0.6  # undated source: can't tell if it covers the claim's period
STRONG = 0.7  # source score at/above this = strong support
LOW_MODULE = 3  # bias/tone/agreement bar at/below this -> rewrite
LOW_ACCURACY = 2  # accuracy bar at/below this -> escalate
MAX_CONFIDENCE = 0.95

_RANKING = re.compile(r"\b(top|largest|biggest|leading|first|record|safest|most|best|#\s?1)\b", re.I)
_WORD_QTY = re.compile(r"\b(half|quarter|third|double|triple|millions|billions|percent|majority)\b", re.I)
_VERB = re.compile(
    r"\b(is|are|was|were|be|been|has|have|had|do|does|did|can|could|will|would|"
    r"\w+(ed|es|s))\b", re.I)
_YEAR = re.compile(r"\b(19|20)\d{2}\b")


def _max(a: Action, b: Action) -> Action:
    return a if SEVERITY_ORDER.index(a) >= SEVERITY_ORDER.index(b) else b


def _short(s: str, n: int = 80) -> str:
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


# ---- claim-level ---------------------------------------------------------------

def is_material(text: str) -> bool:
    """Carries a figure, a date or a ranking someone would quote."""
    return bool(figures(text) or _YEAR.search(text) or _RANKING.search(text) or _WORD_QTY.search(text))


def is_vacuous(text: str) -> bool:
    """A title or list header, not a claim: ends with ':' or is short with no verb and no figure."""
    t = text.strip()
    if t.endswith(":"):
        return True
    words = t.split()
    return len(words) <= 6 and not figures(t) and not _VERB.search(re.sub(_YEAR, "", t))


def claim_year(text: str) -> int | None:
    ys = [int(m.group(0)) for m in _YEAR.finditer(text)]
    return max(ys) if ys else None


def source_score(src: Source, year: int | None) -> tuple[float, str]:
    w = TYPE_WEIGHT.get(src.type, 0.0)
    if src.date is None:
        return w * NO_DATE, "undated"
    if year is not None and int(src.date[:4]) < year:
        return 0.0, f"published {src.date}, before the {year} period it is cited for"
    return w, f"published {src.date}"


def _fmt_src(s: Source, note: str = "") -> str:
    return f"Source {s.n} · {s.type} · {_short(s.title, 60)}" + (f" ({note})" if note else "")


def _best_source(c: Claim) -> tuple[float, Source | None, str]:
    """Score the sources Ezio says matched; if it names none, the best listed source."""
    year = claim_year(c.text)
    matched = {k.source_n for k in c.checks if k.outcome == "matches" and k.source_n}
    pool = [s for s in c.sources if s.n in matched] or c.sources
    best = (0.0, None, "no sources listed")
    for s in pool:
        sc, note = source_score(s, year)
        if best[1] is None or sc > best[0]:
            best = (sc, s, note)
    return best


def _source_notes(c: Claim) -> list[str]:
    notes = []
    year = claim_year(c.text)
    stale = [s for s in c.sources if s.date and year and int(s.date[:4]) < year]
    if stale:
        notes.append("stale: " + ", ".join(f"Source {s.n} ({s.date})" for s in stale))
    titles = [s.title.lower() for s in c.sources]
    dups = len(titles) - len(set(titles))
    if dups:
        notes.append(f"{dups} duplicate source(s): one account repeated, not independent evidence")
    return notes


def _locate(text: str, analyzed: str) -> str | None:
    if text and text in analyzed:
        return text
    words = text.split()
    for n in range(len(words), 3, -1):  # longest verbatim prefix of >= 4 words
        cand = " ".join(words[:n]).rstrip(":.,")
        if cand in analyzed:
            return cand
    figs = figures(text)
    if figs:
        for u in units(analyzed):
            if all(f in figures(u) for f in figs):
                return u
    return None


def assess_claim(c: Claim, analyzed: str, res: str | None) -> ClaimAssessment:
    material = is_material(c.text)
    v = c.verdict.lower()
    score, best, note = _best_source(c)
    best_s = _fmt_src(best, note) if best else None
    extra = _source_notes(c)
    counter = [k for k in c.checks if k.outcome == "sources_say"]

    if c.id.startswith("U"):
        garbled = bool(_MATH.search(c.text))
        status, step = "unchecked", "PULL_SOURCE"
        missing = [k.span for k in c.checks] or figures(c.text)
        why = ("figure(s) " + ", ".join(missing) + " appear in the answer but in none of the claims Ezio assessed"
               + ("; the sentence is also garbled (formatting broke the figure)" if garbled else ""))
        material = True
    elif is_vacuous(c.text):
        status, step, material = "vacuous", "APPROVE", False
        why = (f"'{_short(c.text, 50)}' is a title or list header, not a factual claim; "
               f"its '{c.verdict}' verdict should not count as evidence")
    elif v.startswith("contradicted"):
        said = counter[0] if counter else None
        same_numbers = bool(said and said.said and set(figures(c.text)) & set(figures(said.said)))
        said_src = next((s for s in c.sources if said and s.n == said.source_n), None)
        weak_src = said_src is not None and said_src.type in ("Other", "Reference")
        if same_numbers or weak_src:
            status, step = "contradiction_questionable", "ESCALATE"
            why = f"Ezio says '{c.verdict}'"
            if said:
                why += f", but its own {said.field} check says sources report '{_short(said.said, 70)}'"
                if same_numbers:
                    why += " — the same figure as the claim"
            if said_src:
                why += f"; that evidence is {_fmt_src(said_src)}"
        else:
            status, step = "contradicted", "BLOCK" if material else "REWRITE"
            why = f"Ezio: '{c.verdict}'" + (f"; sources say '{_short(said.said, 70)}'" if said else "")
            if said_src:
                why += f" ({_fmt_src(said_src)})"
    elif v.startswith("supported"):
        if score >= STRONG:
            status, step = "strong_support", "APPROVE"
            why = f"Supported by {best_s}"
        else:
            status, step = "weak_support", "REWRITE"
            why = f"Supported, but only by {best_s or 'unlisted sources'} (score {score:.2f} < {STRONG}): hedge and cite"
    elif "not enough" in v or "unverif" in v or "could not" in v:
        status = "unverified"
        if counter:
            k = counter[0]
            step = "ESCALATE"
            why = (f"Ezio could not verify it, and its own {k.field} check found sources saying "
                   f"'{_short(k.said or '', 70)}' (Source {k.source_n}) — evidence against the claim, not just missing evidence")
        elif material:
            step = "PULL_SOURCE"
            listed = ", ".join(sorted({s.type for s in c.sources})) or "none"
            why = f"Not enough evidence (sources listed: {listed}, none confirmed it); quotable figure/date — needs another source"
        else:
            step = "REWRITE"
            why = "Not enough evidence; not a figure, so hedge it or present it as opinion"
    else:
        status, step = "unknown", "ESCALATE"
        why = f"Unrecognised verdict '{c.verdict}': weighted 0, a human must look"

    if extra:
        why += " [" + "; ".join(extra) + "]"

    # Human resolution overrides the next step for this claim (never the evidence status).
    if res:
        kind = res.split(":", 1)[0]
        if kind == "keep":
            step = "APPROVE"
            why += " → reviewer accepted the cited source"
        elif kind == "source":
            step = "REWRITE"
            why += f" → reviewer attested a source ({res.split(':', 1)[1].strip()}); cite it, not Ezio-verified"
        elif kind == "replace":
            step = "REWRITE"
            why += f" → reviewer supplied the value '{res.split(':', 1)[1].strip()}'"
        elif kind == "caveat":
            step = "REWRITE"
            why += " → keep, explicitly marked as unverified"
        elif kind == "remove":
            step = "REWRITE"
            why += " → remove from the text"

    return ClaimAssessment(
        claim_id=c.id, text=c.text, status=status, material=material, why=why, next_step=step,
        source_score=round(score, 2), best_source=best_s, human_resolution=res,
        span=_locate(c.text, analyzed),
    )


def _claim_question(a: ClaimAssessment, c: Claim) -> HumanQuestion | None:
    if a.human_resolution or a.next_step not in ("ESCALATE", "PULL_SOURCE", "BLOCK"):
        return None
    if a.status == "contradiction_questionable":
        return HumanQuestion(
            ref=a.claim_id, question=f"{a.claim_id}: {a.why}. What should the text say about '{_short(c.text, 60)}'?",
            options={"keep": "Keep the figure and cite the source that matches it",
                     "replace": "Replace it with a value I provide",
                     "remove": "Remove the claim"},
            needs_text=["replace"])
    if a.status in ("contradicted", "unverified") and a.next_step in ("ESCALATE", "BLOCK"):
        return HumanQuestion(
            ref=a.claim_id, question=f"{a.claim_id}: {a.why}. How should we handle '{_short(c.text, 60)}'?",
            options={"replace": "Replace it with the value I provide",
                     "caveat": "Keep it, explicitly marked as unverified",
                     "remove": "Remove the claim"},
            needs_text=["replace"])
    return HumanQuestion(
        ref=a.claim_id,
        question=f"{a.claim_id}: '{_short(c.text, 70)}' — {a.why}. Can you back it?",
        options={"source": "I have a source (paste the URL)",
                 "caveat": "Keep it, explicitly marked as unverified",
                 "remove": "Remove it"},
        needs_text=["source"])


# ---- report-level --------------------------------------------------------------

def assess(report: Report, persona: Persona, resolutions: dict[str, str] | None = None) -> Decision:
    res = dict(resolutions or {})
    analyzed = report.analyzed_text
    items: list[Claim] = list(report.claims) + find_unchecked(report)
    by_id = {c.id: c for c in items}
    claims = [assess_claim(c, analyzed, res.get(c.id)) for c in items]
    reasons: list[Reason] = []
    questions: list[HumanQuestion] = []

    for a in claims:
        if a.next_step != "APPROVE" or a.status == "vacuous":
            reasons.append(Reason(ref=a.claim_id, text=a.why, pushes=a.next_step))
        q = _claim_question(a, by_id[a.claim_id])
        if q:
            questions.append(q)

    # Module bars (Ezio's scores, 5 blocks). Missing accuracy is a note, not a pass.
    m = report.modules
    acc = m.get("accuracy")
    if acc is not None and acc <= LOW_ACCURACY:
        reasons.append(Reason(ref="module:accuracy", text=f"Accuracy {acc}/5: most checked claims did not hold up",
                              pushes="ESCALATE"))
    for k in ("bias", "tone", "agreement"):
        if m.get(k) is not None and m[k] <= LOW_MODULE:
            label = {"bias": "one-sided framing", "tone": "emotional/promotional tone",
                     "agreement": "flattery or excessive agreement"}[k]
            reasons.append(Reason(ref=f"module:{k}", text=f"{k.capitalize()} {m[k]}/5: {label} — rewrite neutrally",
                                  pushes="REWRITE"))

    # Findings: CRITICAL -> human call; WARNING -> rewrite.
    for f in report.findings:
        fres = res.get(f.id)
        if f.severity == "CRITICAL":
            ev = f" Evidence: {', '.join(repr(e) for e in f.evidence[:3])}." if f.evidence else ""
            if fres == "block":
                reasons.append(Reason(ref=f.id, text=f"{f.kind} CRITICAL → reviewer chose not to use it", pushes="BLOCK"))
            elif fres in ("rewrite", "label_opinion"):
                how = "rewrite with balanced framing" if fres == "rewrite" else "keep, clearly labelled as opinion"
                reasons.append(Reason(ref=f.id, text=f"{f.kind} CRITICAL → reviewer: {how}", pushes="REWRITE"))
            else:
                reasons.append(Reason(ref=f.id, text=f"{f.kind} CRITICAL: {_short(f.text, 160)}{ev}", pushes="ESCALATE"))
                questions.append(HumanQuestion(
                    ref=f.id,
                    question=f"{f.id} {f.kind} is CRITICAL: {_short(f.text, 200)}{ev} "
                             f"Should the {persona.who.lower()} {persona.action}?",
                    options={"rewrite": "Rewrite it with balanced framing (agent drafts it)",
                             "label_opinion": "Keep the stance, clearly labelled as opinion",
                             "block": "Don't use this answer"}))
        elif f.severity == "WARNING":
            reasons.append(Reason(ref=f.id, text=f"{f.kind} WARNING: {_short(f.text, 160)}", pushes="REWRITE"))

    if "analyzed_text_garbled" in report.degraded:
        reasons.append(Reason(ref="report:garbled", text="Part of the answer is garbled (math-formatted text); "
                              "a figure in it cannot be read reliably — rewrite that sentence", pushes="REWRITE"))
    for d in report.degraded:
        if d != "analyzed_text_garbled":
            reasons.append(Reason(ref="report:degraded", text=f"Parser could not read: {d}", pushes="ESCALATE"))

    # Nothing material is backed and several material claims are open -> block the whole answer.
    real = [a for a in claims if a.status != "vacuous"]
    mat = [a for a in real if a.material]
    backed = [a for a in mat if a.status in ("strong_support", "weak_support")]
    open_mat = [a for a in mat if a.status in ("unverified", "unchecked", "contradicted", "contradiction_questionable")]
    hard_contra = [a for a in mat if a.status == "contradicted"]
    rr = res.get("report")
    if (not backed and len(open_mat) >= 3) or (mat and len(hard_contra) / len(mat) >= 0.5):
        why = (f"none of the {len(mat)} quotable claims is backed by a source" if not backed
               else f"{len(hard_contra)} of {len(mat)} quotable claims are contradicted")
        if rr == "rebuild":
            reasons.append(Reason(ref="report", text=f"{why} → reviewer: rebuild from sourced research",
                                  pushes="PULL_SOURCE"))
        else:
            reasons.append(Reason(ref="report", text=f"The answer can't be used as is: {why}", pushes="BLOCK"))
            if rr != "block":
                questions.insert(0, HumanQuestion(
                    ref="report", question=f"The agent blocks this answer: {why}. Override?",
                    options={"rebuild": "Rebuild it: pull sources for each claim, then review",
                             "block": "Confirm: do not use this answer"}))

    action: Action = "APPROVE"
    for r in reasons:
        action = _max(action, r.pushes)

    # A blocked answer only asks the block question; claim-level questions come after "rebuild".
    if action == "BLOCK":
        questions = [q for q in questions if q.ref == "report"] if rr != "block" else []

    # Sign-off: the accountable human approves the (rewritten) text before the action runs.
    signed_off = False
    if action in ("APPROVE", "REWRITE") and not questions:
        so = res.get("signoff")
        if so == "approve":
            action, signed_off = "APPROVE", True
            reasons.append(Reason(ref="signoff", text=f"{persona.sign_off} signed off the final text", pushes="APPROVE"))
        elif so == "send_back":
            action = "ESCALATE"
            reasons.append(Reason(ref="signoff", text=f"{persona.sign_off} sent it back", pushes="ESCALATE"))
        else:
            questions.append(HumanQuestion(
                ref="signoff",
                question=f"{persona.sign_off}: approve the {'rewritten ' if action == 'REWRITE' else ''}text so the "
                         f"{persona.who.lower()} can {persona.action}?",
                options={"approve": "Approve and run the action", "send_back": "Send it back"}))

    # Coverage + notes on what the report could not check.
    figs = all_figures(report)
    strong_figs = {f for a in claims if a.status == "strong_support" for f in figures(a.text)}
    if len(figs) >= 3:
        coverage = f"{len([f for f in figs if f in strong_figs])} of {len(figs)} figures in the answer are backed by a strong source"
    else:
        coverage = (f"{len([a for a in real if a.status == 'strong_support'])} of {len(real)} assessed claims "
                    f"are backed by a strong source")
    notes = _not_checked(report, claims)

    strong = len([a for a in real if a.status == "strong_support"])
    disputed = len([a for a in real if a.status in ("contradicted", "contradiction_questionable")])
    conf = (strong / len(real) if real else 0.0) * (1 - disputed / len(real) if real else 0.0)
    conf = round(min(MAX_CONFIDENCE, conf), 2)

    counts = {}
    for a in claims:
        counts[a.status] = counts.get(a.status, 0) + 1
    tally = ", ".join(f"{n} {s.replace('_', ' ')}" for s, n in counts.items())
    headline = f"{action} — {persona.who} wants to {persona.action}. Ezio: '{report.verdict_summary}' Agent: {coverage} ({tally})."

    reasons.sort(key=lambda r: -SEVERITY_ORDER.index(r.pushes))
    return Decision(action=action, headline=headline, coverage=coverage, reasons=reasons, claims=claims,
                    not_checked_notes=notes,
                    questions=questions, confidence=conf, signed_off=signed_off)


def _not_checked(report: Report, claims: list[ClaimAssessment]) -> list[str]:
    notes = []
    for a in claims:
        if a.status == "unchecked":
            figs = re.search(r"figure\(s\) (.*?) appear", a.why)
            notes.append(f"{a.claim_id}: '{_short(a.text, 70)}' — {figs.group(1) if figs else 'figure'} never checked by Ezio")
        elif a.status == "vacuous":
            notes.append(f"{a.claim_id}: counted by Ezio but is a title/header, so the real number of checked claims is lower")
        elif a.status == "unverified":
            notes.append(f"{a.claim_id}: Ezio searched but could not confirm '{_short(a.text, 60)}'")
    if report.modules.get("accuracy") is None:
        notes.append("Accuracy was NOT scored (too few checkable claims): a missing score is not a pass")
    sents = units(report.analyzed_text)
    assessed = [a.span or a.text for a in claims]
    unassessed = [s for s in sents if not any(x[:40] in s or s[:40] in x for x in assessed)]
    if unassessed:
        notes.append(f"{len(unassessed)} of {len(sents)} sentences were not assessed at all (opinions, framing, "
                     f"summary), e.g. '{_short(max(unassessed, key=len), 80)}'")
    return notes


if __name__ == "__main__":  # uv run python -m agent.policy ev_market [C1=remove C2=caveat signoff=approve ...]
    import sys

    from agent.personas import persona_for

    name, answers = sys.argv[1], dict(a.split("=", 1) for a in sys.argv[2:])
    d = assess(Report.model_validate_json(open(f"reports/{name}.json").read()), persona_for(name), answers)
    print(d.headline, f"\nconfidence={d.confidence}")
    for r in d.reasons:
        print(f"  [{r.ref}] {r.pushes}: {r.text}")
    print("could not check:", *d.not_checked_notes, sep="\n  - ")
    print("questions:", *(f"{q.ref}: {list(q.options)}" for q in d.questions), sep="\n  ")
