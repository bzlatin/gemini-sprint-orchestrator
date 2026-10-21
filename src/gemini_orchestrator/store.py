from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .config import private_dir
from .models import TRANSITIONS, OrchestratorError, Plan, State, Ticket, digest


class Store:
    """Single-profile transactional state, approvals and audit trail."""

    def __init__(self, path: Path):
        private_dir(path.parent)
        if path.is_symlink():
            raise OrchestratorError("Refusing a symlink database")
        self.db = sqlite3.connect(path, timeout=10, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self._transaction_sequence = 0
        path.chmod(0o600)
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT OR IGNORE INTO meta VALUES('schema','1');
            CREATE TABLE IF NOT EXISTS tickets(
                key TEXT PRIMARY KEY, data TEXT NOT NULL, state TEXT NOT NULL,
                branch TEXT, worktree TEXT, base TEXT, pid INTEGER, process_token TEXT,
                result TEXT, verification TEXT, feedback TEXT NOT NULL DEFAULT '[]',
                updated TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS plans(id TEXT PRIMARY KEY, data TEXT NOT NULL, hash TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS approvals(
                id INTEGER PRIMARY KEY, action TEXT NOT NULL, subject TEXT NOT NULL,
                fingerprint TEXT NOT NULL, actor TEXT, granted INTEGER NOT NULL DEFAULT 0,
                consumed INTEGER NOT NULL DEFAULT 0, created TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS events(
                id INTEGER PRIMARY KEY, ticket TEXT, message TEXT NOT NULL,
                created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS runs(
                id INTEGER PRIMARY KEY, ticket TEXT NOT NULL, kind TEXT NOT NULL,
                data TEXT NOT NULL, created TEXT DEFAULT CURRENT_TIMESTAMP);
        """)
        if self.db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()[0] != "1":
            raise OrchestratorError(
                "Unsupported database schema; use a compatible application version"
            )

    def close(self) -> None:
        self.db.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        nested = self.db.in_transaction
        self._transaction_sequence += 1
        savepoint = f"nested_{self._transaction_sequence}"
        self.db.execute(f"SAVEPOINT {savepoint}" if nested else "BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute(f"RELEASE {savepoint}" if nested else "COMMIT")
        except BaseException:
            self.db.execute(f"ROLLBACK TO {savepoint}" if nested else "ROLLBACK")
            if nested:
                self.db.execute(f"RELEASE {savepoint}")
            raise

    def event(self, message: str, key: str | None = None) -> None:
        self.db.execute("INSERT INTO events(ticket,message) VALUES(?,?)", (key, message))

    def events(self, limit: int = 100) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.db.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))
        ]

    def upsert(self, tickets: list[Ticket]) -> None:
        with self.transaction():
            for ticket in tickets:
                old = self.db.execute(
                    "SELECT data,state FROM tickets WHERE key=?", (ticket.key,)
                ).fetchone()
                data = ticket.model_dump_json()
                if old and old[0] != data:
                    state = State(old[1])
                    if state not in {State.NOT_SELECTED, State.SELECTED}:
                        raise OrchestratorError(
                            f"{ticket.key} changed; finish/cancel its active plan before refresh"
                        )
                    self.db.execute(
                        "UPDATE approvals SET consumed=1 WHERE subject=? OR action IN ('plan','launch')",
                        (ticket.key,),
                    )
                self.db.execute(
                    "INSERT INTO tickets(key,data,state) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET data=excluded.data",
                    (ticket.key, data, State.NOT_SELECTED),
                )
            self.event(f"Discovered {len(tickets)} tickets")

    def rows(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.db.execute("SELECT * FROM tickets ORDER BY key")]

    def row(self, key: str) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM tickets WHERE key=?", (key,)).fetchone()
        if row is None:
            raise OrchestratorError(f"Unknown ticket {key}")
        return dict(row)

    def ticket(self, key: str) -> Ticket:
        return Ticket.model_validate_json(self.row(key)["data"])

    def patch(self, key: str, **values: Any) -> None:
        allowed = {
            "branch",
            "worktree",
            "base",
            "pid",
            "process_token",
            "result",
            "verification",
            "feedback",
        }
        if not values.keys() <= allowed:
            raise OrchestratorError("Invalid state field")
        assignments = ",".join(f"{column}=?" for column in values)
        self.db.execute(
            f"UPDATE tickets SET {assignments},updated=CURRENT_TIMESTAMP WHERE key=?",
            (*values.values(), key),
        )

    def transition(
        self, key: str, target: State, *, human: str | None = None, fingerprint: str = ""
    ) -> None:
        with self.transaction():
            current = State(self.row(key)["state"])
            if target not in TRANSITIONS[current]:
                raise OrchestratorError(f"Invalid transition {current} → {target}")
            if target == State.APPROVED:
                if not human:
                    raise OrchestratorError(
                        "Only an explicit human review can approve implementation"
                    )
                self.require("accept", key, fingerprint, consume=True)
            self.db.execute(
                "UPDATE tickets SET state=?,updated=CURRENT_TIMESTAMP WHERE key=?", (target, key)
            )
            self.event(f"{current} → {target}" + (f" by {human}" if human else ""), key)

    def request(self, action: str, subject: str, fingerprint: str) -> int:
        self.db.execute(
            "UPDATE approvals SET consumed=1 WHERE action=? AND subject=? AND fingerprint!=? AND consumed=0",
            (action, subject, fingerprint),
        )
        row = self.db.execute(
            "SELECT id FROM approvals WHERE action=? AND subject=? AND fingerprint=? AND consumed=0",
            (action, subject, fingerprint),
        ).fetchone()
        if row:
            return int(row[0])
        cursor = self.db.execute(
            "INSERT INTO approvals(action,subject,fingerprint) VALUES(?,?,?)",
            (action, subject, fingerprint),
        )
        return int(cursor.lastrowid or 0)

    def grant(self, approval: int, actor: str) -> None:
        if not actor.strip() or actor.lower() in {"agent", "worker", "gemini", "system"}:
            raise OrchestratorError("A human identity is required")
        cursor = self.db.execute(
            "UPDATE approvals SET granted=1,actor=? WHERE id=? AND consumed=0", (actor, approval)
        )
        if cursor.rowcount != 1:
            raise OrchestratorError("Approval is missing or already consumed")
        self.event(f"Human {actor} granted approval #{approval}")

    def require(
        self, action: str, subject: str, fingerprint: str, *, consume: bool = False
    ) -> None:
        row = self.db.execute(
            "SELECT id FROM approvals WHERE action=? AND subject=? AND fingerprint=? AND granted=1 AND consumed=0",
            (action, subject, fingerprint),
        ).fetchone()
        if not row:
            raise OrchestratorError(
                f"Human approval required: {action} {subject}; action changed or not approved"
            )
        if consume:
            self.db.execute("UPDATE approvals SET consumed=1 WHERE id=?", (row[0],))

    def approvals(self) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.db.execute("SELECT * FROM approvals WHERE consumed=0 ORDER BY id")
        ]

    def save_plan(self, plan: Plan) -> None:
        with self.transaction():
            self.db.execute("UPDATE approvals SET consumed=1 WHERE action IN ('plan','launch')")
            self.db.execute(
                "INSERT INTO plans VALUES(?,?,?)",
                (plan.id, plan.model_dump_json(), plan.fingerprint),
            )
            self.request("plan", plan.id, plan.fingerprint)
            self.event(f"Proposed plan {plan.id}; prior plan approvals invalidated")

    def plan(self) -> Plan:
        row = self.db.execute("SELECT data FROM plans ORDER BY rowid DESC LIMIT 1").fetchone()
        if row is None:
            raise OrchestratorError("Select tickets and generate a plan first")
        return Plan.model_validate_json(row[0])

    def run_record(self, key: str, kind: str, data: Any) -> None:
        self.db.execute(
            "INSERT INTO runs(ticket,kind,data) VALUES(?,?,?)", (key, kind, json.dumps(data))
        )

    def selection_hash(self, keys: list[str]) -> str:
        return digest({key: self.ticket(key).model_dump() for key in sorted(keys)})
