from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Optional

from recoveryloop.schema import AuditRecord


class AuditLog:
    """Append-only SQLite persistence for ``AuditRecord`` objects.

    The store uses WAL mode for concurrent-read safety.  Every record is
    stored as a single JSON blob keyed by a deterministic id derived from
    ``case_id`` + ``timestamp``.  There are intentionally no ``update`` or
    ``delete`` methods — audit trails must be immutable.
    """

    def __init__(self, db_path: str | Path = "data/audit.db") -> None:
        self._conn = sqlite3.connect(
            str(db_path),
            check_same_thread=False,
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_records (
                id          TEXT PRIMARY KEY,
                case_id     TEXT NOT NULL,
                record_json TEXT NOT NULL,
                created_at  TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def write(self, record: AuditRecord) -> str:
        """Persist an ``AuditRecord`` and return its row id.

        The id is derived from ``case_id`` + ``timestamp`` so that the same
        case processed at the same instant is idempotent.  The caller MUST
        NOT mutate the record after writing — the store has no update path.
        """
        rid = f"{record.case_id}:{record.timestamp.isoformat()}"
        now = datetime.now(UTC).isoformat()
        self._conn.execute(
            "INSERT OR REPLACE INTO audit_records (id, case_id, record_json, created_at) "
            "VALUES (?, ?, ?, ?)",
            (rid, record.case_id, record.model_dump_json(), now),
        )
        self._conn.commit()
        return rid

    def get_by_id(self, record_id: str) -> Optional[AuditRecord]:
        """Fetch a single record by its row id. Returns ``None`` if not found."""
        row = self._conn.execute(
            "SELECT record_json FROM audit_records WHERE id = ?", (record_id,)
        ).fetchone()
        if row is None:
            return None
        return AuditRecord.model_validate_json(row[0])

    def get_by_date_range(self, start: datetime, end: datetime) -> list[AuditRecord]:
        """Return all records whose event timestamp falls within [start, end]."""
        rows = self._conn.execute(
            "SELECT record_json FROM audit_records "
            "WHERE json_extract(record_json, '$.event.timestamp') >= ? "
            "AND json_extract(record_json, '$.event.timestamp') <= ? "
            "ORDER BY created_at",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
        return [AuditRecord.model_validate_json(r[0]) for r in rows]

    def get_all(self) -> list[AuditRecord]:
        """Return every stored record in insertion order."""
        rows = self._conn.execute(
            "SELECT record_json FROM audit_records ORDER BY created_at"
        ).fetchall()
        return [AuditRecord.model_validate_json(r[0]) for r in rows]
