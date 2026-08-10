#!/usr/bin/env python3
"""Pro-detection probe: what it caches, and — more importantly — what it does not.

The probe runs a real Pro command rather than `ix briefing --help`, because
Pro commands are always *registered*: without @ix/pro the CLI installs a stub
whose action prints `The 'briefing' command requires Ix Pro.` and exits 1.
`--help` is handled before any action runs, so it exits 0 on the stub exactly
as on the real command and reports Pro on every OSS install.

Running the real command makes the exit code meaningful, but it also makes
failure ambiguous: a stub exit and a backend hiccup both come back non-zero.
Since PRO_TTL_SECONDS is an hour, caching that ambiguity would let one blip
suppress every Pro feature for a Pro user until it expired. So only the stub
response is cacheable.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HOOKS_DIR = Path(__file__).resolve().parents[1] / ".codex" / "hooks"


def _load_common(cache_dir: Path):
    spec = importlib.util.spec_from_file_location("ix_common", HOOKS_DIR / "common.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.PRO_CACHE_PATH = cache_dir / "ix-pro.json"
    return module


def _completed(returncode: int, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(
        args=["ix", "briefing", "--format", "json"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


class ProDetectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache_dir = Path(self._tmp.name)
        self.common = _load_common(self.cache_dir)

    def _run_probe(self, result):
        with patch.object(self.common, "run_command", return_value=result):
            return self.common.ix_pro_available(None)

    def test_does_not_probe_with_help(self) -> None:
        seen: list[list[str]] = []

        def record(argv, **_kwargs):
            seen.append(argv)
            return _completed(0, stdout='{"revision": 42}')

        with patch.object(self.common, "run_command", side_effect=record):
            self.common.ix_pro_available(None)

        self.assertEqual([["ix", "briefing", "--format", "json"]], seen)

    def test_real_briefing_caches_pro_available(self) -> None:
        self.assertTrue(self._run_probe(_completed(0, stdout='{"revision": 42}')))
        cached = json.loads(self.common.PRO_CACHE_PATH.read_text())
        self.assertTrue(cached["ok"])

    def test_pro_stub_is_cached_as_unavailable(self) -> None:
        stub = _completed(1, stderr="The 'briefing' command requires Ix Pro.\n")
        self.assertFalse(self._run_probe(stub))
        cached = json.loads(self.common.PRO_CACHE_PATH.read_text())
        self.assertFalse(cached["ok"])

    def test_transient_failure_is_not_cached(self) -> None:
        # A backend hiccup / expired session — non-zero, but not the stub.
        blip = _completed(1, stderr="Error: connect ECONNREFUSED 127.0.0.1:8090\n")
        self.assertFalse(self._run_probe(blip))
        self.assertFalse(
            self.common.PRO_CACHE_PATH.exists(),
            "a transient failure must not be cached for PRO_TTL_SECONDS",
        )

    def test_unrunnable_ix_is_not_cached(self) -> None:
        self.assertFalse(self._run_probe(None))
        self.assertFalse(self.common.PRO_CACHE_PATH.exists())

    def test_pro_user_recovers_on_the_next_call_after_a_blip(self) -> None:
        blip = _completed(1, stderr="Error: connect ECONNREFUSED 127.0.0.1:8090\n")
        self.assertFalse(self._run_probe(blip))
        # Without the cache poisoned, the very next call sees the real answer.
        self.assertTrue(self._run_probe(_completed(0, stdout='{"revision": 42}')))


if __name__ == "__main__":
    unittest.main(verbosity=2)
