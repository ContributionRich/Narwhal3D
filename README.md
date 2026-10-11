# Narwhal 3D

Narwhal 3D is a free, open-source, command-driven NURBS CAD application. The current release target is Linux x86_64. It ships as one application bundle: a Narwhal launcher and interface on top of a bundled, open-source viewport runtime.

> **Project status:** early public MVP. Save important work often and verify manufacturing output before production use.

## Geometry model

Narwhal keeps document geometry separate from viewport tessellation:

- Curves and analytic primitives are stored as NURBS/Brep document objects.
- Open CASCADE (OCCT) provides exact booleans, fillets, sweeps, STEP I/O, and other solid operations.
- Triangles generated for shaded display are not treated as manufacturing geometry.
- STEP export refuses geometry that has no exact analytic representation.

`.3dm` reading and writing uses the MIT-licensed `rhino3dm` package. The initial public binary does **not** bundle the optional openNURBS exact-Brep helper while its redistribution requirements are being reviewed. Without that helper, some OCCT-created multi-face solids cannot be encoded as exact `.3dm` Breps. Narwhal reports that limitation when saving; use STEP for exact solid interchange. It never presents a faceted STEP fallback as exact geometry.

## Install on Linux x86_64

The public release artifact is `Narwhal_3D-<version>-x86_64.AppImage`. Make it executable and run it directly:

```bash
chmod +x Narwhal_3D-0.1.0-x86_64.AppImage
./Narwhal_3D-0.1.0-x86_64.AppImage
```

The AppImage contains the natively branded runtime, locked Python dependencies, application package, icons, and license notices. It does not install a separate host application.

## Develop from source

Requirements: Python 3, Bash, HTTPS access, a C/C++ build toolchain, CMake, Ninja, and enough disk space for the runtime and source build.

```bash
./scripts/bootstrap_dev_linux.sh
```

This development bootstrap downloads only SHA-256-locked artifacts and installs the locked wheels into an official development runtime. That runtime is useful for iteration but is deliberately rejected by release assembly because it is not natively branded.

`scripts/build_blender_linux.sh` builds the publishable runtime from the exact Blender 5.2.2 source commit and applies the Narwhal title, icon, app-drawn-object, and CAD-boundary patches. See `packaging/README.md` for the required arguments and the current native-build provenance limitation. Install the three wheels from the locked cache into the resulting runtime before packaging.

Once `tools/blender-patched/` exists, run:

```bash
./narwhal3d.sh
./narwhal3d.sh --install
```

## Build a one-click AppImage

With a validated native runtime and the locked appimagetool downloaded:

```bash
python3 scripts/fetch_locked.py \
  --cache-dir ~/.cache/narwhal3d/artifacts \
  appimagetool appimage-runtime

REVISION=$(git rev-parse HEAD)
EPOCH=$(git show -s --format=%ct HEAD)

python3 scripts/assemble_linux_release.py \
  --version 0.1.0 \
  --source-revision "$REVISION" \
  --source-date-epoch "$EPOCH" \
  --runtime-dir tools/blender-patched \
  --appimagetool ~/.cache/narwhal3d/artifacts/appimagetool-x86_64.AppImage \
  --appimage-runtime ~/.cache/narwhal3d/artifacts/runtime-x86_64 \
  --output-dir dist

python3 scripts/verify_linux_release.py \
  dist/Narwhal_3D-0.1.0-x86_64.AppImage \
  --expected-version 0.1.0 \
  --expected-source-revision "$REVISION" \
  --expected-source-date-epoch "$EPOCH"
```

Assembly fails closed unless the native binary identity, patch hashes, source commit, Python dependencies, notices, paths, and symlinks all pass. It emits the AppImage, a SHA-256 sidecar, and a retained AppDir for diagnostics.

## Tests

Fast public model tests can be run with the bundled interpreter after bootstrap. The full application and UI suites use the bundled runtime:

```bash
./run_tests.sh
./uishot.sh
```

The public branch intentionally excludes non-public development corpora, resource-mining tools, internal reports, and tests that require them.

## Supported interchange

Core, self-contained release functionality includes `.3dm`, STEP, DXF, SVG, OBJ, and STL paths. PDF and Adobe Illustrator conversion can use an optional converter pack; that pack is not part of the initial release and Narwhal does not claim it is bundled. Native CorelDRAW import/export is intentionally excluded from the public branch.

## Repository layout

- `narwhal3d/` — Narwhal commands, document model, geometry bridge, and interface
- `launch/` — startup, desktop integration, MIME registration, and app icon
- `packaging/` — immutable Linux artifact lock and release notes
- `scripts/` — verified bootstrap, assembly, verification, and native-runtime build tools
- `blender_patches/` — focused source patches used by the optional native build
- `spike/` — selected self-contained tests retained on the public branch

Internal names such as `narwhal3d`, `narwhal.*`, `rhino3dm`, and `.3dm` are compatibility or file-format interfaces; they are not product branding.

## License and third-party software

Narwhal 3D project code is licensed under **GPL-3.0-or-later**. See `LICENSE`.

The application bundle contains separately licensed open-source components, including Blender, rhino3dm, cadquery-ocp, and Open CASCADE Technology. See `THIRD_PARTY_NOTICES`, `licenses/`, and the bundled runtime's own `license/` directory.

Narwhal 3D is an independent project and is not affiliated with the vendors of the supported file formats or third-party libraries.

## Security and provenance

Release artifacts are built only from the public orphan branch. Do not publish the private development branch or its history. Before release, run `scripts/audit_public_tree.py` and verify that the public root commit has no parent.
