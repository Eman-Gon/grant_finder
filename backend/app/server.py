"""FastAPI surface. POST /find starts a run and returns its run_id.
GET /find/{run_id}/stream delivers live SSE telemetry.
GET /find/{run_id}/results returns partial or final FindResult."""

from __future__ import annotations

import asyncio
from typing import Any, Optional

import orjson
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sse_starlette import EventSourceResponse

from app.telemetry import TelemetryBus


app = FastAPI(title="Grant Finder")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_RUNS: dict[str, TelemetryBus] = {}
_TASKS: dict[str, asyncio.Task] = {}


class FindRequest(BaseModel):
    org_url: str


class FindAccepted(BaseModel):
    run_id: str
    stream_url: str
    results_url: str


@app.post("/find", response_model=FindAccepted)
async def find(req: FindRequest) -> FindAccepted:
    bus = TelemetryBus()
    _RUNS[bus.run_id] = bus

    from app.pipeline.orchestrator import run_find

    task = asyncio.create_task(run_find(req.org_url, bus=bus))
    _TASKS[bus.run_id] = task

    return FindAccepted(
        run_id=bus.run_id,
        stream_url=f"/find/{bus.run_id}/stream",
        results_url=f"/find/{bus.run_id}/results",
    )


@app.get("/find/{run_id}/stream")
async def stream(run_id: str) -> EventSourceResponse:
    bus = _RUNS.get(run_id)
    if bus is None:
        raise HTTPException(status_code=404, detail="run not found")

    queue = bus.subscribe()

    async def gen():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": ""}
                    continue
                yield {
                    "event": "telemetry",
                    "data": orjson.dumps(event.model_dump(mode="json")).decode("utf-8"),
                }
                if event.stage == "find_done":
                    break
        finally:
            bus.unsubscribe(queue)

    return EventSourceResponse(gen())


@app.get("/find/{run_id}/results")
async def results(run_id: str) -> Any:
    bus = _RUNS.get(run_id)
    if bus is None:
        raise HTTPException(status_code=404, detail="run not found")

    task = _TASKS.get(run_id)
    partial = bus.partial_result

    if task and task.done() and not task.exception():
        final = task.result()
        return orjson.loads(orjson.dumps(final.model_dump(mode="json")))

    if partial is not None:
        return orjson.loads(orjson.dumps(partial.model_dump(mode="json")))

    return {"org_url": "", "grants": [], "status": "running"}


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
