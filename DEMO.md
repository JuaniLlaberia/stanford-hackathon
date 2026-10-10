# Demo script (target 100 s, hard max 120 s)

> Record on the Streamlit app. Pre-load the EV report with the analyst persona. Close other tabs.
> Rehearse once with the exact clicks below. Numbers are from the real reports (`uv run python -m agent.policy ev_market`).

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
