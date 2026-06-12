"""Stage ADVISE. For each STRONG or PARTIAL match, Claude generates one
actionable next step the org should take to pursue this grant."""

from __future__ import annotations

from app.clients import chat, cost_usd
from app.config import settings
from app.schemas import GrantScore, MatchVerdict, OrgProfile
from app.telemetry import TelemetryBus, measure

_SYSTEM = """You are a grant writing advisor helping nonprofits and startups apply for federal funding.

Given an organization profile and a grant they matched, write ONE concrete, specific next step they should take to pursue this grant.

Be direct and actionable. Name specific actions (register, prepare, contact, partner with).
Keep the response under 60 words. Plain text — no headers, no bullets."""


async def advise(
    org: OrgProfile,
    grant: GrantScore,
    *,
    bus: TelemetryBus,
) -> str:
    """Return one-sentence next step for this org + grant. Empty string on failure."""
    if grant.match_verdict == MatchVerdict.LOW_MATCH:
        return ""

    user_content = (
        f"Organization: {org.org_name} ({org.org_type})\n"
        f"Mission: {org.mission}\n\n"
        f"Grant: {grant.grant_title}\n"
        f"Agency: {grant.agency}\n"
        f"Funding: {grant.funding_amount}\n"
        f"Deadline: {grant.deadline}\n"
        f"Why it matches: {'; '.join(grant.matching_criteria[:2])}\n\n"
        "Write the one next step."
    )

    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": user_content},
    ]

    async with measure(bus, stage="advise") as m:
        m.model = settings.PREMIUM_MODEL
        try:
            result = await chat(messages, max_tokens=120, temperature=0.3)
            m.tokens_in = result.tokens_in
            m.tokens_out = result.tokens_out
            m.model = result.model
            m.cost_usd = cost_usd(result.model, result.tokens_in, result.tokens_out)
            return result.text.strip()
        except Exception:
            return ""
