# Decision policy — Trust Agent

**Input:** an Ezio trust-report PDF (any of the four samples). **Output:** one action, **BLOCK > ESCALATE >
PULL_SOURCE > REWRITE > APPROVE**, chosen as the most severe action any signal pushes for. Every reason
carries a reference (`C1` claim, `U2` unchecked figure, `F3` finding, `module:bias`, `report`).

**Principle: the model locates, the code decides.** Parsing (`agent/parse.py`) and the decision
(`agent/policy.py`) are deterministic code with no LLM. The LLM (TokenRouter) only drafts rewrites, source leads and
note prose; code checks its output, and every LLM step has a non-LLM fallback. A prompt injection inside the
analysed text cannot approve anything.

## 1. Per-claim treatment (we read the evidence, not the verdict line)

| Ezio says | What the agent checks | Status → next step |
|---|---|---|
| Supported | Source score of the source Ezio says matched ≥ 0.7 | `strong_support` → keep, cite it |
| Supported | Score < 0.7 (only Other/Reference, undated, or stale) | `weak_support` → REWRITE: hedge + cite |
| any | Text is a title or list header (ends with `:`, or ≤ 6 words with no verb and no figure) | `vacuous` → doesn't count as evidence |
| Contradicted | Its own "sources say X" has the **same figure** as the claim, or the evidence is only Other/Reference | `contradiction_questionable` → **ESCALATE** |
| Contradicted | Consistent, Official/News evidence | `contradicted` → BLOCK the claim (REWRITE if it has no figure) |
| Not enough evidence | Its own check found sources stating a **different** figure | `unverified` with counter-evidence → **ESCALATE** |
| Not enough evidence | Has a figure, date, ranking or quantity | `unverified` → **PULL_SOURCE**, then ask the human |
| Not enough evidence | No figure (opinion, framing) | `unverified` → REWRITE: hedge or present as opinion |
| *(not assessed)* | A figure in the answer that appears in **no** assessed claim | `unchecked` (U1…) → PULL_SOURCE |
| Unknown verdict text | — | `unknown`, weight 0 → ESCALATE (never guessed) |

**Unverified ≠ contradicted.** "Not enough evidence" is an open question with an owner, never "false".

**Source score** = type weight × date factor.
Type: Official 1.0 · News 0.7 · Reference 0.5 · Other 0.3, ordered by how accountable the publisher is for the figure.
Date: published before the period the claim is about → **0**, since a 2010 page cannot confirm 2024 sales;
undated → 0.6; otherwise 1. Threshold 0.7 means one dated Official source or one dated News source is enough; an "Other" blog never is.
Duplicate titles are flagged ("one account repeated, not independent evidence").

## 2. Report-level signals

| Signal | Threshold | Pushes | Why |
|---|---|---|---|
| Accuracy bar | ≤ 2/5 | ESCALATE | most checked claims failed |
| Accuracy | **not scored** | note, never a pass | "nothing checked" looks like green; we say so explicitly |
| Bias / Tone / Agreement bar | ≤ 3/5 | REWRITE | one-sided, promotional or sycophantic text can be fixed by a rewrite |
| Finding CRITICAL | — | ESCALATE + question | a human decides whether a one-sided piece is usable (rewrite / label as opinion / don't use) |
| Finding WARNING | — | REWRITE | |
| Garbled analysed text | math-alphanumeric chars | REWRITE | a figure in it can't be read reliably |
| Nothing material backed and ≥ 3 open material claims, or ≥ 50% of material claims contradicted | — | **BLOCK** + override question | the answer can't be used as is |

**Coverage (headline):** figures backed by a strong source ÷ all figures in the answer (or claims, when the answer
has fewer than 3 figures). EV: Ezio says "2 of 3 hold up"; the agent says **1 of 9 figures backed**.
**Confidence** = share of strong claims × (1 − share disputed), capped at 0.95. It is shown but **never used to decide**.

## 3. Human in the loop

The agent hands off to the accountable person for the persona (analyst → research lead, comms lead → head of comms,
policy advisor → director, VC associate → partner) **only where the evidence cannot decide**:

1. **A disputed or contradicted claim:** keep and cite / replace with a value / remove.
2. **A material unverified or unchecked figure:** I have a source (URL) / keep with an "unverified" caveat / remove.
3. **A CRITICAL framing or sycophancy finding:** rewrite balanced / label as opinion / don't use.
4. **A blocked answer:** rebuild from sources / confirm the block.
5. **Final sign-off** on the rewritten text, required even when everything is backed: approve / send back.

The safe option is always last. Answers are fed back into `assess()`, which re-decides from scratch, and the change
is shown in a decision timeline (e.g. ESCALATE → REWRITE → APPROVE). A human-supplied source is labelled
**human-attested**, not Ezio-verified. The action (e.g. writing the client note) runs **only** on APPROVE plus a
named sign-off. Every step goes to `runs/audit.jsonl`.

## 4. Pulled sources

A lead from the LLM weighs **0**. It counts only if `verify_lead()` confirms that (a) the excerpt is really on the
fetched page, and (b) it states the claim's figure with the same scale, within ±0.5 percentage points (percentages) or ±0.5%
(amounts). A different figure of the same kind is **evidence against** the claim. Claims without figures always need
a human to read the source.

## 5. What the agent says it could not check

Every decision lists: figures never assessed (U1…), claims Ezio searched but could not confirm, title or header
"claims" that inflate Ezio's count, accuracy not scored, and how many sentences were not assessed at all
(opinions, framing, summary).

## 6. Results on the four sample reports

| Report → persona | Decision | Driven by |
|---|---|---|
| EV market → analyst, client note | **ESCALATE** | C1 contradiction whose own source agrees (1.49M); C2 unverified; 5 unchecked figures; 2 vacuous "claims"; garbled price; 1 of 9 figures backed (Tesla 10-K) |
| Nuclear → policy advisor | **ESCALATE** | Framing and sycophancy both CRITICAL; Bias 1/5, Agreement 3/5; C1 unverified |
| Work from home → comms lead | **ESCALATE** | Framing CRITICAL, Bias 1/5; 3 of 5 claims unverified opinions |
| AGI timelines → VC associate | **BLOCK** | Accuracy not scored; no quotable claim backed; C3 says 100% while Ezio's own source says 40–84% |
