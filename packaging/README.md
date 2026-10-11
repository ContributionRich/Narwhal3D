# Linux x86_64 AppImage packaging

This directory defines the fail-closed AppImage scaffold for the public Narwhal 3D source branch.

## Development runtime is not publishable

`linux-x86_64.lock.json` pins the official Blender 5.2.2 Linux x86_64 archive and CPython 3.13 wheels for **development bootstrap only**. `scripts/bootstrap_dev_linux.sh` installs that official runtime for local development. The assembler and verifier explicitly reject its flavor and provenance markers; it must never enter a published AppImage.

The lock also pins appimagetool 1.9.1 x86_64 and the separate type2 `appimage-runtime` tag `20251108` to immutable versioned URLs and SHA-256 digests. There are no `latest` or `continuous` artifact URLs. Assembly requires both files and passes `--runtime-file runtime-x86_64`, preventing appimagetool from downloading a mutable continuous runtime.

## Required native release runtime

A publishable release requires `scripts/build_blender_linux.sh` to build Blender source at exactly:

```text
d13f752e3b9c4f8c261cda552b1021f8bcc0382c
```

The build applies all current Narwhal native patches:

- `blender_patches/apply_narwhal3d_branding.py`
- `blender_patches/apply_app_drawn.py`
- `blender_patches/apply_cad_boundary_wires.py`
- `blender_patches/narwhal3d_app_icon.svg`

After installation, the script requires a version banner beginning `Narwhal 3D 5.2.2`, compiled window-title and quit markers, and writes `.narwhal3d-native-runtime.json`. The marker records the exact Blender commit, each patch input hash, a deterministic aggregate, installed binary hash, build timestamp, and optional `build_source_date_epoch` supplied through `--source-date-epoch`. A native runtime may legitimately predate the final public source commit, so this build epoch is independent of the AppImage manifest's required `source_date_epoch`, which controls public-source identity and AppDir normalization.

The native Blender build does not download Python wheels. Before assembly, the three wheels pinned by the lock must be installed into the native runtime's bundled CPython 3.13 from a verified cache. Assembly probes `rhino3dm`, `OCP`, and all exact distribution versions before invoking appimagetool.

## AppImage assembly

`scripts/assemble_linux_release.py` accepts only a native runtime whose marker and binary match the current public patch inputs. Before copying anything, it requires a sanitized public `narwhal3d/` package: `cdr_reader.py`, `cdr_writer.py`, `kernel/brep_writer.cpp`, and every file or symlink below `kernel/bin/` are forbidden. Their presence is an error, never an ignore rule or silent omission. It stages:

- root `AppRun`, `narwhal3d.desktop`, `narwhal3d.png`, and `.DirIcon`;
- the launcher, `narwhal3d/` package, launch assets, native runtime at `tools/blender-patched`, packaging provenance, and notices under `usr/lib/narwhal3d/`;
- mandatory `LICENSE`, `THIRD_PARTY_NOTICES`, and the complete `licenses/` directory, including `licenses/AppImage-MIT.txt`.

Before inventory hashing, the assembler renders exactly one `X-AppImage-Version=<release version>` line into the root desktop entry. This prevents appimagetool from appending the field after hashing; the desktop remains part of the manifest and any byte mutation still fails verification.

The assembler invokes only the locked appimagetool with `--appimage-extract-and-run` and `--runtime-file` pointing to the separately verified type2 runtime; no runtime network fallback is permitted. It must produce `Narwhal_3D-<version>-x86_64.AppImage` plus its SHA-256 sidecar. The verifier checks the staged AppDir and extracts the AppImage without FUSE to re-check identity, inventory, imports, native provenance, forbidden paths, and symlinks.

## Honest CI limitation

The public lock does not yet pin the Blender source archive, precompiled dependency archives, compiler/container image, or complete native build dependency graph. Therefore `.github/workflows/linux-release.yml` validates only the public source and packaging scaffold. It intentionally does **not** download an official runtime, build an AppImage, or upload a release artifact.

Publishing remains deferred until a sanitized public source tree and locally built native runtime have passed the excluded-payload, native marker, branding, dependency import, AppDir, and extracted-AppImage verification gates—or until the complete native toolchain is locked for CI. The scaffold does not claim byte-for-byte native build reproducibility before that work is complete.

See `NOTICES.md` for required notices and deliberately excluded components.
