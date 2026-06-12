from __future__ import annotations

from app.schemas import FindResult, GrantScore, MatchVerdict


def finalize_result(result: FindResult) -> FindResult:
    result.grants.sort(key=lambda g: g.match_score, reverse=True)
    result.total_grants_found = len(result.grants)
    result.strong_matches = sum(
        1 for g in result.grants if g.match_verdict == MatchVerdict.STRONG_MATCH
    )
    result.total_funding_available = _sum_funding(result.grants)
    if result.grants:
        best = result.grants[0]
        result.best_match_title = best.grant_title
        result.best_match_score = best.match_score
    return result


def _sum_funding(grants: list[GrantScore]) -> str:
    """Best-effort parse of dollar amounts from funding_amount strings."""
    total = 0.0
    for g in grants:
        if g.match_verdict == MatchVerdict.LOW_MATCH:
            continue
        amount = g.funding_amount.lower().replace(",", "").replace("$", "").strip()
        multiplier = 1.0
        if "million" in amount or amount.endswith("m"):
            multiplier = 1_000_000
            amount = amount.replace("million", "").replace("m", "").strip()
        elif "billion" in amount or amount.endswith("b"):
            multiplier = 1_000_000_000
            amount = amount.replace("billion", "").replace("b", "").strip()
        elif "k" in amount:
            multiplier = 1_000
            amount = amount.replace("k", "").strip()
        # Take first number found
        import re
        nums = re.findall(r"[\d.]+", amount)
        if nums:
            try:
                total += float(nums[0]) * multiplier
            except ValueError:
                pass

    if total == 0:
        return "varies"
    if total >= 1_000_000_000:
        return f"${total / 1_000_000_000:.1f}B"
    if total >= 1_000_000:
        return f"${total / 1_000_000:.1f}M"
    if total >= 1_000:
        return f"${total / 1_000:.0f}K"
    return f"${total:.0f}"
