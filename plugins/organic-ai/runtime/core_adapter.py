"""Adapter boundary for the bounded Organic Processing Core.

No LLM fallback is allowed here.
"""

from __future__ import annotations

import importlib
import json
import os


class OrganicCoreAdapter:
    def __init__(self, module_name: str = "organic_core"):
        self.module_name = module_name

    def _load(self):
        try:
            return importlib.import_module(self.module_name)
        except Exception:
            return None

    def status(self):
        mod = self._load()
        return {
            "available": mod is not None,
            "module": self.module_name,
            "llm_fallback": False,
        }

    def reason(self, problem: str, context: list[dict] | None = None):
        mod = self._load()
        if mod is None:
            return {
                "status": "CORE_UNAVAILABLE",
                "answer": None,
                "missing": ["Organic Processing Core module is not installed in this scaffold yet."],
                "llm_fallback_used": False,
            }

        fn = getattr(mod, "reason", None)
        if not callable(fn):
            return {
                "status": "CORE_INTERFACE_ERROR",
                "answer": None,
                "missing": [f"{self.module_name}.reason(problem, context) is not callable."],
                "llm_fallback_used": False,
            }

        result = fn(problem=problem, context=context or [])
        if isinstance(result, dict):
            result = dict(result)
            result["llm_fallback_used"] = False
            return result
        return {
            "status": "OK",
            "answer": result,
            "llm_fallback_used": False,
        }
