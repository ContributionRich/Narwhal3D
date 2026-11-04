# Packaging notices

## Runtime provenance

A published Linux x86_64 AppImage must contain the natively patched Narwhal 3D runtime built from Blender commit `d13f752e3b9c4f8c261cda552b1021f8bcc0382c`. Its optional `build_source_date_epoch` records native-build provenance independently from the AppImage/public-source `source_date_epoch`. The official Blender binary pinned in `linux-x86_64.lock.json` is development-bootstrap input only and is explicitly rejected by release assembly and verification.

Every AppImage must contain the project-level `LICENSE`, `THIRD_PARTY_NOTICES`, and complete `licenses/` directory under `usr/lib/narwhal3d/`. Verification specifically requires the GPL-3.0-or-later, LGPL-2.1-or-later, rhino3dm MIT, OCP Apache-2.0, OCCT exception, and AppImage MIT texts as regular files.

The pinned appimagetool 1.9.1 executable and type2 runtime tag `20251108` are release build inputs. Their exact URLs and SHA-256 digests are locked. Assembly supplies the runtime through `--runtime-file` so appimagetool performs no mutable continuous-runtime download. The runtime is embedded by appimagetool; its MIT notice is carried as `licenses/AppImage-MIT.txt`.

## Deliberate exclusions

- **openNURBS exact-save helper:** the Linux AppImage includes Narwhal's ONBREP helper at `narwhal3d/kernel/bin/brep_writer-linux-x86_64`, statically linked with openNURBS revision `eb92af3ba1806b0a34a99aba0d3bda83e3d46083` and built by `scripts/build_brep_writer_source.sh` in the pinned manylinux container. The project owner decided on 2026-11-02 to redistribute it; its license text ships as `licenses/openNURBS-LICENSE.txt`. Assembly takes the helper as a separate, protocol-checked input (`--exact-save-helper-dir`): the source package's `narwhal3d/kernel/bin/` must stay empty, and `launch/build_brep_writer.sh` and `narwhal3d/kernel/brep_writer.cpp` remain forbidden. Verification accepts only that one helper below `kernel/bin/`, bound to its manifest hash. The Windows portable EXE (0.4.10) also includes the exact-save helper as `narwhal3d/kernel/bin/brep_writer-win32-amd64.exe`, built by `scripts/build_brep_writer_windows.ps1` against the same pinned openNURBS revision, with its license text at `licenses/openNURBS-LICENSE.txt`. The macOS package does not include it yet.
- **Optional converter support:** the excluded private `narwhal3d/cdr_reader.py` and `narwhal3d/cdr_writer.py` remain forbidden. Public CDR commands use a locally installed TraceDraw CLI, but the 0.4.0 AppImage does not download or bundle that executable. A future bundle may include TraceDraw only after an immutable release or reproducible pinned source build, license, and platform hashes are reviewed and locked. Inkscape, Poppler, Ghostscript, and LibreOffice likewise remain optional external tools.

No excluded component may be inferred to be present merely because the launcher can detect an independently installed copy.
