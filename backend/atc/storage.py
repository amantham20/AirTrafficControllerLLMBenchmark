"""SQLite persistence: every run's full event log, periodic state snapshots
(for UI replay), metrics and reports. SQLAlchemy ORM, one file per database.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    select,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
)

from .engine import SimEngine
from .scoring import compute_metrics, delay_report, incident_log

DEFAULT_DB_PATH = "runs.sqlite3"


class Base(DeclarativeBase):
    pass


class RunRecord(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scenario_id: Mapped[str] = mapped_column(String(64), index=True)
    controller: Mapped[str] = mapped_column(String(32))
    model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    label: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    seed: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(String(32))
    duration_s: Mapped[int] = mapped_column(Integer)
    completed: Mapped[int] = mapped_column(Integer, default=0)
    efficiency_score: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True)
    metrics_json: Mapped[str] = mapped_column(Text, default="{}")
    report_json: Mapped[str] = mapped_column(Text, default="{}")
    incidents_json: Mapped[str] = mapped_column(Text, default="[]")
    llm_calls_json: Mapped[str] = mapped_column(Text, default="[]")

    events: Mapped[list["EventRecord"]] = relationship(
        back_populates="run", cascade="all, delete-orphan")
    snapshots: Mapped[list["SnapshotRecord"]] = relationship(
        back_populates="run", cascade="all, delete-orphan")

    @property
    def metrics(self) -> dict:
        return json.loads(self.metrics_json)


class EventRecord(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True,
                                    autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    t_s: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(32))
    callsign: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    text: Mapped[str] = mapped_column(Text, default="")
    severity: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    incident: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    data_json: Mapped[str] = mapped_column(Text, default="{}")

    run: Mapped[RunRecord] = relationship(back_populates="events")


class SnapshotRecord(Base):
    __tablename__ = "snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True,
                                    autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    t_s: Mapped[int] = mapped_column(Integer)
    state_json: Mapped[str] = mapped_column(Text)

    run: Mapped[RunRecord] = relationship(back_populates="snapshots")


class RunStore:
    def __init__(self, path: str = DEFAULT_DB_PATH):
        self.engine = create_engine(f"sqlite:///{path}")
        Base.metadata.create_all(self.engine)

    def session(self) -> Session:
        return Session(self.engine)

    # ------------------------------------------------------------------

    def save_run(self, sim: SimEngine, controller_name: str,
                 model: Optional[str] = None,
                 label: Optional[str] = None,
                 llm_calls: Optional[list[dict]] = None,
                 snapshots: Optional[list[dict]] = None) -> str:
        run_id = str(uuid.uuid4())
        metrics = compute_metrics(sim)
        record = RunRecord(
            id=run_id,
            scenario_id=sim.scenario.id,
            controller=controller_name,
            model=model,
            label=label,
            seed=sim.scenario.seed,
            created_at=datetime.now(timezone.utc).isoformat(
                timespec="seconds"),
            duration_s=sim.t,
            completed=1 if sim.all_complete else 0,
            efficiency_score=metrics["efficiency_score"],
            metrics_json=json.dumps(metrics),
            report_json=json.dumps(delay_report(sim)),
            incidents_json=json.dumps(incident_log(sim)),
            llm_calls_json=json.dumps(llm_calls or []),
        )
        with self.session() as s:
            s.add(record)
            for ev in sim.events:
                s.add(EventRecord(
                    run_id=run_id, seq=ev.seq, t_s=ev.t_s,
                    type=ev.type.value, callsign=ev.callsign, text=ev.text,
                    severity=ev.severity.value if ev.severity else None,
                    incident=ev.incident.value if ev.incident else None,
                    data_json=json.dumps(ev.data)))
            for snap in snapshots or []:
                s.add(SnapshotRecord(run_id=run_id, t_s=snap["t_s"],
                                     state_json=json.dumps(snap)))
            s.commit()
        return run_id

    # ------------------------------------------------------------------

    def list_runs(self, scenario_id: Optional[str] = None) -> list[dict]:
        with self.session() as s:
            q = select(RunRecord).order_by(RunRecord.created_at)
            if scenario_id:
                q = q.where(RunRecord.scenario_id == scenario_id)
            return [self._run_summary(r) for r in s.scalars(q)]

    @staticmethod
    def _run_summary(r: RunRecord) -> dict:
        m = r.metrics
        return {
            "id": r.id,
            "scenario": r.scenario_id,
            "controller": r.controller,
            "model": r.model,
            "label": r.label or r.model or r.controller,
            "seed": r.seed,
            "created_at": r.created_at,
            "completed": bool(r.completed),
            "efficiency_score": r.efficiency_score,
            "total_delay_min": m.get("total_delay_min"),
            "incidents": m.get("incidents"),
            "rejections": m.get("rejections"),
            "throughput_ops_per_hour": m.get("throughput_ops_per_hour"),
            "on_time_performance": m.get("on_time_performance"),
            "llm_latency_p95_ms": m.get("llm_latency_p95_ms"),
        }

    def get_run(self, run_id: str) -> Optional[dict]:
        with self.session() as s:
            r = s.get(RunRecord, run_id)
            if r is None:
                return None
            out = self._run_summary(r)
            out["metrics"] = r.metrics
            out["report"] = json.loads(r.report_json)
            out["incidents_detail"] = json.loads(r.incidents_json)
            out["llm_calls"] = json.loads(r.llm_calls_json)
            return out

    def get_events(self, run_id: str) -> list[dict]:
        with self.session() as s:
            q = select(EventRecord).where(
                EventRecord.run_id == run_id).order_by(EventRecord.seq)
            return [{
                "seq": e.seq, "t_s": e.t_s, "type": e.type,
                "callsign": e.callsign, "text": e.text,
                "severity": e.severity, "incident": e.incident,
                "data": json.loads(e.data_json),
            } for e in s.scalars(q)]

    def get_snapshots(self, run_id: str) -> list[dict]:
        with self.session() as s:
            q = select(SnapshotRecord).where(
                SnapshotRecord.run_id == run_id).order_by(SnapshotRecord.t_s)
            return [json.loads(sn.state_json) for sn in s.scalars(q)]
