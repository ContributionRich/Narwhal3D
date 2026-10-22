# Bundled converter runtime

The intended layout is `runtime/<platform>-<arch>/bin/`, for example
`runtime/linux-x86_64/bin/`. The application prefers this app-local directory,
then `tools/runtime/<platform>-<arch>/bin/` in a development checkout, and only
then the host `PATH`. Set `NARWHAL3D_RUNTIME_DIR` to override the runtime root;
set `NARWHAL3D_STRICT_RUNTIME=1` to refuse PATH fallback.

The release is **not fully self-contained yet**. Linux Inkscape can now be staged
from Fedora's signed RPM closure with `scripts/stage_fedora_inkscape.py`; the current
development checkout runs Inkscape 1.4.4 from that private sysroot. TraceDraw CLI is
pinned to exact nightly revision `821ba1545e3d3f75843fe7d3f912214800929821` after
its v0.9.0 CorelDRAW fixes, with both archive and extracted-executable hashes verified;
its measured maximum requirement is GLIBC 2.35. `pdfinfo`, `gs`, and `libreoffice`
still come from the host PATH. Poppler, Ghostscript, and LibreOffice release bundles
remain unresolved rather than invented. Do not mark the complete converter runtime
ready until every declared tool is staged and verified. `manifest.json` is
immutable source metadata; staging writes machine-specific state to the ignored
`runtime/<platform>-<arch>/inventory.json`, never back into the source manifest.

Use `python3 scripts/bootstrap_linux_runtime.py --help` for verified portable
artifacts. TraceDraw's exact archive is staged with:

```bash
python3 scripts/bootstrap_linux_runtime.py \
  --artifact tracedraw-cli=/path/to/tracedraw-0.9.0-dev.821ba15-linux-x86_64.tar.gz,08e74a27fa7b5a7350e718fd0660e6926212cf36d29203caab21eaeb130af20c
```

For the current Fedora Linux development Inkscape bundle:

```bash
dnf download --resolve --destdir /tmp/narwhal-inkscape-rpms inkscape
python3 scripts/stage_fedora_inkscape.py /tmp/narwhal-inkscape-rpms
```

This verifies every RPM signature, extracts only into the ignored private runtime,
writes a scoped wrapper/inventory, and smoke-tests `inkscape --version`. Normal
application launch never downloads or installs software.
