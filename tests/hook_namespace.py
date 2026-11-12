# Copyright 2026 Ix Infrastructure Inc.

"""Replace a name in the hooks' helpers everywhere it is bound.

The helpers are split across `.codex/hooks/ix_*.py` and re-exported by
`common`. A module that does `from ix_proc import run_command` holds its own
binding, so `patch.object(common, "run_command", ...)` alone would leave
`ix_probes.probe_pro` calling the real one. These reach every module in
`common.MODULES` whose binding is the very object `common` has, which is what
replacing it on `common` did when `common` was one file.
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Iterator
from unittest.mock import DEFAULT, MagicMock, patch

_MISSING = object()


def _bound(common, name: str) -> list:
    original = vars(common)[name]  # KeyError: common never had it, a typo
    return [
        module
        for module in (common, *common.MODULES)
        if vars(module).get(name, _MISSING) is original
    ]


def set_everywhere(common, name: str, value: object) -> None:
    for module in _bound(common, name):
        setattr(module, name, value)


@contextmanager
def patch_everywhere(common, name: str, new: object = DEFAULT, **kwargs) -> Iterator[object]:
    """`patch.object(common, name, ...)`, applied wherever `name` is bound."""
    replacement = MagicMock(**kwargs) if new is DEFAULT else new
    with ExitStack() as stack:
        for module in _bound(common, name):
            stack.enter_context(patch.object(module, name, replacement))
        yield replacement


def point_caches_at(common, cache_dir: Path) -> None:
    """Every cache the hooks keep, under `cache_dir`."""
    set_everywhere(common, "CACHE_DIR", cache_dir)
    set_everywhere(common, "STATUS_CACHE_PATH", cache_dir / "ix-status.json")
    set_everywhere(common, "PRO_CACHE_PATH", cache_dir / "ix-pro.json")
    set_everywhere(common, "BRIEFING_CACHE_PATH", cache_dir / "ix-briefing.txt")
