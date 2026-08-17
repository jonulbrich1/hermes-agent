"""Small scaffold Living Memory store.

This is intentionally conservative. It is a bridge until the full Living Memory
implementation is moved into the Hermes fork.
"""

from __future__ import annotations

from pathlib import Path
import sqlite3
import json
import time
import uuid


SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS memories (
    memory_id TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    trust_state TEXT NOT NULL,
    confidence REAL NOT NULL,
    source_url TEXT,
    provenance_family TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence (
    evidence_id TEXT PRIMARY KEY,
    claim TEXT NOT NULL,
    quote TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_title TEXT,
    provenance_family TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""


class OrganicStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self):
        self.db.close()

    def event(self, event_type: str, payload: dict):
        self.db.execute(
            "INSERT INTO events(event_type,payload_json,created_at) VALUES(?,?,?)",
            (event_type, json.dumps(payload, sort_keys=True), time.time()),
        )
        self.db.commit()

    def add_evidence(self, *, claim: str, quote: str, source_url: str,
                     source_title: str, provenance_family: str, source_kind: str) -> str:
        evidence_id = "evidence:" + uuid.uuid4().hex
        self.db.execute(
            """INSERT INTO evidence(
               evidence_id,claim,quote,source_url,source_title,
               provenance_family,source_kind,status,created_at
               ) VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                evidence_id, claim, quote, source_url, source_title,
                provenance_family, source_kind, "UNVERIFIED", time.time(),
            ),
        )
        self.db.commit()
        self.event("evidence_submitted", {"evidence_id": evidence_id, "claim": claim})
        return evidence_id

    def search(self, query: str, limit: int = 8):
        terms = [t.lower() for t in query.split() if len(t) > 2]
        rows = self.db.execute(
            "SELECT * FROM memories WHERE trust_state='GROUNDED' ORDER BY confidence DESC, created_at DESC LIMIT 500"
        ).fetchall()
        scored = []
        for row in rows:
            text = row["text"].lower()
            score = sum(1 for t in terms if t in text)
            if score:
                scored.append((score, dict(row)))
        scored.sort(key=lambda x: (-x[0], -float(x[1]["confidence"])))
        return [x[1] for x in scored[:limit]]

    def counts(self):
        out = {}
        for table in ("memories", "evidence", "events"):
            out[table] = self.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        return out

    def evidence_for_claim(self, claim: str):
        rows = self.db.execute(
            "SELECT * FROM evidence WHERE claim=? ORDER BY created_at", (claim,)
        ).fetchall()
        return [dict(r) for r in rows]

    def promote_claim(self, claim: str, confidence: float, source_url: str, provenance_family: str):
        memory_id = "memory:" + uuid.uuid4().hex
        self.db.execute(
            "INSERT INTO memories VALUES(?,?,?,?,?,?,?)",
            (
                memory_id, claim, "GROUNDED", confidence,
                source_url, provenance_family, time.time(),
            ),
        )
        self.db.commit()
        self.event("memory_promoted", {"memory_id": memory_id, "claim": claim})
        return memory_id
