"""Actions, LLM client and audit log: all must work with the LLM unavailable."""
import json

import pytest

from agent import actions, audit, llm
from agent.schema import Check, Claim, ClaimAssessment, Decision, Persona, Reason, Report, Source

TEXT = (
    "**2024 snapshot** - **Sales:** About **17.1 million** EVs were sold in **2024**. "
    "- **Makers:** 1. **BYD:** **4.27 million** vehicles 2. **Geely:** **~1.49 million** vehicles "
    "- **Revenue:** Tesla reported **97.7 billion** in 2024. Overall, a record year."
)
PERSONA = Persona(key="analyst", who="Equity research analyst", action="put the figures in a client note", sign_off="Research lead")


def _report():
    return Report(
        name="t", analyzed_text=TEXT, verdict_summary="2 of 3 checkable claims hold up.",
        claims=[Claim(id="C2", text="About 17.1 million EVs were sold in 2024", verdict="Not enough evidence",
                      checks=[Check(field="What", span="17.1 million", outcome="not_found", source_n=1)],
                      sources=[Source(n=1, type="Official", title="Global EV Outlook - IEA")])],
    )


def _decision(**res):
    def ca(cid, text, status, step="APPROVE", **kw):
        return ClaimAssessment(claim_id=cid, text=text, status=status, material=True, why="x", next_step=step,
                               human_resolution=res.get(cid), span=text, **kw)
    return Decision(
        action="ESCALATE", headline="h", coverage="1 of 4 figures strongly verified",
        reasons=[Reason(ref="C1", text="questionable", pushes="ESCALATE")],
        claims=[
            ca("C1", "~1.49 million", "contradiction_questionable", "ESCALATE"),
            ca("C2", "About 17.1 million EVs were sold in 2024", "unverified", "PULL_SOURCE"),
            ca("C4", "Tesla reported 97.7 billion", "strong_support", best_source="Source 1 · Official · tsla-10K"),
            ca("U1", "4.27 million", "unchecked", "PULL_SOURCE"),
        ],
        not_checked_notes=["BYD 4.27 million never checked"], questions=[], confidence=0.2,
    )


@pytest.fixture(autouse=True)
def no_llm(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise llm.LLMError("offline")
    monkeypatch.setattr(actions, "chat_json", boom)
    monkeypatch.setattr(audit, "AUDIT_PATH", tmp_path / "audit.jsonl")
    monkeypatch.chdir(tmp_path)


def test_rewrite_fallback_applies_plan():
    d = actions.rewrite(_report(), _decision(C1="remove", U1="caveat"), PERSONA)
    assert d.via == "fallback"
    assert "1.49" not in d.text  # removed
    assert "[U1: unverified]" in d.text and "[C2: unverified]" in d.text
    assert "[C4: Source 1 · Official · tsla-10K]" in d.text


def test_rewrite_rejects_llm_with_new_figures(monkeypatch):
    monkeypatch.setattr(actions, "chat_json", lambda *a, **k: {"text": "Sales were 18.3 million."})
    d = actions.rewrite(_report(), _decision(), PERSONA)
    assert d.via == "fallback" and "18.3" in d.note


def test_rewrite_accepts_clean_llm(monkeypatch):
    monkeypatch.setattr(actions, "chat_json", lambda *a, **k: {"text": "About 17.1 million (unverified) in 2024. Tesla reported 97.7 billion."})
    assert actions.rewrite(_report(), _decision(), PERSONA).via == "llm"


def test_clean_text_fixes_math_garbling():
    assert "perkWh" in actions.clean_text("115 𝑝𝑒𝑟𝑘𝑊ℎ")


def test_pull_source_fallback_is_a_lead_using_ezio_official_source():
    lead = actions.pull_source(_decision().claims[1], _report())
    assert lead.label == "lead, not evidence" and lead.via == "fallback"
    assert "IEA" in lead.source_name and "17.1" in lead.query


def test_escalation_note_fallback_has_refs():
    d = actions.escalation_note(_decision(), PERSONA, _report())
    assert d.via == "fallback" and "`C1`" in d.text and "Research lead" in d.text and "never checked" in d.text


def test_execute_gated_on_approve_and_signoff():
    d = _decision()
    with pytest.raises(PermissionError):
        actions.execute(_report(), d, PERSONA, "text", "Ana")
    d = d.model_copy(update={"action": "APPROVE"})
    with pytest.raises(PermissionError):
        actions.execute(_report(), d, PERSONA, "text", "Ana")
    d = d.model_copy(update={"signed_off": True})
    path = actions.execute(_report(), d, PERSONA, "text", "Ana")
    assert "Signed off by: Ana" in path.read_text()


def test_audit_appends_jsonl():
    audit.log("a", x=1)
    audit.log("b", decision=_decision())
    lines = audit.AUDIT_PATH.read_text().splitlines()
    assert [json.loads(l)["event"] for l in lines] == ["a", "b"]
    assert audit.tail(1)[0]["decision"]["action"] == "ESCALATE"


def test_llm_parse_json_tolerates_fences():
    assert llm._parse_json('```json\n{"ok": true}\n```') == {"ok": True}
    assert llm._parse_json('Sure: {"ok": 1} done') == {"ok": 1}


def test_llm_retries_once_then_raises(monkeypatch):
    calls = []

    class Msg:
        content = "not json"

    class Resp:
        choices = [type("C", (), {"message": Msg})]

    class Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    calls.append(kw)
                    return Resp

    monkeypatch.setattr(llm, "_get_client", lambda: Client)
    monkeypatch.setenv("TOKENROUTER_MODEL", "m")
    with pytest.raises(llm.LLMError):
        llm.chat_json("s", "u")
    assert len(calls) == 2  # first try + one nudge


def test_fallback_keeps_numbered_items_and_flags_garbling():
    r = _report().model_copy(update={"analyzed_text": TEXT + " - **Price:** fell to 115 𝑝𝑒𝑟𝑘𝑊ℎ in 2024."})
    d = actions.rewrite_fallback(r, _decision())
    assert "1. BYD" in d.text.replace("**", "")
    assert "[garbled in the source answer" in d.text and "perkWh" in d.text


def test_rewrite_rejects_llm_that_keeps_removed_or_drops_backed(monkeypatch):
    dec = _decision(C1="remove")
    monkeypatch.setattr(actions, "chat_json", lambda *a, **k: {"text": "Geely ~1.49 million. Tesla 97.7 billion."})
    d = actions.rewrite(_report(), dec, PERSONA)
    assert d.via == "fallback" and "kept removed figures: 1.49" in d.note
    monkeypatch.setattr(actions, "chat_json", lambda *a, **k: {"text": "About 17.1 million (unverified)."})
    d = actions.rewrite(_report(), dec, PERSONA)
    assert d.via == "fallback" and "dropped backed/kept figures: 97.7" in d.note


def test_fallback_removal_keeps_backed_claim_sharing_a_sentence():
    text = "- **Price:** fell to 115 per kWh and Tesla reported 97.7 billion in 2024."
    r = _report().model_copy(update={"analyzed_text": text})
    def ca(cid, t, status, res=None, **kw):
        return ClaimAssessment(claim_id=cid, text=t, status=status, material=True, why="x", next_step="APPROVE",
                               span=text[2:], human_resolution=res, **kw)
    dec = _decision().model_copy(update={"claims": [
        ca("C4", "Tesla reported 97.7 billion in 2024.", "strong_support", best_source="10-K"),
        ca("U3", "Price fell to 115 per kWh", "unchecked", "remove"),
    ]})
    out = actions.rewrite_fallback(r, dec).text
    assert "97.7" in out and "115" not in out and "[C4: 10-K]" in out


def test_find_excerpt_needs_topic_scope_and_year():
    geely = "Geely Group: 1.49 million new-energy vehicles"
    assert actions.find_excerpt(geely, "In 2024 Geely Group sold 1.49 million new-energy vehicles.") is not None
    # different figure, different scope (all vehicles vs new-energy): not a contradiction
    assert actions.find_excerpt(geely, "Geely Group produced 2.17 million vehicles in 2024.", "2024") is None
    # same figure, unrelated topic: not support
    assert actions.find_excerpt("China made roughly 70% of global EV production",
                                "More than 70% of consumers are optimistic.") is None


def test_check_lead_verdicts(monkeypatch):
    lead = actions.Lead(claim_id="U1", source_name="x", url="https://a.example/byd", query="q",
                        alternates=["https://b.example/byd"])
    pages = {"https://a.example/byd": OSError("403"),
             "https://b.example/byd": "Intro. BYD sold 4,272,145 vehicles in 2024, a record. Other text."}

    def fake(url):
        if isinstance(pages[url], Exception):
            raise pages[url]
        return pages[url]

    monkeypatch.setattr(actions, "fetch_page", fake)
    out = actions.check_lead(lead, "BYD: 4.27 million vehicles", "2024")
    assert out.check.verdict == "supports" and out.url.startswith("https://b.") and actions.lead_verdict(out)[0] == "✅"
    pages["https://b.example/byd"] = "BYD sold 3.02 million new-energy vehicles in 2024 according to its filing."
    out = actions.check_lead(lead, "BYD sold 4.27 million new-energy vehicles in 2024", "2024")
    assert out.check.verdict == "contradicts" and actions.lead_verdict(out)[0] == "❌"
    pages["https://b.example/byd"] = OSError("403")
    out = actions.check_lead(lead, "BYD: 4.27 million vehicles")
    assert actions.check_lead(lead.model_copy(update={"alternates": []}), "x 4.27 million").check is None
    assert out.check is None and out.label == "lead, not evidence" and actions.lead_verdict(out)[0] == "🏷️"
