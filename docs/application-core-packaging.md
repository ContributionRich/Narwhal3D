# Application core — platform packaging

Scope: existing platform build/assemble/verify orchestration and focused packaging
checks only. This is a **future source-qualified package contract**, not a change
to published v0.4.8 assets/tags or a claim that a new native package was built.

## Required payload

`native/application_core` is mandatory, with `narwhal_session_abi() == 1`.
There is no optional component/fallback exemption.

| Platform | Filename | App root |
| --- | --- | --- |
| Linux | `libnarwhal_application_core.so` | `usr/lib/narwhal3d` inside AppDir |
| macOS | `libnarwhal_application_core.dylib` | `Contents/Resources/app` inside app bundle |
| Windows | `narwhal_application_core.dll` | `app` inside portable package |

Every platform includes the library under **both** of these app-relative paths:

- `runtime/<platform>-<architecture>/bin/<filename>`
- `narwhal3d/native/<filename>` — the session loader's adjacent slot

Linux's existing native inventory, ABI probes and adjacent-copy hash checks now
include this library. macOS stages the adjacent copy, verifies identical bytes and
executes its ABI with bundled Python after signing and again from the mounted DMG.
The macOS assembly receipt includes `application_core_abi: 1`.
Windows preserves the generated adjacent DLL during public-source replacement,
requires both copies and matching hashes, and records/checks the ABI and exports.

The Windows source build snapshots the complete application-core input inventory
before Cargo and checks it again afterward. It emits
`app/runtime/windows-x86_64/bin/application-core-source.json`, containing input
hashes, library hash and ABI. Final assembly and verification require that receipt
to match current source and the packaged DLL. An older engineering ZIP lacking
this new component/receipt cannot qualify. Linux and macOS retain their existing
native source-inventory build receipts, which now include the new crate.

### Windows ABI boundary

The Windows build's embedded ctypes probe executes `narwhal_session_abi` from the
adjacent DLL and refuses anything but 1. Cross-host release verification also
checks the exact session exports and resolves the ABI export's PE function RVA
and ordinal. **OURS/DERIVED:** it accepts only the direct x86-64 leaf
`mov eax, 1; ret` (`b8 01 00 00 00 c3`); missing, forwarded, wrong-value or unknown
encodings fail closed. This is a narrow static ABI check, not native execution.
The actual new MSVC output has **not** been built/inspected in this session; if its
encoding differs, qualify it explicitly rather than relaxing the ABI requirement.
Other DLLs' generic PE checks are retained; constant-export checking is opt-in.

## Licensing

Each package copies the maintained
`native/application_core/OPENNURBS_LICENSE` to
`licenses/application_core-OPENNURBS.txt` beneath its app root, and must include
`licenses/Native-Rust-Dependencies.txt`. The Windows public-source check maps the
copied openNURBS notice back to its maintained native source rather than exempting
it from comparison.

MEASURED by the focused tests: the supplied ordinal table contains 205 entries;
its table, generator, Cargo manifest/lock and license are in source inventories.
All registry dependency versions in this crate's lockfile, including serde
1.0.229 and serde_json 1.0.154, already appear in the existing native Rust notices.
No table regeneration, new dependency versions or license-source edits were made.

## Exact paths changed by this packaging lane

- `scripts/build_linux_native.py`
- `scripts/build_macos_native.py`
- `scripts/build_windows_preview.ps1`
- `scripts/assemble_linux_release.py`
- `scripts/assemble_macos_release.py`
- `scripts/assemble_windows_release.py`
- `scripts/verify_linux_release.py`
- `scripts/verify_windows_release.py`
- `scripts/test_release_application_core.py` (new focused host tests/mutations)
- `spike/test_package_completeness.py`
- `spike/test_windows_release.py` (adjacent-DLL expected set only)
- `docs/application-core-packaging.md` (this document)

Some existing paths already contained other work; no commits or bulk staging were
done. Main owns harness/public manifests and should include the new focused test
and retain the existing platform scripts. In particular, Windows source-inventory
orchestration imports the existing `scripts/build_linux_native.py` helper; it does
not invoke its Linux build entry point. No changes were made to
`create_public_branch.py`, `audit_public_tree.py`, `run_tests.sh`, `narwhal3d/`,
`native/` or other agents' task entries.

## Validation — host only, 2026-10-30

All final commands below passed:

```sh
python3 -B scripts/test_release_application_core.py -v
python3 -B scripts/test_release_application_core.py --mutation-check
python3 -B scripts/test_release_packages.py
python3 -B scripts/test_release_automation.py
python3 -B spike/test_package_completeness.py
python3 -B scripts/assemble_linux_release.py --self-check
python3 -B scripts/verify_linux_release.py --self-check
python3 -B scripts/assemble_windows_release.py --self-check
python3 -B spike/test_windows_release.py
```

Results: **14 new tests, 13 mutations RED→GREEN, 6 existing release-package tests,
6 automation-packaging tests, Linux 12/12 and Windows 11/11 component completeness,
three script self-checks, and 230/230 Windows release checks.** Scoped script
bytecode caches were cleared before the final focused group. Mutations operate
on in-memory functions/constants with live module globals; no shared source file
is temporarily broken. Fixtures copy small temporary files and use a synthetic PE;
no real product package, Blender process or platform compiler is invoked.

Initial runs exposed omitted duplicate self-check inventory entries and a test
AST-selector mistake; those were corrected, then the final checks above passed.
The pre-existing broad Linux suite's stale automation inventory was not repaired
or run. No whole-product suite was run, as requested.

## Remaining platform status

- **Linux:** integration/host checks complete; new portable source-qualified native
  output and actual AppImage qualification pending.
- **macOS ARM64:** integration/host checks complete; native Cargo build, app/DMG
  assembly, signing/mounted ABI checks and physical-OS acceptance pending.
- **Windows x86-64:** integration/host checks complete; native MSVC build (including
  static ABI encoding qualification), real ctypes probe, current source receipt,
  portable/executable package verification and physical-OS acceptance pending.

No native-platform rebuild, Blender rebuild, VM operation, real-package creation,
release publication, physical-GPU test, notarization or OS acceptance occurred.
Use a historical release's frozen tooling to inspect historical assets; do not
reinterpret this new mandatory contract as validation of the old packages.
