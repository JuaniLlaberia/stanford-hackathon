# Trust Agent

Reads an Ezio trust-report PDF and decides the next action (**APPROVE / REWRITE / PULL_SOURCE / ESCALATE / BLOCK**)
for the person relying on the answer. Every reason points to a claim, finding, module score or source. An
accountable human answers the open questions, and the action runs only after APPROVE and their sign-off.

*The model locates, the code decides:* parsing and `assess()` are deterministic. The LLM only drafts rewrites,
source leads and prose, and each of those has a non-LLM fallback, so the app also works with TokenRouter down.

## Run

```bash
uv sync
cp .env.example .env                 # fill TOKENROUTER_BASE_URL / _API_KEY / _MODEL (optional)
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
| `agent/actions.py` | `rewrite`, `pull_source` (a lead, not evidence), `escalation_note`, gated `execute` |
| `agent/llm.py` | TokenRouter client: JSON output, 15 s deadline, one retry on bad JSON, `LLMError` |
| `agent/audit.py` | append-only `runs/audit.jsonl` |
| `app.py` | Streamlit UI |
