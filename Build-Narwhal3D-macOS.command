#!/bin/bash
# Double-clickable macOS build and return-bundle collector for Narwhal 3D.
set -euo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
BLENDER_COMMIT="d13f752e3b9c4f8c261cda552b1021f8bcc0382c"
BLENDER_URL="https://projects.blender.org/blender/blender.git"
SELF_CHECK=0
SKIP_UPDATE=0
JOBS=""

usage() {
  cat <<'EOF'
Usage: ./Build-Narwhal3D-macOS.command [--skip-update] [--jobs N] [--self-check]

Double-click with no arguments for the normal build. The command builds the pinned
macOS runtime, runs focused acceptance gates, and creates two ZIP files on Desktop.
It uploads nothing.
EOF
}

while (($#)); do
  case "$1" in
    --skip-update) SKIP_UPDATE=1; shift ;;
    --jobs)
      [[ $# -ge 2 ]] || { echo "missing value for --jobs" >&2; exit 2; }
      JOBS="$2"; shift 2 ;;
    --self-check) SELF_CHECK=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if ((SELF_CHECK)); then
  bash -n "$0"
  grep -q 'build_macos_source.sh' "$0"
  grep -q 'public_ui_report.py' "$0"
  grep -q 'audit_representation.py' "$0"
  grep -q 'ditto -c -k --sequesterRsrc --keepParent' "$0"
  grep -q 'It uploads nothing' "$0"
  echo "Build-Narwhal3D-macOS.command self-check: OK"
  exit 0
fi

[[ "$(uname -s)" == "Darwin" ]] || {
  echo "This collector must run on macOS." >&2
  exit 2
}
[[ -z "$JOBS" || "$JOBS" =~ ^[1-9][0-9]*$ ]] || {
  echo "--jobs must be a positive integer" >&2
  exit 2
}

ARCH="$(uname -m)"
case "$ARCH" in
  arm64|x86_64) ;;
  *) echo "Unsupported Mac architecture: $ARCH" >&2; exit 2 ;;
esac
MACOS_VERSION="$(sw_vers -productVersion)"
MACOS_MAJOR="${MACOS_VERSION%%.*}"
[[ "$MACOS_MAJOR" =~ ^[0-9]+$ && "$MACOS_MAJOR" -ge 14 ]] || {
  echo "macOS 14 or newer is required (found $MACOS_VERSION)." >&2
  exit 2
}

DESKTOP="$HOME/Desktop"
[[ -d "$DESKTOP" ]] || { echo "Desktop directory was not found." >&2; exit 2; }
STAMP="$(date -u +%Y%m%d-%H%M%S)"
BASENAME="Narwhal3D-macOS-${ARCH}-${STAMP}"
RETURN_DIR="$DESKTOP/${BASENAME}-Report"
REPORT_ZIP="$RETURN_DIR.zip"
PAYLOAD_DIR="$DESKTOP/${BASENAME}-Build"
PAYLOAD_ZIP="$PAYLOAD_DIR.zip"
WORK_ROOT="$HOME/Library/Caches/Narwhal3D-macOS-build"
BLENDER_DIR="$WORK_ROOT/blender-$BLENDER_COMMIT"
RAW_LOG="$RETURN_DIR/build-raw-local.log"
SANITIZED_LOG="$RETURN_DIR/build.log"
RESULT="failed"
PHASE="preflight"
mkdir -p "$RETURN_DIR"
exec > >(tee -a "$RAW_LOG") 2>&1

finish() {
  status="$1"
  trap - EXIT
  set +e
  if [[ "$status" -eq 0 ]]; then RESULT="passed"; fi
  sed "s|$HOME|~|g" "$RAW_LOG" > "$SANITIZED_LOG"
  rm -f "$RAW_LOG"
  {
    echo "Narwhal 3D macOS build return bundle"
    echo "Result: $RESULT"
    echo "Last phase: $PHASE"
    echo "Architecture: $ARCH"
    echo "macOS: $MACOS_VERSION"
    echo "Blender commit: $BLENDER_COMMIT"
    echo "Narwhal commit: $(git -C "$ROOT" rev-parse HEAD 2>/dev/null || echo unavailable)"
    echo "Build artifact: $(basename "$PAYLOAD_ZIP")"
    echo "Uploads performed: no"
  } > "$RETURN_DIR/build-report.txt"
  {
    echo "Review build-report.txt and build.log before sharing."
    echo "Return this report ZIP and, when present, the separate Build ZIP."
    echo "The collector intentionally excludes user and computer names, serial numbers,"
    echo "network addresses, environment dumps, personal files, and CAD documents."
    echo "Nothing is uploaded automatically."
  } > "$RETURN_DIR/README-BEFORE-SHARING.txt"
  if [[ -f "$PAYLOAD_ZIP" ]]; then
    shasum -a 256 "$PAYLOAD_ZIP" | sed "s|  .*/|  |" > "$RETURN_DIR/build-artifact.sha256"
  fi
  rm -f "$REPORT_ZIP"
  ditto -c -k --sequesterRsrc --keepParent "$RETURN_DIR" "$REPORT_ZIP"
  echo
  if [[ "$status" -eq 0 ]]; then
    echo "macOS build and collection completed."
    echo "Build ZIP:  $PAYLOAD_ZIP"
  else
    echo "Build stopped during: $PHASE"
  fi
  echo "Report ZIP: $REPORT_ZIP"
  echo "Review the report before returning the ZIP file(s)."
  if [[ -t 0 ]]; then
    read -r -p "Press Return to close this window..." _unused
  fi
  exit "$status"
}
trap 'finish $?' EXIT

PHASE="tool checks"
xcode-select -p >/dev/null 2>&1 || {
  echo "Apple command-line developer tools are missing. Run: xcode-select --install" >&2
  exit 2
}
for tool in git git-lfs cmake make python3 cargo ditto shasum; do
  command -v "$tool" >/dev/null || {
    echo "Required tool is missing: $tool" >&2
    echo "Install prerequisites from docs/macos-build.md, then double-click this file again." >&2
    exit 2
  }
done

{
  echo "Tool versions (sanitized)"
  git --version
  git-lfs version
  cmake --version | sed -n '1p'
  make --version | sed -n '1p'
  python3 --version
  cargo --version
  xcodebuild -version
  echo "Hardware model: $(sysctl -n hw.model 2>/dev/null || echo unavailable)"
  system_profiler SPDisplaysDataType 2>/dev/null | awk -F: '/Chipset Model|Chip/{gsub(/^[ \t]+|[ \t]+$/, "", $2); if ($2 != "") print "GPU: " $2}' | sort -u
} > "$RETURN_DIR/tool-versions.txt"

PHASE="source checks"
[[ -x "$ROOT/scripts/build_macos_source.sh" ]] || {
  echo "scripts/build_macos_source.sh is missing or not executable." >&2
  exit 2
}
[[ -d "$ROOT/.git" ]] || { echo "Run this from a Narwhal 3D Git checkout." >&2; exit 2; }
git -C "$ROOT" diff --check

PHASE="pinned Blender checkout"
mkdir -p "$WORK_ROOT"
if [[ ! -d "$BLENDER_DIR/.git" ]]; then
  [[ ! -e "$BLENDER_DIR" ]] || {
    echo "Unsafe non-Git cache entry exists at the dedicated Blender path." >&2
    exit 2
  }
  git clone "$BLENDER_URL" "$BLENDER_DIR"
fi
[[ ! -L "$BLENDER_DIR" ]] || { echo "Refusing a symlinked Blender cache." >&2; exit 2; }
REMOTE_URL="$(git -C "$BLENDER_DIR" config --get remote.origin.url)"
[[ "$REMOTE_URL" == "$BLENDER_URL" ]] || {
  echo "Dedicated Blender cache has an unexpected origin: $REMOTE_URL" >&2
  exit 2
}
git -C "$BLENDER_DIR" fetch origin "$BLENDER_COMMIT"
git -C "$BLENDER_DIR" reset --hard "$BLENDER_COMMIT"
git -C "$BLENDER_DIR" clean -fdx

declare -a BUILD_ARGS
BUILD_ARGS=(--source-dir "$BLENDER_DIR")
if ((SKIP_UPDATE)); then BUILD_ARGS+=(--skip-update); fi
if [[ -n "$JOBS" ]]; then BUILD_ARGS+=(--jobs "$JOBS"); fi
PHASE="Blender and native component build"
"$ROOT/scripts/build_macos_source.sh" "${BUILD_ARGS[@]}"

APP="$ROOT/tools/blender-patched/Blender.app"
APP_EXE="$APP/Contents/MacOS/Blender"
RUNTIME_BIN="$ROOT/runtime/macos-$ARCH/bin"
[[ -x "$APP_EXE" && -d "$RUNTIME_BIN" ]] || {
  echo "Build outputs are incomplete." >&2
  exit 2
}
BUNDLED_PYTHON="$(find "$APP/Contents/Resources" -type f -path '*/python/bin/python3.13' -print -quit)"
[[ -x "$BUNDLED_PYTHON" ]] || { echo "Bundled CPython 3.13 is missing." >&2; exit 2; }

export NARWHAL_PURE_CORE="$RUNTIME_BIN/libnarwhal_pure_core.dylib"
export NARWHAL_SELECTION_CORE="$RUNTIME_BIN/libnarwhal_selection_core.dylib"
export NARWHAL_CDR_CORE="$RUNTIME_BIN/libnarwhal_cdr_core.dylib"
export NARWHAL_DOCUMENT_CORE="$RUNTIME_BIN/narwhal-document-core"
export NARWHAL_CONVERTER_SUPERVISOR="$RUNTIME_BIN/narwhal-converter-supervisor"
export NARWHAL_CDR_MULTIVERSION="$RUNTIME_BIN/narwhal-cdr-multiversion"
export NARWHAL3D_NO_DIALOGS=1

PHASE="runtime smoke checks"
"$APP_EXE" --version
"$BUNDLED_PYTHON" -c 'import rhino3dm, OCP; print("macOS geometry imports: OK")'
for binary in "$RUNTIME_BIN"/*; do
  file "$binary"
  shasum -a 256 "$binary"
done > "$RETURN_DIR/native-components.txt"

PHASE="exact representation audit"
"$APP_EXE" -b --factory-startup --python-exit-code 1 \
  --python "$ROOT/spike/audit_representation.py"

PHASE="real-window UI report"
"$APP_EXE" --factory-startup \
  --python "$ROOT/launch/bootstrap.py" \
  --python "$ROOT/spike/public_ui_report.py"
[[ -f "$ROOT/artifacts/ui_report.txt" ]] && cp "$ROOT/artifacts/ui_report.txt" "$RETURN_DIR/"
[[ -f "$ROOT/artifacts/ui.png" ]] && cp "$ROOT/artifacts/ui.png" "$RETURN_DIR/"

PHASE="return archive"
rm -rf "$PAYLOAD_DIR" "$PAYLOAD_ZIP"
mkdir -p "$PAYLOAD_DIR/runtime/macos-$ARCH"
ditto "$APP" "$PAYLOAD_DIR/Narwhal 3D.app"
ditto "$RUNTIME_BIN" "$PAYLOAD_DIR/runtime/macos-$ARCH/bin"
{
  echo "Built from Narwhal commit: $(git -C "$ROOT" rev-parse HEAD)"
  echo "Blender commit: $BLENDER_COMMIT"
  echo "Architecture: $ARCH"
  echo "macOS: $MACOS_VERSION"
  echo "Unsigned and unnotarized engineering artifact; not yet a distributable release."
} > "$PAYLOAD_DIR/BUILD-IDENTITY.txt"
ditto -c -k --sequesterRsrc --keepParent "$PAYLOAD_DIR" "$PAYLOAD_ZIP"
RESULT="passed"
PHASE="complete"
