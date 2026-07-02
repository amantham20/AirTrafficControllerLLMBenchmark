"""FastAPI + WebSocket server: live LLM/scripted sessions, manual event
injection, and replay of stored runs.

    uvicorn atc.server:app --reload --port 8000

The simulation runs in a background thread (LLM decisions block the sim
clock, which is faithful to how decision latency is scored); the asyncio
side drains a thread-safe queue and broadcasts to WebSocket clients.
"""

from __future__ import annotations

import asyncio
import json
import queue
import threading
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .airport import Airport
from .controllers import NullController
from .engine import SimEngine
from .run_scripted import load_script
from .scenario import SCENARIO_DIR, ScenarioEvent, list_scenarios, \
    load_scenario
from .scoring import compute_metrics
from .storage import RunStore

app = FastAPI(title="ATC Benchmark")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
    allow_headers=["*"])

store = RunStore()

METRICS_INTERVAL_S = 2
SNAPSHOT_STORE_INTERVAL_S = 2


# ---------------------------------------------------------------------------
# Live session management
# ---------------------------------------------------------------------------


class SessionRunner:
    """Owns one running simulation (live or replay) on a worker thread."""

    def __init__(self):
        self.outbox: "queue.Queue[dict]" = queue.Queue()
        self.thread: Optional[threading.Thread] = None
        self.engine: Optional[SimEngine] = None
        self.stop_flag = threading.Event()
        self.pause_flag = threading.Event()
        self.speed = 8.0
        self.mode = "idle"  # idle | live | replay
        self.scenario_id: Optional[str] = None
        self.label: Optional[str] = None
        self.model: Optional[str] = None
        self.inject_queue: "queue.Queue[ScenarioEvent]" = queue.Queue()
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def status(self) -> dict:
        return {
            "mode": self.mode if self.running else "idle",
            "scenario": self.scenario_id,
            "model": self.model,
            "paused": self.pause_flag.is_set(),
            "speed": self.speed,
            "t_s": self.engine.t if self.engine else 0,
        }

    def push(self, type_: str, data: Any) -> None:
        self.outbox.put({"type": type_, "data": data})

    # -- live ---------------------------------------------------------------

    def start_live(self, scenario_id: str, controller_kind: str,
                   model: Optional[str], seed: Optional[int]) -> None:
        if self.running:
            raise RuntimeError("a session is already running")
        scenario = load_scenario(scenario_id)
        if seed is not None:
            scenario.seed = seed
        airport = Airport.load(scenario.airport)
        if controller_kind == "llm":
            from .llm.adapter import LLMController
            controller = LLMController(model=model or "claude-sonnet-4-6")
        elif controller_kind == "scripted":
            controller = load_script(
                SCENARIO_DIR / f"{scenario_id}_script.json")
        else:
            controller = NullController()
        self.engine = SimEngine(airport, scenario, controller=controller)
        self.mode = "live"
        self.scenario_id = scenario_id
        self.model = model if controller_kind == "llm" else None
        self.label = self.model or controller_kind
        self.stop_flag.clear()
        self.pause_flag.clear()
        self.thread = threading.Thread(
            target=self._live_loop, args=(controller,), daemon=True)
        self.thread.start()

    def _live_loop(self, controller) -> None:
        engine = self.engine
        assert engine is not None
        snapshots: list[dict] = []
        last_event_seq = 0
        self.push("status", self.status())
        self.push("snapshot", engine.ui_snapshot())
        try:
            while not self.stop_flag.is_set() and \
                    engine.t < engine.scenario.duration_s:
                if self.pause_flag.is_set():
                    time.sleep(0.05)
                    continue
                started = time.perf_counter()
                while not self.inject_queue.empty():
                    ev = self.inject_queue.get_nowait()
                    ev.at_s = engine.t + 1
                    engine._pending_events.append(ev)
                    engine._pending_events.sort(key=lambda e: e.at_s)
                engine.step()

                self.push("snapshot", engine.ui_snapshot())
                new_events = [e for e in engine.events
                              if e.seq > last_event_seq]
                if new_events:
                    last_event_seq = new_events[-1].seq
                    self.push("events", [
                        e.model_dump(exclude_none=True) for e in new_events])
                if engine.t % METRICS_INTERVAL_S == 0:
                    self.push("metrics", compute_metrics(engine))
                if engine.t % SNAPSHOT_STORE_INTERVAL_S == 0:
                    snapshots.append(engine.ui_snapshot())
                if engine.all_complete:
                    break
                elapsed = time.perf_counter() - started
                time.sleep(max(0.0, 1.0 / self.speed - elapsed))
        finally:
            self.push("metrics", compute_metrics(engine))
            run_id = store.save_run(
                engine, getattr(controller, "name", "unknown"),
                model=self.model, label=self.label,
                llm_calls=getattr(controller, "call_log", []),
                snapshots=snapshots)
            self.mode = "idle"
            self.push("status", self.status())
            self.push("run_saved", {"run_id": run_id})

    # -- replay --------------------------------------------------------------

    def start_replay(self, run_id: str) -> None:
        if self.running:
            raise RuntimeError("a session is already running")
        detail = store.get_run(run_id)
        if detail is None:
            raise KeyError(run_id)
        snapshots = store.get_snapshots(run_id)
        events = store.get_events(run_id)
        if not snapshots:
            raise ValueError("run has no snapshots to replay")
        self.mode = "replay"
        self.scenario_id = detail["scenario"]
        self.model = detail.get("model")
        self.stop_flag.clear()
        self.pause_flag.clear()
        self.thread = threading.Thread(
            target=self._replay_loop, args=(detail, snapshots, events),
            daemon=True)
        self.thread.start()

    def _replay_loop(self, detail: dict, snapshots: list[dict],
                     events: list[dict]) -> None:
        self.push("status", self.status())
        self.push("metrics", detail["metrics"])
        ev_idx = 0
        try:
            for snap in snapshots:
                if self.stop_flag.is_set():
                    break
                while self.pause_flag.is_set() and \
                        not self.stop_flag.is_set():
                    time.sleep(0.05)
                started = time.perf_counter()
                self.push("snapshot", snap)
                batch = []
                while ev_idx < len(events) and \
                        events[ev_idx]["t_s"] <= snap["t_s"]:
                    batch.append(events[ev_idx])
                    ev_idx += 1
                if batch:
                    self.push("events", batch)
                elapsed = time.perf_counter() - started
                time.sleep(max(
                    0.0,
                    SNAPSHOT_STORE_INTERVAL_S / self.speed - elapsed))
        finally:
            self.mode = "idle"
            self.push("status", self.status())

    def stop(self) -> None:
        self.stop_flag.set()
        self.pause_flag.clear()


runner = SessionRunner()


# ---------------------------------------------------------------------------
# WebSocket hub
# ---------------------------------------------------------------------------


class Hub:
    def __init__(self):
        self.clients: set[WebSocket] = set()
        self.lock = asyncio.Lock()

    async def register(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self.lock:
            self.clients.add(ws)

    async def unregister(self, ws: WebSocket) -> None:
        async with self.lock:
            self.clients.discard(ws)

    async def broadcast(self, message: dict) -> None:
        text = json.dumps(message)
        async with self.lock:
            dead = []
            for ws in self.clients:
                try:
                    await ws.send_text(text)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                self.clients.discard(ws)


hub = Hub()


@app.on_event("startup")
async def start_pump() -> None:
    async def pump():
        loop = asyncio.get_event_loop()
        while True:
            try:
                msg = await loop.run_in_executor(
                    None, runner.outbox.get, True, 0.25)
            except Exception:
                continue
            await hub.broadcast(msg)
    asyncio.create_task(pump())


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await hub.register(ws)
    try:
        # Greet the new client with current state.
        await ws.send_text(json.dumps(
            {"type": "status", "data": runner.status()}))
        if runner.engine is not None:
            await ws.send_text(json.dumps(
                {"type": "snapshot", "data": runner.engine.ui_snapshot()}))
        while True:
            await ws.receive_text()  # client messages are ignored (REST API)
    except WebSocketDisconnect:
        pass
    finally:
        await hub.unregister(ws)


# ---------------------------------------------------------------------------
# REST API
# ---------------------------------------------------------------------------


@app.get("/api/airport/{airport_id}")
def get_airport(airport_id: str) -> dict:
    try:
        return Airport.load(airport_id).to_public_dict()
    except FileNotFoundError:
        raise HTTPException(404, f"unknown airport {airport_id}")


@app.get("/api/scenarios")
def get_scenarios() -> list[dict]:
    return list_scenarios()


@app.get("/api/runs")
def get_runs(scenario: Optional[str] = None) -> list[dict]:
    return store.list_runs(scenario)


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict:
    detail = store.get_run(run_id)
    if detail is None:
        raise HTTPException(404, "unknown run")
    return detail


@app.get("/api/runs/{run_id}/events")
def get_run_events(run_id: str) -> list[dict]:
    return store.get_events(run_id)


class StartRequest(BaseModel):
    scenario: str = "baseline"
    controller: str = "llm"  # llm | scripted | null
    model: Optional[str] = None
    seed: Optional[int] = None
    speed: float = 8.0


@app.post("/api/session/start")
def start_session(req: StartRequest) -> dict:
    if runner.running:
        raise HTTPException(409, "a session is already running")
    runner.speed = max(0.25, min(60.0, req.speed))
    try:
        runner.start_live(req.scenario, req.controller, req.model, req.seed)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))
    return runner.status()


class ReplayRequest(BaseModel):
    run_id: str
    speed: float = 8.0


@app.post("/api/session/replay")
def start_replay(req: ReplayRequest) -> dict:
    if runner.running:
        raise HTTPException(409, "a session is already running")
    runner.speed = max(0.25, min(60.0, req.speed))
    try:
        runner.start_replay(req.run_id)
    except KeyError:
        raise HTTPException(404, "unknown run")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return runner.status()


class ControlRequest(BaseModel):
    action: str  # pause | play | speed | stop
    speed: Optional[float] = None


@app.post("/api/session/control")
def control_session(req: ControlRequest) -> dict:
    if req.action == "pause":
        runner.pause_flag.set()
    elif req.action == "play":
        runner.pause_flag.clear()
    elif req.action == "speed" and req.speed:
        runner.speed = max(0.25, min(60.0, req.speed))
    elif req.action == "stop":
        runner.stop()
    else:
        raise HTTPException(400, f"unknown action {req.action}")
    return runner.status()


class InjectRequest(BaseModel):
    kind: str
    params: dict = {}
    announce: str = ""


@app.post("/api/session/inject")
def inject_event(req: InjectRequest) -> dict:
    if not runner.running or runner.mode != "live":
        raise HTTPException(409, "no live session")
    try:
        ev = ScenarioEvent(at_s=0, kind=req.kind, params=req.params,
                           announce=req.announce)
    except Exception as exc:
        raise HTTPException(400, f"invalid event: {exc}")
    runner.inject_queue.put(ev)
    return {"queued": req.kind}


@app.get("/api/status")
def get_status() -> dict:
    return runner.status()


# Serve the built frontend when present (production mode).
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True),
              name="frontend")
