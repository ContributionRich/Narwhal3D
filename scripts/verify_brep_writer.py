#!/usr/bin/env python3
"""Functionally verify a Narwhal ONBREP 1 helper executable."""
from __future__ import annotations

import argparse
import base64
from pathlib import Path
import subprocess
import sys

import rhino3dm as r3

PROTOCOL = "brep_writer ONBREP 1"
MAX_BOX_ARCHIVE = 16 * 1024 * 1024


class VerificationError(RuntimeError):
    pass


def run(command: list[str], *, data: bytes | None = None, timeout: int = 30):
    try:
        return subprocess.run(
            command, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=timeout, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise VerificationError(f"cannot execute helper: {exc}") from exc


def archive_version(stderr: bytes) -> int:
    text = stderr.decode("utf-8", errors="replace")
    try:
        return int(text.split("opennurbs", 1)[1].split()[0])
    except (IndexError, ValueError) as exc:
        raise VerificationError(f"helper did not report its openNURBS version: {text!r}") from exc


def signed32(value: int) -> int:
    return value - (1 << 32) if value >= (1 << 31) else value


def malformed_refused(result: subprocess.CompletedProcess[bytes]) -> bool:
    """Only the helper's documented clean refusal counts; crashes never do."""
    return (result.returncode == 2 and not result.stdout
            and result.stderr.startswith(b"brep_writer:"))


def verify(helper: Path) -> None:
    if not helper.is_file() or helper.is_symlink():
        raise VerificationError(f"missing or unsafe helper: {helper}")
    version = run([str(helper), "--version"])
    version_text = version.stdout.decode("utf-8", errors="replace").strip()
    if version.returncode != 0 or not version_text.startswith(PROTOCOL + " opennurbs "):
        raise VerificationError(f"unexpected protocol response: {version_text!r}")

    box = run([str(helper), "box"])
    if box.returncode != 0:
        raise VerificationError(
            "unit-box self-test failed: " + box.stderr.decode("utf-8", errors="replace"))
    if not 100 <= len(box.stdout) <= MAX_BOX_ARCHIVE:
        raise VerificationError(f"unit-box archive has unsafe size {len(box.stdout)}")
    decoded = r3.CommonObject.Decode({
        "version": 10000,
        "archive3dm": 60,
        "opennurbs": signed32(archive_version(box.stderr)),
        "data": base64.b64encode(box.stdout).decode("ascii"),
    })
    if not isinstance(decoded, r3.Brep):
        raise VerificationError(f"unit-box archive decoded as {type(decoded).__name__}")
    if not decoded.IsValid or not decoded.IsSolid or len(decoded.Faces) != 6:
        raise VerificationError(
            f"unit-box BRep is not valid/solid/six-faced: "
            f"valid={decoded.IsValid}, solid={decoded.IsSolid}, faces={len(decoded.Faces)}")

    malformed = (
        b"ONBREP 1 tol nan",
        b"ONBREP 1 tol 0 vertices 100001",
        b"ONBREP 1 tol 0 vertices 0 edges 0 faces 0 trailing",
        b"ONBREP 1 tol 0 vertices 1 0 0 0 edges 1 0 2",
    )
    for payload in malformed:
        result = run([str(helper)], data=payload)
        if not malformed_refused(result):
            raise VerificationError(
                f"helper did not cleanly refuse malformed input "
                f"(exit={result.returncode}, stdout={len(result.stdout)}, "
                f"stderr={result.stderr[:120]!r}): {payload[:80]!r}")

    print(
        f"verified {PROTOCOL}: valid solid 6-face box, {len(box.stdout)} bytes; "
        f"{len(malformed)} malformed inputs refused"
    )


def self_check() -> None:
    clean = subprocess.CompletedProcess([], 2, b"", b"brep_writer: refused\n")
    crash = subprocess.CompletedProcess([], -11, b"", b"")
    wrong = subprocess.CompletedProcess([], 1, b"", b"brep_writer: refused\n")
    noisy = subprocess.CompletedProcess([], 2, b"archive", b"brep_writer: refused\n")
    if not malformed_refused(clean) or any(map(malformed_refused, (crash, wrong, noisy))):
        raise VerificationError("malformed-input exit classification is unsafe")
    print("verify_brep_writer.py self-check: OK")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("helper", type=Path, nargs="?")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_check:
            self_check()
        elif args.helper is not None:
            verify(args.helper.resolve())
        else:
            parser.error("provide a helper or --self-check")
    except VerificationError as exc:
        print(f"BRep writer verification failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
