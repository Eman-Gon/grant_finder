"""Stage SCORE. Claude judges how well an org profile matches each grant.
Returns a GrantScore with match_score 0-1, verdict, matching/missing criteria,
and a rationale. One LLM call per grant."""

from __future__ import annotations

import json
import re

from app.clients import chat, cost_usd
from app.config import settings
from app.schemas import GrantOpportunity, GrantScore, MatchVerdict, OrgProfile
from app.telemetry import TelemetryBus, measure

_SYSTEM = """You are a grant eligibility analyst helping nonprofits and startups find the best federal funding matches.

Given an organization profile and a grant opportunity, score how well the org matches the grant.

Return ONLY a valid JSON object. No markdown, no explanation, just the JSON.
Required keys:
- "match_score": float 0.0–1.0 (overall eligibility + mission fit)
- "match_verdict": exactly one of "STRONG_MATCH", "PARTIAL_MATCH", "LOW_MATCH"
- "matching_criteria": list of 2-4 strings explaining why this is a fit (be specific)
- "missing_criteria": list of 0-3 strings explaining gaps or uncertainties
- "rationale": 1-2 sentence plain-English summary

Score thresholds:
- STRONG_MATCH: 0.75–1.0 — org clearly meets eligibility and mission aligns well
- PARTIAL_MATCH: 0.40–0.74 — org meets some criteria, gaps exist
- LOW_MATCH: 0.0–0.39 — significant eligibility gaps

When description or eligibility fields are empty, use your knowledge of the grant program
(agency name, grant title, CFDA number if present) to infer the likely focus and eligibility.
Federal agencies follow consistent patterns — use that knowledge. Be willing to give
STRONG_MATCH or PARTIAL_MATCH scores when the grant title + agency clearly aligns with
the org's mission, even if detailed eligibility text is absent.
"""


def _strip_json(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"```\s*$", "", text)
    return text.strip()


def _build_user(org: OrgProfile, opp: GrantOpportunity) -> str:
    return (
        f"Organization profile:\n"
        f"  Name: {org.org_name}\n"
        f"  Type: {org.org_type}\n"
        f"  Mission: {org.mission}\n"
        f"  Focus areas: {', '.join(org.focus_areas)}\n"
        f"  Location: {org.location}\n"
        f"  Target population: {org.target_population}\n\n"
        f"Grant opportunity:\n"
        f"  Title: {opp.grant_title}\n"
        f"  Agency: {opp.agency}\n"
        f"  Funding: {opp.funding_amount}\n"
        f"  Deadline: {opp.deadline}\n"
        f"  Description: {opp.description[:500]}\n"
        f"  Eligibility: {opp.eligibility}\n"
    )


async def score(
    org: OrgProfile,
    opportunity: GrantOpportunity,
    *,
    bus: TelemetryBus,
) -> GrantScore:
    """Score one grant opportunity against the org profile."""
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": _build_user(org, opportunity)},
    ]

    async with measure(bus, stage="score") as m:
        m.model = settings.PREMIUM_MODEL
        try:
            result = await chat(messages, max_tokens=512, temperature=0.0)
            m.tokens_in = result.tokens_in
            m.tokens_out = result.tokens_out
            m.model = result.model
            m.cost_usd = cost_usd(result.model, result.tokens_in, result.tokens_out)

            data = json.loads(_strip_json(result.text))
            raw_verdict = str(data.get("match_verdict", "LOW_MATCH")).upper().strip()
            if "STRONG" in raw_verdict:
                verdict = MatchVerdict.STRONG_MATCH
            elif "PARTIAL" in raw_verdict:
                verdict = MatchVerdict.PARTIAL_MATCH
            else:
                verdict = MatchVerdict.LOW_MATCH

            match_score = float(data.get("match_score", 0.0))
            match_score = max(0.0, min(1.0, match_score))

            return GrantScore(
                grant_id=opportunity.grant_id,
                grant_title=opportunity.grant_title,
                agency=opportunity.agency,
                funding_amount=opportunity.funding_amount,
                deadline=opportunity.deadline,
                match_score=match_score,
                match_verdict=verdict,
                matching_criteria=[str(x) for x in (data.get("matching_criteria") or [])],
                missing_criteria=[str(x) for x in (data.get("missing_criteria") or [])],
                rationale=str(data.get("rationale", "")),
                opportunity_url=opportunity.opportunity_url,
            )
        except Exception:
            return GrantScore(
                grant_id=opportunity.grant_id,
                grant_title=opportunity.grant_title,
                agency=opportunity.agency,
                funding_amount=opportunity.funding_amount,
                deadline=opportunity.deadline,
                match_score=0.0,
                match_verdict=MatchVerdict.LOW_MATCH,
                matching_criteria=[],
                missing_criteria=["Scoring failed — manual review recommended"],
                rationale="Could not score this opportunity automatically.",
                opportunity_url=opportunity.opportunity_url,
            )
