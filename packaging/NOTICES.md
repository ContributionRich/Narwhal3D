# Packaging notices

## Runtime provenance

A published Linux x86_64 AppImage must contain the natively patched Narwhal 3D runtime built from Blender commit `d13f752e3b9c4f8c261cda552b1021f8bcc0382c`. Its optional `build_source_date_epoch` records native-build provenance independently from the AppImage/public-source `source_date_epoch`. The official Blender binary pinned in `linux-x86_64.lock.json` is development-bootstrap input only and is explicitly rejected by release assembly and verification.

Every AppImage must contain the project-level `LICENSE`, `THIRD_PARTY_NOTICES`, and complete `licenses/` directory under `usr/lib/narwhal3d/`. Verification specifically requires the GPL-3.0-or-later, LGPL-2.1-or-later, rhino3dm MIT, OCP Apache-2.0, OCCT exception, and AppImage MIT texts as regular files.

The pinned appimagetool 1.9.1 executable and type2 runtime tag `20251108` are release build inputs. Their exact URLs and SHA-256 digests are locked. Assembly supplies the runtime through `--runtime-file` so appimagetool performs no mutable continuous-runtime download. The runtime is embedded by appimagetool; its MIT notice is carried as `licenses/AppImage-MIT.txt`.

## Deliberate exclusions

- **openNURBS exact-save helper:** not downloaded, built, or bundled because redistribution clearance is unresolved. In particular, `launch/build_brep_writer.sh`, `narwhal3d/kernel/brep_writer.cpp`, and every file or symlink under `narwhal3d/kernel/bin/` are forbidden. Assembly fails if they are present in the source package, and verification rejects them in an AppDir or extracted AppImage.
- **Optional converter support:** `narwhal3d/cdr_reader.py` and `narwhal3d/cdr_writer.py` are forbidden from this release, and optional converter runtimes are not downloaded or bundled. Their presence fails assembly/verification rather than being silently ignored. They may be introduced only after their artifacts, licenses, and hashes are reviewed and locked.

No excluded component may be inferred to be present merely because the launcher can detect an independently installed copy.
