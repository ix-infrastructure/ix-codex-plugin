#!/usr/bin/env python3
"""Pro-detection probe: what it caches, and — more importantly — what it does not.

The probe runs a real Pro command rather than `ix briefing --help`, because
Pro commands are always *registered*: without @ix/pro the CLI installs a stub
whose action prints `The 'briefing' command requires Ix Pro.` and exits 1.
`--help` is handled before any action runs, so it exits 0 on the stub exactly
as on the real command and reports Pro on every OSS install.

Running the real command makes the exit code meaningful, but it also makes
failure ambiguous: a stub exit and a backend hiccup both come back non-zero.
Since PRO_TTL_SECONDS is an hour, caching that ambiguity as the definitive
answer would let one blip suppress every Pro feature for a Pro user until it
expired. So only a definitive answer gets the long TTL — but a non-verdict is
still recorded on a short backoff, because the probe is now the most expensive
command in the CLI and re-running it on every prompt is its own failure.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
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


class FakeIx:
    """A stand-in for the CLI that answers based on the argv it is handed.

    This is the part that has to be argv-sensitive. Handing `run_command` a fixed
    result regardless of what it was asked to run makes the probe's choice of
    command unobservable — a probe using `--help` would receive the stub's exit 1
    anyway, and a test written for this bug would pass against the bug. The two
    behaviours below are the whole reason the fix exists:

      * `--help` succeeds on the stub *and* on the real command (commander
        resolves it before any action runs), so it discriminates nothing.
      * the real invocation exits 1 with the sentinel on OSS, 0 on Pro.
    """

    HELP_TEXT = "Usage: ix briefing [options]\n\nOptions:\n  -h, --help\n"
    STUB_MESSAGE = "The 'briefing' command requires Ix Pro.\n"

    def __init__(self, pro: bool) -> None:
        self.pro = pro
        self.calls: list[list[str]] = []

    def __call__(self, argv, **_kwargs):
        self.calls.append(list(argv))
        if "--help" in argv or "-h" in argv:
            return _completed(0, stdout=self.HELP_TEXT)
        if self.pro:
            # `revision` is present on every Pro briefing and is frequently null,
            # so it is not a Pro signal. The exit code is.
            return _completed(0, stdout='{"revision": null, "plans": []}')
        return _completed(1, stderr=self.STUB_MESSAGE)


class ProDetectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache_dir = Path(self._tmp.name)
        self.common = _load_common(self.cache_dir)

    def _run_probe(self, result):
        with patch.object(self.common, "run_command", return_value=result):
            return self.common.ix_pro_available(None)

    def _cached(self) -> dict:
        return json.loads(self.common.PRO_CACHE_PATH.read_text())

    def _age_cache(self, seconds: float) -> None:
        """Backdate the cache rather than patching the clock."""
        data = self._cached()
        data["timestamp"] = data["timestamp"] - seconds
        self.common.PRO_CACHE_PATH.write_text(json.dumps(data))

    # ── the bug this exists for ───────────────────────────────────────────────
    # Both of these are driven through FakeIx, so they fail if the probe goes
    # back to `--help`: the fake answers 0 to it whether or not Pro is present.

    def test_oss_install_is_not_reported_as_pro(self) -> None:
        fake = FakeIx(pro=False)
        with patch.object(self.common, "run_command", side_effect=fake):
            self.assertFalse(self.common.ix_pro_available(None))
        self.assertFalse(self._cached()["ok"])

    def test_pro_install_is_reported_as_pro(self) -> None:
        fake = FakeIx(pro=True)
        with patch.object(self.common, "run_command", side_effect=fake):
            self.assertTrue(self.common.ix_pro_available(None))
        self.assertTrue(self._cached()["ok"])

    def test_does_not_probe_with_help(self) -> None:
        fake = FakeIx(pro=False)
        with patch.object(self.common, "run_command", side_effect=fake):
            self.common.ix_pro_available(None)
        self.assertEqual([["ix", "briefing", "--format", "json"]], fake.calls)

    # ── what is cacheable ─────────────────────────────────────────────────────

    def test_pro_stub_is_cached_as_definitive(self) -> None:
        stub = _completed(1, stderr="The 'briefing' command requires Ix Pro.\n")
        self.assertFalse(self._run_probe(stub))
        cached = self._cached()
        self.assertFalse(cached["ok"])
        self.assertNotIn("tentative", cached)

    def test_real_briefing_caches_pro_available(self) -> None:
        self.assertTrue(self._run_probe(_completed(0, stdout='{"revision": 42}')))
        cached = self._cached()
        self.assertTrue(cached["ok"])
        self.assertNotIn("tentative", cached)

    def test_transient_failure_is_only_cached_tentatively(self) -> None:
        # A backend hiccup / expired session — non-zero, but not the stub.
        blip = _completed(1, stderr="Error: connect ECONNREFUSED 127.0.0.1:8090\n")
        self.assertFalse(self._run_probe(blip))
        cached = self._cached()
        self.assertFalse(cached["ok"])
        self.assertTrue(
            cached["tentative"],
            "a blip must not be recorded as the definitive 'not Pro'",
        )

    def test_unrunnable_ix_is_only_cached_tentatively(self) -> None:
        self.assertFalse(self._run_probe(None))
        self.assertTrue(self._cached()["tentative"])

    # ── TTLs ──────────────────────────────────────────────────────────────────

    def test_a_blip_does_not_re_probe_on_every_call(self) -> None:
        """The probe runs a real `ix briefing`; a timeout must not spin on it."""
        with patch.object(self.common, "run_command", return_value=None) as run:
            for _ in range(5):
                self.assertFalse(self.common.ix_pro_available(None))
            self.assertEqual(
                1,
                run.call_count,
                "a slow backend would otherwise cost 8s on every prompt",
            )

    def test_pro_user_recovers_once_the_backoff_expires(self) -> None:
        blip = _completed(1, stderr="Error: connect ECONNREFUSED 127.0.0.1:8090\n")
        self.assertFalse(self._run_probe(blip))
        # Still inside the backoff: answered from cache, no second probe.
        self.assertFalse(self._run_probe(_completed(0, stdout="{}")))
        self._age_cache(self.common.PRO_PROBE_BACKOFF_SECONDS + 1)
        # ...and past it, the real answer lands.
        self.assertTrue(self._run_probe(_completed(0, stdout='{"revision": 42}')))

    def test_the_backoff_cannot_outlast_the_briefing_interval(self) -> None:
        """Suppressing the probe is only acceptable while it delays nothing.

        Constants, not behaviour — this exists so that raising the backoff to
        something that could swallow a due briefing fails here rather than
        showing up as briefings quietly going missing.
        """
        self.assertLess(
            self.common.PRO_PROBE_BACKOFF_SECONDS, self.common.BRIEFING_TTL_SECONDS
        )

    def test_a_definitive_answer_is_held_for_the_long_ttl(self) -> None:
        self.assertFalse(
            self._run_probe(_completed(1, stderr="The 'briefing' command requires Ix Pro.\n"))
        )
        self._age_cache(self.common.PRO_PROBE_BACKOFF_SECONDS + 1)
        with patch.object(self.common, "run_command") as run:
            self.assertFalse(self.common.ix_pro_available(None))
            run.assert_not_called()
        self._age_cache(self.common.PRO_TTL_SECONDS)
        self.assertTrue(self._run_probe(_completed(0, stdout="{}")))

    def test_pro_is_not_re_probed_within_the_ttl(self) -> None:
        self.assertTrue(self._run_probe(_completed(0, stdout="{}")))
        with patch.object(self.common, "run_command") as run:
            self.assertTrue(self.common.ix_pro_available(None))
            run.assert_not_called()

    # ── the probe's output is the briefing, so it is handed back ──────────────

    def test_a_fresh_probe_returns_the_briefing_it_paid_for(self) -> None:
        with patch.object(
            self.common, "run_command", return_value=_completed(0, stdout='{"revision": 42}')
        ):
            pro, briefing = self.common.probe_pro(None)
        self.assertTrue(pro)
        self.assertEqual('{"revision": 42}', briefing)

    def test_nothing_is_handed_back_when_there_was_nothing_to_pay_for(self) -> None:
        """Cache hit, OSS stub and no-verdict all have no briefing to reuse."""
        with patch.object(
            self.common, "run_command", return_value=_completed(0, stdout='{"revision": 42}')
        ):
            self.common.probe_pro(None)
            # Second call is a cache hit — no command ran, so nothing to reuse.
            self.assertEqual((True, None), self.common.probe_pro(None))

        self.common.PRO_CACHE_PATH.unlink()
        stub = _completed(1, stderr="The 'briefing' command requires Ix Pro.\n")
        with patch.object(self.common, "run_command", return_value=stub):
            self.assertEqual((False, None), self.common.probe_pro(None))

        self.common.PRO_CACHE_PATH.unlink()
        with patch.object(self.common, "run_command", return_value=None):
            self.assertEqual((False, None), self.common.probe_pro(None))

    # ── cache robustness ──────────────────────────────────────────────────────

    def test_a_corrupt_timestamp_does_not_crash_the_hook(self) -> None:
        self.common.PRO_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.common.PRO_CACHE_PATH.write_text('{"timestamp": "not-a-number", "ok": true}')
        self.assertTrue(self._run_probe(_completed(0, stdout="{}")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
