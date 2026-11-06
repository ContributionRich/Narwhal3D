#!/usr/bin/env python3
"""Shared helper for bounding process memory in development, native gates, and tests.

Enforces a memory bound using:
1. systemd-run --user --scope -p MemoryMax=... (cgroup v2, preferred on desktop Linux);
2. prlimit --as=... (virtual address space, standard fallback on containers/WSL/CI);
3. ulimit -v ... (shell virtual memory limit fallback).

Refuses to run unbounded silently if no bounding mechanism is available.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from typing import Callable, List, Optional, Tuple

_CACHED_METHOD: Optional[Tuple[str, str]] = None


def parse_memory_bytes(limit: str | int) -> int:
    """Parse a memory limit specification into integer bytes.

    Supports binary and SI suffixes: K, M, G, T (case-insensitive) or raw bytes.
    Example: '1024M' -> 1073741824, '1G' -> 1073741824, '1536M' -> 1610612736.
    """
    if isinstance(limit, int):
        if limit <= 0:
            raise ValueError(f"Memory limit must be positive: {limit}")
        return limit

    text = limit.strip()
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*([a-zA-Z]*)", text)
    if not match:
        raise ValueError(f"Invalid memory limit specification: {limit!r}")

    value_str, unit = match.groups()
    value = float(value_str)
    unit = unit.upper()

    multipliers = {
        "": 1,
        "B": 1,
        "K": 1024,
        "KB": 1024,
        "KIB": 1024,
        "M": 1024**2,
        "MB": 1024**2,
        "MIB": 1024**2,
        "G": 1024**3,
        "GB": 1024**3,
        "GIB": 1024**3,
        "T": 1024**4,
        "TB": 1024**4,
        "TIB": 1024**4,
    }

    if unit not in multipliers:
        raise ValueError(f"Unknown memory unit in limit specification: {limit!r}")

    bytes_count = int(value * multipliers[unit])
    if bytes_count <= 0:
        raise ValueError(f"Memory limit must be positive: {limit!r}")
    return bytes_count


def _probe_systemd_run() -> bool:
    """Check if 'systemd-run --user --scope' is functional in the current environment."""
    if not shutil.which("systemd-run"):
        return False
    try:
        proc = subprocess.run(
            ["systemd-run", "--user", "--scope", "--quiet", "true"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _probe_prlimit() -> bool:
    """Check if 'prlimit' utility is functional."""
    prlimit_bin = shutil.which("prlimit")
    if not prlimit_bin:
        return False
    try:
        proc = subprocess.run(
            [prlimit_bin, "--as=unlimited", "true"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _probe_ulimit() -> bool:
    """Check if 'ulimit -v' shell command is functional."""
    sh_bin = shutil.which("sh")
    if not sh_bin:
        return False
    try:
        proc = subprocess.run(
            [sh_bin, "-c", "ulimit -v unlimited && true"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def detect_bounding_method(
    *,
    force_refresh: bool = False,
    systemd_prober: Callable[[], bool] = _probe_systemd_run,
    prlimit_prober: Callable[[], bool] = _probe_prlimit,
    ulimit_prober: Callable[[], bool] = _probe_ulimit,
) -> Tuple[str, str]:
    """Detect available memory-bounding mechanism in priority order:

    1. systemd-run --user --scope
    2. prlimit --as=...
    3. ulimit -v ...

    Returns (method_name, description).
    Raises RuntimeError if none are available (never run unbounded silently).
    NARWHAL_MEMORY_BOUND_METHOD can override detection ('systemd-run', 'prlimit', 'ulimit', 'none').
    """
    global _CACHED_METHOD
    if not force_refresh and _CACHED_METHOD is not None:
        return _CACHED_METHOD

    override = os.environ.get("NARWHAL_MEMORY_BOUND_METHOD", "").strip().lower()
    if override:
        if override == "systemd-run":
            res = ("systemd-run", "systemd user scope (forced by env)")
        elif override == "prlimit":
            res = ("prlimit", "process address-space limit via prlimit (forced by env)")
        elif override == "ulimit":
            res = ("ulimit", "process address-space limit via ulimit (forced by env)")
        elif override == "none":
            raise RuntimeError(
                "Memory bounding explicitly disabled or unavailable (NARWHAL_MEMORY_BOUND_METHOD=none). "
                "Refusing to run unbounded silently."
            )
        else:
            raise ValueError(f"Unknown NARWHAL_MEMORY_BOUND_METHOD override: {override!r}")
        _CACHED_METHOD = res
        return res

    if systemd_prober():
        res = ("systemd-run", "systemd user scope")
    elif prlimit_prober():
        res = ("prlimit", "process address-space limit via prlimit")
    elif ulimit_prober():
        res = ("ulimit", "process address-space limit via ulimit")
    else:
        raise RuntimeError(
            "No memory bounding mechanism available (systemd-run, prlimit, or ulimit). "
            "Refusing to run unbounded silently."
        )

    _CACHED_METHOD = res
    return res


def bounded_prefix(
    limit: str | int = "1024M",
    *,
    quiet: bool = True,
    swap_max: int | str | None = 0,
    announce: bool = True,
    print_fn: Callable[[str], None] = print,
    force_refresh: bool = False,
    systemd_prober: Callable[[], bool] = _probe_systemd_run,
    prlimit_prober: Callable[[], bool] = _probe_prlimit,
    ulimit_prober: Callable[[], bool] = _probe_ulimit,
) -> List[str]:
    """Return the command prefix tokens that bound memory to `limit`.

    Prints one clear line announcing which bound is in effect.
    Never returns an unbounded command.
    """
    method, _ = detect_bounding_method(
        force_refresh=force_refresh,
        systemd_prober=systemd_prober,
        prlimit_prober=prlimit_prober,
        ulimit_prober=ulimit_prober,
    )

    limit_bytes = parse_memory_bytes(limit)

    if method == "systemd-run":
        details = f"MemoryMax={limit}"
        if swap_max is not None:
            details += f", MemorySwapMax={swap_max}"
        if announce:
            print_fn(f"memory bound: systemd-run ({details})")
        prefix = ["systemd-run", "--user", "--scope"]
        if quiet:
            prefix.append("--quiet")
        prefix.extend(["-p", f"MemoryMax={limit}"])
        if swap_max is not None:
            prefix.extend(["-p", f"MemorySwapMax={swap_max}"])
        return prefix

    if method == "prlimit":
        if announce:
            print_fn(f"memory bound: prlimit (--as={limit_bytes})")
        return ["prlimit", f"--as={limit_bytes}", "--"]

    if method == "ulimit":
        limit_kb = limit_bytes // 1024
        if announce:
            print_fn(f"memory bound: ulimit (-v {limit_kb})")
        return ["sh", "-c", f'ulimit -v {limit_kb} && exec "$@"', "_"]

    raise RuntimeError(f"Unknown bounding method: {method}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a command within a memory bound, using systemd-run, prlimit, or ulimit."
    )
    parser.add_argument(
        "--probe",
        action="store_true",
        help="Check whether a memory bounding mechanism is functional and exit 0 (found) or 2 (not found).",
    )
    parser.add_argument(
        "--limit",
        default="1024M",
        help="Memory limit specification, e.g. 1024M, 1G, 2048M (default: 1024M).",
    )
    parser.add_argument(
        "--no-quiet",
        action="store_true",
        help="Do not pass --quiet to systemd-run.",
    )
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="Command and arguments to execute under the memory bound (prefix with '--').",
    )

    args = parser.parse_args(argv)

    if args.probe:
        try:
            method, description = detect_bounding_method(force_refresh=True)
            print(f"memory bound available: {method} ({description})")
            return 0
        except RuntimeError as err:
            print(f"error: {err}", file=sys.stderr)
            return 2

    cmd = args.command
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]

    if not cmd:
        parser.print_help(sys.stderr)
        return 2

    try:
        prefix = bounded_prefix(
            limit=args.limit,
            quiet=not args.no_quiet,
            announce=True,
        )
    except RuntimeError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2

    full_cmd = prefix + cmd
    try:
        proc = subprocess.run(full_cmd)
        return proc.returncode
    except KeyboardInterrupt:
        return 130
    except OSError as err:
        print(f"error executing bounded command: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
