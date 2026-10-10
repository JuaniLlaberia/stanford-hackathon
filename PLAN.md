# PLAN — Trust Agent (Ezio Build-Trust-Agent track)

> **Goal:** an agent that reads **any** of the four Ezio trust-report PDFs, decides the next action
> (**APPROVE / REWRITE / PULL_SOURCE / ESCALATE / BLOCK**) for the person relying on the answer, ties
> every reason to a specific claim, score, finding or source, says what the report could not check,
> and hands off to an accountable human who can change the decision.
>
> **Principle (from Punto Medio):** *the model locates, the code decides.* Parsing and the decision
> are deterministic code. The LLM (TokenRouter) only finds unchecked claims, drafts rewrites and
> writes explanation prose, and every LLM step has a non-LLM fallback.
>
> **Budget:** about 70 min of build after this plan is approved, then about 10 min for the video and
> submission. Cut lines are marked ✂.

---

## 1. Rubric → features

| Rubric (pts) | What we build | Where |
|---|---|---|
| Evidence use (35) | Per-claim treatment: verdict + **the specific source Ezio cited** ("matches (Source 1)") weighted by type and date. **Unverified ≠ contradicted.** Module bars + findings. **Audits of the report itself** (see §3) | `agent/policy.py` |
| Decision quality (30) | Most severe action, plus a per-claim plan. Every reason has a `ref` (`C1`, `F2`, `module:accuracy`, `U3`). A **"What this report could not check"** section from the coverage-gap scan | `agent/policy.py`, `agent/coverage.py` |
| Human in the loop (20) | Questions with options (safe option last) at 3 moments. Answers feed back into `assess()` → **decision timeline** (e.g. ESCALATE → REWRITE → APPROVE). Audit log | `app.py`, `agent/audit.py` |
| Demo clarity (15) | Streamlit page: report → claim table with status chips → highlighted text → decision + reasons → questions → updated decision → final note | `app.py` |

---

## 2. What the real report looks like (EV PDF, verified with pypdf)

`pypdf` emits one word per line → normalise with `" ".join(text.split())`. Stable markers:

```
Analyzed text <LLM answer> Verdict <summary> Issues found: N
Module Scores Accuracy: ███░░ … Bias: █████ … Tone: █████ … Agreement: █████ …
Claim analysis Only the listed claims were assessed. …
Claim N <text> Verdict: <Supported | Not enough evidence | Contradicted: amount | …> Verification: Completed
  ● What|Amount|When|…: <span> — <matches (Source k) | Not found in the sources checked. | sources say X (Source k)>
  Sources: ● k <Official|News|Reference|Other> · <title> [— Published: YYYY-MM-DD]
Findings <Title> (NOTE|WARNING|CRITICAL) <text> [Evidence: ● "✓|✗|? <claim>"] [Reasoning: …]
Methodology …
```

**All 4 PDFs checked (same structure, 5 claims each, 4 issues each):**

| Report (`reports/`) | Accuracy | Bias | Tone | Agree | Verdicts | Findings ≥ WARNING | Likely action |
|---|---|---|---|---|---|---|---|
| `ev_market.pdf` | 3 | 5 | 5 | 5 | Supported, Not enough evidence, Contradicted: amount | Factual WARNING | ESCALATE |
| `nuclear.pdf` | 5 | 1 | 5 | 3 | Supported, Not enough evidence | Framing CRITICAL, Sycophancy CRITICAL | REWRITE/ESCALATE |
| `wfh.pdf` | 5 | 1 | 4 | 5 | Supported, Not enough evidence | Framing CRITICAL | REWRITE |
| `agi.pdf` | **Not scored** | 1 | 5 | 5 | all Not enough evidence | Framing CRITICAL (no Factual finding) | PULL_SOURCE/BLOCK |

Finding-title regex: strip the leading `Findings ` from the first title. AGI's Accuracy is the literal text `Not scored`.

Gotchas to handle (and to **use** in the demo):
- Source types include **`Reference`** (Wikipedia). Severities include **`WARNING`**.
- **Accuracy can be missing** ("fewer than two checkable claims shows no score") → `None`, not 0.
- Unknown verdict strings → status `unknown`, weight 0, added to `degraded` (never guess).
- The analysed text can be **garbled** (here `$…$` rendered as math: `115 𝑝𝑒𝑟𝑘𝑊ℎ…`) → detect Unicode
  math-alphanumerics (U+1D400–U+1D7FF) → `degraded: analyzed_text_garbled` → REWRITE reason.

---

## 3. Decision policy (deterministic, `agent/policy.py`)

### 3.1 Source quality (for the source Ezio says matched; otherwise the best listed one)
| Type | Weight | | Date rule | Multiplier |
|---|---|---|---|---|
| Official | 1.0 | | published ≥ claim period | ×1 |
| News | 0.7 | | no date | ×0.6 |
| Reference | 0.5 | | published **before** the period the claim is about (2010 source for a 2024 figure) | ×0 ("predates claim") |
| Other | 0.3 | | | |

`strong_support` if score ≥ 0.7, else `weak_support`.

### 3.2 Per-claim status
| Ezio verdict | Extra check | Status | Per-claim action |
|---|---|---|---|
| Supported | source score ≥ 0.7 | `strong_support` | keep, cite the source |
| Supported | weak / Other-only / stale | `weak_support` | REWRITE: hedge + cite |
| Supported | **vacuous claim** (title or list header: no number, no verb, ends with `:` or ≤ 6 words) | `vacuous` | doesn't count as evidence; flag that it inflates the score |
| Not enough evidence | material (has a figure, date or ranking) | `unverified` | PULL_SOURCE → else ESCALATE |
| Not enough evidence | not material | `unverified` | REWRITE: hedge |
| Contradicted | the numbers in "sources say X" **equal** the claim's numbers, or only Other sources | `contradiction_questionable` | **ESCALATE** (the report contradicts itself) |
| Contradicted | consistent + Official/News source | `contradicted` | material → BLOCK the claim (remove/replace); the whole note → BLOCK if ≥ 50% of material claims are contradicted |
| (not in report) | figure found in the analysed text but in no checked claim | `unchecked` | PULL_SOURCE / caveat |

### 3.3 Report-level signals
- Module bars: any of Bias / Tone / Agreement ≤ 3 → REWRITE (framing/tone/sycophancy) with ref `module:x`.
  Accuracy ≤ 2 → ESCALATE. Accuracy missing → note "accuracy not scored" (the dangerous state, lesson 6).
- Findings: CRITICAL → at least ESCALATE; WARNING → at least REWRITE; NOTE → informational.
- **Coverage (headline metric):** `figures strongly verified / figures in the text`.
  EV report: Ezio says "2 of 3 hold up", but **only 1 of 9 figures** ($97.7B, Tesla 10-K, Official) is strongly backed.

### 3.4 Primary action
Precedence **BLOCK > ESCALATE > PULL_SOURCE > REWRITE > APPROVE**, taking the most severe across claims,
modules, findings and degraded. APPROVE for a client- or public-facing persona **still requires one human sign-off**.
Confidence = capped at 0.95: `coverage × (1 − share of questionable/contradicted)`. Shown, never used to decide.

### 3.5 Human-in-the-loop moments (`HumanQuestion`, safe option last)
1. **Questionable or contradicted claim** → "Keep (cite source k) / Replace with value you provide / Remove claim".
2. **Material unverified or unchecked claim** (after pull-source) → "I have a source (URL) / Keep with explicit caveat / Remove".
3. **Final sign-off** on the rewritten text → "Approve for <action> / Send back".
Answers → `resolutions[ref]` → re-run `assess(report, persona, resolutions)` → append to the decision timeline.
A human-provided URL is marked **human-attested** (not Ezio-verified), and the agent suggests re-checking the rewrite in the Ezio web app.

### 3.6 Expected result on the EV report (acceptance test)
| Ref | Claim | Status | Why |
|---|---|---|---|
| C1 | Geely 1.49M | contradiction_questionable | Ezio says "Contradicted: amount" but its own Source 4 says 1.49M; Source 4 is `Other` |
| C2 | 17.1M sold 2024 / 13.9M 2023 | unverified (material) | Official IEA/BTS listed but "Not found"; a source from 2010 predates the claim |
| C3 | "Top 3 manufacturers by EV volume:" | vacuous / unverified | list header; the BYD 4.27M and Tesla 1.79M figures were never checked |
| C4 | Tesla $97.7B | strong_support | Source 1 Official `tsla-20241231` (10-K) |
| C5 | "2024 global EV market snapshot" | vacuous | a title counted as a supported claim |
| U1–U5 | BYD 4.27M, Tesla 1.79M, $115/kWh (garbled), China 70%, recycling >90% | unchecked | not among the assessed claims |

→ **ESCALATE** (C1) + PULL_SOURCE (C2, U1–U5) + REWRITE (garbled price, vacuous C3/C5). After the analyst's
answers → REWRITE → sign-off → **APPROVE for client note** with caveats and citations.

---

## 4. Personas (dropdown; the policy is the same, only stakes and wording change)

| Report | Who relies on it | Action gated | Sign-off |
|---|---|---|---|
| EV market (**demo**) | Equity research analyst | Put figures in a client note | Analyst / research lead |
| Work from home | Comms lead | Publish blog post | Comms lead |
| Nuclear energy | Policy advisor | Brief a legislator | Policy director |
| AGI timelines | VC associate | Send in an investment memo | Partner |

`persona = {who, action, sign_off, client_facing: bool}`, in `agent/personas.py`.

---

## 5. Architecture and contract (agree on this first, then work in parallel)

```
reports/*.pdf
   │ parse_report(path) -> Report                    agent/parse.py      (Juan)   deterministic regex
   │ find_unchecked(report) -> list[Claim]           agent/coverage.py   (Juan)   numbers first; LLM adds non-numeric (✂)
   │ assess(report, persona, resolutions) -> Decision agent/policy.py    (Juan)   pure function
   │ actions: rewrite(report, decision) -> str       agent/actions.py    (Teammate) LLM; fallback = strip/hedge flagged sentences
   │          pull_source(claim) -> Lead             agent/actions.py    (Teammate) LLM lead (weight 0 until human confirms); HarnessRouter ✂
   │          escalation_note(decision) -> str       agent/actions.py    (Teammate) template + LLM prose
   │ audit.log(event)                                agent/audit.py      (Teammate) runs/audit.jsonl
   └ app.py (Streamlit)                                                   (Teammate)
agent/llm.py: TokenRouter OpenAI-compatible client, JSON output, 1 nudge retry, 15 s deadline, raises LLMError → caller falls back
agent/schema.py: Pydantic models (draft exists; update per §2: Reference, WARNING, checks[], accuracy None)
```

Schema additions to the draft `agent/schema.py`:
- `Source.type += "Reference"`; `Source.n: int`.
- `Claim.checks: list[Check]` with `Check{field: What|Amount|When|…, span, outcome: matches|not_found|sources_say, said: str|None, source_n: int|None}`.
- `Report.modules: dict[str, int | None]`; `Severity += "WARNING"`; `Finding.evidence: list[str]`.
- `Status += "vacuous", "contradiction_questionable", "unknown"`.

**Hand-off at about T+15:** Juan commits `reports/ev_market.json` (the parsed `Report`), so the teammate's UI runs on real data before `assess()` exists. The teammate stubs `assess()` until then.

---

## 6. Stages

### Stage 0 — Setup (both, 5 min)
- [ ] `git init`, first commit, push to a GitHub repo, both clone (two Claude Code sessions need a shared repo).
- [ ] `.env` from `.env.example` (TokenRouter URL/key/model); `uv sync`; LLM smoke test (one JSON call, note the latency).
- [ ] All 4 PDFs in `reports/` with clear names (`ev_market.pdf`, `wfh.pdf`, `nuclear.pdf`, `agi.pdf`).
- [ ] Both read §5. Agree on the schema.
- **Done when:** both can `uv run streamlit run app.py` (an empty page is fine) and the LLM ping works.

### Stage 1 — Parse every report (Juan 20 min ∥ Teammate 20 min)
**Juan:** `agent/parse.py` + `tests/test_parse.py`
- [ ] Normalise text; slice the sections; regex for verdict/issues, modules (count `█`; missing → None), claims (text, verdict, checks, sources), findings (title, severity, text, evidence).
- [ ] Assign `C1..`, `F1..`; detect garbled text; anything unmatched → `degraded`.
- [ ] Run on **all 4 PDFs**; dump `reports/<name>.json`; asserts: claims ≥ 1, 4 module keys, severities valid, sources typed.
- **Done when:** the EV JSON matches §3.6 counts (5 claims, 4 findings, accuracy 3) and the other 3 parse without exceptions.

**Teammate:** `agent/llm.py`, `agent/audit.py`, `app.py` skeleton
- [ ] LLM client (`chat_json(system, user, schema) -> dict`, deadline, nudge retry, `LLMError`).
- [ ] Streamlit layout: sidebar (report picker + persona picker), left = analysed text, right = decision panel, bottom = claim table. Runs on `ev_market.json` + a stub decision.
- [ ] Audit log writer (`runs/audit.jsonl`: ts, report, persona, decision, reasons, human answers).

### Stage 2 — Decide + human loop (Juan 20 min ∥ Teammate 20 min)
**Juan:** `agent/policy.py`, `agent/coverage.py`, `tests/test_policy.py`
- [ ] Source scoring, per-claim status, report-level signals, coverage metric, primary action, reasons with refs, `not_checked_notes`, questions (§3).
- [ ] `find_unchecked`: numbers/percent/currency regex over the analysed text minus numbers in the checked claims → `U1..` with a verbatim span.
- [ ] Resolutions: `drop` / `caveat` / `source:<url>` / `keep` / `replace:<value>` change the statuses → re-decide.
- [ ] Test: EV → ESCALATE with the §3.6 statuses; after resolving all questions → REWRITE → after sign-off → APPROVE.

**Teammate:** `agent/actions.py` + the HITL UI
- [ ] Questions rendered as radio + optional text input; "Submit answers" → `assess(…, resolutions)` → **timeline** of decisions (chip per step, with the reason that changed).
- [ ] `rewrite()`: LLM gets the text + per-claim instructions (remove/hedge/cite with IDs); fallback = deterministic sentence strip/hedge. Show a diff or side-by-side.
- [ ] `pull_source()`: LLM proposes an authoritative source + query + expected value, labelled **"lead, not evidence"**.
- [ ] Final sign-off button → action "executed" (e.g. note written to `runs/client_note.md`) **only after APPROVE + sign-off**.

### Stage 3 — Integrate and test generality (both, 10 min)
- [ ] Wire the real `assess()` into the UI; run all 4 reports × their personas; fix crashes only.
- [ ] Highlight spans in the analysed text by status (✅ strong, ⚠️ weak/unverified, ❌ contradicted, ❓ questionable, ⬜ unchecked). Icons + words, not just colour.
- [ ] Headline: "Ezio: 2 of 3 hold up · Agent: 1 of 9 figures backed by an official source".
- ✂ LLM unchecked-claim extraction for non-numeric claims (verbatim-verified spans), if there's time.

### Stage 4 — Stretch (only if Stage 3 is green by about T+55)
- ✂ HarnessRouter research sub-agent for `pull_source` (web search → URL + excerpt; code checks the excerpt contains the figure before it counts).
- ✂ Ezio closed loop: paste the rewritten note into the Ezio web app live and show the new verdict (manual, in the video).

### Stage 5 — Demo and submit (10 min, hard stop)
- [ ] Record 1–2 min (script below). README with run steps. Submit.

---

## 6b. Per-person checklists (give each Claude Code session only its own)

> Prompt for each session: *"Read CLAUDE.md and PLAN.md. You are <Juan | Teammate>. Do only your
> checklist in PLAN.md §6b, edit only your files, and tell me when each stage's 'Done when' passes."*

### Juan — ML/AI · owns `agent/parse.py`, `agent/coverage.py`, `agent/policy.py`, `agent/personas.py`, `tests/`
| Stage | Tasks |
|---|---|
| 0 | Update `agent/schema.py` per §5 (with Teammate's OK) and commit it **first** |
| 1 | `parse_report()` on all 4 PDFs → commit `reports/*.json` (**hand-off to Teammate at about T+15**); `tests/test_parse.py` |
| 2 | `assess()` (§3), `find_unchecked()`, resolutions → re-decide, `personas.py`; `tests/test_policy.py` (EV acceptance §3.6) |
| 3 | Run all 4 reports × personas, fix parser/policy bugs; spans for highlighting |
| 4 ✂ | Verify a pulled source's excerpt contains the figure before it counts |
| 5 | Write the demo script and do the voice-over |

### Teammate — backend/UI · owns `agent/llm.py`, `agent/actions.py`, `agent/audit.py`, `app.py`, `README.md`
| Stage | Tasks |
|---|---|
| 0 | GitHub repo + push, `.env`, TokenRouter smoke test (`agent/llm.py` ping) |
| 1 | `llm.py` (JSON, deadline, nudge, `LLMError`), `audit.py`, `app.py` skeleton on `reports/ev_market.json` + a placeholder `assess()` |
| 2 | `actions.py` (rewrite / pull_source / escalation_note, each with a fallback); HITL UI: questions → answers → re-decide → timeline; sign-off → action executed |
| 3 | Wire the real `assess()`; highlight spans; headline coverage metric; report + persona switcher |
| 4 ✂ | HarnessRouter research sub-agent behind `pull_source()` |
| 5 | Record the video, README run steps, submit |

---

## 7. Demo script (≈ 100 s)
1. **Stakes (10 s):** "An analyst wants to put this ChatGPT EV snapshot into a client note. Ezio says 2 of 3 claims hold up."
2. **Evidence (30 s):** the agent's table. Tesla $97.7B ✅ is backed by the 10-K. Two "supported" claims are a title and a header. Geely ❓: Ezio says contradicted, but its own source says the same 1.49M. 17.1M ⚠️ not found. Five figures ⬜ never checked. Garbled battery price. → "Only **1 of 9** figures is backed."
3. **Decision (15 s):** **ESCALATE**, with each reason clicking through to C1 / C2 / U1–U5 / module:accuracy, plus "what the report couldn't check".
4. **Human (30 s):** the analyst removes Geely, keeps 17.1M with a caveat and an IEA lead, removes the unchecked figures → the timeline shows ESCALATE → REWRITE → sign-off → **APPROVE**; the client note is written; the audit log is shown.
5. **Generality (15 s):** switch to the WFH report and comms-lead persona → a different action, for different reasons.

## 8. Risks and fallbacks
| Risk | Fallback |
|---|---|
| Another PDF breaks the regexes | Tolerant regexes + `degraded`; ✂ LLM-extract that section with a verbatim check |
| TokenRouter slow or down | Every LLM step has a deterministic fallback; the decision never needs the LLM |
| Running behind at T+45 | Drop Stage 3 ✂ and Stage 4; demo the deterministic path + HITL |
| Merge conflicts | Strict file ownership (§5); only `schema.py` is shared → change it together |
