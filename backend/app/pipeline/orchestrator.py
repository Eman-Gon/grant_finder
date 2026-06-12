"""Full pipeline: URL -> INGEST -> PROFILE -> SEARCH -> SCORE -> ADVISE -> FindResult.

All scoring runs concurrently under a semaphore. Advice is generated in parallel
for STRONG and PARTIAL matches. Failures are grey-carded — never raised."""

from __future__ import annotations

import asyncio

from app.config import settings
from app.pipeline.advise import advise
from app.pipeline.ingest import ingest
from app.pipeline.profile import profile
from app.pipeline.score import score
from app.pipeline.search import search
from app.schemas import FindResult, GrantScore, TelemetryEvent
from app.scoring import finalize_result
from app.telemetry import TelemetryBus


async def run_find(org_url: str, *, bus: TelemetryBus) -> FindResult:
    result = FindResult(run_id=bus.run_id, org_url=org_url)
    bus.partial_result = result

    # INGEST
    try:
        markdown = await asyncio.wait_for(
            ingest(org_url, bus=bus),
            timeout=settings.SCRAPE_TIMEOUT_S * 2,
        )
    except Exception:
        markdown = ""

    if not markdown.strip():
        result.status = "error"
        bus.partial_result = result
        bus.emit(TelemetryEvent(stage="find_done"))
        return result

    # PROFILE
    try:
        org_profile = await asyncio.wait_for(
            profile(markdown, bus=bus),
            timeout=settings.LLM_TIMEOUT_S,
        )
    except Exception:
        from app.schemas import OrgProfile
        org_profile = OrgProfile()

    result.profile = org_profile
    bus.partial_result = result
    bus.emit(TelemetryEvent(
        stage="profile_done",
        payload={"org_name": org_profile.org_name, "org_type": org_profile.org_type},
    ))

    # SEARCH
    try:
        opportunities = await asyncio.wait_for(
            search(org_profile, bus=bus),
            timeout=20.0,
        )
    except Exception:
        opportunities = []

    if not opportunities:
        result.status = "done"
        finalize_result(result)
        bus.partial_result = result
        bus.emit(TelemetryEvent(stage="find_done"))
        return result

    bus.emit(TelemetryEvent(
        stage="search_done",
        payload={"n_opportunities": len(opportunities)},
    ))

    # SCORE — all grants concurrently under semaphore
    sem = asyncio.Semaphore(settings.SEMAPHORE)

    async def _score_one(opp) -> GrantScore:
        async with sem:
            try:
                gs = await asyncio.wait_for(
                    score(org_profile, opp, bus=bus),
                    timeout=settings.LLM_TIMEOUT_S,
                )
                result.grants.append(gs)
                finalize_result(result)
                bus.partial_result = result
                return gs
            except Exception as e:
                from app.schemas import MatchVerdict
                return GrantScore(
                    grant_id=opp.grant_id,
                    grant_title=opp.grant_title,
                    agency=opp.agency,
                    funding_amount=opp.funding_amount,
                    deadline=opp.deadline,
                    match_score=0.0,
                    match_verdict=MatchVerdict.LOW_MATCH,
                    rationale=f"Scoring error: {type(e).__name__}",
                    opportunity_url=opp.opportunity_url,
                )

    scored = await asyncio.gather(*[_score_one(opp) for opp in opportunities])

    # ADVISE — parallel for strong/partial matches only
    async def _advise_one(gs: GrantScore) -> None:
        async with sem:
            try:
                advice_text = await asyncio.wait_for(
                    advise(org_profile, gs, bus=bus),
                    timeout=settings.LLM_TIMEOUT_S,
                )
                gs.advice = advice_text
            except Exception:
                pass

    from app.schemas import MatchVerdict
    advise_targets = [gs for gs in scored if gs.match_verdict != MatchVerdict.LOW_MATCH]
    if advise_targets:
        await asyncio.gather(*[_advise_one(gs) for gs in advise_targets])

    finalize_result(result)
    result.status = "done"
    result.telemetry_summary = {
        "run_id": bus.run_id,
        "n_opportunities_searched": len(opportunities),
        "n_scored": len(scored),
        "strong_matches": result.strong_matches,
        "bus_totals": {**bus.totals, "stage_counts": dict(bus.totals["stage_counts"])},
    }
    bus.partial_result = result
    bus.emit(TelemetryEvent(stage="find_done"))
    return result
