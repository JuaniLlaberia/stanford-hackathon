"""Typed model of an Ezio trust report and of the agent's decision."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SourceType = Literal["Official", "News", "Other"]
Severity = Literal["NOTE", "WARNING", "CRITICAL"]


class Source(BaseModel):
    type: SourceType = "Other"
    title: str
    date: str | None = None  # as printed in the report; parsed later


class Claim(BaseModel):
    id: str = ""  # C1..Cn, assigned by code
    text: str
    verdict: str  # verbatim from the report, e.g. "Supported", "Not enough evidence"
    sources: list[Source] = Field(default_factory=list)


class Finding(BaseModel):
    id: str = ""  # F1..Fn, assigned by code
    kind: str  # sentiment | factual claims | framing bias | sycophancy | ...
    severity: Severity
    reasoning: str


class Report(BaseModel):
    analyzed_text: str
    verdict_summary: str
    issue_count: int | None = None
    modules: dict[str, int] = Field(default_factory=dict)  # accuracy/bias/tone/agreement -> filled blocks 0..5
    claims: list[Claim] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    degraded: list[str] = Field(default_factory=list)  # anything we could not parse or verify


# ---- agent output -------------------------------------------------------------

Status = Literal["contradicted", "unverified", "weak_support", "strong_support", "unchecked"]
Action = Literal["BLOCK", "ESCALATE", "PULL_SOURCE", "REWRITE", "APPROVE"]


class ClaimAssessment(BaseModel):
    claim_id: str
    text: str
    status: Status
    material: bool  # carries a figure a client would quote
    why: str  # traces to verdict + sources
    source_score: float = 0.0
    human_resolution: str | None = None  # "source:<url>" | "drop" | "caveat"


class Reason(BaseModel):
    ref: str  # C3 | F2 | module:bias | U1
    text: str
    pushes: Action


class HumanQuestion(BaseModel):
    ref: str
    question: str
    options: dict[str, str]  # option -> meaning; SAFE OPTION LAST


class Decision(BaseModel):
    action: Action
    headline: str
    reasons: list[Reason]
    claims: list[ClaimAssessment]
    unchecked: list[ClaimAssessment]
    not_checked_notes: list[str]
    questions: list[HumanQuestion]
    confidence: float
