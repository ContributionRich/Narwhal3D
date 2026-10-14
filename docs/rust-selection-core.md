# Rust selection-core build and release boundary

Narwhal's first Rust component accelerates **display-only** world-to-screen
selection projection. It does not own or alter document geometry, OCCT masters,
construction recipes, saved NURBS, or manufacturing output.

## Pinned build

The crate is `native/selection_core/` and pins:

- Rust `1.95.0`;
- `x86_64-unknown-linux-gnu`;
- `x86_64-pc-windows-gnu`;
- locked, offline Cargo builds;
- no third-party Rust crates;
- deterministic ELF build-ID and PE timestamp settings.

Build and prove two clean builds identical:

```text
tools/blender-patched/5.2/python/bin/python3.13 scripts/build_selection_core.py --reproducible
```

The ignored `artifacts/selection-core/` output contains:

```text
selection-core-manifest.json
linux-x86_64/libnarwhal_selection_core.so
windows-x86_64/narwhal_selection_core.dll
```

Verify an existing output independently:

```text
tools/blender-patched/5.2/python/bin/python3.13 scripts/verify_selection_core.py artifacts/selection-core
```

## Fail-closed checks

The verifier binds both files to the exact crate source aggregate and checks:

- exact filenames and artifact set;
- complete size and SHA-256 records;
- ABI version 1 and exactly four exported C symbols;
- ELF64/PE32+ x86-64 machine type;
- executable Linux ABI probe when running on Linux x86-64;
- Linux dependencies and maximum `GLIBC_2.35`;
- the measured Windows system-DLL import set;
- zero Windows COFF timestamp;
- no symlinks or unexpected files.

The Windows DLL is a GNU-target C ABI library. It does not require a Python ABI and
can be loaded by the MSVC-built Blender CPython through `ctypes`. Native Windows
execution acceptance is still required before release packaging.

## Not yet a release input

These files are not committed binaries and are not yet consumed by the AppImage or
Windows preview assemblers. Release integration must:

1. consume only a directory that passes `verify_selection_core.py`;
2. copy the matching library beside `narwhal3d/native_selection.py` under
   `narwhal3d/native/`;
3. include the adapter, library, manifest provenance and tests in the public tree;
4. retain scalar Python fallback if loading or ABI verification fails;
5. test the library natively on Windows;
6. pass the captured 5,702-object interaction acceptance before default activation.

Viewport line/triangle data remains display-only. It must never become saved,
measured, booleaned, or exported geometry.
