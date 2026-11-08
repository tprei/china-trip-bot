"""The plan the group edited from Telegram, kept on top of `trip.yaml`."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import get_args

from tripbot.config import TripConfig
from tripbot.domain.plan import Change, ChangeKind, Edit, Plan

KINDS: dict[str, ChangeKind] = {kind: kind for kind in get_args(ChangeKind)}


@dataclass(frozen=True)
class PlanChange:
    id: int
    change: Change
    plan: Plan
    changed_by: str
    changed_at: datetime


def effective_config(conn: sqlite3.Connection, base: TripConfig) -> TripConfig:
    """`trip.yaml`'s rules with the latest plan from the group; the file's plan before any edit."""
    latest = recent_changes(conn, 1)
    return base.with_plan(latest[0].plan) if latest else base


def save_change(
    conn: sqlite3.Connection, base: TripConfig, edit: Edit, by: str, now: datetime
) -> TripConfig:
    """Check the edited plan against the rules, store it, and return the new config.

    Raises `ConfigError` and stores nothing when the plan contradicts the rules.
    """
    plan, change = edit
    config = base.with_plan(plan)
    _insert(conn, change, plan, by, now)
    return config


def recent_changes(conn: sqlite3.Connection, limit: int) -> list[PlanChange]:
    rows = conn.execute("SELECT * FROM plan_changes ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [_change(row) for row in rows]


def plan_change(conn: sqlite3.Connection, change_id: int) -> PlanChange | None:
    row = conn.execute("SELECT * FROM plan_changes WHERE id = ?", (change_id,)).fetchone()
    return _change(row) if row is not None else None


def undo(
    conn: sqlite3.Connection, base: TripConfig, by: str, now: datetime
) -> tuple[PlanChange, TripConfig] | None:
    """Go back to the plan before the latest change not undone yet.

    Each call steps one change further back; the undo itself is stored as a change.
    Returns the change undone, or None when there's nothing left to undo.
    """
    rows = [_change(row) for row in conn.execute("SELECT * FROM plan_changes ORDER BY id DESC")]
    undone = {int(row.change.subject) for row in rows if row.change.kind == "undone"}
    target = next(
        (row for row in rows if row.change.kind != "undone" and row.id not in undone), None
    )
    if target is None:
        return None
    before = next((row.plan for row in rows if row.id < target.id), base.plan)
    config = base.with_plan(before)
    _insert(conn, Change("undone", str(target.id), target.change.kind), before, by, now)
    return target, config


def _insert(conn: sqlite3.Connection, change: Change, plan: Plan, by: str, now: datetime) -> None:
    with conn:
        conn.execute(
            """INSERT INTO plan_changes (kind, subject, detail, plan_json, changed_by, changed_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                change.kind,
                change.subject,
                change.detail,
                json.dumps(plan.to_json(), ensure_ascii=False),
                by,
                now.isoformat(),
            ),
        )


def _change(row: sqlite3.Row) -> PlanChange:
    return PlanChange(
        id=int(row["id"]),
        change=Change(KINDS[row["kind"]], row["subject"], row["detail"]),
        plan=Plan.from_json(json.loads(row["plan_json"])),
        changed_by=row["changed_by"],
        changed_at=datetime.fromisoformat(row["changed_at"]),
    )
