#!/usr/bin/env python3
"""Fetch SHA-256-locked artifacts and safely extract locked archives."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import shutil
import sys
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCK = ROOT / "packaging" / "linux-x86_64.lock.json"
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
JsonObject = dict[str, Any]
EXPECTED_LOCKS = {
    "appimagetool": (
        "https://github.com/AppImage/appimagetool/releases/download/1.9.1/appimagetool-x86_64.AppImage",
        "ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0",
    ),
    "appimage-runtime": (
        "https://github.com/AppImage/type2-runtime/releases/download/20251108/runtime-x86_64",
        "2fca8b443c92510f1483a883f60061ad09b46b978b2631c807cd873a47ec260d",
    ),
    "blender-runtime": (
        "https://download.blender.org/release/Blender5.2/blender-5.2.2-linux-x64.tar.xz",
        "84098912789dc450e95697c4184fb8a90acbe5111c2ba4aede3fecb57806a168",
    ),
    "rhino3dm": (
        "https://files.pythonhosted.org/packages/68/e4/d2835c98ada8c69e3ec478e7568bd9391df7b677826e5c72b9f648195a3d/rhino3dm-8.35.0-cp313-cp313-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl",
        "783db09d49cbe632b1c26cfc96e5b3910d82a4c9dc07b8d174f212a61229c7a4",
    ),
    "cadquery-ocp-novtk": (
        "https://files.pythonhosted.org/packages/83/a8/752777f38c5bd8cb6e239f4639f3a64a704b5812f3247b0a11f2ecb48900/cadquery_ocp_novtk-8.0.1.0.0-cp313-cp313-manylinux_2_28_x86_64.whl",
        "1702dfc8f5bdcb916ce65ea0c6314fe453cc78a6a3d49a044fb3062d47b0ef9f",
    ),
    "cadquery-ocp-proxy": (
        "https://files.pythonhosted.org/packages/15/36/4a8dbfdf4ab61a7841c36398615c6ab3d3c932d4254b978963690650feea/cadquery_ocp_proxy-8.0.1.0.0-py3-none-any.whl",
        "d6a6909a73d2300ca4581ff762ccf0137993ac75c57a2dc82e12bffa3a12b680",
    ),
}


class PackagingError(RuntimeError):
    """A locked artifact is invalid, unavailable, or unsafe to use."""


def sha256_file(path: Path) -> str:
    """Return the lowercase SHA-256 digest of a regular file."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def default_cache_dir() -> Path:
    """Return the cache path, outside the repository by default."""
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "narwhal3d" / "artifacts"


def load_lock(path: Path) -> tuple[JsonObject, dict[str, JsonObject]]:
    """Load and strictly validate the artifact lock."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PackagingError(f"cannot read lock {path}: {exc}") from exc

    if data.get("schema") != 1:
        raise PackagingError("unsupported lock schema (expected 1)")
    if data.get("product") != "Narwhal 3D":
        raise PackagingError("lock product must be 'Narwhal 3D'")
    if data.get("platform") != "linux-x86_64":
        raise PackagingError("lock platform must be linux-x86_64")
    artifacts = data.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise PackagingError("lock must contain a non-empty artifacts list")

    by_id: dict[str, JsonObject] = {}
    filenames: set[str] = set()
    for index, artifact in enumerate(artifacts):
        if not isinstance(artifact, dict):
            raise PackagingError(f"artifact {index} is not an object")
        artifact_id = artifact.get("id")
        filename = artifact.get("filename")
        url = artifact.get("url")
        expected = artifact.get("sha256")
        version = artifact.get("version")
        if not isinstance(artifact_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", artifact_id):
            raise PackagingError(f"artifact {index} has an invalid id")
        if artifact_id in by_id:
            raise PackagingError(f"duplicate artifact id: {artifact_id}")
        if not isinstance(filename, str) or Path(filename).name != filename:
            raise PackagingError(f"artifact {artifact_id} has an invalid filename")
        if filename in filenames:
            raise PackagingError(f"duplicate artifact filename: {filename}")
        if not isinstance(version, str) or not version:
            raise PackagingError(f"artifact {artifact_id} has no immutable version")
        if not isinstance(url, str):
            raise PackagingError(f"artifact {artifact_id} has no URL")
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment:
            raise PackagingError(f"artifact {artifact_id} must use a query-free, fragment-free HTTPS URL")
        remote_name = PurePosixPath(urllib.parse.unquote(parsed.path)).name
        if remote_name != filename:
            raise PackagingError(f"artifact {artifact_id} URL filename does not match lock filename")
        if version not in filename and version not in parsed.path.split("/"):
            raise PackagingError(f"artifact {artifact_id} version is absent from its filename and URL path")
        if not isinstance(expected, str) or not SHA256_RE.fullmatch(expected):
            raise PackagingError(f"artifact {artifact_id} has an invalid SHA-256")
        if artifact.get("kind") not in {"wheel", "tar.xz", "appimage", "appimage-runtime"}:
            raise PackagingError(f"artifact {artifact_id} has an unsupported kind")
        if artifact.get("kind") == "appimage" and artifact.get("executable") is not True:
            raise PackagingError(f"AppImage artifact {artifact_id} must be marked executable")
        redirect_hosts = artifact.get("allowed_redirect_hosts", [])
        if not isinstance(redirect_hosts, list) or any(
            not isinstance(host, str) or not re.fullmatch(r"[a-z0-9.-]+", host)
            for host in redirect_hosts
        ):
            raise PackagingError(f"artifact {artifact_id} has invalid allowed_redirect_hosts")
        if artifact.get("kind") not in {"appimage", "appimage-runtime"} and redirect_hosts:
            raise PackagingError(f"only locked AppImage artifacts may declare redirect hosts")
        if artifact.get("kind") == "tar.xz":
            archive_root = artifact.get("archive_root")
            if not isinstance(archive_root, str) or Path(archive_root).name != archive_root:
                raise PackagingError(f"archive {artifact_id} has an invalid archive_root")
        by_id[artifact_id] = artifact
        filenames.add(filename)
    return data, by_id


def fetch_artifact(artifact: JsonObject, cache_dir: Path, offline: bool) -> Path:
    """Return a verified cached artifact, downloading atomically if needed."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    destination = cache_dir / artifact["filename"]
    expected = artifact["sha256"]
    if destination.exists():
        if not destination.is_file() or destination.is_symlink():
            raise PackagingError(f"cache path is not a regular file: {destination}")
        actual = sha256_file(destination)
        if actual == expected:
            os.chmod(destination, 0o755 if artifact.get("executable") is True else 0o644)
            return destination
        if offline:
            raise PackagingError(
                f"cached {artifact['id']} has SHA-256 {actual}, expected {expected}; offline mode cannot replace it"
            )
        destination.unlink()
    elif offline:
        raise PackagingError(f"artifact {artifact['id']} is not cached: {destination}")

    fd, temporary_name = tempfile.mkstemp(prefix=f".{artifact['filename']}.", suffix=".part", dir=cache_dir)
    temporary = Path(temporary_name)
    try:
        digest = hashlib.sha256()
        request = urllib.request.Request(
            artifact["url"], headers={"User-Agent": "Narwhal3D-locked-fetch/1"}
        )
        with os.fdopen(fd, "wb") as output, urllib.request.urlopen(request, timeout=60) as response:
            final_url = response.geturl()
            if final_url != artifact["url"]:
                final = urllib.parse.urlsplit(final_url)
                allowed_hosts = artifact.get("allowed_redirect_hosts", [])
                if final.scheme != "https" or final.hostname not in allowed_hosts:
                    raise PackagingError(
                        f"artifact {artifact['id']} redirected to a non-locked host: {final_url}"
                    )
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                digest.update(chunk)
            output.flush()
            os.fsync(output.fileno())
        actual = digest.hexdigest()
        if actual != expected:
            raise PackagingError(
                f"downloaded {artifact['id']} has SHA-256 {actual}, expected {expected}"
            )
        os.chmod(temporary, 0o755 if artifact.get("executable") is True else 0o644)
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return destination


def _normalized_archive_path(name: str, *, base: str = "") -> PurePosixPath:
    """Normalize an archive path and reject absolute or escaping paths."""
    if not name or "\\" in name:
        raise PackagingError(f"unsafe archive path: {name!r}")
    candidate = name if not base else posixpath.join(base, name)
    if candidate.startswith("/"):
        raise PackagingError(f"absolute archive path: {name!r}")
    normalized = posixpath.normpath(candidate)
    path = PurePosixPath(normalized)
    if normalized in {"", ".", ".."} or ".." in path.parts:
        raise PackagingError(f"escaping archive path: {name!r}")
    return path


def validate_tar_members(archive: tarfile.TarFile, archive_root: str) -> None:
    """Reject traversal, escaping links, devices, and unexpected top-level entries."""
    for member in archive.getmembers():
        path = _normalized_archive_path(member.name)
        if not path.parts or path.parts[0] != archive_root:
            raise PackagingError(f"archive member is outside locked root {archive_root!r}: {member.name!r}")
        if member.ischr() or member.isblk() or member.isfifo() or member.isdev():
            raise PackagingError(f"archive contains a special device entry: {member.name!r}")
        if member.issym():
            target = _normalized_archive_path(member.linkname, base=posixpath.dirname(member.name))
            if not target.parts or target.parts[0] != archive_root:
                raise PackagingError(f"archive symlink escapes locked root: {member.name!r}")
        elif member.islnk():
            target = _normalized_archive_path(member.linkname)
            if not target.parts or target.parts[0] != archive_root:
                raise PackagingError(f"archive hard link escapes locked root: {member.name!r}")


def extract_locked_archive(artifact: JsonObject, archive_path: Path, destination: Path) -> None:
    """Safely and atomically extract a locked single-root tar archive."""
    marker_name = ".narwhal3d-artifact.json"
    if destination.exists():
        marker = destination / marker_name
        try:
            provenance = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PackagingError(f"existing extraction lacks valid provenance: {destination}") from exc
        if (
            provenance.get("artifact_id") == artifact["id"]
            and provenance.get("sha256") == artifact["sha256"]
        ):
            return
        raise PackagingError(f"existing extraction does not match the current lock: {destination}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    try:
        with tarfile.open(archive_path, mode="r:xz") as archive:
            validate_tar_members(archive, artifact["archive_root"])
            if not hasattr(tarfile, "data_filter"):
                raise PackagingError("safe extraction requires Python with tarfile.data_filter")
            archive.extractall(temporary, filter="data")
        extracted_root = temporary / artifact["archive_root"]
        if not extracted_root.is_dir() or extracted_root.is_symlink():
            raise PackagingError("archive did not produce its locked top-level directory")
        marker_data = {
            "schema": 1,
            "artifact_id": artifact["id"],
            "filename": artifact["filename"],
            "sha256": artifact["sha256"],
        }
        (extracted_root / marker_name).write_text(
            json.dumps(marker_data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        os.replace(extracted_root, destination)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def parse_extract_specs(values: list[str]) -> dict[str, Path]:
    """Parse repeated ARTIFACT_ID=DESTINATION extraction arguments."""
    result: dict[str, Path] = {}
    for value in values:
        artifact_id, separator, destination = value.partition("=")
        if not separator or not artifact_id or not destination:
            raise PackagingError(f"invalid --extract value {value!r}; expected ARTIFACT_ID=DESTINATION")
        if artifact_id in result:
            raise PackagingError(f"duplicate extraction request: {artifact_id}")
        result[artifact_id] = Path(destination).expanduser().resolve()
    return result


def self_check() -> None:
    """Exercise lock validation and archive traversal checks without networking."""
    lock, artifacts = load_lock(DEFAULT_LOCK)
    expected_development_runtime = {
        "purpose": "development-bootstrap-only",
        "flavor": "official-blender-runtime",
        "blender_version": "5.2.2",
        "bundle_path": "tools/blender-5.2.2-linux-x64",
    }
    if lock.get("runtime") != expected_development_runtime:
        raise PackagingError("official Blender lock must remain development-bootstrap-only")
    if set(artifacts) != set(EXPECTED_LOCKS):
        raise PackagingError(f"lock artifact ids differ from required set: {sorted(artifacts)}")
    for artifact_id, (expected_url, expected_sha256) in EXPECTED_LOCKS.items():
        artifact = artifacts[artifact_id]
        if artifact["url"] != expected_url or artifact["sha256"] != expected_sha256:
            raise PackagingError(f"lock URL or SHA-256 changed for required artifact: {artifact_id}")
    for artifact_id in ("appimagetool", "appimage-runtime"):
        if artifacts[artifact_id].get("allowed_redirect_hosts") != [
            "release-assets.githubusercontent.com"
        ]:
            raise PackagingError(f"{artifact_id} redirect host policy changed")
    assert _normalized_archive_path("root/bin/tool") == PurePosixPath("root/bin/tool")
    for unsafe in ("../escape", "/absolute", "root/../../escape", "root\\escape"):
        try:
            _normalized_archive_path(unsafe)
        except PackagingError:
            pass
        else:
            raise PackagingError(f"self-check accepted unsafe archive path: {unsafe!r}")
    print("fetch_locked.py self-check: OK")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch immutable artifacts from a JSON lock and verify SHA-256 before use."
    )
    parser.add_argument("artifact_ids", nargs="*", help="artifact ids to fetch (default: all)")
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK, help="artifact lock JSON")
    parser.add_argument("--cache-dir", type=Path, default=default_cache_dir(), help="artifact cache directory")
    parser.add_argument("--offline", action="store_true", help="require already-cached verified artifacts")
    parser.add_argument(
        "--extract",
        action="append",
        default=[],
        metavar="ARTIFACT_ID=DESTINATION",
        help="safely extract a selected locked tar archive",
    )
    parser.add_argument("--self-check", action="store_true", help="run local parser/security checks and exit")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.self_check:
            self_check()
            return 0
        _, artifacts = load_lock(args.lock.resolve())
        extracts = parse_extract_specs(args.extract)
        selected_ids = args.artifact_ids or list(artifacts)
        unknown = (set(selected_ids) | set(extracts)) - set(artifacts)
        if unknown:
            raise PackagingError(f"unknown artifact ids: {', '.join(sorted(unknown))}")
        selected_ids = list(dict.fromkeys([*selected_ids, *extracts]))
        cache_dir = args.cache_dir.expanduser().resolve()
        fetched: dict[str, Path] = {}
        for artifact_id in selected_ids:
            fetched[artifact_id] = fetch_artifact(artifacts[artifact_id], cache_dir, args.offline)
            print(f"{artifact_id}={fetched[artifact_id]}")
        for artifact_id, destination in extracts.items():
            artifact = artifacts[artifact_id]
            if artifact["kind"] != "tar.xz":
                raise PackagingError(f"artifact {artifact_id} is not an extractable tar.xz archive")
            extract_locked_archive(artifact, fetched[artifact_id], destination)
            print(f"extracted {artifact_id} -> {destination}")
        return 0
    except (PackagingError, OSError, tarfile.TarError, urllib.error.URLError) as exc:
        print(f"fetch_locked.py: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
