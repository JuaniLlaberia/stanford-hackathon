from pathlib import Path

import pytest

from agent.personas import PERSONAS, persona_for
from agent.policy import assess, is_vacuous
from agent.schema import Report


def load(name: str) -> Report:
    return Report.model_validate_json(Path(f"reports/{name}.json").read_text())


def status(d, cid):
    return next(a for a in d.claims if a.claim_id == cid)


@pytest.mark.parametrize("name", ["ev_market", "nuclear", "wfh", "agi"])
@pytest.mark.parametrize("persona", list(PERSONAS))
def test_generic_contract(name, persona):
    d = assess(load(name), PERSONAS[persona])
    assert d.action in {"BLOCK", "ESCALATE", "PULL_SOURCE", "REWRITE", "APPROVE"}
    assert d.reasons and all(r.ref and r.text for r in d.reasons)
    assert d.not_checked_notes  # always say what the report could not check
    assert d.questions  # there is always a human hand-off before any action runs
    assert all(len(q.options) >= 2 for q in d.questions)
    assert 0 <= d.confidence <= 0.95 and not d.signed_off


def test_ev_acceptance():
    d = assess(load("ev_market"), persona_for("ev_market"))
    assert d.action == "ESCALATE"
    assert status(d, "C1").status == "contradiction_questionable"  # Ezio's own source says 1.49M
    assert status(d, "C2").status == "unverified" and status(d, "C2").next_step == "PULL_SOURCE"
    assert status(d, "C3").status == "vacuous" and status(d, "C5").status == "vacuous"
    assert status(d, "C4").status == "strong_support"
    assert [a.claim_id for a in d.claims if a.status == "unchecked"] == ["U1", "U2", "U3", "U4", "U5"]
    assert "115" in status(d, "U3").why and "97.7" not in status(d, "U3").why
    assert d.coverage.startswith("1 of 9 figures")
    assert any(r.ref == "report:garbled" for r in d.reasons)
    assert all(a.span for a in d.claims), "every claim must be locatable for highlighting"


def test_ev_human_loop_changes_decision():
    r, p = load("ev_market"), persona_for("ev_market")
    first = assess(r, p)
    answers = {q.ref: ("remove" if q.ref != "C2" else "caveat") for q in first.questions}
    second = assess(r, p, answers)
    assert second.action == "REWRITE"
    assert [q.ref for q in second.questions] == ["signoff"]
    third = assess(r, p, {**answers, "signoff": "approve"})
    assert third.action == "APPROVE" and third.signed_off
    back = assess(r, p, {**answers, "signoff": "send_back"})
    assert back.action == "ESCALATE" and not back.signed_off


def test_agi_blocks_then_rebuild():
    r, p = load("agi"), persona_for("agi")
    d = assess(r, p)
    assert d.action == "BLOCK" and [q.ref for q in d.questions] == ["report"]
    assert any("not scored" in n.lower() for n in d.not_checked_notes)
    c3 = status(d, "C3")
    assert c3.status == "unverified" and c3.next_step == "ESCALATE" and "40–84%" in c3.why  # counter-evidence
    rebuilt = assess(r, p, {"report": "rebuild"})
    assert rebuilt.action != "BLOCK" and {"C1", "C3"} <= {q.ref for q in rebuilt.questions}
    confirmed = assess(r, p, {"report": "block"})
    assert confirmed.action == "BLOCK" and not confirmed.questions


def test_critical_framing_needs_human():
    for name in ("nuclear", "wfh"):
        d = assess(load(name), persona_for(name))
        assert d.action == "ESCALATE"
        assert any(q.ref.startswith("F") for q in d.questions)
        assert any(r.ref == "module:bias" for r in d.reasons)
    d = assess(load("wfh"), persona_for("wfh"), {"F3": "block"})
    assert d.action == "BLOCK"


def test_vacuous():
    assert is_vacuous("Top 3 manufacturers by EV volume:")
    assert is_vacuous("2024 global EV market snapshot")
    assert not is_vacuous("Tesla reported $97.7 billion in 2024 revenue.")
    assert not is_vacuous("Permanent remote work removes that ambiguity.")
