#!/usr/bin/env python3
"""Bound a child process's memory wherever the gates run.

The native gates, heavy builds and GUI suites run their children under a memory cap so
a runaway compiler or Blender cannot take the desktop down. The cap is a systemd user
scope (``systemd-run --user --scope -p MemoryMax=...``) when one works. Containers, WSL
without systemd and some CI runners have no systemd user session; there the child gets
an address-space limit (``RLIMIT_AS``) instead, applied by this file's own launcher, so
nothing beyond the bootstrapped Python is needed.

``RLIMIT_AS`` bounds VIRTUAL memory, not resident memory, and compilers and Blender
reserve far more address space than they touch. Measured: rustc fails to build
``native/pure_core`` under a 1 GiB address-space limit while its peak resident set is
293 MB, and Blender does not start under 1 GiB; both work at 2 GiB. OURS: the fallback
therefore allows :data:`ADDRESS_SPACE_FACTOR` times the ``MemoryMax`` value. It is a
looser bound than the systemd scope, and the notice says so.

The bound in effect is printed once per process, to stderr. A child is never run
unbounded: when neither a user scope nor ``RLIMIT_AS`` can be applied, :func:`prefix`
raises. ``NARWHAL_MEMORY_BOUND=systemd`` or ``=rlimit`` forces the choice (the suites
that check the exact systemd command line force ``systemd``).

Command line, for shell and Rust callers::

    memory_bound.py --max 1024M [--swap-max 0] [-p PROPERTY=VALUE]... -- command...
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

MODE_ENV = "NARWHAL_MEMORY_BOUND"
MODES = ("systemd", "rlimit")

#: The address-space allowance in the fallback, as a multiple of ``MemoryMax``.
ADDRESS_SPACE_FACTOR = 4

_SIZE = re.compile(r"^(\d+)([KMGT]?)$")
_UNITS = {"": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3, "T": 1024 ** 4}
_mode = None
_announced = False
#: The probe's own reference: suites that fake ``subprocess.run`` for the commands under
#: test must not receive this environment query.
_probe_run = subprocess.run


def size_bytes(text):
    """Bytes for a systemd size such as ``1024M`` or ``3G`` (powers of 1024)."""
    match = _SIZE.match(str(text).strip().upper())
    if not match:
        raise ValueError(f"not a memory size: {text!r}")
    return int(match.group(1)) * _UNITS[match.group(2)]


def _user_scope_works():
    if shutil.which("systemd-run") is None:
        return False
    try:
        probe = _probe_run(["systemd-run", "--user", "--scope", "--quiet", "true"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return probe.returncode == 0


def _rlimit_available():
    try:
        import resource
    except ImportError:
        return False
    return hasattr(resource, "RLIMIT_AS")


def mode():
    """``"systemd"`` when a user scope works (or is forced), else ``"rlimit"``.

    Decided once per process. Raises RuntimeError when neither bound is available,
    so a child is never run unbounded.
    """
    global _mode
    forced = os.environ.get(MODE_ENV, "").strip()
    if forced:
        if forced not in MODES:
            raise RuntimeError(f"{MODE_ENV} must be one of {', '.join(MODES)}, not {forced!r}")
        return forced
    if _mode is None:
        if _user_scope_works():
            _mode = "systemd"
        elif _rlimit_available():
            _mode = "rlimit"
        else:
            raise RuntimeError("no memory bound available: no systemd user scope and no RLIMIT_AS")
    return _mode


def describe(memory_max):
    """The one line that says which bound applies to a ``memory_max`` child."""
    if mode() == "systemd":
        return f"memory bound: systemd user scope, MemoryMax={memory_max}"
    limit = ADDRESS_SPACE_FACTOR * size_bytes(memory_max)
    return (f"memory bound: no systemd user session; RLIMIT_AS={limit // 1024 ** 2}M "
            f"({ADDRESS_SPACE_FACTOR} x MemoryMax={memory_max}; address space, not resident "
            f"memory; other scope properties are not enforced)")


def _announce(memory_max):
    global _announced
    if not _announced:
        _announced = True
        print(describe(memory_max), file=sys.stderr, flush=True)


def prefix(memory_max, *, swap_max="0", properties=(), quiet=True, scope=True):
    """The argv prefix that runs a command under the memory bound.

    With a user scope this is exactly ``systemd-run --user --scope [--quiet]
    -p MemoryMax=... -p MemorySwapMax=... [-p PROPERTY]...``; ``scope=False`` gives the
    service form ``--wait --pipe`` instead of ``--scope``. Without one it is this file's
    launcher, which sets ``RLIMIT_AS`` and executes the command; of the properties only
    ``WorkingDirectory=`` is honoured there.
    """
    size_bytes(memory_max)  # validate before choosing
    _announce(memory_max)
    properties = tuple(properties)
    if mode() == "systemd":
        argv = ["systemd-run", "--user"]
        argv += ["--scope"] if scope else []
        argv += ["--quiet"] if quiet else []
        argv += [] if scope else ["--wait", "--pipe"]
        argv += ["-p", f"MemoryMax={memory_max}", "-p", f"MemorySwapMax={swap_max}"]
        for prop in properties:
            argv += ["-p", prop]
        return argv
    argv = [sys.executable, "-I", str(Path(__file__).resolve()), "--rlimit", "--max", str(memory_max)]
    for prop in properties:
        if prop.startswith("WorkingDirectory="):
            argv += ["--cwd", prop.split("=", 1)[1]]
    return argv + ["--"]


def _exec_under_rlimit(memory_max, cwd, command):
    import resource

    limit = ADDRESS_SPACE_FACTOR * size_bytes(memory_max)
    soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    if hard != resource.RLIM_INFINITY:
        limit = min(limit, hard)
    resource.setrlimit(resource.RLIMIT_AS, (limit, hard if hard != resource.RLIM_INFINITY else limit))
    if cwd:
        os.chdir(cwd)
    os.execvp(command[0], command)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--max", required=True, help="the MemoryMax value, e.g. 1024M")
    parser.add_argument("--swap-max", default="0")
    parser.add_argument("-p", "--property", action="append", default=[])
    parser.add_argument("--cwd")
    parser.add_argument("--rlimit", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("no command after --")
    if args.rlimit:
        _exec_under_rlimit(args.max, args.cwd, command)
    head = prefix(args.max, swap_max=args.swap_max, properties=args.property)
    if mode() == "rlimit":
        _exec_under_rlimit(args.max, args.cwd, command)
    if args.cwd:
        os.chdir(args.cwd)
    os.execvp(head[0], head + command)


if __name__ == "__main__":
    main()
