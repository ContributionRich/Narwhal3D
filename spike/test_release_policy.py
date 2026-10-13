#!/usr/bin/env python3
"""Version-series and host-glibc gates for Narwhal prerelease packages."""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "narwhal_release_policy", ROOT / "scripts" / "release_policy.py")
assert spec is not None and spec.loader is not None
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)

checks = 0


def check(condition: bool, label: str) -> None:
    global checks
    checks += 1
    print(f"  {'PASS' if condition else 'FAIL'}  {label}")
    if not condition:
        raise AssertionError(label)


for version in ("0.4.0", "0.4.1", "0.4.999", "0.4.2-alpha.1", "0.4.3-preview_2"):
    check(policy.validate_preview_version(version) == version,
          f"0.4 prerelease-line version is accepted: {version}")
for version in ("0.3.9", "0.5.0", "1.0.0", "0.4", "0.4.01", "0.4.1/escape", ""):
    try:
        policy.validate_preview_version(version)
    except ValueError as exc:
        refused = "capped at 0.4.x" in str(exc)
    else:
        refused = False
    check(refused, f"version outside the frozen 0.4.x line is refused: {version!r}")
versions = policy.glibc_versions(
    "Name: GLIBC_2.2.5  Name: GLIBC_2.35  Name: GLIBC_2.38  Name: GLIBC_2.43")
check(versions == {(2, 2), (2, 35), (2, 38), (2, 43)},
      f"readelf GLIBC requirements are parsed numerically ({sorted(versions)})")
check(policy.MAX_HOST_GLIBC == (2, 35)
      and max(versions) > policy.MAX_HOST_GLIBC,
      "current glibc 2.43-built runtime is rejected against Ubuntu 22.04/glibc 2.35")
check(policy.glibc_versions("no version information found") == set(),
      "non-version readelf text invents no requirement")

print(f"\n{checks}/{checks} release-policy checks passed")
