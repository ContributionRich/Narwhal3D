# Narwhal selection-core feasibility pilot

This dependency-free Rust crate implements native acceleration for two existing,
display-only selection operations:

- homogeneous world-to-screen projection;
- inclusive line-segment/selection-rectangle intersection.

It is an opt-in production adapter dependency but is not yet bundled in an AppImage
or Windows preview. The stable C ABI lets Blender's CPython 3.13 and other CPython
versions load the same platform library through `ctypes`.

The pilot never receives, creates, or modifies document geometry. Its inputs are
packed numeric copies of viewport matrices and display segments. Triangles or line
segments produced for a viewport must never replace analytic OCCT/openNURBS geometry.

Build and validate from the repository root:

```text
tools/blender-patched/5.2/python/bin/python3.13 scripts/build_selection_core.py --reproducible
tools/blender-patched/5.2/python/bin/python3.13 scripts/verify_selection_core.py artifacts/selection-core
tools/blender-patched/5.2/python/bin/python3.13 spike/test_rust_selection_core.py
```

The verified output remains ignored. A later release-integration task must explicitly
own AppImage/Windows package copying, public allowlists, and native-Windows acceptance.
