#!/usr/bin/env bash
# Build and stage the native components a source checkout needs to launch and test, after
# scripts/bootstrap_dev_linux.sh. Development only: release packages use the separately built and
# qualified components. Outputs go to gitignored artifacts/ and tools/ paths and to the user cache.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME="$ROOT/tools/blender-5.2.2-linux-x64"
PYTHON="$RUNTIME/5.2/python/bin/python3.13"
DEV_LOCK="$ROOT/packaging/linux-x86_64-dev.lock.json"
FETCHER="$ROOT/scripts/fetch_locked.py"
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/narwhal3d"
INCLUDE="$ROOT/tools/cpython-3.13-include/python3.13"
CPYTHON_SOURCE="$ROOT/tools/cpython-3.13.13-src"
TOOLCHAIN=1.95.0
PATCHED="$ROOT/tools/blender-patched"
OCCT_SOURCE="$ROOT/vendor/occt-src"
OCCT_REPOSITORY=https://github.com/Open-Cascade-SAS/OCCT.git
# The exact official OCCT revision whose headers match the pinned OCP wheel libraries.
OCCT_REVISION=b8f597c677811d1f9f4d8a97f5ae2825c0353a42
PILLOW_VERSION=12.0.0
TRIPLE=x86_64-unknown-linux-gnu

usage() {
  cat <<'USAGE'
Usage: scripts/setup_dev_native.sh [--self-check] [-h|--help]

After scripts/bootstrap_dev_linux.sh, build the native development components:
  * CPython 3.13.13 headers (pinned python.org source, configured locally)
  * the Rust cores (pure, selection, platform, cdr, document, application, UI)
  * the document observer (qualified against the pinned rhino3dm source)
  * the patched application runtime (tools/blender-patched, from the pinned published AppImage)
  * the pinned OCCT source (vendor/occt-src) and the Rust geometry core
  * the automation library, the exact-save helper and a Pillow environment for icon tests
then check that the application's required native startup checks pass.

Needs: python3 (with venv), git, curl, rustup with toolchain 1.95.0 (rustfmt and clippy), cmake,
make, a C and C++ compiler, a memory bounding mechanism (systemd-run, prlimit, or ulimit), and about 4 GB of disk.
podman or docker is used for the exact-save helper when available.
USAGE
}

SELF_CHECK=0
case "${1:-}" in
  --self-check) SELF_CHECK=1 ;;
  -h|--help) usage; exit 0 ;;
  "") ;;
  *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
esac

if ((SELF_CHECK)); then
  [[ -f "$DEV_LOCK" && -f "$FETCHER" ]] || { echo "development lock or fetcher is missing" >&2; exit 2; }
  python3 "$FETCHER" --self-check >/dev/null
  for script in check_application_core.py check_ui_core.py check_document_observer.py memory_bound.py; do
    [[ -f "$ROOT/scripts/$script" ]] || { echo "missing scripts/$script" >&2; exit 2; }
  done
  echo "setup_dev_native.sh self-check: OK"
  exit 0
fi

step() { printf '\n==> %s\n' "$*"; }
need() { command -v "$1" >/dev/null 2>&1 || { echo "error: '$1' is required ($2)" >&2; exit 2; }; }

[[ "$(uname -s)-$(uname -m)" == "Linux-x86_64" ]] || { echo "error: Linux x86_64 only" >&2; exit 2; }
[[ -x "$PYTHON" ]] || { echo "error: run scripts/bootstrap_dev_linux.sh first (missing $PYTHON)" >&2; exit 2; }
need python3 "runs the locked fetcher"
need git "fetches the pinned OCCT source"
need curl "downloads the pinned openNURBS source for the exact-save helper"
need cargo "install rustup, then: rustup toolchain install $TOOLCHAIN --component rustfmt clippy"
need cmake "builds openNURBS for the document observer"
need make "builds CPython configuration and openNURBS"
need cc "C compiler"
need c++ "C++ compiler"
python3 "$ROOT/scripts/memory_bound.py" --probe >/dev/null || {
  echo "error: no memory bounding mechanism available (systemd-run, prlimit, or ulimit)" >&2; exit 2; }
cargo +"$TOOLCHAIN" --version >/dev/null 2>&1 || {
  echo "error: Rust $TOOLCHAIN is not installed: rustup toolchain install $TOOLCHAIN --component rustfmt clippy" >&2; exit 2; }
"$PYTHON" -c 'import sys; sys.exit(sys.version_info[:3] != (3, 13, 13))' || {
  echo "error: the bootstrapped runtime is not CPython 3.13.13; headers would not match" >&2; exit 2; }
mkdir -p "$CACHE/artifacts"

if [[ ! -f "$INCLUDE/Python.h" || ! -f "$INCLUDE/pyconfig.h" ]]; then
  step "CPython 3.13.13 headers"
  python3 "$FETCHER" --lock "$DEV_LOCK" --cache-dir "$CACHE/artifacts" cpython-source \
    --extract "cpython-source=$CPYTHON_SOURCE"
  build="$(mktemp -d)"
  staging="$(mktemp -d "$ROOT/tools/.cpython-include.XXXXXX")"
  trap 'rm -rf "$build" "$staging"' EXIT
  (cd "$build" && "$CPYTHON_SOURCE/configure" --quiet >configure.log 2>&1) || {
    echo "error: CPython configure failed; see $build/configure.log" >&2; trap - EXIT; exit 1; }
  mkdir -p "$staging/python3.13"
  cp -R "$CPYTHON_SOURCE/Include/." "$staging/python3.13/"
  cp "$build/pyconfig.h" "$staging/python3.13/pyconfig.h"
  rm -rf "$ROOT/tools/cpython-3.13-include"
  mv "$staging" "$ROOT/tools/cpython-3.13-include"
  rm -rf "$build"
  trap - EXIT
fi

if [[ ! -x "$PATCHED/blender" ]]; then
  step "Patched application runtime (from the pinned published AppImage)"
  appimage="$(python3 "$FETCHER" --lock "$DEV_LOCK" --cache-dir "$CACHE/artifacts" narwhal-appimage-runtime | sed -n 's/^narwhal-appimage-runtime=//p')"
  extract="$(mktemp -d "$ROOT/tools/.patched-runtime.XXXXXX")"
  (cd "$extract" && APPIMAGE_EXTRACT_AND_RUN=1 "$appimage" --appimage-extract >/dev/null)
  [[ -x "$extract/squashfs-root/usr/lib/narwhal3d/tools/blender-patched/blender" ]] || {
    echo "error: the AppImage has no patched runtime" >&2; rm -rf "$extract"; exit 1; }
  mv "$extract/squashfs-root/usr/lib/narwhal3d/tools/blender-patched" "$PATCHED"
  rm -rf "$extract"
  printf '{"source": "%s", "purpose": "development runtime extracted from a published release"}\n' \
    "$(basename "$appimage")" > "$PATCHED/.narwhal3d-dev-origin.json"
fi

if [[ ! -d "$OCCT_SOURCE/src" ]] || [[ "$(git -C "$OCCT_SOURCE" rev-parse HEAD 2>/dev/null)" != "$OCCT_REVISION" ]]; then
  step "Pinned OCCT source (vendor/occt-src)"
  [[ -e "$OCCT_SOURCE" && ! -d "$OCCT_SOURCE/.git" ]] && {
    echo "error: $OCCT_SOURCE exists and is not a git checkout; move it away" >&2; exit 2; }
  if [[ ! -d "$OCCT_SOURCE/.git" ]]; then
    git init -q "$OCCT_SOURCE"
    git -C "$OCCT_SOURCE" remote add origin "$OCCT_REPOSITORY"
  fi
  git -C "$OCCT_SOURCE" fetch -q --depth 1 origin "$OCCT_REVISION"
  git -C "$OCCT_SOURCE" -c advice.detachedHead=false checkout -q --force "$OCCT_REVISION"
  [[ "$(git -C "$OCCT_SOURCE" rev-parse HEAD)" == "$OCCT_REVISION" ]] || {
    echo "error: OCCT checkout is not the pinned revision" >&2; exit 1; }
fi

step "Rust cores"
cargo +"$TOOLCHAIN" build --release --locked --manifest-path "$ROOT/native/pure_core/Cargo.toml" --target "$TRIPLE"
for crate in selection_core platform_core cdr_core document_core; do
  cargo +"$TOOLCHAIN" build --release --locked --manifest-path "$ROOT/native/$crate/Cargo.toml"
done

OCCT_LIBS="$PATCHED/5.2/python/lib/python3.13/site-packages/cadquery_ocp_novtk.libs"
NARWHAL_OCCT_SOURCE_DIR="$OCCT_SOURCE" NARWHAL_OCCT_LIB_DIR="$OCCT_LIBS" LD_LIBRARY_PATH="$OCCT_LIBS" \
  cargo +"$TOOLCHAIN" build --release --locked --manifest-path "$ROOT/native/geometry_core/Cargo.toml"

step "Automation library and server (staged in artifacts/automation)"
cargo +"$TOOLCHAIN" build --release --locked --manifest-path "$ROOT/native/automation_core/Cargo.toml"
mkdir -p "$ROOT/artifacts/automation/linux-x86_64"
cp "$ROOT/native/automation_core/target/release/libnarwhal_automation.so" \
   "$ROOT/native/automation_core/target/release/narwhal-automation" "$ROOT/artifacts/automation/linux-x86_64/"

step "Application core (qualified and staged in artifacts/application-core)"
"$PYTHON" "$ROOT/scripts/check_application_core.py" --stage-dev --target-dir "$CACHE/application-core-target"

step "UI core (qualified and staged in artifacts/ui-core)"
"$PYTHON" "$ROOT/scripts/check_ui_core.py" --target-dir "$CACHE/ui-core-target"
mkdir -p "$ROOT/artifacts/ui-core/linux-x86_64"
cp "$CACHE/ui-core-target/release/libnarwhal_ui_core.so" "$ROOT/artifacts/ui-core/linux-x86_64/"
cp "$CACHE/ui-core-target/release/ui-core-source.json" "$ROOT/artifacts/ui-core/linux-x86_64/" 2>/dev/null || true

step "Document observer (qualified and staged in artifacts/document-observer)"
python3 "$FETCHER" --lock "$DEV_LOCK" --cache-dir "$CACHE/artifacts" rhino3dm-sdist >/dev/null
mkdir -p "$CACHE/document-observer"
cp -n "$CACHE/artifacts/rhino3dm-8.35.0.tar.gz" "$CACHE/document-observer/" 2>/dev/null || true
"$PYTHON" -B "$ROOT/scripts/check_document_observer.py" --python "$PYTHON" --python-include "$INCLUDE" \
  --work-dir "$CACHE/document-observer" --stage-dev

if [[ ! -x "$ROOT/artifacts/brep-writer/linux-x86_64/brep_writer-linux-x86_64" ]]; then
  step "Exact-save helper (artifacts/brep-writer)"
  if command -v podman >/dev/null 2>&1 || command -v docker >/dev/null 2>&1; then
    "$ROOT/scripts/build_brep_writer_source.sh"
  else
    "$ROOT/scripts/build_brep_writer_source.sh" --host
  fi
fi

if [[ ! -x "$CACHE/icon-test-venv/bin/python" ]]; then
  step "Pillow $PILLOW_VERSION environment for icon tests ($CACHE/icon-test-venv)"
  python3 -m venv "$CACHE/icon-test-venv"
  "$CACHE/icon-test-venv/bin/python" -m pip --disable-pip-version-check install -q "pillow==$PILLOW_VERSION"
fi

step "Required native startup checks"
NARWHAL_PURE_CORE="$ROOT/native/pure_core/target/$TRIPLE/release/libnarwhal_pure_core.so" \
NARWHAL_SELECTION_CORE="$ROOT/native/selection_core/target/release/libnarwhal_selection_core.so" \
NARWHAL_PLATFORM_CORE="$ROOT/native/platform_core/target/release/libnarwhal_platform_core.so" \
LD_LIBRARY_PATH="$OCCT_LIBS" \
  "$PATCHED/5.2/python/bin/python3.13" -I -B "$ROOT/launch/check_native.py"

cat <<DONE

Native development components are ready.
  Launch the app:   ./narwhal3d.sh
  Run the tests:    ./run_tests.sh
  Contributor guide: docs/contributing-with-ai.md
DONE
