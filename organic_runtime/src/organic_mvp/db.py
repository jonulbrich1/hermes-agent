from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable, Optional

from .util import compact_json, stable_uid, utcnow


SCHEMA = r'''
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS meta(
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sources(
    source_id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    title TEXT,
    provider TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    cache_path TEXT,
    trust REAL NOT NULL DEFAULT 0.60,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    metadata_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_sources_url ON sources(url);

CREATE TABLE IF NOT EXISTS claims(
    claim_id TEXT PRIMARY KEY,
    source_id TEXT,
    source_kind TEXT NOT NULL,
    sentence_index INTEGER,
    text TEXT NOT NULL,
    evidence_quote TEXT,
    confidence REAL NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    metadata_json TEXT,
    FOREIGN KEY(source_id) REFERENCES sources(source_id)
);
CREATE INDEX IF NOT EXISTS idx_claims_source ON claims(source_id);
CREATE INDEX IF NOT EXISTS idx_claims_status ON claims(status);

CREATE TABLE IF NOT EXISTS concepts(
    concept_id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    normalized TEXT NOT NULL UNIQUE,
    kind TEXT,
    status TEXT NOT NULL DEFAULT 'PROVISIONAL',
    confidence REAL NOT NULL DEFAULT 0.50,
    mention_count INTEGER NOT NULL DEFAULT 0,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    metadata_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_concepts_label ON concepts(label);
CREATE INDEX IF NOT EXISTS idx_concepts_status ON concepts(status);

CREATE TABLE IF NOT EXISTS aliases(
    alias TEXT NOT NULL,
    normalized TEXT NOT NULL,
    concept_id TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.60,
    source_id TEXT,
    PRIMARY KEY(normalized, concept_id),
    FOREIGN KEY(concept_id) REFERENCES concepts(concept_id),
    FOREIGN KEY(source_id) REFERENCES sources(source_id)
);
CREATE INDEX IF NOT EXISTS idx_aliases_norm ON aliases(normalized);

CREATE TABLE IF NOT EXISTS lexical_anchors(
    anchor_id INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id TEXT NOT NULL,
    source_id TEXT,
    surface TEXT NOT NULL,
    normalized TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(claim_id, normalized),
    FOREIGN KEY(claim_id) REFERENCES claims(claim_id),
    FOREIGN KEY(source_id) REFERENCES sources(source_id)
);
CREATE INDEX IF NOT EXISTS idx_lexical_anchors_norm ON lexical_anchors(normalized);

CREATE TABLE IF NOT EXISTS mentions(
    mention_id INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id TEXT NOT NULL,
    concept_id TEXT NOT NULL,
    surface TEXT NOT NULL,
    start_char INTEGER,
    end_char INTEGER,
    FOREIGN KEY(claim_id) REFERENCES claims(claim_id),
    FOREIGN KEY(concept_id) REFERENCES concepts(concept_id)
);
CREATE INDEX IF NOT EXISTS idx_mentions_concept ON mentions(concept_id);

CREATE TABLE IF NOT EXISTS relations(
    relation_id TEXT PRIMARY KEY,
    claim_id TEXT NOT NULL,
    source_id TEXT,
    subject_id TEXT NOT NULL,
    predicate TEXT NOT NULL,
    object_id TEXT,
    object_text TEXT,
    confidence REAL NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    metadata_json TEXT,
    FOREIGN KEY(claim_id) REFERENCES claims(claim_id),
    FOREIGN KEY(source_id) REFERENCES sources(source_id),
    FOREIGN KEY(subject_id) REFERENCES concepts(concept_id),
    FOREIGN KEY(object_id) REFERENCES concepts(concept_id)
);
CREATE INDEX IF NOT EXISTS idx_rel_subject ON relations(subject_id);
CREATE INDEX IF NOT EXISTS idx_rel_object ON relations(object_id);
CREATE INDEX IF NOT EXISTS idx_rel_predicate ON relations(predicate);

CREATE TABLE IF NOT EXISTS tasks(
    task_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    origin TEXT NOT NULL,
    goal TEXT NOT NULL,
    priority INTEGER NOT NULL,
    status TEXT NOT NULL,
    parent_task_id TEXT,
    target_concept_id TEXT,
    generated_reason TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    started_at TEXT,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    result_text TEXT,
    metadata_json TEXT,
    FOREIGN KEY(parent_task_id) REFERENCES tasks(task_id),
    FOREIGN KEY(target_concept_id) REFERENCES concepts(concept_id)
);
CREATE INDEX IF NOT EXISTS idx_tasks_status_priority ON tasks(status, priority DESC, created_at ASC);

CREATE TABLE IF NOT EXISTS task_events(
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT,
    created_at TEXT NOT NULL,
    event_type TEXT NOT NULL,
    message TEXT NOT NULL,
    data_json TEXT,
    FOREIGN KEY(task_id) REFERENCES tasks(task_id)
);
CREATE INDEX IF NOT EXISTS idx_task_events_task ON task_events(task_id);

CREATE TABLE IF NOT EXISTS user_claims(
    user_claim_id TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    status TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.20,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    resolution TEXT,
    evidence_json TEXT
);

CREATE TABLE IF NOT EXISTS growth_history(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    concept_id TEXT,
    task_id TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    before_degree INTEGER,
    after_degree INTEGER,
    result TEXT,
    FOREIGN KEY(concept_id) REFERENCES concepts(concept_id),
    FOREIGN KEY(task_id) REFERENCES tasks(task_id)
);

CREATE TABLE IF NOT EXISTS conversation(
    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    role TEXT NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    task_id TEXT,
    metadata_json TEXT,
    FOREIGN KEY(task_id) REFERENCES tasks(task_id)
);

CREATE TABLE IF NOT EXISTS core_learning_events(
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    decision_kind TEXT NOT NULL,
    selected_action TEXT NOT NULL,
    reward REAL NOT NULL,
    outcome TEXT NOT NULL,
    features_json TEXT NOT NULL,
    scores_json TEXT NOT NULL,
    metadata_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_core_learning_created ON core_learning_events(created_at);

CREATE TABLE IF NOT EXISTS planner_outcomes(
    outcome_id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    task_signature_json TEXT NOT NULL,
    resources_json TEXT NOT NULL,
    world_mode TEXT NOT NULL,
    success INTEGER NOT NULL,
    cost_units REAL NOT NULL,
    duration_ms REAL NOT NULL,
    result_code TEXT NOT NULL,
    metadata_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_planner_outcomes_created ON planner_outcomes(created_at);
CREATE INDEX IF NOT EXISTS idx_planner_outcomes_plan ON planner_outcomes(plan_id);

CREATE TABLE IF NOT EXISTS processing_episodes(
    episode_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    task_id TEXT,
    request_id TEXT NOT NULL,
    task_signature TEXT NOT NULL,
    active_weave_json TEXT NOT NULL,
    attempts_json TEXT NOT NULL,
    selected_pathway TEXT,
    selected_answer_json TEXT,
    accepted INTEGER NOT NULL,
    result_code TEXT NOT NULL,
    rewards_json TEXT NOT NULL,
    feedback_source TEXT NOT NULL,
    state_before_hash TEXT NOT NULL,
    state_after_hash TEXT NOT NULL,
    duration_ms REAL NOT NULL,
    metadata_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_processing_episodes_created ON processing_episodes(created_at);
CREATE INDEX IF NOT EXISTS idx_processing_episodes_task ON processing_episodes(task_id);
'''


class MemoryDB:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        with self._lock:
            self.conn.executescript(SCHEMA)
            self.conn.commit()
        self._seed_meta()

    def _seed_meta(self):
        if self.get_meta('created_at') is None:
            self.set_meta('created_at', utcnow())
        if self.get_meta('schema_version') != '3':
            self.set_meta('schema_version', '3')

    def close(self):
        with self._lock:
            self.conn.close()

    def execute(self, sql: str, params: Iterable[Any] = ()):
        with self._lock:
            cur = self.conn.execute(sql, tuple(params))
            self.conn.commit()
            return cur

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self.conn.execute(sql, tuple(params)).fetchall())

    def one(self, sql: str, params: Iterable[Any] = ()) -> Optional[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(sql, tuple(params)).fetchone()

    def set_meta(self, key: str, value: str):
        self.execute('INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, value))

    def get_meta(self, key: str) -> Optional[str]:
        row = self.one('SELECT value FROM meta WHERE key=?', (key,))
        return row['value'] if row else None

    def add_source(self, source_id: str, url: str, title: str, provider: str, sha256: str, cache_path: str,
                   trust: float = 0.60, metadata: dict | None = None):
        self.execute(
            '''INSERT INTO sources(source_id,url,title,provider,retrieved_at,sha256,cache_path,trust,metadata_json)
               VALUES(?,?,?,?,?,?,?,?,?)
               ON CONFLICT(source_id) DO UPDATE SET title=excluded.title, provider=excluded.provider,
               retrieved_at=excluded.retrieved_at, sha256=excluded.sha256, cache_path=excluded.cache_path,
               trust=excluded.trust, metadata_json=excluded.metadata_json''',
            (source_id, url, title, provider, utcnow(), sha256, cache_path, trust, compact_json(metadata or {}))
        )

    def source_by_url(self, url: str):
        return self.one('SELECT * FROM sources WHERE url=? ORDER BY retrieved_at DESC LIMIT 1', (url,))

    def add_claim(self, claim_id: str, source_id: str | None, source_kind: str, sentence_index: int | None, text: str,
                  evidence_quote: str | None, confidence: float, status: str, metadata: dict | None = None):
        now = utcnow()
        self.execute(
            '''INSERT OR IGNORE INTO claims(claim_id,source_id,source_kind,sentence_index,text,evidence_quote,confidence,status,created_at,updated_at,metadata_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
            (claim_id, source_id, source_kind, sentence_index, text, evidence_quote, confidence, status, now, now, compact_json(metadata or {}))
        )

    def upsert_concept(self, concept_id: str, label: str, normalized: str, kind: str | None = None,
                       status: str = 'PROVISIONAL', confidence: float = 0.50, metadata: dict | None = None):
        now = utcnow()
        self.execute(
            '''INSERT INTO concepts(concept_id,label,normalized,kind,status,confidence,mention_count,first_seen_at,last_seen_at,metadata_json)
               VALUES(?,?,?,?,?,?,0,?,?,?)
               ON CONFLICT(normalized) DO UPDATE SET
                 label=CASE WHEN length(excluded.label)<length(concepts.label) THEN excluded.label ELSE concepts.label END,
                 kind=COALESCE(concepts.kind, excluded.kind),
                 confidence=MAX(concepts.confidence, excluded.confidence),
                 last_seen_at=excluded.last_seen_at''',
            (concept_id, label, normalized, kind, status, confidence, now, now, compact_json(metadata or {}))
        )
        row = self.one('SELECT concept_id FROM concepts WHERE normalized=?', (normalized,))
        return row['concept_id'] if row else concept_id

    def touch_concept(self, concept_id: str, delta: int = 1):
        self.execute('UPDATE concepts SET mention_count=mention_count+?, last_seen_at=? WHERE concept_id=?', (delta, utcnow(), concept_id))

    def add_alias(self, alias: str, normalized: str, concept_id: str, confidence: float = 0.60, source_id: str | None = None):
        self.execute(
            '''INSERT INTO aliases(alias,normalized,concept_id,confidence,source_id) VALUES(?,?,?,?,?)
               ON CONFLICT(normalized,concept_id) DO UPDATE SET confidence=MAX(aliases.confidence, excluded.confidence)''',
            (alias, normalized, concept_id, confidence, source_id)
        )

    def add_lexical_anchor(self, claim_id: str, source_id: str | None, surface: str, normalized: str):
        self.execute('INSERT OR IGNORE INTO lexical_anchors(claim_id,source_id,surface,normalized,created_at) VALUES(?,?,?,?,?)',
                     (claim_id, source_id, surface, normalized, utcnow()))

    def add_mention(self, claim_id: str, concept_id: str, surface: str, start_char: int | None = None, end_char: int | None = None):
        self.execute('INSERT INTO mentions(claim_id,concept_id,surface,start_char,end_char) VALUES(?,?,?,?,?)',
                     (claim_id, concept_id, surface, start_char, end_char))
        self.touch_concept(concept_id, 1)

    def add_relation(self, relation_id: str, claim_id: str, source_id: str | None, subject_id: str, predicate: str,
                     object_id: str | None, object_text: str | None, confidence: float, status: str,
                     metadata: dict | None = None):
        self.execute(
            '''INSERT OR IGNORE INTO relations(relation_id,claim_id,source_id,subject_id,predicate,object_id,object_text,confidence,status,created_at,metadata_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
            (relation_id, claim_id, source_id, subject_id, predicate, object_id, object_text, confidence, status, utcnow(), compact_json(metadata or {}))
        )

    def create_task(self, task_id: str, kind: str, origin: str, goal: str, priority: int, status: str = 'PENDING',
                    parent_task_id: str | None = None, target_concept_id: str | None = None,
                    generated_reason: str | None = None, metadata: dict | None = None):
        now = utcnow()
        self.execute(
            '''INSERT OR IGNORE INTO tasks(task_id,kind,origin,goal,priority,status,parent_task_id,target_concept_id,generated_reason,
               created_at,updated_at,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
            (task_id, kind, origin, goal, priority, status, parent_task_id, target_concept_id, generated_reason, now, now, compact_json(metadata or {}))
        )

    def update_task(self, task_id: str, **changes):
        allowed = {'status','started_at','updated_at','completed_at','result_text','attempts','priority','generated_reason','metadata_json'}
        fields, vals = [], []
        for k, v in changes.items():
            if k in allowed:
                fields.append(f'{k}=?')
                vals.append(v)
        if 'updated_at' not in changes:
            fields.append('updated_at=?')
            vals.append(utcnow())
        if not fields:
            return
        vals.append(task_id)
        self.execute(f"UPDATE tasks SET {', '.join(fields)} WHERE task_id=?", vals)

    def next_task(self):
        return self.one("SELECT * FROM tasks WHERE status='PENDING' ORDER BY priority DESC, created_at ASC LIMIT 1")

    def task(self, task_id: str):
        return self.one('SELECT * FROM tasks WHERE task_id=?', (task_id,))

    def add_task_event(self, task_id: str | None, event_type: str, message: str, data: dict | None = None):
        self.execute('INSERT INTO task_events(task_id,created_at,event_type,message,data_json) VALUES(?,?,?,?,?)',
                     (task_id, utcnow(), event_type, message, compact_json(data or {})))

    def add_user_claim(self, user_claim_id: str, text: str):
        now = utcnow()
        self.execute('INSERT OR IGNORE INTO user_claims(user_claim_id,text,status,confidence,created_at,updated_at) VALUES(?,?,\'UNVERIFIED\',0.20,?,?)',
                     (user_claim_id, text, now, now))

    def update_user_claim(self, user_claim_id: str, status: str, confidence: float, resolution: str, evidence: dict | list | None = None):
        self.execute('UPDATE user_claims SET status=?,confidence=?,updated_at=?,resolution=?,evidence_json=? WHERE user_claim_id=?',
                     (status, confidence, utcnow(), resolution, compact_json(evidence or {}), user_claim_id))

    def add_conversation(self, role: str, kind: str, text: str, task_id: str | None = None, metadata: dict | None = None):
        self.execute('INSERT INTO conversation(created_at,role,kind,text,task_id,metadata_json) VALUES(?,?,?,?,?,?)',
                     (utcnow(), role, kind, text, task_id, compact_json(metadata or {})))

    def record_core_learning_event(self, decision_kind: str, selected_action: str, reward: float, outcome: str,
                                   features: dict | None = None, scores: dict | None = None, metadata: dict | None = None):
        self.execute(
            'INSERT INTO core_learning_events(created_at,decision_kind,selected_action,reward,outcome,features_json,scores_json,metadata_json) VALUES(?,?,?,?,?,?,?,?)',
            (utcnow(), decision_kind, selected_action, float(reward), outcome, compact_json(features or {}), compact_json(scores or {}), compact_json(metadata or {}))
        )

    def list_core_learning_events(self, limit: int = 100):
        return self.query('SELECT * FROM core_learning_events ORDER BY event_id DESC LIMIT ?', (limit,))

    def record_planner_outcome(
        self,
        plan_id: str,
        request_id: str,
        task_signature: list[str],
        resources: list[dict],
        world_mode: str,
        success: bool,
        cost_units: float,
        duration_ms: float,
        result_code: str,
        metadata: dict | None = None,
    ):
        self.execute(
            '''INSERT INTO planner_outcomes(
                   created_at,plan_id,request_id,task_signature_json,resources_json,
                   world_mode,success,cost_units,duration_ms,result_code,metadata_json
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
            (
                utcnow(),
                plan_id,
                request_id,
                compact_json(task_signature),
                compact_json(resources),
                world_mode,
                1 if success else 0,
                float(cost_units),
                float(duration_ms),
                result_code,
                compact_json(metadata or {}),
            ),
        )

    def list_planner_outcomes(self, limit: int = 100):
        return self.query('SELECT * FROM planner_outcomes ORDER BY outcome_id DESC LIMIT ?', (limit,))

    def record_processing_episode(
        self,
        episode_id: str,
        task_id: str | None,
        request_id: str,
        task_signature: str,
        active_weave: dict,
        attempts: list[dict],
        selected_pathway: str | None,
        selected_answer: Any,
        accepted: bool,
        result_code: str,
        rewards: list[dict],
        feedback_source: str,
        state_before_hash: str,
        state_after_hash: str,
        duration_ms: float,
        metadata: dict | None = None,
    ):
        self.execute(
            '''INSERT INTO processing_episodes(
                   episode_id,created_at,task_id,request_id,task_signature,
                   active_weave_json,attempts_json,selected_pathway,selected_answer_json,
                   accepted,result_code,rewards_json,feedback_source,state_before_hash,
                   state_after_hash,duration_ms,metadata_json
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (
                episode_id,
                utcnow(),
                task_id,
                request_id,
                task_signature,
                compact_json(active_weave),
                compact_json(attempts),
                selected_pathway,
                compact_json(selected_answer),
                1 if accepted else 0,
                result_code,
                compact_json(rewards),
                feedback_source,
                state_before_hash,
                state_after_hash,
                float(duration_ms),
                compact_json(metadata or {}),
            ),
        )

    def list_processing_episodes(self, limit: int = 100):
        return self.query('SELECT * FROM processing_episodes ORDER BY created_at DESC LIMIT ?', (limit,))

    def recent_conversation(self, limit: int = 100):
        rows = self.query('SELECT * FROM conversation ORDER BY message_id DESC LIMIT ?', (limit,))
        return list(reversed(rows))

    def list_tasks(self, limit: int = 100):
        return self.query('SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?', (limit,))

    def list_sources(self, limit: int = 100):
        return self.query('SELECT * FROM sources ORDER BY retrieved_at DESC LIMIT ?', (limit,))

    def list_user_claims(self, limit: int = 100):
        return self.query('SELECT * FROM user_claims ORDER BY created_at DESC LIMIT ?', (limit,))

    def counts(self) -> dict[str, int]:
        out = {}
        for table in ('sources','claims','concepts','lexical_anchors','relations','tasks','user_claims','conversation','core_learning_events','planner_outcomes','processing_episodes'):
            row = self.one(f'SELECT COUNT(*) AS n FROM {table}')
            out[table] = int(row['n']) if row else 0
        out['pending_tasks'] = int((self.one("SELECT COUNT(*) n FROM tasks WHERE status='PENDING'") or {'n':0})['n'])
        out['active_tasks'] = int((self.one("SELECT COUNT(*) n FROM tasks WHERE status='ACTIVE'") or {'n':0})['n'])
        return out

    def concept_degree(self, concept_id: str) -> int:
        row = self.one('SELECT COUNT(*) n FROM relations WHERE status IN (\'GROUNDED\',\'USER_VALIDATED\') AND (subject_id=? OR object_id=?)', (concept_id, concept_id))
        return int(row['n']) if row else 0

    def concept(self, concept_id: str):
        return self.one('SELECT * FROM concepts WHERE concept_id=?', (concept_id,))

    def find_concept(self, normalized: str):
        return self.one('SELECT * FROM concepts WHERE normalized=?', (normalized,))

    def list_frontier_candidates(self, limit: int = 200):
        # Growth attempts include partial/failed runs so empty searches cannot be retried in a hot loop.
        return self.query(
            '''SELECT c.*, 
                (SELECT COUNT(*) FROM relations r WHERE r.subject_id=c.concept_id OR r.object_id=c.concept_id) AS degree,
                (SELECT MAX(completed_at) FROM tasks t
                 WHERE t.target_concept_id=c.concept_id
                   AND t.kind IN ('IDLE_GROWTH','PRECREATED_GROWTH')
                   AND t.status IN ('COMPLETED','PARTIAL','FAILED')) AS last_growth_at,
                (SELECT COUNT(*) FROM tasks t
                 WHERE t.target_concept_id=c.concept_id
                   AND t.kind IN ('IDLE_GROWTH','PRECREATED_GROWTH')
                   AND t.status IN ('PARTIAL','FAILED')
                   AND t.completed_at > COALESCE(
                       (SELECT MAX(success.completed_at) FROM tasks success
                        WHERE success.target_concept_id=c.concept_id
                          AND success.kind IN ('IDLE_GROWTH','PRECREATED_GROWTH')
                          AND success.status='COMPLETED'),
                       ''
                   )) AS failed_growth_attempts
               FROM concepts c
               WHERE c.mention_count > 0
               ORDER BY c.last_seen_at DESC
               LIMIT ?''', (limit,)
        )

    def claims_for_terms(self, terms: list[str], limit: int = 20):
        if not terms:
            return []
        clauses = []
        params: list[Any] = []
        for term in terms[:8]:
            clauses.append('lower(c.text) LIKE ?')
            params.append(f'%{term.lower()}%')
        params.append(limit)
        return self.query(
            f'''SELECT c.*, s.title AS source_title, s.url AS source_url, s.provider AS source_provider
                FROM claims c LEFT JOIN sources s ON s.source_id=c.source_id
                WHERE c.status IN ('GROUNDED','USER_VALIDATED') AND ({' OR '.join(clauses)})
                ORDER BY c.confidence DESC, c.created_at DESC LIMIT ?''', params
        )

    def related_claims_for_concept(self, concept_id: str, limit: int = 20):
        return self.query(
            '''SELECT DISTINCT c.*, s.title source_title, s.url source_url
               FROM relations r JOIN claims c ON c.claim_id=r.claim_id LEFT JOIN sources s ON s.source_id=c.source_id
               WHERE (r.subject_id=? OR r.object_id=?) AND r.status IN ('GROUNDED','USER_VALIDATED')
               ORDER BY r.confidence DESC, c.created_at DESC LIMIT ?''', (concept_id, concept_id, limit)
        )

    def dump_table(self, table: str, limit: int = 10000) -> list[dict]:
        allowed = {'sources','claims','concepts','aliases','lexical_anchors','mentions','relations','tasks','task_events','user_claims','growth_history','conversation','core_learning_events','planner_outcomes','processing_episodes','meta'}
        if table not in allowed:
            raise ValueError('invalid table')
        rows = self.query(f'SELECT * FROM {table} LIMIT ?', (limit,))
        return [dict(r) for r in rows]

    def backup_to(self, target: Path):
        target.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            dest = sqlite3.connect(str(target))
            try:
                self.conn.backup(dest)
            finally:
                dest.close()
