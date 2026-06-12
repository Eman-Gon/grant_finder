from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from anthropic import AsyncAnthropic

from app.config import settings


# Sonnet 4.6: $3 / MTok input, $15 / MTok output
COST_TABLE: dict[str, dict[str, float]] = {
    settings.PREMIUM_MODEL: {"input_per_mtok": 3.0, "output_per_mtok": 15.0},
}


def cost_usd(model: str, tokens_in: int, tokens_out: int) -> float:
    rates = COST_TABLE.get(model)
    if rates is None:
        return 0.0
    return (
        tokens_in / 1_000_000.0 * rates["input_per_mtok"]
        + tokens_out / 1_000_000.0 * rates["output_per_mtok"]
    )


@dataclass
class ChatResult:
    text: str
    tokens_in: int
    tokens_out: int
    model: str
    raw: Any = None


_client: Optional[AsyncAnthropic] = None


def anthropic_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _client


async def chat(
    messages: list[dict[str, str]],
    *,
    max_tokens: int = 1024,
    temperature: float = 0.0,
) -> ChatResult:
    model = settings.PREMIUM_MODEL
    system_parts = [m["content"] for m in messages if m["role"] == "system"]
    convo = [m for m in messages if m["role"] != "system"]
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": convo,
        "timeout": settings.LLM_TIMEOUT_S,
    }
    if system_parts:
        kwargs["system"] = "\n\n".join(system_parts)
    resp = await anthropic_client().messages.create(**kwargs)
    text = "".join(
        block.text for block in resp.content if getattr(block, "type", None) == "text"
    )
    return ChatResult(
        text=text,
        tokens_in=resp.usage.input_tokens,
        tokens_out=resp.usage.output_tokens,
        model=model,
        raw=resp,
    )
