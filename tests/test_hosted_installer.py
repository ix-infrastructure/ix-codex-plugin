#!/usr/bin/env python3
# Copyright 2026 Ix Infrastructure Inc.

"""The hosted installers install the ref they are asked for.

`codex-install.sh` / `codex-install.ps1` fetch a ref of this repository into a
cached checkout and run its `scripts/install_codex_integration.py`. Pinning a
release means asking for a tag (or a full commit SHA) and getting exactly that
commit, on a first install and on every re-run over an existing checkout.

Nothing here touches the network: git's `url.<base>.insteadOf`, set through
GIT_CONFIG_COUNT for the child process only, points the installers' GitHub URL
at a local bare repository whose `install_codex_integration.py` is a stub that
records its argv and the commit it was run from.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SH_INSTALLER = REPO / "codex-install.sh"
PS_INSTALLER = REPO / "codex-install.ps1"
GITHUB_URL = "https://github.com/ix-infrastructure/ix-codex-plugin.git"
DEFAULT_ARGS = ["--home", "--plugin", "--hooks", "--mcp"]

STUB = textwrap.dedent(
    """
    import json, os, subprocess, sys
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                          capture_output=True, text=True).stdout.strip()
    with open(os.environ["IX_STUB_LOG"], "a", encoding="utf-8") as log:
        log.write(json.dumps({"argv": sys.argv[1:], "head": head}) + "\\n")
    """
)


def _git(*args: str, cwd: Path) -> str:
    result = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
         "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *args],
        cwd=str(cwd), capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


class HostedInstallerCases:
    """A local "GitHub" with three commits on main, tags on the first, a branch on the second.

    A mixin, not a TestCase, so discovery runs the cases once per installer and
    never on their own.
    """

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="ix-hosted-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        src = self.tmp / "src"
        (src / "scripts").mkdir(parents=True)
        (src / "scripts" / "install_codex_integration.py").write_text(STUB, encoding="utf-8")
        _git("init", "-q", "-b", "main", str(src), cwd=self.tmp)
        _git("add", "-A", cwd=src)
        _git("commit", "-q", "-m", "first", cwd=src)
        self.first = _git("rev-parse", "HEAD", cwd=src)
        _git("tag", "v1.0.0", cwd=src)
        _git("tag", "-a", "v1.0.1", "-m", "annotated", cwd=src)
        _git("commit", "-q", "--allow-empty", "-m", "second", cwd=src)
        self.second = _git("rev-parse", "HEAD", cwd=src)
        _git("branch", "feature", cwd=src)
        _git("commit", "-q", "--allow-empty", "-m", "third", cwd=src)
        self.third = _git("rev-parse", "HEAD", cwd=src)
        self.remote = self.tmp / "remote.git"
        _git("clone", "-q", "--bare", str(src), str(self.remote), cwd=self.tmp)

        self.home = self.tmp / "home"
        self.home.mkdir()
        self.ix_home = self.tmp / "ix"
        self.source_dir = self.ix_home / "codex-plugin-source"
        self.log = self.tmp / "stub.log"
        gitconfig = self.tmp / "gitconfig"
        gitconfig.write_text("", encoding="utf-8")
        self.base_env = {
            k: v for k, v in os.environ.items()
            if not k.startswith("GIT_") and k != "IX_CODEX_REF"
        }
        self.base_env.update({
            "HOME": str(self.home),
            "USERPROFILE": str(self.home),
            "IX_HOME": str(self.ix_home),
            "IX_STUB_LOG": str(self.log),
            "GIT_CONFIG_GLOBAL": str(gitconfig),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_COUNT": "2",
            "GIT_CONFIG_KEY_0": f"url.{self.remote.as_uri()}.insteadOf",
            "GIT_CONFIG_VALUE_0": GITHUB_URL,
            "GIT_CONFIG_KEY_1": "advice.detachedHead",
            "GIT_CONFIG_VALUE_1": "false",
        })

    def command(self, *args: str) -> list[str]:
        raise NotImplementedError

    def run_installer(self, *args: str, **env: str) -> subprocess.CompletedProcess:
        full_env = dict(self.base_env)
        full_env.update(env)
        return subprocess.run(
            self.command(*args), env=full_env, capture_output=True, text=True,
            timeout=120, check=False,
        )

    def runs(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def install(self, *args: str, **env: str) -> dict:
        before = len(self.runs())
        result = self.run_installer(*args, **env)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        runs = self.runs()
        self.assertEqual(before + 1, len(runs), "installer did not run exactly once")
        return runs[-1]

    def refused(self, *args: str, **env: str) -> subprocess.CompletedProcess:
        before = len(self.runs())
        result = self.run_installer(*args, **env)
        self.assertNotEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(before, len(self.runs()), "installer ran after a refused ref")
        return result

    # -- the cases, shared by both installers -------------------------------

    def test_default_is_main(self) -> None:
        run = self.install()
        self.assertEqual(self.third, run["head"])
        # The order differs between the two installers; argparse does not care.
        self.assertEqual(sorted(DEFAULT_ARGS), sorted(run["argv"]))

    def test_env_ref_installs_a_tag(self) -> None:
        self.assertEqual(self.first, self.install(IX_CODEX_REF="v1.0.0")["head"])

    def test_ref_flag_installs_an_annotated_tag_and_is_not_forwarded(self) -> None:
        run = self.install("--ref", "v1.0.1", "--repo", "/some/project", "--plugin")
        self.assertEqual(self.first, run["head"])
        self.assertEqual(["--repo", "/some/project", "--plugin"], run["argv"])

    def test_ref_equals_form_installs_a_full_commit_sha_on_a_fresh_install(self) -> None:
        run = self.install(f"--ref={self.second}")
        self.assertEqual(self.second, run["head"])
        self.assertEqual(sorted(DEFAULT_ARGS), sorted(run["argv"]))

    def test_a_fully_qualified_tag_ref(self) -> None:
        self.assertEqual(self.first, self.install("--ref", "refs/tags/v1.0.0")["head"])

    def test_ref_flag_beats_the_environment(self) -> None:
        run = self.install("--ref", "feature", IX_CODEX_REF="v1.0.0")
        self.assertEqual(self.second, run["head"])

    def test_rerun_over_the_cached_checkout_moves_to_each_requested_ref(self) -> None:
        self.assertEqual(self.third, self.install()["head"])
        self.assertEqual(self.first, self.install("--ref", "v1.0.1")["head"])
        self.assertEqual(self.second, self.install("--ref", self.second)["head"])
        self.assertEqual(self.first, self.install(IX_CODEX_REF="v1.0.0")["head"])
        self.assertEqual(self.third, self.install()["head"])

    def test_the_resolved_commit_is_reported(self) -> None:
        result = self.run_installer("--ref", "v1.0.0")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn(self.first[:12], result.stdout)

    def test_an_unknown_ref_installs_nothing(self) -> None:
        self.refused("--ref", "v9.9.9")

    def test_a_ref_that_looks_like_an_option_is_refused(self) -> None:
        self.refused("--ref", "--upload-pack=touch pwned")
        self.refused(IX_CODEX_REF="-x")
        self.assertFalse(self.source_dir.exists())

    def test_ref_without_a_value_is_refused(self) -> None:
        self.refused("--ref")


@unittest.skipIf(os.name == "nt", "codex-install.sh is the POSIX installer")
@unittest.skipUnless(shutil.which("sh") and shutil.which("git"), "needs sh and git")
class ShellInstaller(HostedInstallerCases, unittest.TestCase):
    def command(self, *args: str) -> list[str]:
        return ["sh", str(SH_INSTALLER), *args]

    def test_help_mentions_ref(self) -> None:
        result = self.run_installer("--help")
        self.assertEqual(0, result.returncode)
        self.assertIn("--ref", result.stdout)
        self.assertIn("IX_CODEX_REF", result.stdout)
        self.assertEqual([], self.runs())


def _powershell() -> list[str] | None:
    if os.name == "nt" and shutil.which("powershell"):
        return ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File"]
    if shutil.which("pwsh"):
        return ["pwsh", "-NoProfile", "-NonInteractive", "-File"]
    return None


@unittest.skipUnless(_powershell() and shutil.which("git"), "needs PowerShell and git")
class PowerShellInstaller(HostedInstallerCases, unittest.TestCase):
    def command(self, *args: str) -> list[str]:
        return [*_powershell(), str(PS_INSTALLER), *args]  # type: ignore[misc]

    def setUp(self) -> None:
        super().setUp()
        # The installer prefers `py -3`, then `python`; make sure whichever it
        # finds is a Python 3 by putting this interpreter's directory first.
        self.base_env["PATH"] = os.pathsep.join(
            [str(Path(sys.executable).parent), self.base_env.get("PATH", "")]
        )


class VersionFields(unittest.TestCase):
    """The version is written by hand in two places; they must agree."""

    def test_plugin_json_and_marketplace_agree(self) -> None:
        plugin = json.loads(
            (REPO / "plugins" / "ix-memory" / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        market = json.loads((REPO / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8"))
        entries = [p for p in market["plugins"] if p.get("name") == plugin["name"]]
        self.assertEqual(1, len(entries))
        self.assertTrue(plugin["version"])
        self.assertEqual(plugin["version"], entries[0]["version"])


if __name__ == "__main__":
    unittest.main()
