# Copyright 2026 Ix Infrastructure Inc.

"""What every hook imports: the shared helpers, re-exported from one module per concern.

The helpers live beside this file:

    ix_hookio      the event on stdin, the reply on stdout, the version file
    ix_proc        running `ix`: Windows resolution, the cmd.exe shim guard, output parsing
    ix_workspace   the project a payload belongs to (its git root, never `$HOME`)
    ix_state       the per-user state directory and its caches
    ix_probes      the health and Pro probes
    ix_lookup      graph lookups and the records ix answers them with
    ix_shellparse  the search pattern, read path and write targets of a shell command
    ix_patchparse  the files an `apply_patch` (or any tool call) writes
    ix_summaries   one-line summaries of ix output
    ix_messages    the PreToolUse messages and the pre-edit warning
    ix_automap     the guarded automatic `ix map`

`from common import <name>` still works for every name those modules define, so
the hooks themselves did not change when the helpers were split out of here.

The modules are loaded from the directory this file really lives in (symlinks
resolved), not from wherever `sys.path` points first. That is the same rule
`_launch.py` follows for `common` itself: a project-pinned hook must never pick
up the home copy's helpers. Resolving symlinks keeps a `--mode symlink` install
whole when the source checkout gains a module the install has no link for yet.
Executing this file again loads them again, so a fresh `common` shares no state
(caches, the resolved `ix` path) with an earlier one -- as when it was one file.

A name is bound in every module that imports it, so replacing it on `common`
alone reaches only `common`. MODULES lists the modules in load order, for
whoever needs to replace a name everywhere it is bound (the tests do).
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types

# Kept as attributes of `common`, as they were when it was one file: callers
# reach the standard library through it (`common.subprocess.run`).
import concurrent.futures  # noqa: F401
import functools  # noqa: F401
import hashlib  # noqa: F401
import json  # noqa: F401
import re  # noqa: F401
import shlex  # noqa: F401
import shutil  # noqa: F401
import subprocess  # noqa: F401
import tempfile  # noqa: F401
import time  # noqa: F401

_HOOKS_DIR = os.path.dirname(os.path.realpath(__file__))

# Each module after the ones it imports from.
_MODULE_NAMES = (
    "ix_hookio",
    "ix_proc",
    "ix_workspace",
    "ix_state",
    "ix_probes",
    "ix_lookup",
    "ix_shellparse",
    "ix_patchparse",
    "ix_summaries",
    "ix_messages",
    "ix_automap",
)


def _load_module(name: str) -> types.ModuleType:
    path = os.path.join(_HOOKS_DIR, f"{name}.py")
    if not os.path.isfile(path):
        # An install that copied some hook files and not others. A hook must
        # never fail the Codex turn, and a traceback is no use to the user, so
        # say what is wrong in one line and do nothing (exit 0, no output).
        sys.stderr.write(
            f"ix hooks: {name}.py is missing from {_HOOKS_DIR}; "
            "re-run the Ix Codex installer to restore it.\n"
        )
        raise SystemExit(0)
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered before it runs, so the modules loaded after it import this
    # copy rather than searching sys.path for one.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


MODULES: tuple[types.ModuleType, ...] = tuple(_load_module(name) for name in _MODULE_NAMES)

for _module in MODULES:
    for _name, _value in vars(_module).items():
        if _name.startswith("__") or _name == "annotations" or isinstance(_value, types.ModuleType):
            continue
        globals().setdefault(_name, _value)
del _module, _name, _value
