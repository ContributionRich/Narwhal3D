"""Focused P-01 tests; run with the repository's Python interpreter."""
from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import sys
import tarfile
import tempfile
import types
from importlib import import_module, util as importlib_util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# The production package eagerly imports Blender UI. Keep this pure resolver
# suite independent of bpy while retaining real package-relative imports.
pkg = types.ModuleType("narwhal3d")
pkg.__path__ = [str(ROOT / "narwhal3d")]
sys.modules["narwhal3d"] = pkg
runtime_tools = import_module("narwhal3d.runtime_tools")
vector_import = import_module("narwhal3d.vector_import")


def exe(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    with tempfile.TemporaryDirectory() as work:
        root = Path(work) / "app"
        dev = root / "tools" / "runtime"
        env_root = Path(work) / "override"
        key = "linux-x86_64"

        bundled = exe(root / "runtime" / key / "bin" / "pdfinfo")
        path_tool = exe(Path(work) / "path" / "pdfinfo")
        path_inkscape = exe(Path(work) / "path" / "inkscape")
        env_tool = exe(env_root / key / "bin" / "pdfinfo")
        alias = exe(root / "runtime" / key / "bin" / "libreoffice")
        check(runtime_tools.resolve_tool("pdfinfo", app_root=root, env={"PATH": str(path_tool.parent)}) == str(bundled),
              "app-local bundle must beat PATH")
        check(runtime_tools.resolve_tool("soffice", app_root=root, env={"PATH": ""}) == str(alias),
              "soffice must resolve the libreoffice alias")
        check(runtime_tools.resolve_tool("pdfinfo", app_root=root, env={"NARWHAL3D_RUNTIME_DIR": str(env_root), "PATH": ""}) == str(env_tool),
              "explicit runtime override must win")
        check(runtime_tools.runtime_key(platform="Linux", arch="amd64") == key,
              "platform/architecture normalization")
        runtime_root = root / "runtime"
        inventory = runtime_root / key / "inventory.json"
        digest = hashlib.sha256(bundled.read_bytes()).hexdigest()
        inventory.parent.mkdir(parents=True, exist_ok=True)
        (runtime_root / "manifest.json").write_text(json.dumps({
            "schema": 1,
            "tools": {"pdfinfo": {
                "executable": "pdfinfo", "executable_sha256": digest,
                "license": "test", "sha256": None, "version": "test",
            }},
        }), encoding="utf-8")
        inventory.write_text(json.dumps({
            "schema": 1,
            "platform": key,
            "tools": {"pdfinfo": {
                "executable": "pdfinfo", "executable_sha256": digest, "verified": True,
            }},
        }), encoding="utf-8")
        check(runtime_tools.resolve_tool(
            "pdfinfo", app_root=root, env={"PATH": ""}, strict_bundled=True) == str(bundled),
            "strict bundled mode accepts an inventory-bound executable")
        bundled.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        try:
            runtime_tools.resolve_tool(
                "pdfinfo", app_root=root, env={"PATH": ""}, strict_bundled=True)
        except runtime_tools.RuntimeToolError:
            pass
        else:
            raise AssertionError("strict bundled mode accepted a mutated executable")
        check(True, "strict bundled mode rejects executable hash mutation")

        trace = exe(runtime_root / key / "bin" / "tracedraw-cli")
        archive = trace.parent / "trace.tar.gz"
        archive.write_bytes(b"archive")
        trace_digest = hashlib.sha256(trace.read_bytes()).hexdigest()
        archive_digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        (runtime_root / "manifest.json").write_text(json.dumps({
            "schema": 1,
            "tools": {"tracedraw-cli": {
                "executable": "tracedraw-cli", "executable_sha256": trace_digest,
                "revision": "abc", "sha256": archive_digest, "version": "test",
            }},
        }), encoding="utf-8")
        inventory.write_text(json.dumps({
            "schema": 1, "platform": key,
            "tools": {"tracedraw-cli": {
                "artifact": "trace.tar.gz", "executable": "tracedraw-cli",
                "executable_sha256": trace_digest, "revision": "abc",
                "sha256": archive_digest, "version": "test", "verified": True,
            }},
        }), encoding="utf-8")
        check(runtime_tools.resolve_tool(
            "tracedraw-cli", app_root=root, env={"PATH": ""}, strict_bundled=True)
              == str(trace),
              "strict mode binds TraceDraw archive, revision, and executable")
        archive.write_bytes(b"changed")
        try:
            runtime_tools.resolve_tool(
                "tracedraw-cli", app_root=root, env={"PATH": ""}, strict_bundled=True)
        except runtime_tools.RuntimeToolError:
            pass
        else:
            raise AssertionError("strict mode accepted a mutated TraceDraw archive")
        check(True, "strict mode rejects archive mutation during resolution")
        inventory.write_text(json.dumps({
            "schema": 1, "platform": key, "tools": {"tracedraw-cli": []},
        }), encoding="utf-8")
        try:
            runtime_tools.resolve_tool(
                "tracedraw-cli", app_root=root, env={"PATH": ""}, strict_bundled=True)
        except runtime_tools.RuntimeToolError:
            pass
        else:
            raise AssertionError("strict mode accepted malformed inventory")
        check(True, "malformed inventory fails closed without leaking an exception")
        try:
            runtime_tools.resolve_tool("inkscape", app_root=root, env={"PATH": str(path_inkscape.parent)}, strict_bundled=True)
        except runtime_tools.RuntimeToolError:
            pass
        else:
            raise AssertionError("strict bundled mode must refuse PATH fallback")

        calls = []
        original = runtime_tools.resolve_tool
        runtime_tools.resolve_tool = lambda name, **kwargs: calls.append(name) or "/bundle/" + name
        try:
            check(vector_import._need("pdfinfo", "PDF import") == "/bundle/pdfinfo",
                  "vector import must use the runtime resolver")
        finally:
            runtime_tools.resolve_tool = original
        check(calls == ["pdfinfo"], "vector import resolver call")

    manifest = json.loads((ROOT / "runtime" / "manifest.json").read_text(encoding="utf-8"))
    check("inventory" not in manifest and "platform" not in manifest
          and "/home/" not in json.dumps(manifest),
          "source manifest is immutable and contains no machine-specific inventory")
    trace = manifest["tools"]["tracedraw-cli"]
    check(trace["revision"] == "821ba1545e3d3f75843fe7d3f912214800929821"
          and trace["sha256"] == "08e74a27fa7b5a7350e718fd0660e6926212cf36d29203caab21eaeb130af20c"
          and trace["executable_sha256"] == "fe21ae1024bdfe0935ae28a38c70a67d743a28c3ccbcb003238f58882305894d",
          "TraceDraw source, archive, and executable are pinned independently")
    bootstrap_path = ROOT / "scripts" / "bootstrap_linux_runtime.py"
    bootstrap = bootstrap_path.read_text(encoding="utf-8")
    check("inventory_path.write_text" in bootstrap
          and "manifest_path.write_text" not in bootstrap,
          "bootstrap writes generated platform inventory, never the source manifest")

    spec = importlib_util.spec_from_file_location("runtime_bootstrap_test", bootstrap_path)
    bootstrap_module = importlib_util.module_from_spec(spec)
    spec.loader.exec_module(bootstrap_module)
    class BrokenResponse:
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def read(self, _size=-1):
            raise TimeoutError("test timeout")

    original_open = bootstrap_module.urllib.request.urlopen
    bootstrap_module.urllib.request.urlopen = lambda *_a, **_k: BrokenResponse()
    try:
        with tempfile.TemporaryDirectory() as work:
            target = Path(work) / "tool.AppImage"
            try:
                bootstrap_module._download("https://invalid.example/tool", target)
            except SystemExit as exc:
                check("download failed" in str(exc) and not target.exists()
                      and not target.with_name(target.name + ".partial").exists(),
                      "failed downloads report concisely and leave no partial artifact")
            else:
                raise AssertionError("failed download should stop staging")
    finally:
        bootstrap_module.urllib.request.urlopen = original_open
    with tempfile.TemporaryDirectory() as work:
        work = Path(work)
        archive = work / "trace.tar.gz"
        payload = b"#!/bin/sh\nexit 0\n"
        with tarfile.open(archive, "w:gz") as bundle:
            member = tarfile.TarInfo("tracedraw-cli")
            member.size = len(payload)
            bundle.addfile(member, io.BytesIO(payload))
        extracted = work / "tracedraw-cli"
        bootstrap_module._extract_archive_member(archive, "tracedraw-cli", extracted)
        check(extracted.read_bytes() == payload,
              "archive staging extracts only the exact TraceDraw CLI member")
        unsafe = work / "unsafe.tar.gz"
        with tarfile.open(unsafe, "w:gz") as bundle:
            member = tarfile.TarInfo("../escape")
            member.size = 1
            bundle.addfile(member, io.BytesIO(b"x"))
            wanted = tarfile.TarInfo("tracedraw-cli")
            wanted.size = len(payload)
            bundle.addfile(wanted, io.BytesIO(payload))
        try:
            bootstrap_module._extract_archive_member(unsafe, "tracedraw-cli", extracted)
        except SystemExit:
            pass
        else:
            raise AssertionError("unsafe archive path was accepted")
        check(True, "archive staging rejects traversal even beside a valid member")
    with tempfile.TemporaryDirectory() as work:
        work = Path(work)
        stage_root = work / "root"
        runtime_root = stage_root / "runtime"
        runtime_root.mkdir(parents=True)
        source = work / "trace.tar.gz"
        payload = b"#!/bin/sh\nexit 0\n"
        with tarfile.open(source, "w:gz") as bundle:
            member = tarfile.TarInfo("tracedraw-cli")
            member.size = len(payload)
            bundle.addfile(member, io.BytesIO(payload))
        source_digest = hashlib.sha256(source.read_bytes()).hexdigest()
        payload_digest = hashlib.sha256(payload).hexdigest()
        (runtime_root / "manifest.json").write_text(json.dumps({
            "schema": 1,
            "tools": {"tracedraw-cli": {
                "archive_member": "tracedraw-cli",
                "archive_name": source.name,
                "executable": "tracedraw-cli",
                "executable_sha256": payload_digest,
                "license": "test", "revision": "abc",
                "sha256": source_digest, "source": "test", "version": "test",
            }},
        }), encoding="utf-8")
        original_root = bootstrap_module._root
        bootstrap_module._root = lambda: stage_root
        args = ["--artifact", f"tracedraw-cli={source},{source_digest}"]
        try:
            check(bootstrap_module.main(args) == 0,
                  "one-tool TraceDraw runtime stages with complete verification")
            check(bootstrap_module.main(args) == 0,
                  "TraceDraw archive staging is idempotent")
            staged_archive = runtime_root / "linux-x86_64" / "bin" / source.name
            staged_archive.write_bytes(b"mutated")
            check(bootstrap_module.main(["--check"]) == 2,
                  "bootstrap check rehashes and rejects a mutated staged archive")
        finally:
            bootstrap_module._root = original_root
    stage_path = ROOT / "scripts" / "stage_fedora_inkscape.py"
    stage_spec = importlib_util.spec_from_file_location("fedora_stage_test", stage_path)
    stage = importlib_util.module_from_spec(stage_spec)
    stage_spec.loader.exec_module(stage)
    with tempfile.TemporaryDirectory() as work:
        wrapper = Path(work) / "inkscape"
        stage._wrapper(wrapper)
        body = wrapper.read_text(encoding="utf-8")
        check(wrapper.is_file() and os.access(wrapper, os.X_OK)
              and "SYSROOT" in body and "LD_LIBRARY_PATH" in body,
              "Fedora wrapper scopes executable and libraries to the private sysroot")

    class BadSignature:
        returncode = 1
        stdout = "NOT OK"
        stderr = "bad signature"

    original_run = stage.subprocess.run
    stage.subprocess.run = lambda *_a, **_k: BadSignature()
    try:
        try:
            stage._verified(Path("untrusted.rpm"))
        except SystemExit as exc:
            check("verification failed" in str(exc),
                  "Fedora staging refuses an RPM whose signature is not valid")
        else:
            raise AssertionError("untrusted RPM was accepted")
    finally:
        stage.subprocess.run = original_run
    print("runtime tools: 23/23")


if __name__ == "__main__":
    main()
