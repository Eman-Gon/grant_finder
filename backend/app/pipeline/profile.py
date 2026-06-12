"""Stage PROFILE. Claude extracts a structured OrgProfile from the ingested
website text. The profile drives all downstream grant searching and scoring."""

from __future__ import annotations

import json
import re

from app.clients import chat, cost_usd
from app.config import settings
from app.schemas import OrgProfile
from app.telemetry import TelemetryBus, measure

_SYSTEM = """You are an expert at reading nonprofit and startup websites and extracting a structured profile.

Given the text from an organization's website, extract a structured profile.

Return ONLY a valid JSON object. No markdown, no explanation, just the JSON.
Required keys:
- "org_name": the organization's name (string)
- "org_type": one of "nonprofit", "startup", "research", "government"
- "mission": one concise sentence describing what the org does and for whom
- "focus_areas": list of 3-7 topic areas (e.g. ["education", "youth development", "arts"])
- "location": city, state, or region (e.g. "Oakland, CA" or "national")
- "target_population": who the org serves (e.g. "low-income youth in urban areas")
- "keywords": list of 8-12 keywords for grant database search queries

Example output:
{
  "org_name": "Oakland Youth Arts Collective",
  "org_type": "nonprofit",
  "mission": "Provides free arts education to underserved youth in the Oakland Bay Area.",
  "focus_areas": ["arts education", "youth development", "underserved communities", "Bay Area"],
  "location": "Oakland, CA",
  "target_population": "low-income youth ages 8-18 in Oakland",
  "keywords": ["arts education", "youth arts", "after-school", "underserved youth", "Oakland", "creative arts", "K-12", "community arts"]
}
"""


def _strip_json(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"```\s*$", "", text)
    return text.strip()


async def profile(
    markdown: str,
    *,
    bus: TelemetryBus,
) -> OrgProfile:
    """Extract OrgProfile from website markdown. Returns empty profile on failure."""
    if not markdown.strip():
        return OrgProfile()

    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": f"Organization website text:\n\n{markdown[:12_000]}"},
    ]

    async with measure(bus, stage="profile") as m:
        m.model = settings.PREMIUM_MODEL
        try:
            result = await chat(messages, max_tokens=1024, temperature=0.0)
            m.tokens_in = result.tokens_in
            m.tokens_out = result.tokens_out
            m.model = result.model
            m.cost_usd = cost_usd(result.model, result.tokens_in, result.tokens_out)

            data = json.loads(_strip_json(result.text))
            return OrgProfile(
                org_name=str(data.get("org_name", "")),
                org_type=str(data.get("org_type", "nonprofit")),
                mission=str(data.get("mission", "")),
                focus_areas=[str(x) for x in (data.get("focus_areas") or [])],
                location=str(data.get("location", "")),
                target_population=str(data.get("target_population", "")),
                keywords=[str(x) for x in (data.get("keywords") or [])],
            )
        except Exception:
            return OrgProfile()
