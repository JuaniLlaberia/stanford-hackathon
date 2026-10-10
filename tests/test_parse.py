from pathlib import Path

import pytest

from agent.parse import parse_report

PDFS = sorted(Path("reports").glob("*.pdf"))


@pytest.fixture(scope="module")
def reports():
    return {p.stem: parse_report(p) for p in PDFS}


def test_all_four_reports_present():
    assert {p.stem for p in PDFS} >= {"ev_market", "nuclear", "wfh", "agi"}


@pytest.mark.parametrize("pdf", PDFS, ids=lambda p: p.stem)
def test_structure_generic(pdf):
    r = parse_report(pdf)
    assert r.analyzed_text and r.verdict_summary
    assert set(r.modules) == {"accuracy", "bias", "tone", "agreement"}
    assert all(v is None or 0 <= v <= 5 for v in r.modules.values())
    assert r.claims and all(c.id.startswith("C") and c.verdict for c in r.claims)
    assert all(s.type in {"Official", "News", "Reference", "Other"} for c in r.claims for s in c.sources)
    assert r.findings and all(f.severity in {"NOTE", "WARNING", "CRITICAL", "N/A"} for f in r.findings)
    assert not [d for d in r.degraded if d != "analyzed_text_garbled"]


def test_ev_market(reports):
    r = reports["ev_market"]
    assert r.modules == {"accuracy": 3, "bias": 5, "tone": 5, "agreement": 5}
    assert r.issue_count == 4 and "analyzed_text_garbled" in r.degraded
    c1 = r.claims[0]
    assert c1.verdict.startswith("Contradicted")
    say = [k for k in c1.checks if k.outcome == "sources_say"][0]
    assert "1.49 million" in say.said and say.source_n == 4
    tesla = r.claims[3]
    assert tesla.verdict == "Supported" and tesla.sources[0].type == "Official" and tesla.sources[0].date == "2025-01-29"


def test_agi_accuracy_not_scored(reports):
    r = reports["agi"]
    assert r.modules["accuracy"] is None and r.modules["bias"] == 1
    assert any(f.severity == "N/A" for f in r.findings)
    assert any(k.outcome == "sources_say" for c in r.claims for k in c.checks)


def test_nuclear_findings(reports):
    sev = {f.kind: f.severity for f in reports["nuclear"].findings}
    assert sev["Framing Bias Detection"] == "CRITICAL" and sev["Sycophancy Detection"] == "CRITICAL"
