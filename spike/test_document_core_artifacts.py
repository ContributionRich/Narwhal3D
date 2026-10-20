#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the reproducible document-core artifact pipeline."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import build_document_core as build  # noqa: E402
from scripts import verify_document_core as verify  # noqa: E402


class ArtifactFixture:
    root: Path
    sources: list[dict[str, str]]

    def __init__(self, parent: Path) -> None:
        self.root = parent / "document-core"
        self.root.mkdir()
        self.paths: dict[str, Path] = {}
        for platform_name, info in verify.PLATFORMS.items():
            path = self.root / platform_name / str(info["filename"])
            path.parent.mkdir()
            path.write_bytes((f"synthetic {platform_name} artifact\n").encode("ascii"))
            self.paths[platform_name] = path
        self.sources = verify.source_inventory()
        self.manifest: dict[str, object] = {
            "schema": 1,
            "component": verify.COMPONENT,
            "component_version": verify.COMPONENT_VERSION,
            "protocol": verify.PROTOCOL,
            "rust_toolchain": verify.RUST_TOOLCHAIN,
            "linux_build_image": verify.LINUX_IMAGE,
            "windows_target": verify.PLATFORMS["windows-x86_64"]["target"],
            "builds_per_platform": 2,
            "source_date_epoch": 0,
            "source_aggregate_sha256": verify.source_aggregate(self.sources),
            "source_files": self.sources,
            "artifacts": [
                self.record(self.paths[name], name)
                for name in sorted(verify.PLATFORMS)
            ],
        }
        self.write_manifest()

    @staticmethod
    def record(path: Path, platform_name: str) -> dict[str, object]:
        info = verify.PLATFORMS[platform_name]
        common: dict[str, object] = {
            "platform": platform_name,
            "target": info["target"],
            "path": f"{platform_name}/{info['filename']}",
            "size": path.stat().st_size,
            "sha256": verify.sha256(path),
        }
        if platform_name == "linux-x86_64":
            common.update(
                {
                    "format": "ELF64-x86-64",
                    "dependencies": list(verify.LINUX_DEPENDENCIES),
                    "max_glibc": "2.28",
                    "build_id": None,
                    "protocol": verify.PROTOCOL,
                    "protocol_check": "executed-version-pack-inspect-unpack",
                }
            )
        else:
            common.update(
                {
                    "format": "PE32+-x86-64",
                    "dependencies": list(verify.WINDOWS_IMPORTS),
                    "coff_timestamp": 0,
                    "protocol": verify.PROTOCOL,
                    "protocol_check": "inspected-binary-bytes",
                }
            )
        return common

    def write_manifest(self) -> None:
        (self.root / verify.MANIFEST_NAME).write_text(
            json.dumps(self.manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def verify(self) -> dict[str, object]:
        with mock.patch.object(verify, "inspect_artifact", side_effect=self.record):
            return verify.verify_directory(self.root)


class DocumentCoreArtifactTests(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.temporary = tempfile.TemporaryDirectory(prefix="document-core-artifact-test-")
        self.temp = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def fixture(self) -> ArtifactFixture:
        return ArtifactFixture(self.temp)

    def test_builder_and_verifier_self_checks_are_offline(self) -> None:
        for relative, marker in (
            ("scripts/build_document_core.py", "build_document_core.py self-check: OK"),
            ("scripts/verify_document_core.py", "verify_document_core.py self-check: OK"),
        ):
            result = subprocess.run(
                [sys.executable, str(ROOT / relative), "--self-check"],
                cwd=ROOT,
                env={"LC_ALL": "C", "PATH": os.defpath, "TZ": "UTC"},
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn(marker, result.stdout)

    def test_registered_gate_runs_all_rust_tests_and_clippy(self) -> None:
        target = self.temp / "rust-gate-target"
        environment = os.environ.copy()
        environment.update({
            "CARGO_INCREMENTAL": "0",
            "CARGO_NET_OFFLINE": "true",
            "LC_ALL": "C",
            "TZ": "UTC",
        })
        common = [
            "--all-targets",
            "--locked",
            "--offline",
            "--manifest-path",
            str(verify.CRATE / "Cargo.toml"),
            "--target-dir",
            str(target),
        ]
        for subcommand, suffix in (("test", []), ("clippy", ["--", "-D", "warnings"])):
            result = subprocess.run(
                ["cargo", f"+{verify.RUST_TOOLCHAIN}", subcommand, *common, *suffix],
                cwd=ROOT,
                env=environment,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                timeout=180,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_current_artifact_directory_is_fully_verified_when_present(self) -> None:
        current = ROOT / "artifacts" / "document-core"
        if current.exists():
            if (current / verify.MANIFEST_NAME).is_file():
                manifest = verify.verify_directory(current)
                self.assertEqual(manifest["protocol"], verify.PROTOCOL)
            else:
                with self.assertRaises(verify.VerificationError):
                    verify.verify_directory(current)

    def test_valid_manifest_binds_all_current_crate_sources(self) -> None:
        fixture = self.fixture()
        manifest = fixture.verify()
        self.assertEqual(manifest["source_files"], verify.source_inventory())
        paths = {item["path"] for item in fixture.sources}
        self.assertIn("Cargo.toml", paths)
        self.assertIn("Cargo.lock", paths)
        self.assertIn("rust-toolchain.toml", paths)
        self.assertIn(".cargo/config.toml", paths)
        self.assertIn("README.md", paths)
        self.assertIn("src/lib.rs", paths)
        self.assertIn("src/main.rs", paths)
        self.assertIn("tests/document_core.rs", paths)

    def test_manifest_identity_and_hash_mutations_are_rejected(self) -> None:
        for field, value, message in (
            ("component", "different-component", "manifest component"),
            ("protocol", "narwhal-document-core 2", "manifest protocol"),
            ("rust_toolchain", "stable", "manifest rust_toolchain"),
            ("linux_build_image", "mutable:latest", "manifest linux_build_image"),
        ):
            with self.subTest(field=field):
                child = self.temp / field
                child.mkdir()
                fixture = ArtifactFixture(child)
                fixture.manifest[field] = value
                fixture.write_manifest()
                with self.assertRaisesRegex(verify.VerificationError, message):
                    fixture.verify()

        child = self.temp / "artifact-hash"
        child.mkdir()
        fixture = ArtifactFixture(child)
        artifacts = fixture.manifest["artifacts"]
        assert isinstance(artifacts, list)
        artifacts[0]["sha256"] = "0" * 64
        fixture.write_manifest()
        with self.assertRaisesRegex(verify.VerificationError, "manifest hash differs"):
            fixture.verify()

        child = self.temp / "source-hash"
        child.mkdir()
        fixture = ArtifactFixture(child)
        source_files = fixture.manifest["source_files"]
        assert isinstance(source_files, list)
        source_files[0]["sha256"] = "0" * 64
        fixture.write_manifest()
        with self.assertRaisesRegex(verify.VerificationError, "source inventory differs"):
            fixture.verify()

    def test_source_addition_and_source_content_drift_are_rejected(self) -> None:
        for mutation in ("addition", "content"):
            with self.subTest(mutation=mutation):
                child = self.temp / mutation
                child.mkdir()
                fixture = ArtifactFixture(child)
                copied_crate = child / "crate"
                shutil.copytree(
                    verify.CRATE,
                    copied_crate,
                    ignore=shutil.ignore_patterns("target", ".git"),
                )
                if mutation == "addition":
                    (copied_crate / "src" / "unexpected.rs").write_text(
                        "pub const DRIFT: bool = true;\n", encoding="utf-8"
                    )
                else:
                    main = copied_crate / "src" / "main.rs"
                    main.write_text(main.read_text(encoding="utf-8") + "\n", encoding="utf-8")
                with mock.patch.object(verify, "CRATE", copied_crate):
                    with self.assertRaisesRegex(
                        verify.VerificationError,
                        "source inventory differs",
                    ):
                        verify.verify_directory(fixture.root)

    def test_artifact_corruption_is_rejected_before_binary_inspection(self) -> None:
        fixture = self.fixture()
        linux = fixture.paths["linux-x86_64"]
        content = bytearray(linux.read_bytes())
        content[0] ^= 0x01
        linux.write_bytes(content)
        with (
            mock.patch.object(verify, "inspect_artifact") as inspect,
            self.assertRaisesRegex(verify.VerificationError, "manifest hash differs"),
        ):
            verify.verify_directory(fixture.root)
        inspect.assert_not_called()

    def test_missing_and_extra_artifacts_are_rejected(self) -> None:
        missing_parent = self.temp / "missing"
        missing_parent.mkdir()
        missing = ArtifactFixture(missing_parent)
        missing.paths["windows-x86_64"].unlink()
        with self.assertRaisesRegex(verify.VerificationError, "artifact set differs"):
            missing.verify()

        extra_parent = self.temp / "extra"
        extra_parent.mkdir()
        extra = ArtifactFixture(extra_parent)
        (extra.root / "untracked.txt").write_text("not allowed\n", encoding="utf-8")
        with self.assertRaisesRegex(verify.VerificationError, "artifact set differs"):
            extra.verify()

        directory_parent = self.temp / "extra-directory"
        directory_parent.mkdir()
        extra_directory = ArtifactFixture(directory_parent)
        (extra_directory.root / "empty").mkdir()
        with self.assertRaisesRegex(verify.VerificationError, "artifact set differs"):
            extra_directory.verify()

    def test_windows_protocol_bytes_must_be_exact_and_unique(self) -> None:
        binary = self.temp / "protocol.exe"
        binary.write_bytes(b"prefix\0" + verify.PROTOCOL.encode("ascii") + b"\0suffix")
        verify.inspect_windows_protocol_bytes(binary)

        for payload in (
            b"narwhal-document-core 2",
            b"narwhal-document-core 1\0narwhal-document-core 1",
            b"no protocol",
        ):
            binary.write_bytes(payload)
            with self.assertRaisesRegex(verify.VerificationError, "protocol bytes differ"):
                verify.inspect_windows_protocol_bytes(binary)

    @unittest.skipIf(os.name == "nt", "POSIX executable fixture")
    def test_linux_version_protocol_must_be_exact(self) -> None:
        executable = self.temp / "version-probe"
        executable.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "sys.stdout.write('narwhal-document-core 1\\n')\n",
            encoding="utf-8",
        )
        executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
        verify.execute_protocol(executable)

        executable.write_text(
            "#!/usr/bin/env python3\n"
            "print('narwhal-document-core 2')\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(verify.VerificationError, "--version output differs"):
            verify.execute_protocol(executable)

    def test_reproducible_mode_runs_two_isolated_builds_per_platform(self) -> None:
        def run_case(reproducible: bool) -> tuple[dict[str, int], dict[str, object]]:
            calls = {platform_name: 0 for platform_name in verify.PLATFORMS}

            def fake_build(platform_name: str, workspace: Path) -> Path:
                calls[platform_name] += 1
                workspace.mkdir(parents=True)
                artifact = workspace / str(verify.PLATFORMS[platform_name]["filename"])
                artifact.write_bytes(platform_name.encode("ascii"))
                return artifact

            def fake_staging(
                _staging: Path,
                _binaries: dict[str, Path],
                _epoch: int,
                _sources: list[dict[str, str]],
                builds_per_platform: int,
            ) -> dict[str, object]:
                return {"artifacts": [], "builds_per_platform": builds_per_platform}

            with (
                mock.patch.object(build, "verify_crate_contract"),
                mock.patch.object(build, "source_inventory", return_value=[]),
                mock.patch.object(build, "_toolchain_root", return_value=self.temp),
                mock.patch.object(build, "_check_local_tools"),
                mock.patch.object(
                    build,
                    "build_linux",
                    side_effect=lambda _toolchain, workspace, _epoch: fake_build(
                        "linux-x86_64", workspace
                    ),
                ),
                mock.patch.object(
                    build,
                    "build_windows",
                    side_effect=lambda _toolchain, workspace, _epoch: fake_build(
                        "windows-x86_64", workspace
                    ),
                ),
                mock.patch.object(build, "write_staging", side_effect=fake_staging),
                mock.patch.object(build, "publish_atomically"),
                mock.patch.object(build, "verify_directory", return_value={"artifacts": []}),
            ):
                manifest = build.build(
                    self.temp / ("reproducible" if reproducible else "single"),
                    0,
                    reproducible,
                )
            return calls, manifest

        single_calls, single_manifest = run_case(False)
        self.assertEqual(single_calls, {name: 1 for name in verify.PLATFORMS})
        self.assertEqual(single_manifest["builds_per_platform"], 1)
        reproducible_calls, reproducible_manifest = run_case(True)
        self.assertEqual(reproducible_calls, {name: 2 for name in verify.PLATFORMS})
        self.assertEqual(reproducible_manifest["builds_per_platform"], 2)

    def test_two_clean_build_hash_comparison_covers_both_platforms(self) -> None:
        builds: dict[str, list[Path]] = {}
        for platform_name in verify.PLATFORMS:
            first = self.temp / f"{platform_name}-first"
            second = self.temp / f"{platform_name}-second"
            first.write_bytes(platform_name.encode("ascii"))
            shutil.copyfile(first, second)
            builds[platform_name] = [first, second]
        hashes = build._reproducible_hashes(builds)
        self.assertEqual(set(hashes), set(verify.PLATFORMS))

        builds["windows-x86_64"][1].write_bytes(b"mutated")
        with self.assertRaisesRegex(build.BuildError, "two clean windows-x86_64 builds differ"):
            build._reproducible_hashes(builds)

    def test_glibc_ceiling_build_id_and_nonzero_pe_timestamp_are_rejected(self) -> None:
        acceptable = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=b"Name: GLIBC_2.28\nName: GLIBC_2.17\n", stderr=b""
        )
        with mock.patch.object(verify, "run_tool", return_value=acceptable):
            self.assertEqual(verify.elf_max_glibc(self.temp / "unused"), (2, 28))

        no_build_id = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=b"NT_GNU_ABI_TAG\n", stderr=b""
        )
        with mock.patch.object(verify, "run_tool", return_value=no_build_id):
            verify.verify_no_elf_build_id(self.temp / "unused")
        has_build_id = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=b"GNU_BUILD_ID\nBuild ID: 1234\n", stderr=b""
        )
        with mock.patch.object(verify, "run_tool", return_value=has_build_id):
            with self.assertRaisesRegex(verify.VerificationError, "forbidden linker build ID"):
                verify.verify_no_elf_build_id(self.temp / "unused")

        too_new = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=b"Name: GLIBC_2.29\n", stderr=b""
        )
        with mock.patch.object(verify, "run_tool", return_value=too_new):
            with self.assertRaisesRegex(verify.VerificationError, "above GLIBC_2.28"):
                verify.elf_max_glibc(self.temp / "unused")

        pe = self.temp / "timestamp.exe"
        verify.write_synthetic_pe(pe, 1)
        self.assertEqual(verify.pe_timestamp(pe), 1)
        with mock.patch.object(verify, "pe_timestamp", return_value=1):
            metadata = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=(
                    b"file format pei-x86-64\nPE32+\nCharacteristics 0x22e\n executable\n"
                    + b"".join(
                        f"DLL Name: {name}\n".encode("ascii")
                        for name in verify.WINDOWS_IMPORTS
                    )
                ),
                stderr=b"",
            )
            with mock.patch.object(verify, "run_tool", return_value=metadata):
                with self.assertRaises(verify.VerificationError):
                    verify.inspect_windows(pe)

    @unittest.skipUnless(sys.platform == "linux", "Linux artifact execution test")
    def test_real_rust_binary_protocol_and_pack_inspect_unpack_round_trip(self) -> None:
        target = self.temp / "native-target"
        environment = os.environ.copy()
        environment.update(
            {
                "CARGO_INCREMENTAL": "0",
                "CARGO_NET_OFFLINE": "true",
                "LC_ALL": "C",
                "TZ": "UTC",
            }
        )
        result = subprocess.run(
            [
                "cargo",
                f"+{verify.RUST_TOOLCHAIN}",
                "build",
                "--release",
                "--locked",
                "--offline",
                "--manifest-path",
                str(verify.CRATE / "Cargo.toml"),
                "--target-dir",
                str(target),
            ],
            cwd=ROOT,
            env=environment,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        executable = target / "release" / str(verify.PLATFORMS["linux-x86_64"]["filename"])
        verify.execute_protocol(executable)
        verify.execute_container_smoke(executable)

    def test_forbidden_and_unexpected_dynamic_dependencies_are_rejected(self) -> None:
        elf_header = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=(
                b"Class: ELF64\nType: DYN (Position-Independent Executable file)\n"
                b"Machine: Advanced Micro Devices X86-64\n"
            ),
            stderr=b"",
        )
        forbidden = ("libc.so.6", "libgcc_s.so.1", "libpthread.so.0")
        with (
            mock.patch.object(verify, "run_tool", return_value=elf_header),
            mock.patch.object(verify, "elf_dependencies", return_value=forbidden),
        ):
            with self.assertRaisesRegex(verify.VerificationError, "forbidden runtime"):
                verify.inspect_linux(self.temp / "unused")

        imports = list(verify.WINDOWS_IMPORTS) + ["unexpected.dll"]
        pe_metadata = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=(
                b"file format pei-x86-64\nPE32+\nCharacteristics 0x22e\n executable\n"
                + b"".join(f"DLL Name: {name}\n".encode("ascii") for name in imports)
            ),
            stderr=b"",
        )
        with mock.patch.object(verify, "run_tool", return_value=pe_metadata):
            with self.assertRaisesRegex(verify.VerificationError, "unexpected Windows"):
                verify.inspect_windows(self.temp / "unused.exe")


if __name__ == "__main__":
    unittest.main(verbosity=2)
