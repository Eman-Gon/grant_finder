from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class MatchVerdict(str, Enum):
    STRONG_MATCH = "STRONG_MATCH"
    PARTIAL_MATCH = "PARTIAL_MATCH"
    LOW_MATCH = "LOW_MATCH"


class OrgProfile(BaseModel):
    org_name: str = ""
    org_type: str = "nonprofit"
    mission: str = ""
    focus_areas: list[str] = Field(default_factory=list)
    location: str = ""
    target_population: str = ""
    keywords: list[str] = Field(default_factory=list)


class GrantOpportunity(BaseModel):
    grant_id: str
    grant_title: str
    agency: str
    funding_amount: str
    deadline: str
    description: str = ""
    eligibility: str = ""
    opportunity_url: Optional[str] = None


class GrantScore(BaseModel):
    grant_id: str
    grant_title: str
    agency: str
    funding_amount: str
    deadline: str
    match_score: float = Field(ge=0.0, le=1.0)
    match_verdict: MatchVerdict
    matching_criteria: list[str] = Field(default_factory=list)
    missing_criteria: list[str] = Field(default_factory=list)
    rationale: str
    advice: Optional[str] = None
    opportunity_url: Optional[str] = None


class FindResult(BaseModel):
    run_id: str = ""
    org_url: str
    profile: Optional[OrgProfile] = None
    grants: list[GrantScore] = Field(default_factory=list)
    total_grants_found: int = 0
    strong_matches: int = 0
    total_funding_available: str = ""
    best_match_title: Optional[str] = None
    best_match_score: Optional[float] = None
    status: str = "running"
    telemetry_summary: dict[str, Any] = Field(default_factory=dict)


class TelemetryEvent(BaseModel):
    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    stage: str
    model: Optional[str] = None
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: float = 0.0
    ttft_ms: Optional[float] = None
    cost_usd: float = 0.0
    escalated: bool = False
    vendor: Optional[str] = None
    claim_id: Optional[str] = None
    payload: Optional[dict[str, Any]] = None
