# Demo script (target 100 s, hard max 120 s)

> Record on the Streamlit app (`uv run streamlit run app.py`, LLM on). Before recording: sidebar **Reset this case**
> for EV and AGI, browser zoom ~80% so the left text and the right decision panel fit on one screen.
> Numbers are from the real reports (`uv run python -m agent.policy ev_market`).

## Exact clicks (UI layout)
1. **Top header:** "Ezio: 2 of 3 hold up · Agent: 1 of 9 figures backed". **Left:** highlighted text (✅ ❓ ⚠️ ➖ ⬜ labels per claim). **Right:** 🙋 ESCALATE badge → *Reasons* → *What this report could not check* (expanded).
2. Right column, **🙋 Needs a human: Research lead** radios: C1 → *Keep the figure and cite…* · C2 → *Keep it, explicitly marked as unverified* · U1–U5 → *Remove it*. Click **Submit answers**.
3. The badge turns ✏️ **REWRITE**; the timeline shows ESCALATE → REWRITE.
4. Scroll to **Final sign-off · Research lead**: *Text to be used* (the LLM rewrite, figures checked by code). Type a name → **✅ Sign off**.
5. The timeline shows → ✅ APPROVE; a green "Done" box shows the client note written to `runs/…_final.md`. Optionally open the **Audit log** expander.
6. Sidebar → report **agi** (persona switches to VC associate): ⛔ **BLOCK**, the C3 reason (100% vs 40–84%), "Accuracy was NOT scored" in the notes.
7. (If time) **Agent actions → 🔎 Pull source** on EV before step 2, to show leads labelled "lead, not evidence" with the verify verdict.

| t | Screen | Voice-over |
|---|---|---|
| 0–12 s | EV report loaded: analysed text + Ezio verdict | "An equity analyst wants to put this AI-written EV market snapshot into a client note. Ezio checked it and says **2 of 3 checkable claims hold up**. Is that enough to send to a client? Our agent reads the whole report, not just that line." |
| 12–40 s | Claim table + highlighted text | "Tesla's $97.7 billion is backed by Tesla's own 10-K, an **official** source: ✅. But the agent questions the rest. Of Ezio's two 'supported' claims, one is just the **title**. Ezio marks Geely's 1.49 million as **contradicted**, yet its own evidence says *'sources say 1.49 million'*, the same number, from a source typed 'Other': ❓ questionable, not false. 17.1 million global sales: **not enough evidence**, so it's open, not wrong. And five figures, BYD, Tesla deliveries, battery price, China's 70%, recycling 90%, were **never checked at all**. The battery-price sentence is even garbled." |
| 40–50 s | Headline + decision ESCALATE + "What the report couldn't check" | "So the headline isn't 2 of 3. It's **1 of 9 figures backed by a strong source**. Decision: **ESCALATE**. Every reason links to a claim, a finding or a source." |
| 50–80 s | Questions → answers → timeline | "The analyst is accountable, so the agent asks them specific questions. Geely: keep it and cite the matching source. Global sales: keep with an explicit 'unverified' caveat. The unchecked figures: remove them. The agent re-decides: **ESCALATE → REWRITE**. It drafts the corrected note and asks the research lead to sign off. Approved: **APPROVE**, and only now is the client note written. Everything goes to the audit log." |
| 80–100 s | Switch report: AGI → VC persona (then WFH, if time) | "Same agent, a different report. AGI timelines for an investment memo: accuracy **not scored**, nothing backed, and one claim says 100% resolution while Ezio's own source says 40–84%. That's evidence **against** it, not just missing evidence. Decision: **BLOCK**, with the partner able to override by rebuilding from sources. The model finds and drafts; **the code decides**." |

## Backup lines (if a judge asks)
- *Why not trust "Supported"?* Source quality: Official 1.0 > News 0.7 > Reference 0.5 > Other 0.3; undated ×0.6; a source published **before** the period it's cited for counts 0 (EV cites a 2010 page for 2024 sales).
- *Unverified vs contradicted?* Four separate outcomes. Unverified → pull another source or ask a human; contradicted → block or replace; a contradiction whose own evidence agrees with the claim → escalate.
- *What does the LLM do?* It drafts rewrites, source leads and prose. The action is plain code, so prompt injection in the analysed text can't approve anything.
- *A pulled source?* It counts only if the page really contains the excerpt **and** the excerpt states the same figure (±0.5 pp / ±0.5%). Otherwise its weight is 0.
- *Punto Medio:* "We build claim verification for news. We once withdrew a feature because repeated runs showed 64–87% precision. Measurement over demos."
