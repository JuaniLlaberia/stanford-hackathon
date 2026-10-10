from agent.verify import quantities, verify_lead

U = "https://example.org"


def test_scale_and_format():
    q = {x.value for x in quantities("BYD sold 4.27 million vehicles; revenue $97.7bn; 4,270,000 units")}
    assert {4.27e6, 97.7e9} <= q and 4270000.0 in q


def test_supports_same_figure_other_format():
    r = verify_lead("BYD: 4.27 million vehicles", U, "BYD delivered 4,270,000 new-energy vehicles in 2024.")
    assert r.verdict == "supports" and r.counts_as_evidence


def test_contradicts_different_figure():
    r = verify_lead("They drive resolution rates to 100%", U, "Resolution rate ranges from 40% to 84% by industry")
    assert r.verdict == "contradicts" and r.counts_as_evidence and "100%" in r.missing


def test_percent_tolerance():
    assert verify_lead("China made roughly 70% of EVs", U, "China produced 70.4% of all EVs").verdict == "supports"
    assert verify_lead("China made roughly 70% of EVs", U, "China produced 72% of all EVs").verdict == "contradicts"


def test_partial_is_insufficient():
    r = verify_lead("17.1 million sold in 2024, up from 13.9 million in 2023", U, "Sales reached 17.1 million in 2024.")
    assert r.verdict == "insufficient" and r.matched and r.missing and not r.counts_as_evidence


def test_excerpt_must_be_in_page():
    r = verify_lead("BYD: 4.27 million", U, "BYD sold 4.27 million", page_text="Nothing about BYD here.")
    assert r.verdict == "excerpt_not_in_source" and not r.counts_as_evidence


def test_non_numeric_needs_human():
    assert verify_lead("Permanent remote work removes that ambiguity.", U, "Remote work helps.").verdict == "insufficient"
