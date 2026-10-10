"""Typed model of an Ezio trust report and of the agent's decision (shared contract: change together)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SourceType = Literal["Official", "News", "Reference", "Other"]
Severity = Literal["NOTE", "WARNING", "CRITICAL"]
Outcome = Literal["matches", "not_found", "sources_say", "other"]


class Source(BaseModel):
    n: int  # number as printed in the report ("Source 4")
    type: SourceType = "Other"
    title: str
    date: str | None = None  # YYYY-MM-DD as printed, or None


class Check(BaseModel):
    """One sub-check line under a claim, e.g. '● Amount: 1.49 million — sources say ... (Source 4)'."""
    field: str  # What | Amount | When | Who | Where | ...
    span: str
    outcome: Outcome
    said: str | None = None  # text after "sources say"
    source_n: int | None = None


class Claim(BaseModel):
    id: str = ""  # C1..Cn (Ezio-checked) or U1..Un (found by us, not checked by Ezio)
    text: str
    verdict: str  # verbatim from the report, e.g. "Supported", "Not enough evidence", "Contradicted: amount"
    checks: list[Check] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)


class Finding(BaseModel):
    id: str = ""  # F1..Fn
    kind: str  # "Sentiment & Objectivity" | "Factual Claim Analysis" | "Framing Bias Detection" | ...
    severity: Severity
    text: str
    evidence: list[str] = Field(default_factory=list)


class Report(BaseModel):
    name: str = ""  # file stem, e.g. "ev_market"
    analyzed_text: str
    verdict_summary: str
    issue_count: int | None = None
    modules: dict[str, int | None] = Field(default_factory=dict)  # accuracy/bias/tone/agreement -> 0..5, None = not scored
    claims: list[Claim] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    degraded: list[str] = Field(default_factory=list)  # anything we could not parse or trust


# ---- agent output -------------------------------------------------------------

Status = Literal[
    "strong_support", "weak_support", "vacuous", "unverified",
    "contradiction_questionable", "contradicted", "unchecked", "unknown",
]
Action = Literal["BLOCK", "ESCALATE", "PULL_SOURCE", "REWRITE", "APPROVE"]
SEVERITY_ORDER: list[str] = ["APPROVE", "REWRITE", "PULL_SOURCE", "ESCALATE", "BLOCK"]  # low -> high


class Persona(BaseModel):
    key: str
    who: str  # "Equity research analyst"
    action: str  # "put the figures in a client note"
    sign_off: str  # "Research lead"
    client_facing: bool = True


class ClaimAssessment(BaseModel):
    claim_id: str
    text: str
    status: Status
    material: bool  # carries a figure, date or ranking someone would quote
    why: str  # traces to verdict + specific sources/checks
    next_step: Action  # per-claim action
    source_score: float = 0.0
    best_source: str | None = None  # "Source 1 · Official · tsla-20241231 (2025-01-29)"
    human_resolution: str | None = None  # see Resolution values below
    span: str | None = None  # verbatim text in analyzed_text to highlight, if located


class Reason(BaseModel):
    ref: str  # C3 | F2 | U1 | module:bias | report:garbled
    text: str
    pushes: Action


class HumanQuestion(BaseModel):
    ref: str  # what the answer resolves (C1, U2, signoff)
    question: str
    options: dict[str, str]  # option key -> meaning; SAFE OPTION LAST
    needs_text: list[str] = Field(default_factory=list)  # option keys that need a free-text value (url, value)


class Decision(BaseModel):
    action: Action
    headline: str
    coverage: str  # "1 of 9 figures strongly verified"
    reasons: list[Reason]
    claims: list[ClaimAssessment]  # Ezio-checked claims (C*) followed by unchecked figures (U*)
    not_checked_notes: list[str]
    questions: list[HumanQuestion]
    confidence: float  # shown, never used to decide; capped at 0.95
    signed_off: bool = False


# Resolution values (resolutions: dict[ref, str]) passed back into assess():
#   "remove"            drop the claim from the text
#   "caveat"            keep, explicitly marked unverified
#   "keep"              keep as is (human accepts the cited source)
#   "source:<url>"      human-attested source (not Ezio-verified)
#   "replace:<value>"   human supplies the correct value
#   "approve" / "send_back"   for ref "signoff"
