#!/usr/bin/env python3
"""Stage and verify the Linux converter runtime, never during normal launch.

Local artifacts are safest: ``--artifact tool=/path/to/file``. Downloads require
both an explicit URL and SHA256 (``--download tool=url,sha256``). All outputs
remain under this checkout's ``runtime/`` directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import urllib.error
import urllib.request
from pathlib import Path

TOOLS = ("inkscape", "pdfinfo", "gs", "soffice", "tracedraw-cli")
ALIASES = {"soffice": "soffice", "libreoffice": "soffice", "ghostscript": "gs"}


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _parse_pairs(values: list[str], label: str) -> dict[str, str]:
    result = {}
    for value in values:
        if "=" not in value:
            raise SystemExit(f"{label} must be TOOL=VALUE: {value}")
        tool, item = value.split("=", 1)
        tool = ALIASES.get(tool.lower(), tool.lower())
        if tool not in TOOLS:
            raise SystemExit(f"unknown converter {tool!r}")
        result[tool] = item
    return result


def _download(url: str, target: Path) -> None:
    """Download atomically; a failed network request never leaves an artifact."""
    partial = target.with_name(target.name + ".partial")
    partial.unlink(missing_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "Narwhal3D-runtime/1"})
    try:
        with urllib.request.urlopen(request, timeout=600) as response, partial.open("wb") as out:
            shutil.copyfileobj(response, out)
        partial.replace(target)
    except (OSError, TimeoutError, urllib.error.URLError) as exc:
        partial.unlink(missing_ok=True)
        raise SystemExit(f"download failed for {url}: {exc}") from exc


def _extract_archive_member(archive: Path, member_name: str, target: Path) -> None:
    """Extract one exact regular-file member without permitting archive paths."""
    try:
        with tarfile.open(archive, "r:gz") as bundle:
            matches = [member for member in bundle.getmembers()
                       if member.name == member_name and member.isfile()]
            if len(matches) != 1 or any(member.name.startswith(("/", "../"))
                                        or ".." in Path(member.name).parts
                                        for member in bundle.getmembers()):
                raise SystemExit(f"unsafe or missing archive member {member_name!r}")
            stream = bundle.extractfile(matches[0])
            if stream is None:
                raise SystemExit(f"could not read archive member {member_name!r}")
            temporary = target.with_name(target.name + ".extracting")
            with temporary.open("wb") as output:
                shutil.copyfileobj(stream, output)
            temporary.replace(target)
    except (OSError, tarfile.TarError) as exc:
        raise SystemExit(f"could not extract {member_name!r} from {archive}: {exc}") from exc


def _install_payload(source: Path, executable: Path, details: dict) -> str:
    member = details.get("archive_member")
    if member:
        _extract_archive_member(source, member, executable)
    elif details["executable"] == "inkscape" and source.suffix.lower() == ".appimage":
        _appimage_wrapper(executable, source)
    elif source.name != executable.name:
        shutil.copy2(source, executable)
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    return _sha256(executable)


def _appimage_wrapper(target: Path, payload: Path) -> None:
    target.write_text(
        "#!/bin/sh\nset -eu\n"
        'BASE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"\n'
        f'exec "$BASE/{payload.name}" --appimage-extract-and-run "$@"\n',
        encoding="utf-8",
    )
    target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", action="append", default=[], metavar="TOOL=PATH")
    parser.add_argument("--download", action="append", default=[], metavar="TOOL=URL,SHA256")
    parser.add_argument("--platform", default="linux-x86_64")
    parser.add_argument("--check", action="store_true", help="verify staged inventory only")
    args = parser.parse_args(argv)
    if not args.platform.startswith("linux-"):
        raise SystemExit("this bootstrap currently stages Linux artifacts only")

    root = _root()
    runtime = root / "runtime" / args.platform / "bin"
    runtime.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "runtime" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    inventory_path = runtime.parent / "inventory.json"
    previous = (json.loads(inventory_path.read_text(encoding="utf-8"))
                if inventory_path.is_file() else {})
    staged = dict(previous.get("tools", {}))
    artifacts: dict[str, tuple[str, str | None]] = {}
    for item in args.artifact:
        if "=" not in item:
            raise SystemExit(f"--artifact must be TOOL=PATH[,SHA256]: {item}")
        tool, spec = item.split("=", 1)
        parts = spec.rsplit(",", 1)
        path = parts[0]
        digest = parts[1].lower() if len(parts) == 2 else None
        normalized = ALIASES.get(tool.lower(), tool.lower())
        if normalized not in TOOLS:
            raise SystemExit(f"unknown converter {normalized!r}")
        artifacts[normalized] = (path, digest)
    downloads = {}
    for item in args.download:
        if "=" not in item or "," not in item.split("=", 1)[1]:
            raise SystemExit("--download must be TOOL=URL,SHA256")
        tool, spec = item.split("=", 1)
        url, digest = spec.rsplit(",", 1)
        downloads[ALIASES.get(tool.lower(), tool.lower())] = (url, digest.lower())

    if not args.check:
        for tool, (source, supplied_digest) in artifacts.items():
            src = Path(source).expanduser().resolve()
            if not src.is_file():
                raise SystemExit(f"artifact is not a regular file: {src}")
            target = runtime / src.name
            shutil.copy2(src, target)
            expected = supplied_digest or manifest["tools"][tool].get("sha256")
            actual = _sha256(target)
            if expected and actual != expected:
                target.unlink()
                raise SystemExit(f"SHA256 mismatch for {tool}; refusing to stage it")
            details = manifest["tools"][tool]
            executable = runtime / details["executable"]
            executable_digest = _install_payload(target, executable, details)
            expected_executable = details.get("executable_sha256")
            executable_verified = (not expected_executable
                                   or executable_digest == expected_executable)
            if not executable_verified:
                executable.unlink(missing_ok=True)
                raise SystemExit(f"executable SHA256 mismatch for {tool}; refusing to stage it")
            staged[tool] = {
                "artifact": target.name,
                "executable": executable.name,
                "executable_sha256": executable_digest,
                "revision": details.get("revision"),
                "sha256": actual,
                "source": details.get("source"),
                "version": details.get("version"),
                "verified": bool(expected and actual == expected and executable_verified),
            }
        for tool, (url, expected) in downloads.items():
            if len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
                raise SystemExit(f"--download for {tool} needs a 64-character SHA256")
            details = manifest["tools"][tool]
            payload_name = details.get("archive_name") or (
                f"{tool}.AppImage" if tool == "inkscape" else f"{tool}.artifact")
            target = runtime / payload_name
            _download(url, target)
            if _sha256(target) != expected:
                target.unlink()
                raise SystemExit(f"SHA256 mismatch for downloaded {tool}; refusing to stage it")
            executable = runtime / details["executable"]
            if tool == "inkscape":
                target.chmod(target.stat().st_mode | stat.S_IXUSR)
            executable_digest = _install_payload(target, executable, details)
            expected_executable = details.get("executable_sha256")
            if expected_executable and executable_digest != expected_executable:
                executable.unlink(missing_ok=True)
                raise SystemExit(f"executable SHA256 mismatch for {tool}; refusing to stage it")
            staged[tool] = {
                "artifact": target.name,
                "executable": executable.name,
                "executable_sha256": executable_digest,
                "revision": details.get("revision"),
                "source": url,
                "sha256": expected,
                "version": details.get("version"),
                "verified": True,
            }

    inventory = {}
    complete = True
    for tool, details in manifest["tools"].items():
        executable = runtime / details["executable"]
        record = staged.get(tool, {})
        present = executable.is_file() and os.access(executable, os.X_OK)
        executable_digest = _sha256(executable) if present else None
        expected_executable = details.get("executable_sha256")
        expected_archive = details.get("sha256")
        artifact_name = record.get("artifact")
        artifact_path = (runtime / artifact_name
                         if isinstance(artifact_name, str)
                         and Path(artifact_name).name == artifact_name else None)
        archive_digest = (_sha256(artifact_path)
                          if artifact_path is not None and artifact_path.is_file() else None)
        verified = (bool(record.get("verified")) and present
                    and (not expected_archive
                         or archive_digest == expected_archive == record.get("sha256"))
                    and (not expected_executable
                         or executable_digest == expected_executable
                         == record.get("executable_sha256"))
                    and (not details.get("revision")
                         or record.get("revision") == details.get("revision"))
                    and (not details.get("version")
                         or record.get("version") == details.get("version")))
        inventory[tool] = {
            **record,
            "executable": details["executable"],
            "executable_sha256": executable_digest,
            "present": present,
            "verified": verified,
        }
        complete = complete and verified
    generated = {"schema": 1, "platform": args.platform,
                 "complete": complete, "tools": inventory}
    inventory_path.write_text(json.dumps(generated, indent=2, sort_keys=True) + "\n",
                              encoding="utf-8")
    print(json.dumps(inventory, indent=2, sort_keys=True))
    if not complete:
        print("runtime incomplete: every declared converter and verified license/hash is required", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
