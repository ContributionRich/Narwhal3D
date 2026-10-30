#!/usr/bin/env python3
"""Wrap a verified Windows distribution, then verify the extracted EXE payload.

Python orchestrates existing package gates. Rust owns ZIP parsing, streaming,
member integrity, path admission, extraction and executable assembly.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import struct
import subprocess
import tempfile

from release_policy import validate_preview_version
import verify_windows_release as verifier


def validate_stub(stub: Path, version: str, icon: Path) -> None:
    if stub.is_symlink() or not stub.is_file() or stub.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("portable stub must be a bounded regular file")
    data = stub.read_bytes()
    if not data.startswith(b"MZ") or version.encode("utf-16le") not in data:
        raise ValueError("portable stub lacks the expected native version resource")
    if b"level='asInvoker'" not in data or b">true</longPathAware>" not in data:
        raise ValueError("portable stub lacks non-elevated long-path application manifest")
    images = icon.read_bytes()
    if len(images) < 6 or images[:4] != b"\0\0\1\0":
        raise ValueError("invalid source icon")
    count = struct.unpack_from("<H", images, 4)[0]
    if count != 7 or len(images) < 6 + count * 16:
        raise ValueError("expected all seven native icon sizes")
    for i in range(count):
        size, offset = struct.unpack_from("<II", images, 6 + i * 16 + 8)
        if not size or offset + size > len(images) or images[offset:offset + size] not in data:
            raise ValueError("portable stub has stale or missing native icon resources")


def verify_executable(executable: Path, packer: Path, version: str, source: str) -> None:
    expected = f"Narwhal_3D-{version}-Windows-x86_64.exe"
    if executable.name != expected or executable.is_symlink():
        raise ValueError(f"expected a regular {expected}")
    # The host verifier never launches a Windows payload. It extracts actual bytes
    # through the same bounded Rust reader used by the Windows launcher.
    with tempfile.TemporaryDirectory(prefix="narwhal-exe-verify-", dir=executable.parent) as temporary:
        package = Path(temporary) / "package"
        result = subprocess.run([str(packer), "--extract-for-verification", str(executable), str(package)],
                                check=True, text=True, capture_output=True, timeout=1800)
        identity = json.loads(result.stdout)
        if identity.get("version") != version or identity.get("source_revision") != source:
            raise ValueError("portable EXE identity differs from the requested release")
        verifier.verify_directory(package, argparse.Namespace(expected_version=version,
                                  expected_source_revision=source))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path)
    parser.add_argument("--stub", type=Path)
    parser.add_argument("--packer", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify-existing", type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--source-revision", required=True)
    args = parser.parse_args()
    validate_preview_version(args.version)
    packer = args.packer.resolve(strict=True)
    if args.verify_existing:
        verify_executable(args.verify_existing.resolve(strict=True), packer, args.version, args.source_revision)
        print("Extracted portable EXE passed the Windows package gates.")
        return
    if not args.package or not args.stub or not args.output:
        parser.error("assembly requires --package, --stub and --output")
    package, stub, output = args.package.resolve(), args.stub.resolve(), args.output.resolve()
    for path in (output, Path(str(output) + ".sha256"), Path(str(output) + ".build.json")):
        if path.exists() or path.is_symlink():
            raise ValueError(f"refusing to overwrite {path}")
    verifier.verify_directory(package, argparse.Namespace(expected_version=args.version,
                              expected_source_revision=args.source_revision))
    icon = Path(__file__).resolve().parents[1] / "packaging/windows/narwhal3d.ico"
    validate_stub(stub, args.version, icon)
    subprocess.run([str(packer), "--pack", str(stub), str(package), str(output), args.version,
                    args.source_revision], check=True, timeout=1800)
    verify_executable(output, packer, args.version, args.source_revision)
    digest = verifier.sha256(output)
    Path(str(output) + ".sha256").write_text(f"{digest}  {output.name}\n")
    receipt = {"schema": 1, "version": args.version, "source_revision": args.source_revision,
               "sha256": digest, "size": output.stat().st_size,
               "stub_sha256": verifier.sha256(stub), "packer_sha256": verifier.sha256(packer),
               "extracted_package_verified": True, "native_gui_qualified": False}
    Path(str(output) + ".build.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(f"Assembled and verified {output.name}; native Windows acceptance remains required.")


if __name__ == "__main__":
    main()
