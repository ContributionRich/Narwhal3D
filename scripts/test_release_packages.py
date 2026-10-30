#!/usr/bin/env python3
"""Host tests of release orchestration; do not claim target GUI acceptance."""
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

import assemble_macos_release as mac
import assemble_linux_release as linux
import assemble_windows_exe as windows


class PackageTests(unittest.TestCase):
    def test_appimage_compression_keeps_explicit_cpu_and_memory_bounds(self):
        command = linux.appimagetool_command(Path("tool"), Path("runtime"), Path("appdir"), Path("appimage"))
        self.assertEqual(command[2:4], ["--runtime-file", "runtime"])
        self.assertIn("--mksquashfs-opt=4", command)
        self.assertIn("--mksquashfs-opt=512M", command)
        self.assertEqual(command[-2:], ["appdir", "appimage"])

    def test_mac_inventory_tracks_bytes_and_rejects_escaping_links(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "App.app"
            bundle.mkdir()
            (bundle / "file").write_bytes(b"one")
            before = mac.inventory(bundle)
            (bundle / "file").write_bytes(b"two")
            self.assertNotEqual(before, mac.inventory(bundle))
            (bundle / "alias").symlink_to("file")
            self.assertIn({"path": "alias", "link": "file"}, mac.inventory(bundle))
            (root / "outside").write_text("not bundled")
            (bundle / "escape").symlink_to("../outside")
            with self.assertRaises(ValueError):
                mac.inventory(bundle)

    def test_macho_scan_does_not_hide_wrong_architectures(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            magic = [b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xce",
                     b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"]
            for i, header in enumerate(magic):
                (root / str(i)).write_bytes(header + b"fixture")
            (root / "text").write_text("not a binary")
            self.assertEqual(len(list(mac.binaries(root))), 8)

    def test_macho_dependency_and_minimum_os_gates(self):
        def result(dependency="/usr/lib/libSystem.B.dylib", minimum="14.0"):
            def call(*args, **kwargs):
                if args[0] == "lipo":
                    return ""
                if "-L" in args:
                    return "fixture:\n\t" + dependency + " (compatibility version 1.0.0)\n"
                return "cmd LC_BUILD_VERSION\n minos " + minimum + "\n"
            return call
        with patch.object(mac, "run", side_effect=result()):
            mac.verify_macho(Path("fixture"))
        for dependency, minimum in (("/Users/builder/lib.dylib", "14.0"),
                                    ("/opt/homebrew/lib.dylib", "14.0"),
                                    ("/usr/lib/libSystem.B.dylib", "14.0.1"),
                                    ("/usr/lib/libSystem.B.dylib", "15.0")):
            with patch.object(mac, "run", side_effect=result(dependency, minimum)):
                with self.assertRaises(ValueError):
                    mac.verify_macho(Path("fixture"))

    def test_windows_stub_requires_current_version_and_every_icon(self):
        icon = Path(__file__).resolve().parents[1] / "packaging/windows/narwhal3d.ico"
        with tempfile.TemporaryDirectory() as directory:
            stub = Path(directory) / "stub.exe"
            manifest = b"level='asInvoker' >true</longPathAware>"
            stub.write_bytes(b"MZ" + "0.4.8".encode("utf-16le") + manifest + icon.read_bytes())
            windows.validate_stub(stub, "0.4.8", icon)
            with self.assertRaises(ValueError):
                windows.validate_stub(stub, "0.4.9", icon)
            stub.write_bytes(b"MZ" + "0.4.8".encode("utf-16le") + manifest)
            with self.assertRaises(ValueError):
                windows.validate_stub(stub, "0.4.8", icon)
            stub.write_bytes(b"MZ" + "0.4.8".encode("utf-16le") + icon.read_bytes())
            with self.assertRaises(ValueError):
                windows.validate_stub(stub, "0.4.8", icon)

    def test_exe_verification_checks_extracted_identity_and_package(self):
        with tempfile.TemporaryDirectory() as directory:
            exe = Path(directory) / "Narwhal_3D-0.4.8-Windows-x86_64.exe"
            packet = types.SimpleNamespace(stdout=json.dumps({"version": "0.4.8", "source_revision": "a" * 40}))
            with patch.object(windows.subprocess, "run", return_value=packet) as extract, \
                 patch.object(windows.verifier, "verify_directory") as verify:
                windows.verify_executable(exe, Path("/packer"), "0.4.8", "a" * 40)
                self.assertIn("--extract-for-verification", extract.call_args.args[0])
                verify.assert_called_once()
                with self.assertRaises(ValueError):
                    windows.verify_executable(exe, Path("/packer"), "0.4.8", "b" * 40)
                self.assertEqual(verify.call_count, 1)


if __name__ == "__main__":
    unittest.main()
