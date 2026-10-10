# Trust Agent

Reads an Ezio trust-report PDF and decides the next action (**APPROVE / REWRITE / PULL_SOURCE / ESCALATE / BLOCK**)
for the person relying on the answer. Every reason points to a claim, finding, module score or source. An
accountable human answers the open questions, and the action runs only after APPROVE and their sign-off.

*The model locates, the code decides:* parsing and `assess()` are deterministic. The LLM only drafts rewrites,
source leads and prose, and each of those has a non-LLM fallback, so the app also works with TokenRouter down.

The full decision rules (source weights, per-claim statuses, report-level signals, precedence) are in
**[POLICY.md](POLICY.md)**; the demo script is in [DEMO.md](DEMO.md).

## Source check (Pull source)

The LLM only suggests where to look (a primary URL plus up to 2 alternates). Code then fetches the page (HTML or
PDF), finds the sentence carrying the claim's figure and runs `verify_lead()` (`agent/verify.py`): same figure and
scale within ±0.5% (±0.5 pp for percentages), and the excerpt must really be on the fetched page.

| Verdict | Meaning |
|---|---|
| ✅ the source states it | the figure is on the page, in an on-topic sentence |
| ❌ the source says something else | an on-topic sentence about the same thing and year gives a different figure |
| ⚪ not found | the page was fetched but does not state the figure |
| 🏷️ lead, not evidence | no page could be fetched (blocked, timeout, no URL): it weighs 0 |

A contradiction needs real context (≥ 3 shared topic words, the same measured thing, the same year), so a short
claim can only come out ✅ or ⚪: unverified is never reported as contradicted. A ✅ source still counts only when
the accountable human clicks **Use this source** (recorded as `source:<url>`, human-attested).

## Run

```bash
uv sync
cp .env.example .env                 # add TOKENROUTER_API_KEY (model: anthropic/claude-haiku-5.5)
uv run python -m agent.llm           # LLM smoke test (prints latency or LLMError)
uv run streamlit run app.py
uv run pytest -q
```

In the app, pick a report and persona in the sidebar, read the decision and its reasons, answer the questions
(the decision is re-run and added to the timeline), then sign off to write `runs/<report>_<persona>_final.md`.
Every step is appended to `runs/audit.jsonl`.

## Layout

| File | What it does |
|---|---|
| `agent/parse.py` | PDF → `Report` (deterministic regex) |
| `agent/policy.py` | `assess(report, persona, resolutions) -> Decision` |
| `agent/actions.py` | `rewrite`, `pull_source` + `check_lead` (fetch + verify), `escalation_note`, gated `execute` |
| `agent/verify.py` | `verify_lead()`: deterministic figure check of a pulled source |
| `agent/llm.py` | TokenRouter client: JSON output, 15 s deadline, one retry on bad JSON, `LLMError` |
| `agent/audit.py` | append-only `runs/audit.jsonl` |
| `app.py` | Streamlit UI |
