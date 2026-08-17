from __future__ import annotations

import datetime as dt
import json
import logging
import shutil
import tempfile
import zipfile
from pathlib import Path

from .config import AppConfig
from .db import MemoryDB
from .loggingx import AuditLog
from .memory import MemoryCompiler
from .util import sha256_file, utcnow


class ReviewExporter:
    def __init__(self, root: Path, platform_kind: str, config: AppConfig, db: MemoryDB,
                 memory: MemoryCompiler, logger: logging.Logger, audit: AuditLog,
                 trace_dir: Path | None = None):
        self.root = root
        self.platform_kind = platform_kind
        self.config = config
        self.db = db
        self.memory = memory
        self.logger = logger
        self.audit = audit
        self.trace_dir = trace_dir.expanduser().resolve() if trace_dir else None
        self.local_review_dir = root / 'review_packages'
        self.local_review_dir.mkdir(parents=True, exist_ok=True)

    def default_external_dir(self) -> Path:
        # Review packages intentionally stay inside the unzipped MVP folder.
        return self.local_review_dir

    def export(self, reason: str = 'manual') -> str:
        stamp = dt.datetime.now().strftime('%Y%m%d_%H%M%S')
        name = f'ORGANIC_AI_SEMANTIC_GATE_MVP_{self.platform_kind.upper()}_REVIEW_{stamp}.zip'
        out_dir = self.default_external_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        final_path = out_dir / name
        local_path = self.local_review_dir / name

        with tempfile.TemporaryDirectory(prefix='organic_review_') as td:
            stage = Path(td) / 'review'
            (stage / 'db').mkdir(parents=True)
            (stage / 'logs').mkdir(parents=True)
            (stage / 'reports').mkdir(parents=True)
            (stage / 'sources').mkdir(parents=True)
            (stage / 'core').mkdir(parents=True)
            (stage / 'processor').mkdir(parents=True)
            (stage / 'run_results').mkdir(parents=True)
            (stage / 'traces').mkdir(parents=True)

            db_copy = stage / 'db' / 'organic_memory.sqlite'
            self.db.backup_to(db_copy)

            manifest = {
                'exported_at': utcnow(),
                'reason': reason,
                'platform': self.platform_kind,
                'counts': self.db.counts(),
                'config': self.config.public_snapshot(),
                'database_sha256': sha256_file(db_copy),
                'source_count': self.db.counts().get('sources', 0),
                'core_note': 'Organic Executive policy and bounded Processor state are included separately for audit. Search API keys are deliberately excluded/redacted.',
                'core_state_bytes': sum(p.stat().st_size for p in (self.root / 'data' / 'core').rglob('*') if p.is_file()) if (self.root / 'data' / 'core').exists() else 0,
                'processor_state_bytes': sum(p.stat().st_size for p in (self.root / 'data' / 'processor').rglob('*') if p.is_file()) if (self.root / 'data' / 'processor').exists() else 0,
                'run_result_count': len(list((self.root / 'run_results').glob('*.json'))) if (self.root / 'run_results').exists() else 0,
                'trace_file_count': len(list(self.trace_dir.rglob('*.jsonl'))) if self.trace_dir and self.trace_dir.exists() else 0,
            }
            (stage / 'manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding='utf-8')

            # Human-reviewable exports.
            table_names = ['tasks','task_events','user_claims','sources','claims','concepts','lexical_anchors','relations','growth_history','conversation','core_learning_events','planner_outcomes','processing_episodes']
            for table in table_names:
                data = self.db.dump_table(table, limit=100000)
                (stage / 'reports' / f'{table}.json').write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')

            graph = self.memory.graph_summary(limit=100)
            (stage / 'reports' / 'graph_summary.json').write_text(json.dumps(graph, indent=2, ensure_ascii=False), encoding='utf-8')

            # Copy logs/audit if present.
            for p in (self.root / 'data' / 'logs').glob('*'):
                if p.is_file():
                    try:
                        shutil.copy2(p, stage / 'logs' / p.name)
                    except Exception:
                        pass

            route_cache = self.root / 'data' / 'route_cache.json'
            if route_cache.exists():
                try:
                    shutil.copy2(route_cache, stage / 'reports' / 'route_cache.json')
                except Exception:
                    pass

            # Copy the bounded Processing Core state so learning can be audited independently
            # from domain knowledge. The core directory must never contain source text.
            core_root = self.root / 'data' / 'core'
            if core_root.exists():
                for p in core_root.rglob('*'):
                    if p.is_file():
                        rel = p.relative_to(core_root)
                        dst = stage / 'core' / rel
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        try:
                            shutil.copy2(p, dst)
                        except Exception:
                            pass

            processor_root = self.root / 'data' / 'processor'
            if processor_root.exists():
                for p in processor_root.rglob('*'):
                    if p.is_file():
                        rel = p.relative_to(processor_root)
                        dst = stage / 'processor' / rel
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        try:
                            shutil.copy2(p, dst)
                        except Exception:
                            pass

            # Copy cached evidence to allow provenance review. Review archives never include private_config.json.
            for p in (self.root / 'data' / 'source_cache').glob('*.txt'):
                if p.is_file():
                    try:
                        shutil.copy2(p, stage / 'sources' / p.name)
                    except Exception:
                        pass

            for p in (self.root / 'run_results').glob('*.json'):
                if p.is_file():
                    try:
                        shutil.copy2(p, stage / 'run_results' / p.name)
                    except Exception:
                        pass

            if self.trace_dir and self.trace_dir.exists():
                for p in self.trace_dir.rglob('*.jsonl'):
                    if p.is_file():
                        rel = p.relative_to(self.trace_dir)
                        dst = stage / 'traces' / rel
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        try:
                            shutil.copy2(p, dst)
                        except Exception:
                            pass

            public_cfg = self.config.public_snapshot()
            (stage / 'config_redacted.json').write_text(json.dumps(public_cfg, indent=2, ensure_ascii=False), encoding='utf-8')

            temp_zip = Path(td) / name
            with zipfile.ZipFile(temp_zip, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
                for p in stage.rglob('*'):
                    if p.is_file():
                        zf.write(p, p.relative_to(stage))
            shutil.copy2(temp_zip, local_path)
            if final_path.resolve() != local_path.resolve():
                shutil.copy2(temp_zip, final_path)

        if not final_path.exists():
            raise RuntimeError(f'Review ZIP was not created at expected location: {final_path}')
        self.audit.write('review_exported', reason=reason, path=str(final_path), bytes=final_path.stat().st_size)
        self.logger.info('Review package exported: %s', final_path)
        return str(final_path)
