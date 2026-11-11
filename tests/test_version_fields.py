#!/usr/bin/env python3
# Copyright 2026 Ix Infrastructure Inc.

"""The plugin version is written by hand in two places; they must agree."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


class VersionFields(unittest.TestCase):
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
