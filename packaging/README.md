# Linux x86_64 AppImage packaging

This directory defines the fail-closed AppImage scaffold for the public Narwhal 3D source branch.

## Early-prerelease version and host baseline

Narwhal 3D remains an extremely early prerelease. The assembler and verifier accept only `0.4.x` package versions until the project owner explicitly lifts that cap; this release is `0.4.7`, not `0.5.0`. Its Linux asset is `Narwhal_3D-0.4.7-x86_64.AppImage`. Final shipping-asset gates passed for both platforms; Windows build and acceptance evidence is recorded separately in [the Windows guide](../docs/windows-build.md).

A release runtime must also require no newer than **GLIBC 2.35**, the baseline from Ubuntu 22.04 (the oldest Ubuntu LTS still in standard support in 2026). Every regular ELF in the runtime is checked with `readelf --version-info` before assembly and again after extraction. This gate exists because the 0.4.0 runtime was built on glibc 2.43 and the AppImage catalog measured immediate startup failure on its older supported test system. Build native Linux release runtimes in an Ubuntu 22.04 container or equivalent old supported base; assembling on a newer host does not make a newer-linked executable portable.

The 0.4.7 AppImage has been built and its inventory verified: all 290 bundled ELFs require at most GLIBC 2.28. Its portable native runtime and welcome have been rebuilt with the current UI patches and matching native components. The separate source-development runtime and welcome still require GLIBC 2.43 and must not ship. Use `--version 0.4.7` for assembly and `--expected-version 0.4.7` for verification; every reassembled final candidate must pass the same gates.

## Shipping 0.4.7 source and evidence

The AppImage is **437,832,184 bytes**, SHA-256 `6097aa60cdc8702e59370f83b66d13a6de42da2c3b10c0a4cc3cded753cd67a9`. Both shipping assets use frozen public source revision `94534c954dc77f1f1d22f2be6064bedcc8cd1f5b`, source epoch `1793144099`. The release tag identifies that asset-build revision; this documentation follow-up does not require a repackage. See [both asset checksums](../README.md#release-source-and-integrity).

Final Linux source release gates, including the full suite, passed. The AppDir and actual extracted-AppImage verifiers passed before and after package acceptance, checking source binding, native provenance, imports and inventory. All 809 packaged source/license/launcher files match the frozen source; all nine native components match verified artifacts. Negative controls rejected wrong revisions, epochs and source/native/runtime hashes.

Actual shipping-archive GUI acceptance used NVIDIA/OpenGL: Gumball mouse extrusion then scale 24/24, render quality 47/47, preferences write/restore 183/193 checks, window overlays 105/105, component/fillet interaction 74/74, and shell interaction 73/73. Background Gumball checks passed 47/47 and all 28 meshed-master transform/round-trip combinations passed. Clean-copy and overlay mutations went RED; restored production paths passed. All 8,650 extraction inventory entries and the final archive digest remained unchanged.

The AppImage is unsigned: a SHA-256 checksum is integrity evidence, not publisher authentication. Pristine Ubuntu-desktop and optional GPU-backend acceptance remain open. Standard desktop libraries and optional AMD HIP/Intel Level Zero drivers remain host-provided; the GLIBC ceiling does not certify those environments.

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
- `blender_patches/apply_deferred_first_present.py`
- `blender_patches/apply_statusbar_height.py`
- `blender_patches/narwhal3d_app_icon.svg`
- `blender_patches/apply_window_overlay.py`

After installation, the script requires a version banner beginning `Narwhal 3D 5.2.2`, compiled window-title and quit markers, and writes `.narwhal3d-native-runtime.json`. The marker records the exact Blender commit, each patch input hash, a deterministic aggregate, installed binary hash, build timestamp, and optional `build_source_date_epoch` supplied through `--source-date-epoch`. A native runtime may legitimately predate the final public source commit, so this build epoch is independent of the AppImage manifest's required `source_date_epoch`, which controls public-source identity and AppDir normalization.

The statusbar patch is applied through `apply(source / "source" / "blender")`, not its CLI `main()`. Its native 37-logical-pixel sizing is opt-in: both `NARWHAL3D_STATUSBAR_HEIGHT=37` and `NARWHAL3D_DEFER_FIRST_PRESENT=1` must be set; otherwise Blender keeps its normal statusbar sizing.

The window-overlay patch also uses the explicit `apply(source / "source" / "blender")` API. It adds the native Window POST_PIXEL callback bridge and `python/BPY_window_draw.hh`. Provenance now covers seven inputs (six patch scripts plus the icon); older six-input markers are rejected.

After an **already completed incremental build and install of all current patch inputs**, refresh the installed marker with the same validation/hash-writing path, without rerunning a clean build:

```sh
scripts/build_blender_linux.sh --record-installed-provenance \
  --source-dir tools/blender-src --install-dir tools/blender-patched
```

This deliberately allows patched source, requires the pinned source commit, current statusbar/window-overlay source anchors and exact overlay header, and validates the installed banner, branding and deferred/statusbar/overlay (`RNA_WINDOW_DRAW_HANDLE`) binary markers before atomically replacing provenance. It does not patch source, configure, compile, install, or launch the GUI. It is the caller's attestation that this installed binary was built from these current inputs: marker strings cannot prove source-to-binary equivalence. Do not use it to bless an unbuilt patch-script change. `--source-date-epoch` may specify the actual build epoch; without it the new marker records the current recording time and a null build epoch (it does not preserve stale marker metadata).

The native Blender build does not download Python wheels. Before assembly, the three wheels pinned by the lock must be installed into the native runtime's bundled CPython 3.13 from a verified cache. Assembly probes `rhino3dm`, `OCP`, and all exact distribution versions before invoking appimagetool.

## AppImage assembly

`scripts/assemble_linux_release.py` accepts only a native runtime whose marker and binary match the current public patch inputs. Before copying anything, it requires a sanitized public `narwhal3d/` package. The reviewed built-in `cdr_reader.py` and `cdr_writer.py` are included; private `kernel/brep_writer.cpp` and every file or symlink below `kernel/bin/` are forbidden. Their presence is an error, never an ignore rule or silent omission. It stages:

- root `AppRun`, `narwhal3d.desktop`, `narwhal3d.png`, and `.DirIcon`;
- the launcher, `narwhal3d/` package, launch assets, native runtime at `tools/blender-patched`, packaging provenance, and notices under `usr/lib/narwhal3d/`;
- all nine mandatory native components, including the native `narwhal-welcome` startup window;
- mandatory `LICENSE`, `THIRD_PARTY_NOTICES`, and the complete `licenses/` directory, including `licenses/AppImage-MIT.txt`.

Before inventory hashing, the assembler renders exactly one `X-AppImage-Version=<release version>` line into the root desktop entry. This prevents appimagetool from appending the field after hashing; the desktop remains part of the manifest and any byte mutation still fails verification.

The assembler invokes only the locked appimagetool with `--appimage-extract-and-run` and `--runtime-file` pointing to the separately verified type2 runtime; no runtime network fallback is permitted. It must produce `Narwhal_3D-<version>-x86_64.AppImage` plus its SHA-256 sidecar. The verifier checks the staged AppDir and extracts the AppImage without FUSE to re-check identity, inventory, imports, native provenance, forbidden paths, and symlinks.

## Honest CI limitation

The public lock does not yet pin the Blender source archive, precompiled dependency archives, compiler/container image, or complete native build dependency graph. Therefore `.github/workflows/linux-release.yml` validates only the public source and packaging scaffold. It intentionally does **not** download an official runtime, build an AppImage, or upload a release artifact.

The frozen 0.4.7 shipping candidate passed the final sanitized-source and asset gates: excluded-payload, native marker, branding, dependency import, AppDir and extracted-AppImage verification, plus the full source suite and packaged acceptance described above. Windows independently passed its engineering build, directory/ZIP verification and extracted-ZIP acceptance; its software-rendered VM GUI evidence does not certify a physical GPU. Future candidates still require their own gates. The scaffold does not claim byte-for-byte native build reproducibility or complete native CI toolchain locking.

See `NOTICES.md` for required notices and deliberately excluded components.
