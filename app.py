"""Trust Agent UI: Ezio report -> decision -> human in the loop -> signed-off action."""
from __future__ import annotations

import html
import json
import re
from pathlib import Path

import pypdf
import streamlit as st

from agent import actions, audit, llm
from agent.schema import SEVERITY_ORDER, ClaimAssessment, Decision, HumanQuestion, Persona, Reason, Report

REPORTS = Path("reports")

# ---- integration seams: real modules when Juan has pushed them, placeholders otherwise -------

try:
    from agent.parse import parse_report
except ImportError:
    parse_report = None

try:
    from agent.policy import assess

    ASSESS_IS_REAL = True
except ImportError:
    ASSESS_IS_REAL = False

try:
    from agent.personas import PERSONAS
except ImportError:
    PERSONAS = {
        "analyst": Persona(key="analyst", who="Equity research analyst", action="put the figures in a client note", sign_off="Research lead"),
        "comms": Persona(key="comms", who="Comms lead", action="publish the blog post", sign_off="Comms lead"),
        "policy": Persona(key="policy", who="Policy advisor", action="brief a legislator", sign_off="Policy director"),
        "vc": Persona(key="vc", who="VC associate", action="send it in an investment memo", sign_off="Partner"),
    }
try:
    from agent.personas import DEFAULT_FOR_REPORT as DEFAULT_PERSONA
except ImportError:
    DEFAULT_PERSONA = {"ev_market": "analyst", "wfh": "comms", "nuclear": "policy", "agi": "vc"}


def _raw_report(path: Path) -> Report:
    """Last-resort loader (no parser, no JSON): analysed text + verdict only."""
    t = " ".join("".join(p.extract_text() or "" for p in pypdf.PdfReader(path).pages).split())
    m = re.search(r"Analyzed text (.*?) Verdict (.*?) Issues found: (\d+)", t)
    return Report(
        name=path.stem,
        analyzed_text=m.group(1) if m else t[:3000],
        verdict_summary=m.group(2) if m else "",
        issue_count=int(m.group(3)) if m else None,
        degraded=["placeholder loader: claims not parsed"],
    )


@st.cache_data(show_spinner=False)
def load_report(name: str) -> dict:
    pdf, js = REPORTS / f"{name}.pdf", REPORTS / f"{name}.json"
    if parse_report:
        rep = parse_report(pdf)
    elif js.exists():
        rep = Report.model_validate(json.loads(js.read_text()))
    else:
        rep = _raw_report(pdf)
    if not rep.name:
        rep.name = name
    return rep.model_dump()


def _placeholder_assess(report: Report, persona: Persona, resolutions: dict[str, str] | None = None) -> Decision:
    """Stand-in for agent.policy.assess until it lands: crude, but drives the same UI flow."""
    res = resolutions or {}
    cas, reasons, questions = [], [], []
    for c in report.claims:
        v = c.verdict.lower()
        has_official = any(s.type == "Official" for s in c.sources)
        if v.startswith("supported"):
            status, step = ("strong_support", "APPROVE") if has_official else ("weak_support", "REWRITE")
        elif v.startswith("not enough"):
            status, step = "unverified", "PULL_SOURCE"
        elif v.startswith("contradicted"):
            status, step = "contradicted", "ESCALATE"
        else:
            status, step = "unknown", "APPROVE"
        r = res.get(c.id)
        if r:
            step = "REWRITE"
        cas.append(ClaimAssessment(
            claim_id=c.id, text=c.text, status=status, material=bool(re.search(r"\d", c.text)),
            why=f"Ezio verdict: {c.verdict}", next_step=step, human_resolution=r, span=c.text,
        ))
        if step != "APPROVE":
            reasons.append(Reason(ref=c.id, text=f"{status}: {c.text[:80]}", pushes=step))
        if status in ("unverified", "contradicted") and not r:
            questions.append(HumanQuestion(
                ref=c.id, question=f"{c.id} is {status}: what should happen to it?",
                options={"source": "I have a source (URL)", "caveat": "Keep with explicit caveat", "remove": "Remove claim"},
                needs_text=["source"],
            ))
    for f in report.findings:
        if f.severity != "NOTE":
            reasons.append(Reason(ref=f.id, text=f"{f.kind} ({f.severity})", pushes="REWRITE" if not res else "APPROVE"))
    for k, v in report.modules.items():
        if v is not None and v <= 3 and not res:
            reasons.append(Reason(ref=f"module:{k}", text=f"{k} scored {v}/5", pushes="REWRITE"))
    action = max((r.pushes for r in reasons), key=SEVERITY_ORDER.index, default="APPROVE")
    if not questions and res:
        action = "APPROVE"
    signed = res.get("signoff") == "approve" and action == "APPROVE"
    if action == "APPROVE" and not signed:
        questions.append(HumanQuestion(ref="signoff", question=f"Approve to {persona.action}?",
                                       options={"approve": f"Approve: {persona.action}", "send_back": "Send back"}))
    strong = sum(ca.status == "strong_support" for ca in cas)
    return Decision(
        action=action, headline=f"[placeholder assess] {action} for {persona.who}",
        coverage=f"{strong} of {len(cas)} claims strongly verified", reasons=reasons, claims=cas,
        not_checked_notes=["placeholder policy: unchecked figures not scanned yet"], questions=questions,
        confidence=min(0.95, strong / max(len(cas), 1)), signed_off=signed,
    )


if not ASSESS_IS_REAL:
    assess = _placeholder_assess

# ---- presentation helpers ---------------------------------------------------------

STATUS = {  # icon, word, css class
    "strong_support": ("✅", "backed", "ok"),
    "weak_support": ("⚠️", "weak", "warn"),
    "vacuous": ("➖", "vacuous", "muted"),
    "unverified": ("⚠️", "unverified", "warn"),
    "contradiction_questionable": ("❓", "questionable", "q"),
    "contradicted": ("❌", "contradicted", "bad"),
    "unchecked": ("⬜", "unchecked", "muted"),
    "unknown": ("❔", "unknown", "muted"),
}
ACTION_COLOR = {"APPROVE": "#1f9d55", "REWRITE": "#d69e2e", "PULL_SOURCE": "#3182ce", "ESCALATE": "#dd6b20", "BLOCK": "#c53030"}
ACTION_ICON = {"APPROVE": "✅", "REWRITE": "✏️", "PULL_SOURCE": "🔎", "ESCALATE": "🙋", "BLOCK": "⛔"}

CSS = """
<style>
.atext{line-height:1.75;font-size:0.95rem}
.atext mark{padding:1px 3px;border-radius:4px;color:inherit}
.atext mark.ok{background:rgba(31,157,85,.22);border-bottom:2px solid #1f9d55}
.atext mark.warn{background:rgba(214,158,46,.25);border-bottom:2px solid #d69e2e}
.atext mark.bad{background:rgba(197,48,48,.22);border-bottom:2px solid #c53030}
.atext mark.q{background:rgba(128,90,213,.22);border-bottom:2px dashed #805ad5}
.atext mark.muted{background:rgba(128,128,128,.18);border-bottom:2px dotted #888}
.atext sup{font-size:.68rem;font-weight:600;white-space:nowrap;margin-left:2px}
.badge{display:inline-block;padding:.35rem .9rem;border-radius:8px;color:#fff;font-weight:700;font-size:1.4rem}
.chip{display:inline-block;padding:.1rem .55rem;border-radius:12px;color:#fff;font-weight:600;font-size:.85rem}
.steps{display:flex;flex-wrap:wrap;gap:.5rem;margin:.2rem 0 1rem 0}
.pill{display:inline-block;padding:.35rem .9rem;border-radius:999px;font-size:.9rem}
.card{border:1px solid rgba(128,128,128,.35);border-radius:12px;padding:1rem 1.2rem;height:100%}
.card.strong{border:2px solid currentColor}
.card.dec{background:rgba(221,107,32,.08);border-color:rgba(221,107,32,.45)}
.kicker{font-size:.75rem;letter-spacing:.06em;text-transform:uppercase;opacity:.75}
.big{font-size:2.6rem;font-weight:700;line-height:1.1;margin-top:.2rem}
.sub{font-size:.95rem;opacity:.85}
.next{margin-top:.6rem;font-weight:600}
</style>
"""


def ezio_headline(report: Report) -> str:
    m = re.search(r"(\d+) of (\d+)\b[^.]*?hold up", report.verdict_summary)
    if m:
        return f"{m.group(1)} of {m.group(2)} hold up"
    sup = sum(c.verdict.lower().startswith("supported") for c in report.claims)
    return f"{sup} of {len(report.claims)} supported"


def _display_text(text: str) -> str:
    return actions.clean_text(text).replace("**", "")


def _locate(text: str, ca: ClaimAssessment) -> tuple[int, int] | None:
    for needle in (ca.span, ca.text):
        if not needle:
            continue
        needle = actions.clean_text(needle).replace("**", "").strip()
        i = text.find(needle)
        if i < 0:
            i = text.lower().find(needle.lower())
        if needle and i >= 0:
            return i, i + len(needle)
    m = re.search(r"\d+(?:[.,]\d+)*\s*(?:million|billion|%|percent)?", ca.span or ca.text)
    if m:  # fall back to the claim's first figure
        i = text.find(m.group(0).strip())
        if i >= 0:
            return i, i + len(m.group(0).strip())
    return None


def highlight(report: Report, decision: Decision) -> str:
    text = _display_text(report.analyzed_text)
    spans: list[tuple[int, int, ClaimAssessment]] = []
    for ca in decision.claims:
        loc = _locate(text, ca)
        if not loc:
            continue
        s, e = loc
        # allow disjoint or properly nested spans only (valid HTML)
        if all(e <= s2 or s >= e2 or (s <= s2 and e >= e2) or (s >= s2 and e <= e2) for s2, e2, _ in spans):
            spans.append((s, e, ca))
    opens: dict[int, list] = {}
    closes: dict[int, list] = {}
    for s, e, ca in spans:
        opens.setdefault(s, []).append((e, ca))
        closes.setdefault(e, []).append((s, ca))
    out = []
    for i in range(len(text) + 1):
        for s, ca in sorted(closes.get(i, []), key=lambda x: -x[0]):  # inner first
            icon, word, _ = STATUS.get(ca.status, STATUS["unknown"])
            out.append(f"</mark><sup>{icon} {word} · {ca.claim_id}</sup>")
        for e, ca in sorted(opens.get(i, []), key=lambda x: -x[0]):  # outer first
            cls = STATUS.get(ca.status, STATUS["unknown"])[2]
            out.append(f'<mark class="{cls}" title="{html.escape(ca.why)}">')
        if i < len(text):
            out.append("<br>" if text[i] == "\n" else html.escape(text[i]))
    return f'<div class="atext">{"".join(out)}</div>'


QLABEL = {"contradiction_questionable": "DISPUTED", "contradicted": "CONTRADICTED", "unverified": "UNVERIFIED",
          "unchecked": "NEVER CHECKED", "weak_support": "WEAK SUPPORT", "finding": "CRITICAL FINDING",
          "report": "BLOCKED", "unknown": "UNKNOWN VERDICT"}


def _short(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def action_badge(action: str, big: bool = True) -> str:
    cls = "badge" if big else "chip"
    return f'<span class="{cls}" style="background:{ACTION_COLOR[action]}">{ACTION_ICON[action]} {action}</span>'


def run_assess(report: Report, persona: Persona, resolutions: dict[str, str]) -> Decision:
    d = assess(report, persona, dict(resolutions))
    if resolutions.get("signoff") == "approve" and d.action == "APPROVE" and not d.signed_off:
        d = d.model_copy(update={"signed_off": True})
    return d


def _resolution_value(q: HumanQuestion, key: str, text: str) -> str:
    if key in q.needs_text:
        return f"{key.rstrip(':')}:{text.strip()}"
    return key


# ---- page -------------------------------------------------------------------------

st.set_page_config(page_title="Trust Agent", page_icon="🛡️", layout="wide")
st.markdown(CSS, unsafe_allow_html=True)

with st.sidebar:
    st.header("🛡️ Trust Agent")
    names = sorted(p.stem for p in REPORTS.glob("*.pdf"))
    name = st.selectbox("Ezio report", names, index=names.index("ev_market") if "ev_market" in names else 0)
    pkeys = list(PERSONAS)
    default_p = DEFAULT_PERSONA.get(name, pkeys[0])
    pkey = st.selectbox(
        "Who relies on it", pkeys, index=pkeys.index(default_p) if default_p in pkeys else 0,
        format_func=lambda k: f"{PERSONAS[k].who} → {PERSONAS[k].action}", key=f"persona_{name}",
    )
    persona = PERSONAS[pkey]
    st.caption(f"Sign-off: **{persona.sign_off}**")
    st.divider()
    st.caption(("🟢 LLM configured" if llm.available() else "⚪ LLM off: deterministic fallbacks") + "  \n"
               + ("policy: `agent.policy.assess`" if ASSESS_IS_REAL else "policy: **placeholder** assess()"))
    if st.button("Reset this case"):
        st.session_state.pop(f"case::{name}::{pkey}", None)
        st.rerun()

report = Report.model_validate(load_report(name))

case_key = f"case::{name}::{pkey}"
if case_key not in st.session_state:
    d0 = run_assess(report, persona, {})
    st.session_state[case_key] = {"resolutions": {}, "timeline": [{"action": d0.action, "why": "initial assessment", "answers": {}}],
                                  "decision": d0.model_dump(), "drafts": {}, "leads": {}, "executed": None}
    audit.log("assess", report=name, persona=pkey, step=0, action=d0.action,
              reasons=[r.model_dump() for r in d0.reasons], coverage=d0.coverage)
case = st.session_state[case_key]
decision = Decision.model_validate(case["decision"])

# ---- step bar + headline cards ----------------------------------------------------------
open_qs = [q for q in decision.questions if q.ref != "signoff"]
if case["executed"]:
    step = 4
elif open_qs:
    step = 1
else:
    step = 2
steps = ["Evidence read", f"Your call · {len(open_qs)} decision{'s' if len(open_qs) != 1 else ''}" if open_qs else "Your call",
         "Review rewrite & sign off", "Sent"]
pills = []
for i, s in enumerate(steps):
    if i < step:
        style = "background:#E7E7E1;color:#3D4148"
        s = "✓ " + s
    elif i == step:
        style = "background:#1B1D21;color:#fff;font-weight:600"
    else:
        style = "border:1px dashed #B9B9B1;color:#6B7079"
    pills.append(f'<span class="pill" style="{style}">{i + 1} · {html.escape(s)}</span>')
st.markdown(f'<div class="steps">{"".join(pills)}</div>', unsafe_allow_html=True)

agent_cov = re.sub(r"\s+(?:in the answer\s+)?are backed by a strong source", "", decision.coverage)
cov_num, _, cov_rest = agent_cov.partition(" figures") if " figures" in agent_cov else agent_cov.partition(" assessed claims")
cov_unit = "figures backed by a strong source" if " figures" in agent_cov else "assessed claims backed by a strong source"
ez_num, _, ez_rest = ezio_headline(report).partition(" hold up") if "hold up" in ezio_headline(report) else (ezio_headline(report), "", "")
by_action: dict[str, list[Reason]] = {}
for r in decision.reasons:
    if r.pushes != "APPROVE" or r.ref == "signoff":
        by_action.setdefault(r.pushes, []).append(r)
summary = " · ".join(f"{len(v)} {k.replace('_', ' ').lower()}" for k, v in
                     sorted(by_action.items(), key=lambda kv: -SEVERITY_ORDER.index(kv[0])))
c1, c2, c3 = st.columns([1, 1, 1.4], gap="medium")
c1.markdown(f'<div class="card"><div class="kicker">Ezio says</div><div class="big">{html.escape(ez_num)}</div>'
            f'<div class="sub">{"checkable claims hold up" if ez_rest is not None and "hold up" in ezio_headline(report) else "supported"}</div></div>',
            unsafe_allow_html=True)
c2.markdown(f'<div class="card strong"><div class="kicker">Agent finds</div><div class="big">{html.escape(cov_num)}</div>'
            f'<div class="sub">{cov_unit}</div></div>', unsafe_allow_html=True)
next_line = (f"Answer {len(open_qs)} decision{'s' if len(open_qs) != 1 else ''} on the right →" if open_qs
             else ("Done: the action ran." if case["executed"] else f"Review the rewrite and sign off below ↓"))
c3.markdown(f'<div class="card dec">{action_badge(decision.action)}'
            f'<div class="sub" style="margin-top:.5rem">Reasons: {summary or "none"}. '
            f'Confidence {decision.confidence:.0%} (shown, never used to decide).</div>'
            f'<div class="next">{next_line}</div></div>', unsafe_allow_html=True)
if report.degraded:
    st.caption("⚠️ degraded: " + ", ".join(report.degraded))

left, right = st.columns([3, 2], gap="large")

with left:
    st.subheader("Analysed text")
    st.caption("Legend: ✅ backed · ⚠️ weak/unverified · ❌ contradicted · ❓ questionable · ➖ vacuous · ⬜ unchecked")
    st.markdown(highlight(report, decision), unsafe_allow_html=True)
    with st.expander(f"Ezio verdict · {report.issue_count} issues · modules"):
        st.write(report.verdict_summary)
        st.write({k: ("not scored" if v is None else f"{'█' * v}{'░' * (5 - v)} {v}/5") for k, v in report.modules.items()})
        for f in report.findings:
            st.markdown(f"- `{f.id}` **{f.kind}** ({f.severity}): {f.text}")

with right:
    # ---- human in the loop: short question cards first --------------------------
    if open_qs:
        st.subheader(f"🙋 Your call, {persona.sign_off}")
        st.caption("The agent decides again after you answer.")
        answers: dict[str, str] = {}
        claim_by_id = {ca.claim_id: ca for ca in decision.claims}
        unchecked_qs = [q for q in open_qs if claim_by_id.get(q.ref) and claim_by_id[q.ref].status == "unchecked"]
        other_qs = [q for q in open_qs if q not in unchecked_qs]
        for q in other_qs:
            ca = claim_by_id.get(q.ref)
            label = QLABEL.get(ca.status if ca else ("finding" if q.ref.startswith("F") else q.ref), "DECISION")
            with st.container(border=True):
                title = _short(ca.text, 90) if ca else _short(q.question.split(":", 1)[0], 90)
                st.markdown(f"<span class='kicker'>{q.ref} · {label}</span><br><b>{html.escape(title)}</b>",
                            unsafe_allow_html=True)
                why = ca.why if ca else q.question
                st.caption(_short(re.sub(r"\s*\[.*?\]\s*$", "", why), 220))
                keys = list(q.options)
                k = st.radio(q.ref, keys, format_func=lambda o, q=q: q.options[o], index=None, horizontal=True,
                             key=f"{case_key}::q::{q.ref}", label_visibility="collapsed")
                if k and k in q.needs_text:
                    val = st.text_input(q.options[k], key=f"{case_key}::t::{q.ref}::{k}")
                    if val.strip():
                        answers[q.ref] = _resolution_value(q, k, val)
                elif k:
                    answers[q.ref] = k
        if unchecked_qs:
            with st.container(border=True):
                refs = ", ".join(q.ref for q in unchecked_qs)
                st.markdown(f"<span class='kicker'>{refs} · NEVER CHECKED BY EZIO</span>", unsafe_allow_html=True)
                bulk = st.checkbox(f"Remove all {len(unchecked_qs)} unchecked figures", key=f"{case_key}::bulk")
                for q in unchecked_qs:
                    ca = claim_by_id[q.ref]
                    figs = re.search(r"figure\(s\) (.*?) appear", ca.why)
                    line = f"`{q.ref}` {_short(ca.text, 70)}" + (f" · **{figs.group(1)}**" if figs else "")
                    if bulk:
                        st.markdown(f"{line} → remove")
                        answers[q.ref] = "remove"
                        continue
                    st.markdown(line)
                    k = st.radio(q.ref, list(q.options), format_func=lambda o, q=q: q.options[o], index=None,
                                 horizontal=True, key=f"{case_key}::q::{q.ref}", label_visibility="collapsed")
                    if k and k in q.needs_text:
                        val = st.text_input(q.options[k], key=f"{case_key}::t::{q.ref}::{k}")
                        if val.strip():
                            answers[q.ref] = _resolution_value(q, k, val)
                    elif k:
                        answers[q.ref] = k
        missing = len(open_qs) - len(answers)
        if st.button("Apply answers and re-decide" + (f" ({missing} unanswered)" if missing else ""),
                     type="primary", disabled=not answers, use_container_width=True):
            case["resolutions"].update(answers)
            d = run_assess(report, persona, case["resolutions"])
            case["timeline"].append({"action": d.action, "answers": answers,
                                     "why": "human answered " + ", ".join(f"{k}={v}" for k, v in answers.items())})
            case["decision"] = d.model_dump()
            case["drafts"].pop("rewrite", None)
            audit.log("human_answers", report=name, persona=pkey, answers=answers, action=d.action,
                      reasons=[r.model_dump() for r in d.reasons])
            st.rerun()

    # ---- why: reasons grouped by the action they push ---------------------------
    st.subheader(f"Why {decision.action}")
    first = True
    for act in sorted(by_action, key=lambda a: -SEVERITY_ORDER.index(a)):
        rs = by_action[act]
        with st.expander(f"{ACTION_ICON[act]} {act.replace('_', ' ').title()} · {len(rs)}", expanded=first):
            for r in rs:
                st.markdown(f"- `{r.ref}` {_short(r.text, 260)}")
        first = False
    with st.expander("What this report could not check", expanded=not open_qs):
        for n in decision.not_checked_notes:
            st.markdown(f"- {n}")
    st.markdown("**Decision timeline:** " + " → ".join(action_badge(t["action"], big=False) for t in case["timeline"]),
                unsafe_allow_html=True)
    for i, t in enumerate(case["timeline"]):
        st.caption(f"{i}. {t['action']}: {_short(t['why'], 140)}")

# ---- agent actions ------------------------------------------------------------------

st.divider()
st.subheader("Agent actions")
tab_rw, tab_src, tab_esc = st.tabs(["✏️ Rewrite", "🔎 Pull source", "🙋 Escalation note"])

with tab_rw:
    if st.button("Draft rewrite", type="primary" if decision.action in ("REWRITE", "APPROVE") else "secondary"):
        with st.spinner("Rewriting…"):
            dr = actions.rewrite(report, decision, persona)
        case["drafts"]["rewrite"] = dr.model_dump()
        audit.log("rewrite", report=name, persona=pkey, via=dr.via, note=dr.note)
    if "rewrite" in case["drafts"]:
        dr = actions.Draft.model_validate(case["drafts"]["rewrite"])
        st.caption(f"via **{dr.via}** · {dr.note}")
        a, b = st.columns(2)
        a.markdown("**Original**")
        a.text(_display_text(report.analyzed_text))
        b.markdown("**Rewrite**")
        b.markdown(dr.text)

with tab_src:
    targets = [ca for ca in decision.claims if ca.status in ("unverified", "unchecked", "weak_support", "contradiction_questionable") and ca.material]
    if not targets:
        st.caption("No material unverified or unchecked claims.")
    elif st.button(f"Find and check sources for {len(targets)} claims",
                   type="primary" if decision.action == "PULL_SOURCE" else "secondary"):
        with st.spinner("Asking for sources, fetching the pages, checking the figures…"):
            for ld in actions.pull_and_check(targets, report):
                case["leads"][ld.claim_id] = ld.model_dump()
        audit.log("pull_source", report=name, persona=pkey,
                  leads={k: {"url": v["url"], "verdict": (v["check"] or {}).get("verdict"), "fetch_error": v["fetch_error"]}
                         for k, v in case["leads"].items()})
    st.caption("The LLM only suggests where to look. Code fetches the page, finds the sentence with the claim's figure "
               "and checks it (same figure and scale, ±0.5%). ✅ the source states it · ❌ the source says something else "
               "· ⚪ not found · 🏷️ page could not be fetched: still a lead, not evidence.")
    for cid, ld in case["leads"].items():
        ld = actions.Lead.model_validate(ld)
        icon, words = actions.lead_verdict(ld)
        with st.container(border=True):
            label = f" · *{ld.label}*" if ld.label != words else ""
            st.markdown(f"**{cid}** · {icon} **{words}**{label} · via {ld.via}  \n{ld.source_name}"
                        + (f" · [{ld.url}]({ld.url})" if ld.url else ""))
            if ld.check:
                st.markdown(f"_{ld.check.reason}_")
            if ld.excerpt:
                st.markdown(f"> {ld.excerpt}")
            if ld.fetch_error:
                st.caption(f"fetch: {ld.fetch_error}")
            st.caption(f"query: `{ld.query}`" + (f" · expected: {ld.expected_value}" if ld.expected_value else "")
                       + (f" · {ld.why}" if ld.why else ""))
            unresolved = cid not in case["resolutions"] and any(q.ref == cid for q in decision.questions)
            if ld.check and ld.check.verdict == "supports" and unresolved:
                if st.button(f"Use this source for {cid}", key=f"{case_key}::use::{cid}"):
                    res = f"source:{ld.url}"
                    case["resolutions"][cid] = res
                    d = run_assess(report, persona, case["resolutions"])
                    case["timeline"].append({"action": d.action, "answers": {cid: res},
                                             "why": f"human accepted the checked source for {cid}"})
                    case["decision"] = d.model_dump()
                    case["drafts"].pop("rewrite", None)
                    audit.log("human_answers", report=name, persona=pkey, answers={cid: res}, action=d.action,
                              lead_check=ld.check.model_dump())
                    st.rerun()

with tab_esc:
    if st.button("Draft escalation note", type="primary" if decision.action in ("ESCALATE", "BLOCK") else "secondary"):
        with st.spinner("Drafting…"):
            dr = actions.escalation_note(decision, persona, report)
        case["drafts"]["escalation"] = dr.model_dump()
        audit.log("escalation_note", report=name, persona=pkey, via=dr.via)
    if "escalation" in case["drafts"]:
        dr = actions.Draft.model_validate(case["drafts"]["escalation"])
        st.caption(f"via **{dr.via}** · {dr.note}")
        st.markdown(dr.text)

# ---- final sign-off -------------------------------------------------------------------

st.divider()
st.subheader(f"Final sign-off · {persona.sign_off}")
awaiting_signoff = any(q.ref == "signoff" for q in decision.questions) or (
    decision.action == "APPROVE" and not decision.signed_off)
if case["executed"]:
    st.success(f"Done: {persona.action} → `{case['executed']}`")
    st.markdown(Path(case["executed"]).read_text(encoding="utf-8"))
elif case["resolutions"].get("signoff") == "send_back":
    st.warning(f"Sent back by {persona.sign_off}: decision is **{decision.action}**.")
    if st.button("Re-open for sign-off"):
        case["resolutions"].pop("signoff")
        d = run_assess(report, persona, case["resolutions"])
        case["decision"] = d.model_dump()
        case["timeline"].append({"action": d.action, "why": "re-opened for sign-off", "answers": {}})
        audit.log("reopen", report=name, persona=pkey, action=d.action)
        st.rerun()
elif not awaiting_signoff:
    st.info(f"Locked: the agent's decision is **{decision.action}**. Resolve the questions above first; "
            f"the action ({persona.action}) only runs after APPROVE + sign-off.")
else:
    if "rewrite" not in case["drafts"]:
        case["drafts"]["rewrite"] = actions.rewrite(report, decision, persona).model_dump()
    dr = actions.Draft.model_validate(case["drafts"]["rewrite"])
    st.markdown("**Text to be used:**")
    st.markdown(dr.text)
    signer = st.text_input(f"Your name ({persona.sign_off})", key=f"{case_key}::signer")
    c1, c2 = st.columns(2)
    if c1.button(f"✅ Sign off: {persona.action}", type="primary", disabled=not signer.strip()):
        case["resolutions"]["signoff"] = "approve"
        d = run_assess(report, persona, case["resolutions"])
        case["decision"] = d.model_dump()
        case["timeline"].append({"action": d.action, "why": f"signed off by {signer}", "answers": {"signoff": "approve"}})
        audit.log("signoff", report=name, persona=pkey, signer=signer, action=d.action, signed_off=d.signed_off)
        try:
            path = actions.execute(report, d, persona, dr.text, signer)
            case["executed"] = str(path)
            audit.log("executed", report=name, persona=pkey, path=str(path), signer=signer)
        except PermissionError as e:
            case["exec_error"] = str(e)
            audit.log("execute_refused", report=name, persona=pkey, error=str(e))
        st.rerun()
    if case.get("exec_error"):
        st.error(f"Not executed: {case['exec_error']}")
    if c2.button("↩️ Send back"):
        case["resolutions"]["signoff"] = "send_back"
        d = run_assess(report, persona, case["resolutions"])
        case["decision"] = d.model_dump()
        case["timeline"].append({"action": d.action, "why": "sent back by reviewer", "answers": {"signoff": "send_back"}})
        audit.log("send_back", report=name, persona=pkey, action=d.action)
        st.rerun()

# ---- claim table + audit ----------------------------------------------------------------

st.divider()
with st.expander(f"All claims ({len(decision.claims)}) · status, sources and your answers"):
    st.dataframe(
        [{
            "Ref": ca.claim_id,
            "Status": " ".join(STATUS.get(ca.status, STATUS["unknown"])[:2]),
            "Material": "yes" if ca.material else "",
            "Next step": f"{ACTION_ICON[ca.next_step]} {ca.next_step}",
            "Claim": ca.text,
            "Why": ca.why,
            "Best source": ca.best_source or "",
            "Human": ca.human_resolution or "",
        } for ca in decision.claims],
        hide_index=True,
    )
with st.expander("Audit log (runs/audit.jsonl)"):
    for ev in reversed(audit.tail(15)):
        st.caption(f"{ev['ts']} · {ev['event']} · {ev.get('report', '')} · {ev.get('action', '')}")
