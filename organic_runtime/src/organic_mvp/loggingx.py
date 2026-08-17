from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import Lock
from typing import Any

from .util import utcnow


class AuditLog:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()

    def write(self, event: str, **payload: Any) -> None:
        row = {'time': utcnow(), 'event': event, **payload}
        line = json.dumps(row, ensure_ascii=False, sort_keys=True)
        with self._lock:
            with self.path.open('a', encoding='utf-8') as f:
                f.write(line + '\n')


def setup_logging(log_path: Path, verbose: bool = True) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger('organic_mvp')
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    fmt = logging.Formatter('%(asctime)s [%(levelname)s] %(threadName)s %(message)s')
    fh = RotatingFileHandler(log_path, maxBytes=5_000_000, backupCount=4, encoding='utf-8')
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    if verbose:
        sh = logging.StreamHandler()
        sh.setLevel(logging.DEBUG)
        sh.setFormatter(fmt)
        logger.addHandler(sh)
    return logger
