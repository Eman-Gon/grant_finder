"""Stage SEARCH. Query Grants.gov (primary, no key required) and
simpler.grants.gov (secondary, free API key) for live opportunities.
Returns up to N_GRANTS GrantOpportunity objects."""

from __future__ import annotations

import hashlib

import httpx

from app import cache
from app.config import settings
from app.schemas import GrantOpportunity, OrgProfile
from app.telemetry import TelemetryBus, measure

GRANTS_GOV_SEARCH = "https://api.grants.gov/v1/api/search2"
SIMPLER_GRANTS_SEARCH = "https://api.simpler.grants.gov/v1/opportunities/search"


async def search(
    org_profile: OrgProfile,
    *,
    bus: TelemetryBus,
) -> list[GrantOpportunity]:
    """Search federal grant databases using the org profile keywords.
    Always returns a list — empty list is acceptable (no matches)."""
    async with measure(bus, stage="search") as _m:
        query_key = ":".join(sorted(org_profile.keywords[:6]))
        cache_key = hashlib.sha256(query_key.encode()).hexdigest()[:16]
        cached = cache.get("search", cache_key)
        if cached is not None:
            return [GrantOpportunity(**g) for g in cached]

        results: list[GrantOpportunity] = []

        # Primary: simpler.grants.gov (richer data, free key)
        if settings.SIMPLER_GRANTS_API_KEY:
            try:
                results = await _simpler_grants(org_profile)
            except Exception:
                pass

        # Fallback / supplement: Grants.gov public API
        if len(results) < settings.N_GRANTS:
            try:
                grants_gov = await _grants_gov(org_profile)
                seen_ids = {g.grant_id for g in results}
                for g in grants_gov:
                    if g.grant_id not in seen_ids:
                        results.append(g)
                        seen_ids.add(g.grant_id)
            except Exception:
                pass

        results = results[: settings.N_GRANTS]
        if results:
            cache.set("search", cache_key, [g.model_dump() for g in results])
        return results


async def _simpler_grants(org: OrgProfile) -> list[GrantOpportunity]:
    query = " ".join(org.keywords[:8])
    body = {
        "query": query,
        "filters": {"opportunity_status": {"one_of": ["posted"]}},
        "pagination": {"page_offset": 0, "page_size": settings.N_GRANTS, "sort_by": "relevancy"},
    }
    headers = {
        "Authorization": f"Bearer {settings.SIMPLER_GRANTS_API_KEY}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=15.0) as http:
        resp = await http.post(SIMPLER_GRANTS_SEARCH, json=body, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    results: list[GrantOpportunity] = []
    for item in (data.get("data") or []):
        opp_id = str(item.get("opportunity_id", ""))
        summary = item.get("summary") or {}
        results.append(GrantOpportunity(
            grant_id=f"sg_{opp_id}",
            grant_title=str(item.get("opportunity_title", "Untitled")),
            agency=str(item.get("agency_name") or summary.get("agency_name", "")),
            funding_amount=_fmt_amount(
                summary.get("award_floor"),
                summary.get("award_ceiling"),
            ),
            deadline=str(summary.get("close_date") or "See listing"),
            description=str(item.get("opportunity_assistance_listings", [{}])[0].get("program_title", "")
                            if item.get("opportunity_assistance_listings") else ""),
            eligibility=_fmt_eligibility(summary.get("applicant_types") or []),
            opportunity_url=f"https://simpler.grants.gov/opportunity/{opp_id}",
        ))
    return results


# Grants.gov category codes (from API docs). Used when a focus area matches.
_CATEGORY_MAP: dict[str, str] = {
    "education": "ED",
    "health": "HL",
    "environment": "ENV",
    "science": "ST",
    "technology": "ST",
    "arts": "AC",
    "agriculture": "AG",
    "housing": "HO",
    "community": "CD",
    "workforce": "ELT",
    "food": "FN",
    "energy": "NR",
    "transportation": "TR",
}

_BROAD_QUERY_MAP: dict[str, tuple[str, ...]] = {
    "advocacy": ("community advocacy", "civil rights", "public services"),
    "community": ("community services", "community development", "public benefit"),
    "housing": ("homelessness housing", "affordable housing", "supportive housing"),
    "poverty": ("poverty relief", "low income assistance", "economic mobility"),
    "relief": ("emergency assistance", "basic needs", "social services"),
    "social": ("social services", "human services", "community support"),
    "spiritual": ("faith based community services", "community services"),
    "workforce": ("workforce development", "job training", "employment services"),
}


def _detect_category(org: OrgProfile) -> str | None:
    text = " ".join(org.focus_areas + org.keywords).lower()
    for keyword, code in _CATEGORY_MAP.items():
        if keyword in text:
            return code
    return None


async def _grants_gov(org: OrgProfile) -> list[GrantOpportunity]:
    """Run 2-3 targeted searches against Grants.gov public API.
    Notes on API limitations: `eligibility` filter returns 0 results with org-type
    codes; `sortBy` only accepts 'openDate|desc' or 'closeDate|asc'. No synopsis
    or funding amounts available without auth."""
    import asyncio as _asyncio

    category = _detect_category(org)
    all_results: list[GrantOpportunity] = []
    seen_ids: set[str] = set()

    async def _one_search(keyword: str, category_code: str | None) -> list[GrantOpportunity]:
        body: dict = {
            "keyword": keyword,
            "oppStatuses": "posted",
            "rows": 10,
            "sortBy": "openDate|desc",
        }
        if category_code:
            body["category"] = category_code
        try:
            async with httpx.AsyncClient(timeout=12.0) as http:
                resp = await http.post(GRANTS_GOV_SEARCH, json=body)
                resp.raise_for_status()
                data = resp.json()
            items = []
            for item in (data.get("data", {}).get("oppHits") or []):
                opp_id = str(item.get("id", ""))
                if opp_id in seen_ids:
                    continue
                seen_ids.add(opp_id)
                cfda = ", ".join(item.get("cfdaList") or [])
                items.append(GrantOpportunity(
                    grant_id=f"gg_{opp_id}",
                    grant_title=str(item.get("title", "Untitled")),
                    agency=str(item.get("agency", "")),
                    funding_amount="See listing",
                    deadline=str(item.get("closeDate") or "See listing"),
                    description=_cfda_description(cfda),
                    eligibility=_cfda_eligibility(cfda),
                    opportunity_url=f"https://www.grants.gov/search-results-detail/{opp_id}",
                ))
            return items
        except Exception:
            return []

    def _append_query(
        queries: list[tuple[str, str | None]],
        seen: set[tuple[str, str | None]],
        keyword: str,
        category_code: str | None,
    ) -> None:
        keyword = " ".join(keyword.split()).strip()
        if not keyword:
            return
        key = (keyword.lower(), category_code)
        if key in seen:
            return
        seen.add(key)
        queries.append((keyword, category_code))

    # Start targeted, then widen. Grants.gov search can return nothing for
    # narrow nonprofit language like "spiritual community" even when adjacent
    # human-services opportunities exist.
    primary_kw = " ".join(org.keywords[:3])
    secondary_kw = " ".join(org.keywords[3:6]) if len(org.keywords) > 3 else " ".join(org.focus_areas[:2])
    catchall_kw = org.org_name or " ".join(org.keywords[:4])

    queries: list[tuple[str, str | None]] = []
    seen_queries: set[tuple[str, str | None]] = set()
    _append_query(queries, seen_queries, primary_kw, category)
    _append_query(queries, seen_queries, secondary_kw, None)
    _append_query(queries, seen_queries, catchall_kw, category)
    for focus in org.focus_areas[:6]:
        _append_query(queries, seen_queries, focus, None)
        lower = focus.lower()
        for trigger, expansions in _BROAD_QUERY_MAP.items():
            if trigger in lower:
                for expansion in expansions:
                    _append_query(queries, seen_queries, expansion, None)
                break
    for keyword in org.keywords[:8]:
        lower = keyword.lower()
        for trigger, expansions in _BROAD_QUERY_MAP.items():
            if trigger in lower:
                for expansion in expansions:
                    _append_query(queries, seen_queries, expansion, None)
                break
    if category:
        _append_query(queries, seen_queries, "nonprofit", category)

    batches = await _asyncio.gather(*[_one_search(q, c) for q, c in queries[:12]])
    for batch in batches:
        for item in batch:
            if item.grant_id not in {r.grant_id for r in all_results}:
                all_results.append(item)
            if len(all_results) >= settings.N_GRANTS:
                return all_results[:settings.N_GRANTS]

    return all_results[:settings.N_GRANTS]


def _cfda_description(cfda: str) -> str:
    """Best-effort description from the lead CFDA prefix so the scorer has
    agency context even when the public search API returns no synopsis."""
    if not cfda:
        return ""
    first = cfda.split(",")[0].strip()
    prefix = first.split(".")[0]
    labels = {
        "84": "U.S. Department of Education program — eligible recipients include nonprofits, schools, LEAs, and IHEs.",
        "45": "National Endowment for the Arts or Humanities — supports arts/humanities nonprofits and research.",
        "93": "U.S. Department of Health and Human Services — health, social services, and community programs.",
        "17": "U.S. Department of Labor — workforce training, employment services, and job placement.",
        "14": "U.S. Department of Housing and Urban Development — housing, community development, homelessness.",
        "10": "U.S. Department of Agriculture — rural development, food, nutrition, and agricultural programs.",
        "20": "U.S. Department of Transportation — transit, infrastructure, and mobility programs.",
        "66": "U.S. Environmental Protection Agency — environmental protection and conservation.",
        "15": "U.S. Department of the Interior — conservation, tribal, and natural resources.",
        "16": "U.S. Department of Justice — public safety, justice, and victim services.",
        "19": "U.S. Department of State — international exchange, diplomacy, and cultural programs.",
        "12": "U.S. Department of Defense — defense research, STEM at HBCUs, and military programs.",
        "47": "National Science Foundation — STEM research and education.",
        "94": "AmeriCorps — national service and volunteering programs.",
        "21": "U.S. Department of the Treasury — financial and economic programs.",
    }
    label = labels.get(prefix, "")
    return f"CFDA: {cfda}. {label}".strip(" .")


def _cfda_eligibility(cfda: str) -> str:
    """Infer common eligibility from CFDA prefix."""
    if not cfda:
        return ""
    prefix = cfda.split(".")[0].strip()
    eligibility_map = {
        "84": "Nonprofits, schools, local educational agencies, institutions of higher education.",
        "45": "Nonprofits, arts organizations, educational institutions.",
        "93": "Nonprofits, state agencies, health organizations, community groups.",
        "17": "Nonprofits, workforce boards, community colleges, employers.",
        "14": "Nonprofits, local governments, housing agencies.",
        "47": "Universities, research institutions, nonprofits.",
        "94": "Nonprofits, schools, government agencies.",
        "16": "Nonprofits, law enforcement, victim service providers.",
        "66": "Nonprofits, local governments, research institutions.",
    }
    return eligibility_map.get(prefix, "")


def _fmt_amount(floor, ceiling) -> str:
    if ceiling and floor and floor != ceiling:
        return f"${_abbrev(floor)} – ${_abbrev(ceiling)}"
    if ceiling:
        return f"Up to ${_abbrev(ceiling)}"
    if floor:
        return f"From ${_abbrev(floor)}"
    return "See listing"


def _abbrev(n) -> str:
    try:
        v = int(n)
    except (TypeError, ValueError):
        return str(n)
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"{v // 1_000}K"
    return str(v)


def _fmt_eligibility(types: list) -> str:
    if not types:
        return ""
    return ", ".join(str(t) for t in types[:4])
