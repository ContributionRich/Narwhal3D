# Narwhal pure-core

This dependency-free Rust crate contains checked implementations for Narwhal 3D's
pure interchange and parsing modules. A Rust symbol is not automatically a production
fast path: the Python adapter enables it only when complete end-to-end measurements,
including packing and result conversion, beat the established Python path.

Current production routing:

| operation | production route | measured reason |
|---|---|---|
| STL encode | Rust | 1.31× faster at 100k triangles |
| OBJ decode | Rust | 1.67× faster on a 1.8 MB file |
| DXF decode | Rust | 3.23× faster on 20k entities |
| length parsing | Rust | about 1.14× faster |
| calculator | Rust | about 2.5× faster |
| coordinate parsing | Rust | about 1.05× faster |
| macro tokenize / plan | Rust only at 240 / 192 UTF-8 bytes | long workloads measured 1.32× / 1.58× faster |
| STL decode API | Python | generic triangle-tuple reshaping was 24.9% slower |
| STL Import parse+weld | Rust at ≥50 kB | fused indexed output measured 3.8–4.3× faster at 1k–100k triangles |
| OBJ encode | Python | native path was 2.18× slower |
| DXF encode | Python | two-pass output build never reached a stable 10% win |
| standalone number / bearing parsing | Python | FFI cost erased the tiny-parser gain |
| SVG path parsing | Python | JSON ABI was slower through the 1 MiB input limit |

The slower Rust implementations remain directly tested for correctness and future ABI work,
but they are not allowed to slow production.

The CPython adapter is `narwhal3d/native_pure.py`. It loads the crate only from
the `NARWHAL_PURE_CORE` environment path or an adjacent packaged slot. Every active
native route has a complete Python fallback. The crate is an opt-in production
dependency but is not yet bundled in an AppImage or Windows preview. The stable
C ABI (version 1, additive) lets Blender's CPython 3.13 and other CPython
versions load the same platform library through `ctypes`.

The crate never receives, creates, or modifies document geometry. Its inputs
are packed numeric copies of triangles already produced for export; its output
is an encoded file body. This must never replace analytic OCCT/openNURBS
geometry — the display and kernel routes are untouched.

The crate is held to a **mandatory source gate** (``scripts/check_pure_core.py``,
registered in ``run_tests.sh`` beside the welcome-window gate) that runs, in order:
``cargo fmt --check``; ``cargo clippy --all-targets -- -D warnings``;
``cargo test --offline --locked``; one Linux ``--release`` build (a temporary
build, not a committed artifact); a direct ``ctypes`` probe of the stable C ABI;
the dedicated pure-core Python suites (an explicit manifest, not a
``test_native_*.py`` glob); and a Windows GNU ``cargo check`` when the pinned
target is installed. A skipped clippy step is a hard gate failure. The gate's
load-bearing self-test is ``spike/test_pure_core_gate.py``, which proves on a
temporary copied crate that a clippy ``-D warnings`` mutation fails the gate and
that a skipped clippy step fails it -- the live tree is never mutated.

Build and validate from the repository root:

```text
scripts/check_pure_core.py          # the mandatory source gate (all steps above)
cargo +1.95.0 test --offline --manifest-path native/pure_core/Cargo.toml
cargo +1.95.0 build --release --locked --offline --manifest-path native/pure_core/Cargo.toml --target x86_64-unknown-linux-gnu
tools/blender-patched/5.2/python/bin/python3.13 spike/test_native_stl.py
tools/blender-patched/5.2/python/bin/python3.13 spike/test_native_obj.py
tools/blender-patched/5.2/python/bin/python3.13 spike/test_native_dxf.py
```

The verified output remains ignored. A later release-integration task must
explicitly own AppImage/Windows package copying, public allowlists, and
native-Windows acceptance.
