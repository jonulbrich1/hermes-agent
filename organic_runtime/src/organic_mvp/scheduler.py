from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from .config import AppConfig
from .core import BaseCore
from .db import MemoryDB
from .evidence import EvidenceBroker, EvidenceDocument
from .loggingx import AuditLog
from .memory import MemoryCompiler
from .util import compact_json, utcnow


class OrganicEngine:
    """Persistent task/growth scheduler for the Organic AI MVP."""

    def __init__(self, root: Path, config: AppConfig, db: MemoryDB, core: BaseCore,
                 broker: EvidenceBroker, memory: MemoryCompiler, logger: logging.Logger,
                 audit: AuditLog, processor: Any | None = None,
                 processor_growth_callback: Callable[[], dict[str, Any]] | None = None):
        self.root = root
        self.config = config
        self.db = db
        self.core = core
        self.broker = broker
        self.memory = memory
        self.logger = logger
        self.audit = audit
        self.processor = processor
        self._processor_growth_callback = processor_growth_callback
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._state_lock = threading.RLock()
        self._current_task_id: Optional[str] = None
        self._manual_growth_budget = 0
        self._completed_idle_cycles = 0
        self._completed_processor_growth_cycles = 0
        self._external_user_active = False
        self._review_callback: Optional[Callable[[str], str]] = None
        self._seed_precreated_tasks()

    def _seed_precreated_tasks(self) -> None:
        seeds = [
            ('frontier', 'Explore one underdeveloped concept already present in Living Memory and connect it to grounded evidence.',
             'Pre-created developmental task: graph frontier exploration.'),
            ('uncertainty', 'Revisit a provisional or low-confidence memory and seek independent evidence that can strengthen, revise, or reject it.',
             'Pre-created developmental task: uncertainty reduction.'),
            ('contradictions', 'Review unresolved or contradicted user information and preserve the evidence trail without treating user priority as truth.',
             'Pre-created maintenance task: epistemic validation.'),
        ]
        for key, goal, reason in seeds:
            tid = f'task:precreated:{key}'
            if not self.db.task(tid):
                self.db.create_task(tid, 'PRECREATED_GROWTH', 'SYSTEM', goal, 220, status='DORMANT', generated_reason=reason,
                                    metadata={'template': key})
                self.db.add_task_event(tid, 'CREATED_DORMANT', reason)

    def set_review_callback(self, callback: Callable[[str], str]) -> None:
        self._review_callback = callback

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name='OrganicGrowthLoop', daemon=True)
        self._thread.start()
        self.logger.info('Organic growth loop started')
        self.audit.write('growth_loop_started')

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=5)
        self.audit.write('growth_loop_stopped')

    def wake(self) -> None:
        self._wake.set()

    def submit_user_task(self, text: str) -> str:
        text = text.strip()
        if not text:
            raise ValueError('Task is empty')
        tid = 'task:user:' + hashlib.sha256((utcnow() + '|' + text).encode('utf-8')).hexdigest()[:18]
        self.db.create_task(tid, 'USER_TASK', 'USER', text, 1000, generated_reason='Explicit user task. Highest control priority.')
        self.db.add_conversation('user', 'task', text, tid)
        self.db.add_task_event(tid, 'USER_TASK_CREATED', text)
        self.audit.write('user_task_created', task_id=tid, goal=text, control_priority=1000)
        self.wake()
        return tid

    def submit_user_information(self, text: str) -> tuple[str, str]:
        text = text.strip()
        if not text:
            raise ValueError('Information is empty')
        uid = 'userclaim:' + hashlib.sha256((utcnow() + '|' + text).encode('utf-8')).hexdigest()[:18]
        self.db.add_user_claim(uid, text)
        tid = 'task:verify:' + uid.split(':', 1)[1]
        self.db.create_task(tid, 'USER_INFO_VERIFY', 'USER', f'Validate user-provided information: {text}', 950,
                            generated_reason='User information has high interaction priority but must be externally grounded.',
                            metadata={'user_claim_id': uid, 'claim_text': text})
        self.db.add_conversation('user', 'information', text, tid, {'user_claim_id': uid})
        self.db.add_task_event(tid, 'USER_INFORMATION_RECEIVED', text, {'epistemic_status': 'UNVERIFIED'})
        self.audit.write('user_information_received', user_claim_id=uid, task_id=tid, text=text,
                         control_priority=950, epistemic_confidence=0.20)
        self.wake()
        return uid, tid

    def submit_url(self, url: str, note: str = '') -> str:
        tid = 'task:url:' + hashlib.sha256((utcnow() + '|' + url).encode('utf-8')).hexdigest()[:18]
        self.db.create_task(tid, 'URL_INGEST', 'USER', f'Ingest and ground this user-selected web source: {url}', 925,
                            generated_reason='Explicit user-provided source URL.', metadata={'url': url, 'note': note})
        self.db.add_task_event(tid, 'URL_INGEST_CREATED', url)
        self.wake()
        return tid

    def set_idle_growth(self, enabled: bool) -> None:
        self.config.data['idle_growth_enabled'] = bool(enabled)
        self.config.save()
        self.audit.write('idle_growth_toggled', enabled=bool(enabled))
        self.wake()

    def set_external_user_active(self, active: bool) -> None:
        with self._state_lock:
            self._external_user_active = bool(active)
        self.audit.write('external_user_interaction', active=bool(active))
        self.wake()

    def begin_interaction_task(self, user_text: str, normalized_goal: str, route: str,
                               gate: dict, semantic: dict) -> str:
        """Register a synchronous user interaction as Organic Executive work."""
        tid = 'task:interaction:' + hashlib.sha256((utcnow() + '|' + normalized_goal).encode('utf-8')).hexdigest()[:18]
        self.db.create_task(
            tid,
            'INTERACTIVE_TASK',
            'SEMANTIC_INTERFACE',
            normalized_goal,
            1000,
            status='ACTIVE',
            generated_reason='Semantic Interface request authorized by the Interaction Gate.',
            metadata={
                'user_text': user_text,
                'route': route,
                'gate': gate,
                'semantic': semantic,
                'pipeline': 'semantic_gate_executive_active_weave_core_evidence_memory_retry',
            },
        )
        self.db.update_task(tid, started_at=utcnow(), attempts=1)
        self.db.add_task_event(tid, 'EXECUTIVE_ACCEPTED', normalized_goal, {'route': route, 'gate': gate})
        self.audit.write('executive_interaction_task_started', task_id=tid, route=route, goal=normalized_goal)
        self._set_current(tid)
        return tid

    def record_interaction_stage(self, task_id: str | None, stage: str, message: str = '',
                                 data: dict | None = None) -> None:
        if not task_id:
            return
        self.db.add_task_event(task_id, stage, message or stage, data or {})

    def complete_interaction_task(self, task_id: str | None, result: str, success: bool,
                                  metadata: dict | None = None) -> None:
        if not task_id:
            return
        status = 'COMPLETED' if success else 'PARTIAL'
        self.db.update_task(task_id, status=status, completed_at=utcnow(), result_text=result[:4000],
                            metadata_json=compact_json(metadata or {}))
        self.db.add_task_event(task_id, status, result[:1200], metadata or {})
        self.audit.write('executive_interaction_task_completed', task_id=task_id, status=status,
                         result_preview=result[:500])
        with self._state_lock:
            if self._current_task_id == task_id:
                self._current_task_id = None

    def request_growth_cycles(self, cycles: int) -> None:
        cycles = max(0, min(int(cycles), 1000))
        with self._state_lock:
            self._manual_growth_budget += cycles
        self.audit.write('manual_growth_cycles_requested', cycles=cycles)
        self.wake()

    def state(self) -> dict:
        with self._state_lock:
            current = self._current_task_id
            budget = self._manual_growth_budget
            idle_count = self._completed_idle_cycles
            processor_idle_count = self._completed_processor_growth_cycles
            external_user_active = self._external_user_active
        return {
            'running': bool(self._thread and self._thread.is_alive()),
            'current_task_id': current,
            'idle_growth_enabled': bool(self.config.get('idle_growth_enabled', False)),
            'manual_growth_budget': budget,
            'completed_idle_cycles': idle_count,
            'completed_processor_growth_cycles': processor_idle_count,
            'external_user_active': external_user_active,
            'executive': self.core.status(),
            'core': self.core.status(),
            'processor': self.processor.status() if self.processor else {'mode': 'unavailable'},
            'web': self.broker.provider_status(),
            'counts': self.db.counts(),
        }

    def _loop(self) -> None:
        idle_delay = max(2, int(self.config.get('idle_delay_seconds', 15)))
        last_idle_attempt = 0.0
        while not self._stop.is_set():
            try:
                task = self.db.next_task()
                if task:
                    self._run_task(dict(task))
                    continue

                should_idle = bool(self.config.get('idle_growth_enabled', False))
                with self._state_lock:
                    manual = self._manual_growth_budget > 0
                    external_user_active = self._external_user_active
                if external_user_active:
                    self._wake.wait(timeout=0.25)
                    self._wake.clear()
                    continue
                now = time.time()
                if (should_idle or manual) and now - last_idle_attempt >= idle_delay:
                    last_idle_attempt = now
                    if self._processor_growth_callback is not None:
                        processor_growth = self._processor_growth_callback() or {}
                        if processor_growth.get('attempted'):
                            with self._state_lock:
                                self._completed_processor_growth_cycles += 1
                            self.audit.write('processor_idle_growth_cycle', **processor_growth)
                            continue
                    created = self._create_idle_growth_task()
                    if created:
                        continue

                self._wake.wait(timeout=1.0)
                self._wake.clear()
            except Exception as exc:
                self.logger.exception('Growth loop error: %s', exc)
                self.audit.write('growth_loop_error', error=repr(exc))
                time.sleep(1.0)

    def _set_current(self, task_id: Optional[str]):
        with self._state_lock:
            self._current_task_id = task_id

    def _run_task(self, task: dict) -> None:
        tid = task['task_id']
        self._set_current(tid)
        self.db.update_task(tid, status='ACTIVE', started_at=task.get('started_at') or utcnow(), attempts=int(task.get('attempts') or 0) + 1)
        self.db.add_task_event(tid, 'STARTED', task['goal'], {'kind': task['kind'], 'priority': task['priority']})
        self.audit.write('task_started', task_id=tid, kind=task['kind'], priority=task['priority'], goal=task['goal'])
        try:
            kind = task['kind']
            if kind == 'USER_TASK':
                self._process_user_task(task)
            elif kind == 'USER_INFO_VERIFY':
                self._process_user_information(task)
            elif kind == 'URL_INGEST':
                self._process_url(task)
            elif kind in {'IDLE_GROWTH', 'PRECREATED_GROWTH'}:
                self._process_growth_task(task)
            elif kind == 'BLOCKING_GROWTH':
                self._process_blocking_growth(task)
            else:
                self._complete_task(tid, f'No handler for task kind {kind}.', status='FAILED')
        except Exception as exc:
            self.logger.exception('Task %s failed: %s', tid, exc)
            self.db.add_task_event(tid, 'FAILED', str(exc))
            self.db.update_task(tid, status='FAILED', completed_at=utcnow(), result_text=str(exc))
            self.audit.write('task_failed', task_id=tid, error=repr(exc))
        finally:
            self._set_current(None)

    def _higher_priority_pending(self, current_priority: int) -> bool:
        row = self.db.one("SELECT 1 FROM tasks WHERE status='PENDING' AND priority>? LIMIT 1", (current_priority,))
        return bool(row)

    def _complete_task(self, task_id: str, result: str, status: str = 'COMPLETED') -> None:
        self.db.update_task(task_id, status=status, completed_at=utcnow(), result_text=result)
        self.db.add_task_event(task_id, status, result[:1200])
        self.audit.write('task_completed', task_id=task_id, status=status, result_preview=result[:500])

    def _make_blocking_task(self, parent_task_id: str, goal: str, query: str, round_no: int) -> dict:
        tid = 'task:block:' + hashlib.sha256(f'{parent_task_id}|{round_no}|{query}'.encode('utf-8')).hexdigest()[:18]
        self.db.create_task(tid, 'BLOCKING_GROWTH', 'COGNITION', goal, 990, parent_task_id=parent_task_id,
                            generated_reason='The current user task could not be completed from existing grounded memory.',
                            metadata={'query': query, 'round': round_no})
        self.db.add_task_event(tid, 'CREATED', goal, {'query': query})
        row = self.db.task(tid)
        return dict(row)

    def _search_learn(self, task_id: str, query: str, reason: str, max_fetches: int = 2) -> list[EvidenceDocument]:
        self.db.add_task_event(task_id, 'QUERY_GENERATED', query, {'reason': reason})
        self.audit.write('self_directed_query', task_id=task_id, query=query, reason=reason)
        results = self.broker.search(query, limit=max(4, max_fetches + 1))
        self.db.add_task_event(task_id, 'SEARCH_RESULTS', f'{len(results)} results',
                               {'results': [r.__dict__ for r in results[:8]]})
        docs = []
        for r in results[:max_fetches]:
            try:
                doc = self.broker.fetch_result(r)
                docs.append(doc)
                ingest = self.memory.ingest(doc, reason=f'{task_id}: {reason}')
                self.db.add_task_event(task_id, 'EVIDENCE_INGESTED', doc.title, ingest.__dict__)
            except Exception as exc:
                self.logger.warning('Evidence fetch/ingest failed for %s: %s', r.url, exc)
                self.db.add_task_event(task_id, 'EVIDENCE_ERROR', f'{r.url}: {exc}')
        return docs

    def _process_blocking_growth(self, task: dict) -> None:
        meta = json.loads(task.get('metadata_json') or '{}')
        query = meta.get('query') or self.core.generate_query(task['goal'], [])
        docs = self._search_learn(task['task_id'], query, task['generated_reason'] or 'blocking knowledge gap', max_fetches=2)
        self._complete_task(task['task_id'], f'Blocking growth acquired {len(docs)} external source(s) for query: {query}')

    def _process_user_task(self, task: dict) -> None:
        tid, goal = task['task_id'], task['goal']
        max_rounds = int(self.config.get('task_max_learning_rounds', 4))
        limit = int(self.config.get('max_active_weave_claims', 18))
        final = None
        for round_no in range(max_rounds + 1):
            evidence = self.memory.retrieve(goal, limit=limit)
            self.db.add_task_event(tid, 'ACTIVE_WEAVE', f'{len(evidence)} grounded claims selected',
                                   {'claim_ids': [e.get('claim_id') for e in evidence],
                                    'scores': [e.get('retrieval_score') for e in evidence]})
            answer = self.core.answer(goal, evidence)
            decision = answer.get('decision') or {}
            confidence = float(answer.get('confidence', 0.0) or 0.0)
            final = answer
            self.db.add_task_event(tid, 'CORE_DECISION', str(decision.get('action') or 'UNKNOWN'), decision)
            self.audit.write('task_attempt', task_id=tid, round=round_no, evidence_count=len(evidence),
                             confidence=confidence, missing=answer.get('missing', []), core_decision=decision)
            if answer.get('answer') and confidence >= 0.62:
                self.core.learn_decision(tid, decision, 1.0, 'grounded_answer_completed',
                                         {'round': round_no, 'confidence': confidence})
                self.db.add_task_event(tid, 'CORE_LEARNING', 'Reinforced successful answer decision.',
                                       {'reward': 1.0, 'round': round_no})
                break
            if round_no >= max_rounds:
                self.core.learn_decision(tid, decision, -0.35, 'insufficient_evidence_at_round_limit',
                                         {'round': round_no, 'confidence': confidence})
                break
            context = [e.get('text', '') for e in evidence[:8]]
            query = self.core.generate_query(goal, context)
            child_goal = f'Acquire missing evidence needed to complete user task: {goal}'
            child = self._make_blocking_task(tid, child_goal, query, round_no + 1)
            before_sources = self.db.counts()['sources']
            before_claims = self.db.counts()['claims']
            self.db.update_task(child['task_id'], status='ACTIVE', started_at=utcnow(), attempts=1)
            self._process_blocking_growth(child)
            after_sources = self.db.counts()['sources']
            after_claims = self.db.counts()['claims']
            gained = (after_sources > before_sources) or (after_claims > before_claims)
            action = str(decision.get('action') or '')
            reward = 0.70 if action == 'SEARCH' and gained else (0.15 if gained else -0.25)
            self.core.learn_decision(tid, decision, reward, 'evidence_acquisition_result',
                                     {'round': round_no, 'new_sources': after_sources-before_sources,
                                      'new_claims': after_claims-before_claims})
            self.db.add_task_event(tid, 'CORE_LEARNING', 'Updated processing policy from evidence-acquisition result.',
                                   {'reward': reward, 'round': round_no, 'new_sources': after_sources-before_sources,
                                    'new_claims': after_claims-before_claims})

        final = final or {'answer': '', 'confidence': 0.0, 'missing': ['No result']}
        answer_text = str(final.get('answer') or '').strip()
        if not answer_text:
            missing = '; '.join(final.get('missing') or ['Insufficient grounded evidence.'])
            answer_text = f'I could not complete the task from grounded memory yet. Missing: {missing}'
            status = 'PARTIAL'
        else:
            status = 'COMPLETED' if float(final.get('confidence', 0)) >= 0.62 else 'PARTIAL'
        sources = final.get('sources') or []
        if sources:
            answer_text += '\n\nSources used from Living Memory:\n' + '\n'.join(f"- {x.get('title')}: {x.get('url')}" for x in sources[:6])
        self.db.add_conversation('assistant', 'answer', answer_text, tid,
                                 {'confidence': final.get('confidence'), 'missing': final.get('missing', []),
                                  'core_decision': final.get('decision') or {}})
        self._complete_task(tid, answer_text, status=status)
        if self.config.get('auto_review_user_tasks', True) and self._review_callback:
            try:
                path = self._review_callback('user_task')
                self.db.add_task_event(tid, 'REVIEW_EXPORTED', path)
            except Exception as exc:
                self.logger.warning('Auto review export failed: %s', exc)

    def _process_user_information(self, task: dict) -> None:
        tid = task['task_id']
        meta = json.loads(task.get('metadata_json') or '{}')
        user_claim_id = meta.get('user_claim_id')
        claim_text = meta.get('claim_text') or task['goal'].split(':', 1)[-1].strip()
        context = [e.get('text','') for e in self.memory.retrieve(claim_text, limit=8)]
        query = self.core.generate_query(claim_text, context)
        self._search_learn(tid, query, 'Validate user-provided factual information against external evidence.', max_fetches=3)
        evidence = [e for e in self.memory.retrieve(claim_text, limit=24) if e.get('source_kind') != 'USER_VALIDATED']
        judgment = self.core.judge_claim(claim_text, evidence)
        decision = judgment.get('decision') or {}
        st = judgment.get('status', 'UNRESOLVED')
        conf = float(judgment.get('confidence', 0.2) or 0.2)
        reason = str(judgment.get('reason', ''))
        self.db.add_task_event(tid, 'CORE_DECISION', str(decision.get('action') or 'UNKNOWN'), decision)
        verify_reward = 1.0 if st in {'SUPPORTED','CONTRADICTED'} else -0.20
        self.core.learn_decision(tid, decision, verify_reward, f'user_claim_{st.lower()}',
                                 {'confidence': conf, 'evidence_count': len(evidence)})
        self.db.add_task_event(tid, 'CORE_LEARNING', 'Updated verification policy from claim-validation outcome.',
                               {'reward': verify_reward, 'status': st, 'confidence': conf})
        if st == 'SUPPORTED':
            user_status = 'USER_VALIDATED'
            self.db.update_user_claim(user_claim_id, user_status, conf, reason,
                                      {'claim_ids': judgment.get('used_claim_ids', [])})
            self.memory.ingest_validated_user_claim(claim_text, user_claim_id, confidence=max(0.62, conf))
            msg = f'User information validated against external evidence and promoted to durable memory. Confidence={conf:.2f}. {reason}'
        elif st == 'CONTRADICTED':
            user_status = 'USER_CONTRADICTED'
            self.db.update_user_claim(user_claim_id, user_status, conf, reason,
                                      {'claim_ids': judgment.get('used_claim_ids', [])})
            msg = f'User information conflicts with retrieved evidence and was NOT promoted to trusted memory. Confidence={conf:.2f}. {reason}'
        else:
            user_status = 'USER_UNRESOLVED'
            self.db.update_user_claim(user_claim_id, user_status, conf, reason,
                                      {'claim_ids': judgment.get('used_claim_ids', [])})
            msg = f'User information remains unresolved and was NOT promoted to trusted memory. Confidence={conf:.2f}. {reason}'
        self.db.add_conversation('assistant', 'validation', msg, tid, {'status': user_status})
        self._complete_task(tid, msg)
        if self.config.get('auto_review_user_tasks', True) and self._review_callback:
            try:
                self._review_callback('user_information')
            except Exception as exc:
                self.logger.warning('Auto review export failed: %s', exc)

    def _process_url(self, task: dict) -> None:
        meta = json.loads(task.get('metadata_json') or '{}')
        url = meta.get('url')
        if not url:
            self._complete_task(task['task_id'], 'No URL supplied.', status='FAILED')
            return
        doc = self.broker.fetch_url(url)
        result = self.memory.ingest(doc, reason=f'Explicit user source: {meta.get("note", "")}')
        msg = f'Ingested {doc.title}: {result.claims_added} claims, {result.relations_added} relations.'
        self.db.add_conversation('assistant', 'system', msg, task['task_id'])
        self._complete_task(task['task_id'], msg)

    def _activate_precreated_if_applicable(self, target: dict, selection_trace: dict | None = None) -> Optional[str]:
        row = self.db.one("SELECT * FROM tasks WHERE kind='PRECREATED_GROWTH' AND status='DORMANT' ORDER BY created_at ASC LIMIT 1")
        if not row:
            return None
        tid = row['task_id']
        goal = f"{row['goal']} Current selected frontier concept: {target['label']}."
        meta = json.loads(row['metadata_json'] or '{}')
        meta['activated_target_label'] = target['label']
        meta['core_selection_trace'] = selection_trace or {}
        self.db.execute('UPDATE tasks SET status=\'PENDING\', goal=?, target_concept_id=?, updated_at=?, metadata_json=? WHERE task_id=?',
                        (goal, target['concept_id'], utcnow(), compact_json(meta), tid))
        self.db.add_task_event(tid, 'ACTIVATED', goal, {'frontier_score': target.get('frontier_score'),
                                                       'core_selection_trace': selection_trace or {}})
        return tid

    def _create_idle_growth_task(self) -> Optional[str]:
        frontier = self.memory.frontier(limit=20)
        if not frontier:
            self.audit.write('idle_growth_no_frontier', reason='Living Memory has no sufficiently grounded underdeveloped concept yet.')
            return None
        target, selection_trace = self.core.select_growth_target(frontier)
        if not target:
            self.audit.write('idle_growth_no_core_selection', reason='Processing Core did not select a frontier target.')
            return None
        pre = self._activate_precreated_if_applicable(target, selection_trace)
        if pre:
            with self._state_lock:
                if self._manual_growth_budget > 0:
                    self._manual_growth_budget -= 1
            return pre
        tid = 'task:idle:' + hashlib.sha256((utcnow() + '|' + target['concept_id']).encode('utf-8')).hexdigest()[:18]
        reason = (f"Organic Processing Core selected '{target['label']}' from the graph frontier using only structural "
                  f"features. mention_count={target['mention_count']}, degree={target['degree']}, "
                  f"frontier_score={target['frontier_score']}, core_score={selection_trace.get('score') if selection_trace else None}.")
        goal = f"Learn more about {target['label']} using external evidence, emphasizing what it is and how it connects to existing memory."
        self.db.create_task(tid, 'IDLE_GROWTH', 'COGNITION', goal, 100, target_concept_id=target['concept_id'], generated_reason=reason,
                            metadata={'frontier_snapshot': target, 'core_selection_trace': selection_trace or {}})
        self.db.add_task_event(tid, 'AUTONOMOUS_TASK_CREATED', goal, {'reason': reason, 'core_selection_trace': selection_trace or {}})
        self.audit.write('autonomous_growth_task_created', task_id=tid, target=target['label'], reason=reason,
                         core_selection_trace=selection_trace or {})
        with self._state_lock:
            if self._manual_growth_budget > 0:
                self._manual_growth_budget -= 1
        return tid

    def _process_growth_task(self, task: dict) -> None:
        tid = task['task_id']
        task_meta = json.loads(task.get('metadata_json') or '{}')
        target_id = task.get('target_concept_id')
        selection_trace = task_meta.get('core_selection_trace') or None
        if not target_id:
            frontier = self.memory.frontier(limit=10)
            target, selection_trace = self.core.select_growth_target(frontier)
            if not target:
                self._complete_task(tid, 'No graph frontier was available; task remains conceptually valid but had nothing to investigate.', status='PARTIAL')
                return
            target_id = target['concept_id']
            task_meta['core_selection_trace'] = selection_trace or {}
            self.db.execute('UPDATE tasks SET target_concept_id=?, metadata_json=? WHERE task_id=?',
                            (target_id, compact_json(task_meta), tid))
        concept = self.db.concept(target_id)
        if not concept:
            self._complete_task(tid, 'Target concept disappeared.', status='FAILED')
            return
        before_degree = self.db.concept_degree(target_id)
        before_claims = self.db.counts()['claims']
        known = [dict(r) for r in self.db.related_claims_for_concept(target_id, limit=10)]
        context = [r.get('text','') for r in known]
        query = self.core.generate_query(concept['label'], context)
        if self._higher_priority_pending(int(task['priority'])):
            self.db.update_task(tid, status='PENDING')
            self.db.add_task_event(tid, 'PREEMPTED', 'Higher-priority user work arrived before external search.')
            return
        docs = self._search_learn(tid, query, task.get('generated_reason') or 'idle graph growth',
                                  max_fetches=int(self.config.get('idle_max_source_fetches', 2)))
        after_degree = self.db.concept_degree(target_id)
        after_claims = self.db.counts()['claims']
        self.db.execute('INSERT INTO growth_history(concept_id,task_id,started_at,completed_at,before_degree,after_degree,result) VALUES(?,?,?,?,?,?,?)',
                        (target_id, tid, task.get('started_at') or utcnow(), utcnow(), before_degree, after_degree,
                         f'sources={len(docs)} query={query}'))
        delta_degree = after_degree - before_degree
        delta_claims = after_claims - before_claims
        reward = 1.0 if delta_degree > 0 else (0.45 if docs and delta_claims > 0 else -0.35)
        self.core.learn_growth_outcome(tid, selection_trace, reward, 'growth_cycle_result',
                                       {'sources': len(docs), 'delta_degree': delta_degree, 'delta_claims': delta_claims})
        self.db.add_task_event(tid, 'CORE_LEARNING', 'Updated curiosity/growth policy from growth outcome.',
                               {'reward': reward, 'delta_degree': delta_degree, 'delta_claims': delta_claims})
        msg = f"Autonomous growth for '{concept['label']}' used {len(docs)} source(s). Graph degree {before_degree} -> {after_degree}. Query: {query}"
        if not docs:
            msg += ' Empty evidence result entered per-concept retry cooldown.'
        self._complete_task(tid, msg, status='COMPLETED' if docs else 'PARTIAL')
        if not docs:
            self.db.add_task_event(
                tid,
                'GROWTH_RETRY_COOLDOWN',
                'Suppressed immediate reselection after an empty external-evidence run.',
                {'target_concept_id': target_id, 'query': query},
            )
            self.audit.write(
                'growth_retry_cooldown',
                task_id=tid,
                target_concept_id=target_id,
                query=query,
            )
        with self._state_lock:
            self._completed_idle_cycles += 1
            idle_count = self._completed_idle_cycles
        every = int(self.config.get('auto_review_idle_every', 5) or 0)
        if every > 0 and idle_count % every == 0 and self._review_callback:
            try:
                self._review_callback(f'idle_cycle_{idle_count}')
            except Exception as exc:
                self.logger.warning('Idle review export failed: %s', exc)
