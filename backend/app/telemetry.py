from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Optional

import orjson

from app.schemas import TelemetryEvent


LOGS_DIR = Path(__file__).resolve().parent / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)


class TelemetryBus:
    def __init__(self, run_id: Optional[str] = None) -> None:
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self._subscribers: list[asyncio.Queue[TelemetryEvent]] = []
        self._log_path = LOGS_DIR / f"run_{self.run_id}.jsonl"
        self.partial_result: Optional[object] = None
        self.totals: dict[str, Any] = {
            "n_events": 0,
            "n_llm_calls": 0,
            "total_tokens_in": 0,
            "total_tokens_out": 0,
            "total_cost_usd": 0.0,
            "stage_counts": {},
        }

    def subscribe(self) -> asyncio.Queue[TelemetryEvent]:
        q: asyncio.Queue[TelemetryEvent] = asyncio.Queue()
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[TelemetryEvent]) -> None:
        if q in self._subscribers:
            self._subscribers.remove(q)

    def emit(self, event: TelemetryEvent) -> None:
        try:
            with self._log_path.open("ab") as f:
                f.write(orjson.dumps(event.model_dump(mode="json")))
                f.write(b"\n")
        except Exception:
            pass

        t = self.totals
        t["n_events"] += 1
        t["total_tokens_in"] += event.tokens_in
        t["total_tokens_out"] += event.tokens_out
        t["total_cost_usd"] += event.cost_usd
        if event.model:
            t["n_llm_calls"] += 1
        t["stage_counts"][event.stage] = t["stage_counts"].get(event.stage, 0) + 1

        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def emit_async(self, event: TelemetryEvent) -> asyncio.Task[None]:
        async def _do() -> None:
            self.emit(event)
        return asyncio.create_task(_do())


@asynccontextmanager
async def measure(
    bus: TelemetryBus,
    *,
    stage: str,
    model: Optional[str] = None,
    vendor: Optional[str] = None,
    claim_id: Optional[str] = None,
) -> AsyncIterator["MeasureHandle"]:
    handle = MeasureHandle(stage=stage, model=model, vendor=vendor, claim_id=claim_id)
    t0 = time.perf_counter()
    try:
        yield handle
    finally:
        handle.latency_ms = (time.perf_counter() - t0) * 1000.0
        bus.emit_async(handle.to_event())


class MeasureHandle:
    __slots__ = (
        "stage", "model", "vendor", "claim_id",
        "tokens_in", "tokens_out", "latency_ms", "ttft_ms", "cost_usd", "escalated",
    )

    def __init__(self, *, stage: str, model: Optional[str], vendor: Optional[str], claim_id: Optional[str]) -> None:
        self.stage = stage
        self.model = model
        self.vendor = vendor
        self.claim_id = claim_id
        self.tokens_in: int = 0
        self.tokens_out: int = 0
        self.latency_ms: float = 0.0
        self.ttft_ms: Optional[float] = None
        self.cost_usd: float = 0.0
        self.escalated: bool = False

    def to_event(self) -> TelemetryEvent:
        return TelemetryEvent(
            stage=self.stage,
            model=self.model,
            tokens_in=self.tokens_in,
            tokens_out=self.tokens_out,
            latency_ms=self.latency_ms,
            ttft_ms=self.ttft_ms,
            cost_usd=self.cost_usd,
            escalated=self.escalated,
            vendor=self.vendor,
            claim_id=self.claim_id,
        )
