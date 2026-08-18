from __future__ import annotations

import collections
import datetime as dt
import hashlib
import logging
import math
import re
from dataclasses import dataclass

from .core import BaseCore
from .db import MemoryDB
from .evidence import EvidenceDocument
from .loggingx import AuditLog
from .util import (
    GENERIC_RELATION_VERBS,
    STOPWORDS,
    content_words,
    norm_space,
    normalize_label,
    sentence_split,
    stable_uid,
)

ALLOWED_GROWTH_KINDS = {
    "ENTITY",
    "CONCEPT",
    "PROCESS",
    "EVENT",
    "PROCEDURE",
    "TOPIC",
}

HARD_REJECT_GROWTH_LABELS = {
    "answer",
    "available",
    "basic",
    "common",
    "may",
    "one",
    "two",
    "part",
    "according",
    "critical",
    "current",
    "details",
    "evidence",
    "effective",
    "every output",
    "existing",
    "external",
    "false",
    "generated",
    "general",
    "handled",
    "including",
    "information",
    "input",
    "important",
    "limited",
    "main",
    "many",
    "most",
    "output",
    "particularly effective",
    "phrase",
    "press",
    "question",
    "research",
    "result",
    "sample",
    "selected",
    "since",
    "simple",
    "specific",
    "standard",
    "systematic",
    "time",
    "various",
    "even though",
    "new",
    "true",
    "using",
}

GROWTH_FAILURE_BACKOFF_BASE_SECONDS = 5 * 60
GROWTH_FAILURE_BACKOFF_MAX_SECONDS = 6 * 60 * 60
GROWTH_SUCCESS_COOLDOWN_SECONDS = 6 * 60 * 60


def growth_retry_delay_seconds(failed_attempts: int) -> int:
    failures = max(0, int(failed_attempts))
    if failures <= 0:
        return 0
    exponent = min(failures - 1, 12)
    return min(
        GROWTH_FAILURE_BACKOFF_MAX_SECONDS,
        GROWTH_FAILURE_BACKOFF_BASE_SECONDS * (2**exponent),
    )


def _growth_retry_remaining_seconds(
    last_growth_at: str | None,
    failed_attempts: int,
    *,
    now: dt.datetime,
) -> int:
    delay = growth_retry_delay_seconds(failed_attempts)
    return _growth_cooldown_remaining_seconds(last_growth_at, delay, now=now)


def _growth_cooldown_remaining_seconds(
    last_growth_at: str | None,
    delay: int,
    *,
    now: dt.datetime,
) -> int:
    if delay <= 0 or not last_growth_at:
        return 0
    try:
        attempted_at = dt.datetime.fromisoformat(str(last_growth_at).replace("Z", "+00:00"))
    except ValueError:
        return delay
    if attempted_at.tzinfo is None:
        attempted_at = attempted_at.replace(tzinfo=dt.timezone.utc)
    elapsed = max(0.0, (now - attempted_at.astimezone(dt.timezone.utc)).total_seconds())
    return max(0, math.ceil(delay - elapsed))


@dataclass
class IngestResult:
    source_id: str
    sentences: int = 0
    claims_added: int = 0
    concepts_touched: int = 0
    relations_added: int = 0
    core_proposals: int = 0
    core_proposals_accepted: int = 0
    core_proposals_rejected: int = 0


class MemoryCompiler:
    def __init__(self, db: MemoryDB, core: BaseCore, logger: logging.Logger, audit: AuditLog):
        self.db, self.core, self.logger, self.audit = db, core, logger, audit

    def _claim_id(self, source_id: str, idx: int, sentence: str) -> str:
        h = hashlib.sha256(f'{source_id}|{idx}|{sentence}'.encode('utf-8')).hexdigest()[:20]
        return f'claim:{h}'

    def _concept_candidates(self, sentence: str) -> list[str]:
        # Programmatic candidate discovery from raw prose. It does not use a domain dictionary.
        toks = re.findall(r"[A-Za-z][A-Za-z'\-]{1,}", sentence)
        lower = [t.lower() for t in toks]
        candidates: list[str] = []

        # Capitalized terms and sequences, useful for names/terms.
        i = 0
        while i < len(toks):
            if toks[i][0].isupper() and lower[i] not in STOPWORDS:
                j = i + 1
                while j < len(toks) and toks[j][0].isupper() and j - i < 4:
                    j += 1
                candidates.append(' '.join(toks[i:j]))
                i = j
            else:
                i += 1

        # Content unigrams/bigrams/trigrams. Promotion is conservative; the lexical
        # anchors can still help retrieval even when not heavily connected.
        cw = [w for w in lower if len(w) >= 3 and w not in STOPWORDS]
        for n in (1, 2, 3):
            for i in range(len(cw) - n + 1):
                phrase = ' '.join(cw[i:i+n])
                if any(x in GENERIC_RELATION_VERBS for x in phrase.split()):
                    continue
                candidates.append(phrase)

        # De-duplicate while preserving order and avoid ultra-long/noisy phrases.
        out, seen = [], set()
        for c in candidates:
            n = normalize_label(c)
            if not n or n in seen or len(n) > 70:
                continue
            seen.add(n)
            out.append(c)
        return out[:80]

    def _ensure_concept(self, label: str, source_id: str | None = None, confidence: float = 0.5, durable: bool = False) -> str:
        norm = normalize_label(label)
        if not norm:
            raise ValueError('empty concept')
        # Lightweight canonicalization for common plural variants, preserving alias.
        canonical = norm
        if len(norm) > 4 and norm.endswith('ies'):
            canonical = norm[:-3] + 'y'
        elif len(norm) > 4 and norm.endswith('s') and not norm.endswith('ss'):
            existing_singular = self.db.find_concept(norm[:-1])
            if existing_singular:
                canonical = norm[:-1]
        existing = self.db.find_concept(canonical)
        cid = existing['concept_id'] if existing else stable_uid('organic', canonical)
        cid = self.db.upsert_concept(cid, label if existing is None else existing['label'], canonical,
                                     kind=self._infer_concept_kind(label),
                                     status='GROUNDED' if durable else 'PROVISIONAL', confidence=confidence)
        self.db.add_alias(label, norm, cid, confidence=confidence, source_id=source_id)
        if durable:
            self.db.execute("UPDATE concepts SET status='GROUNDED', confidence=MAX(confidence,?) WHERE concept_id=?", (confidence, cid))
        return cid

    @staticmethod
    def _infer_concept_kind(label: str) -> str:
        clean = norm_space(label)
        low = normalize_label(clean)
        if not clean:
            return "CONCEPT"
        if clean[:1].isupper() and len(clean.split()) <= 4:
            return "ENTITY"
        if low.endswith(("ing", "tion", "sion", "ment")):
            return "PROCESS"
        if len(clean.split()) >= 2:
            return "TOPIC"
        return "CONCEPT"

    def _reference_relations(self, sentence: str) -> list[dict]:
        # Generic surface relation extraction. These patterns contain no domain facts.
        patterns = [
            r'^(?P<s>[A-Z][^.;:]{1,80}?)\s+(?P<r>is|are|was|were|becomes?|became)\s+(?P<o>[^.;]{2,120})',
            r'^(?P<s>[^.;:]{2,80}?)\s+(?P<r>contains?|includes?|has|have|uses?|produces?|releases?|regulates?|absorbs?|causes?|provides?|requires?|prevents?|connects?)\s+(?P<o>[^.;]{2,120})',
            r'(?P<s>[^.;:]{2,80}?)\s+(?P<r>open|opens|close|closes|lose|loses|draw|draws|carry|carries|enter|enters|escape|escapes|diffuse|diffuses|convert|converts|split|splits|supply|supplies)\s+(?P<o>[^.;]{2,120})',
        ]
        out = []
        clean = norm_space(sentence)
        for p in patterns:
            m = re.search(p, clean, flags=re.I)
            if not m:
                continue
            s = norm_space(m.group('s')).strip(',- ')
            r = normalize_label(m.group('r'))
            o = norm_space(m.group('o')).strip(',- ')
            # Trim clauses so the relation is useful but stays grounded.
            o = re.split(r'\b(?:when|where|while|because|although|however|which|that)\b', o, maxsplit=1, flags=re.I)[0].strip(' ,')
            s = re.sub(r'^(?:the|a|an)\s+', '', s, flags=re.I)
            o = re.sub(r'^(?:the|a|an)\s+', '', o, flags=re.I)
            if 2 <= len(s) <= 90 and 2 <= len(o) <= 120:
                out.append({'subject': s, 'relation': r, 'object': o, 'evidence_quote': clean, 'confidence': 0.58, 'extractor': 'reference'})
            break
        return out

    def _validate_proposal(self, p: dict, source_text: str) -> tuple[bool, str]:
        s = norm_space(str(p.get('subject', '')))
        r = norm_space(str(p.get('relation', '')))
        o = norm_space(str(p.get('object', '')))
        quote = norm_space(str(p.get('evidence_quote', '')))
        if not s or not r or not o or len(quote) < 15:
            return False, 'missing subject/relation/object/evidence_quote'
        src_norm = norm_space(source_text)
        if quote not in src_norm:
            return False, 'evidence quote is not an exact normalized substring of source'
        qlow = quote.lower()
        if not any(w in qlow for w in content_words(s)[:3]):
            return False, 'subject is not grounded in evidence quote'
        if not any(w in qlow for w in content_words(o)[:3]):
            return False, 'object is not grounded in evidence quote'
        return True, 'grounded'

    def ingest(
        self,
        doc: EvidenceDocument,
        reason: str = '',
        max_sentences: int | None = None,
    ) -> IngestResult:
        result = IngestResult(source_id=doc.source_id)
        text = norm_space(doc.text)
        configured_limit = int(
            getattr(self.core, 'config', {}).get('max_sentences_per_source', 350)
            if hasattr(getattr(self.core, 'config', None), 'get')
            else 350
        )
        sentence_limit = configured_limit
        if max_sentences is not None:
            sentence_limit = max(1, min(configured_limit, int(max_sentences)))
        sentences = sentence_split(text)[:sentence_limit]
        processed_text = ' '.join(sentences)
        result.sentences = len(sentences)
        self.audit.write('memory_ingest_start', source_id=doc.source_id, title=doc.title, reason=reason, sentences=len(sentences))

        # Frequency map is derived from this raw source only. It helps decide which
        # lexical candidates deserve durable graph identity.
        freq = collections.Counter()
        per_sentence_candidates: list[list[str]] = []
        for sent in sentences:
            cs = self._concept_candidates(sent)
            per_sentence_candidates.append(cs)
            for c in {normalize_label(x) for x in cs}:
                freq[c] += 1

        lexical_anchors: list[tuple[str, str | None, str, str]] = []
        for idx, sent in enumerate(sentences):
            claim_id = self._claim_id(doc.source_id, idx, sent)
            before = self.db.one('SELECT 1 FROM claims WHERE claim_id=?', (claim_id,))
            self.db.add_claim(claim_id, doc.source_id, 'WEB_EVIDENCE', idx, sent, sent, doc.trust, 'GROUNDED',
                              {'reason': reason, 'provider': doc.provider})
            if not before:
                result.claims_added += 1

            for surface in per_sentence_candidates[idx]:
                norm = normalize_label(surface)
                # Weak one-off lexical material stays in the disk-backed anchor index.
                # Only recurring source concepts are promoted here; validated relation
                # extraction can also promote a one-off subject/object when evidence warrants it.
                durable = freq[norm] >= 2
                try:
                    if not durable:
                        lexical_anchors.append((claim_id, doc.source_id, surface, norm))
                        continue
                    cid = self._ensure_concept(surface, doc.source_id, confidence=min(0.78, 0.42 + freq[norm]*0.06), durable=True)
                    exists = self.db.one('SELECT 1 FROM mentions WHERE claim_id=? AND concept_id=?', (claim_id, cid))
                    if not exists:
                        self.db.add_mention(claim_id, cid, surface)
                        result.concepts_touched += 1
                except Exception:
                    continue

            for p in self._reference_relations(sent):
                self._commit_relation(p, claim_id, doc, result)

        self.db.add_lexical_anchors(lexical_anchors)

        # Optional Processing Core proposals never bypass programmatic evidence validation.
        proposals = self.core.extract_proposals(processed_text, doc.title)
        result.core_proposals = len(proposals)
        for p in proposals:
            ok, why = self._validate_proposal(p, processed_text)
            if not ok:
                result.core_proposals_rejected += 1
                self.audit.write('memory_proposal_rejected', source_id=doc.source_id, reason=why,
                                 proposal={k: p.get(k) for k in ('subject','relation','object','evidence_quote')})
                continue
            quote = norm_space(str(p.get('evidence_quote')))
            idx = next((i for i, s in enumerate(sentences) if quote in norm_space(s) or norm_space(s) in quote), 0)
            claim_id = self._claim_id(doc.source_id, idx, sentences[idx] if sentences else quote)
            if not self.db.one('SELECT 1 FROM claims WHERE claim_id=?', (claim_id,)):
                self.db.add_claim(claim_id, doc.source_id, 'WEB_EVIDENCE', idx, quote, quote, doc.trust, 'GROUNDED', {'provider': doc.provider})
            p = dict(p)
            p['extractor'] = 'processing_core'
            self._commit_relation(p, claim_id, doc, result)
            result.core_proposals_accepted += 1

        self.audit.write('memory_ingest_complete', **result.__dict__)
        self.logger.info('Ingested source %s: claims+%d concepts_touched=%d relations+%d core_proposals=%d/%d',
                         doc.title, result.claims_added, result.concepts_touched, result.relations_added,
                         result.core_proposals_accepted, result.core_proposals)
        return result

    def _commit_relation(self, p: dict, claim_id: str, doc: EvidenceDocument, result: IngestResult) -> None:
        s = norm_space(str(p.get('subject', '')))
        r = normalize_label(str(p.get('relation', '')))
        o = norm_space(str(p.get('object', '')))
        if not s or not r or not o:
            return
        sid = self._ensure_concept(s, doc.source_id, confidence=0.72, durable=True)
        oid = self._ensure_concept(o, doc.source_id, confidence=0.68, durable=True)
        raw_conf = float(p.get('confidence', 0.65) or 0.65)
        conf = max(0.30, min(0.95, raw_conf * 0.65 + doc.trust * 0.35))
        rid_hash = hashlib.sha256(f'{claim_id}|{sid}|{r}|{oid}'.encode('utf-8')).hexdigest()[:20]
        rid = f'relation:{rid_hash}'
        before = self.db.one('SELECT 1 FROM relations WHERE relation_id=?', (rid,))
        self.db.add_relation(rid, claim_id, doc.source_id, sid, r, oid, o, conf, 'GROUNDED',
                             {'extractor': p.get('extractor', 'unknown'), 'evidence_quote': p.get('evidence_quote', '')})
        if not before:
            result.relations_added += 1


    def ingest_validated_user_claim(self, text: str, user_claim_id: str, confidence: float = 0.75) -> dict:
        """Promote a user statement only after an external verification task supports it."""
        sent = norm_space(text)
        claim_id = f"claim:user:{hashlib.sha256((user_claim_id+'|'+sent).encode('utf-8')).hexdigest()[:20]}"
        self.db.add_claim(claim_id, None, 'USER_VALIDATED', None, sent, sent, confidence, 'USER_VALIDATED',
                          {'user_claim_id': user_claim_id, 'externally_verified': True})
        touched = 0
        for surface in self._concept_candidates(sent):
            try:
                cid = self._ensure_concept(surface, None, confidence=0.62, durable=len(surface.split()) >= 2)
                if not self.db.one('SELECT 1 FROM mentions WHERE claim_id=? AND concept_id=?', (claim_id, cid)):
                    self.db.add_mention(claim_id, cid, surface)
                    touched += 1
            except Exception:
                pass
        relations = 0
        for prop in self._reference_relations(sent):
            s = norm_space(str(prop.get('subject', '')))
            r = normalize_label(str(prop.get('relation', '')))
            o = norm_space(str(prop.get('object', '')))
            if not s or not r or not o:
                continue
            sid = self._ensure_concept(s, None, confidence=0.72, durable=True)
            oid = self._ensure_concept(o, None, confidence=0.68, durable=True)
            rid = 'relation:user:' + hashlib.sha256(f'{claim_id}|{sid}|{r}|{oid}'.encode('utf-8')).hexdigest()[:20]
            before = self.db.one('SELECT 1 FROM relations WHERE relation_id=?', (rid,))
            self.db.add_relation(rid, claim_id, None, sid, r, oid, o, confidence, 'USER_VALIDATED',
                                 {'user_claim_id': user_claim_id, 'externally_verified': True})
            if not before:
                relations += 1
        self.audit.write('validated_user_memory_committed', user_claim_id=user_claim_id, claim_id=claim_id,
                         concepts_touched=touched, relations_added=relations)
        return {'claim_id': claim_id, 'concepts_touched': touched, 'relations_added': relations}

    def retrieve(self, query: str, limit: int = 18) -> list[dict]:
        terms = list(dict.fromkeys(content_words(query)))[:8]
        rows = self.db.claims_for_terms(terms, limit=max(limit*3, 24))
        qset = set(terms)
        ranked = []
        for row in rows:
            text = row['text']
            tset = set(content_words(text))
            term_recall = len(qset & tset) / max(1, len(qset))
            specificity = len(qset & tset) / max(1, math.sqrt(len(tset)))
            score = term_recall * 0.72 + min(0.25, specificity * 0.12) + float(row['confidence']) * 0.08
            d = dict(row)
            d['retrieval_score'] = round(score, 4)
            rel_rows = self.db.query(
                """SELECT r.predicate, r.confidence relation_confidence, cs.label subject_label, co.label object_label,
                          r.object_text, r.relation_id
                   FROM relations r JOIN concepts cs ON cs.concept_id=r.subject_id
                   LEFT JOIN concepts co ON co.concept_id=r.object_id
                   WHERE r.claim_id=? ORDER BY r.confidence DESC""",
                (row['claim_id'],)
            )
            d['relations'] = [dict(x) for x in rel_rows]
            ranked.append(d)
        ranked.sort(key=lambda x: (x['retrieval_score'], x.get('confidence', 0)), reverse=True)
        return ranked[:limit]

    def retrieve_from_sources(
        self,
        query: str,
        source_ids: set[str],
        limit: int = 18,
    ) -> list[dict]:
        """Rank validated claims from a just-completed, source-bounded growth pass."""
        bounded_ids = sorted({str(source_id) for source_id in source_ids if source_id})[:32]
        if not bounded_ids:
            return []

        placeholders = ','.join('?' for _ in bounded_ids)
        rows = self.db.query(
            f'''SELECT c.*, s.title AS source_title, s.url AS source_url,
                       s.provider AS source_provider
                FROM claims c LEFT JOIN sources s ON s.source_id=c.source_id
                WHERE c.status IN ('GROUNDED','USER_VALIDATED')
                  AND c.source_id IN ({placeholders})
                ORDER BY c.created_at DESC''',
            bounded_ids,
        )
        action_terms = {
            'check', 'find', 'identify', 'look', 'lookup', 'report', 'research',
            'summary', 'summarize', 'verify',
        }
        terms = list(dict.fromkeys(content_words(query)))
        focused_terms = [term for term in terms if term not in action_terms] or terms
        qset = set(focused_terms[:12])
        asks_for_version = bool({'release', 'version'} & qset)
        asks_for_current = bool({'current', 'latest', 'newest', 'recent', 'stable'} & qset)

        ranked: list[dict] = []
        for row in rows:
            text = str(row['text'] or '')
            title = str(row['source_title'] or '')
            text_terms = set(content_words(text))
            title_terms = set(content_words(title))
            overlap = len(qset & text_terms)
            term_recall = overlap / max(1, len(qset))
            specificity = overlap / max(1, math.sqrt(len(text_terms)))
            title_recall = len(qset & title_terms) / max(1, len(qset))
            score = (
                term_recall * 0.55
                + min(0.18, specificity * 0.10)
                + title_recall * 0.10
                + float(row['confidence']) * 0.07
            )
            has_version = bool(re.search(r'\b\d+(?:\.\d+){1,3}\b', text))
            has_current_marker = bool(
                re.search(
                    r'\b(?:release date|released|maintenance release|stable release|latest release)\b',
                    text,
                    flags=re.IGNORECASE,
                )
                or re.search(r'\b20\d{2}\b', text)
            )
            if asks_for_version and has_version:
                score += 0.15
            if asks_for_current and has_current_marker:
                score += 0.08
            if re.search(r'\b(?:fallback|skip to content|privacy notice)\b', text, re.IGNORECASE):
                score -= 0.12
            if len(text) > 2400:
                score -= 0.12

            item = dict(row)
            item['retrieval_score'] = round(max(0.0, min(1.0, score)), 4)
            relation_rows = self.db.query(
                '''SELECT r.predicate, r.confidence relation_confidence,
                          cs.label subject_label, co.label object_label,
                          r.object_text, r.relation_id
                   FROM relations r JOIN concepts cs ON cs.concept_id=r.subject_id
                   LEFT JOIN concepts co ON co.concept_id=r.object_id
                   WHERE r.claim_id=? ORDER BY r.confidence DESC''',
                (row['claim_id'],),
            )
            item['relations'] = [dict(relation) for relation in relation_rows]
            ranked.append(item)

        ranked.sort(
            key=lambda item: (item['retrieval_score'], item.get('confidence', 0)),
            reverse=True,
        )
        return ranked[:limit]

    def frontier(self, limit: int = 25) -> list[dict]:
        rows = self.db.list_frontier_candidates(limit=500)
        scored = []
        now = dt.datetime.now(dt.timezone.utc)
        for r in rows:
            label = r['label']
            normalized = normalize_label(label)
            if len(label) < 3 or normalized in STOPWORDS or normalized in HARD_REJECT_GROWTH_LABELS:
                continue
            degree = int(r['degree'] or 0)
            mentions = int(r['mention_count'] or 0)
            kind = str(r['kind'] or self._infer_concept_kind(label)).upper()
            if kind not in ALLOWED_GROWTH_KINDS:
                continue
            if degree <= 0:
                continue
            source_count = int(r['relation_source_count'] or 0)
            if len(label.split()) == 1 and kind != "ENTITY" and source_count < 2:
                continue
            failed_attempts = int(r["failed_growth_attempts"] or 0)
            if failed_attempts == 0:
                success_remaining = _growth_cooldown_remaining_seconds(
                    r["last_growth_at"],
                    GROWTH_SUCCESS_COOLDOWN_SECONDS,
                    now=now,
                )
                if success_remaining > 0:
                    continue
            retry_remaining = _growth_retry_remaining_seconds(
                r["last_growth_at"],
                failed_attempts,
                now=now,
            )
            if retry_remaining > 0:
                continue
            # Frontier interest favors concepts that recur but have little graph structure.
            knowledge_gap = 1.0 / (1.0 + degree)
            recurrence = min(1.0, math.log1p(mentions) / math.log(8))
            status_bonus = 0.35 if r['status'] != 'GROUNDED' else 0.0
            # Single common tokens are noisier than compact multiword concepts.
            phrase_bonus = 0.12 if len(label.split()) >= 2 else 0.0
            score = knowledge_gap * 0.48 + recurrence * 0.36 + status_bonus + phrase_bonus
            if mentions < 2 and degree == 0:
                score *= 0.45
            d = dict(r)
            d['kind'] = kind
            d['relation_source_count'] = source_count
            d['frontier_score'] = round(score, 4)
            d['growth_retry_failures'] = failed_attempts
            d['growth_retry_remaining_seconds'] = retry_remaining
            scored.append(d)
        scored.sort(key=lambda x: (x['frontier_score'], x['mention_count']), reverse=True)
        return scored[:limit]

    def graph_summary(self, limit: int = 20) -> dict:
        return {
            'counts': self.db.counts(),
            'frontier': self.frontier(limit),
            'recent_relations': [dict(r) for r in self.db.query(
                '''SELECT r.*, cs.label subject_label, co.label object_label, s.title source_title, s.url source_url
                   FROM relations r JOIN concepts cs ON cs.concept_id=r.subject_id
                   LEFT JOIN concepts co ON co.concept_id=r.object_id LEFT JOIN sources s ON s.source_id=r.source_id
                   ORDER BY r.created_at DESC LIMIT ?''', (limit,))],
            'recent_claims': [dict(r) for r in self.db.query(
                '''SELECT c.*, s.title source_title, s.url source_url FROM claims c LEFT JOIN sources s ON s.source_id=c.source_id
                   ORDER BY c.created_at DESC LIMIT ?''', (limit,))],
        }
