# CLAUDE.md — Trust Agent (hackathon, 90-min build)

**Read `PLAN.md` first. It is the source of truth.** `CHALLENGE.md` is the official brief.

## Rules
- We are on the **Build Trust Agent** track: Ezio report PDF → next action (APPROVE / REWRITE /
  PULL_SOURCE / ESCALATE / BLOCK) with a human in the loop. It must work on **all 4 PDFs** in `reports/`.
- `CONTEXT.md` (local only, not in git; may be missing) is background only: use §3, §5, §6, §8. **Ignore §2 and §7 (Approval Gate track:
  gate.py, mock API, harness rules do not apply here).**
- **Ownership (PLAN.md §6b):** edit only your own files. `agent/schema.py` is shared, so agree before changing it.
- **The model locates, the code decides.** Parsing and `assess()` are deterministic. The LLM only drafts
  rewrites, source leads and prose, and every LLM call has a non-LLM fallback. Never let the LLM set the action.
- Every reason carries a `ref` (`C1`, `F2`, `U3`, `module:bias`). Unverified ≠ contradicted. Unknown verdicts weigh 0
  and go to `degraded`.
- No hard-coding of answers for specific reports: rules must be general.
- Secrets only from `.env` (`TOKENROUTER_*`). Never print or commit keys.
- Small changes; run `uv run pytest -q` (and the app) before saying something is done.

## Commands
```bash
uv sync
uv run pytest -q
uv run streamlit run app.py
uv run python -m agent.parse reports/ev_market.pdf   # dump parsed Report JSON
```
