#!/usr/bin/env bash
# Build an experimental local Narwhal 3D runtime on macOS 14+.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REQUIRED_BLENDER_COMMIT="d13f752e3b9c4f8c261cda552b1021f8bcc0382c"
SOURCE_DIR=""
JOBS=""
SKIP_UPDATE=0
SELF_CHECK=0

usage() {
  cat <<'EOF'
Usage: scripts/build_macos_source.sh --source-dir PATH [OPTIONS]

Build an experimental, unsigned Narwhal 3D source checkout for the current Mac
architecture. This is a community/developer build, not a supported release.

Required:
  --source-dir PATH   Clean Blender checkout at the pinned 5.2.2 commit

Options:
  --jobs N            Parallel build jobs (default: logical CPU count)
  --skip-update       Do not run Blender's networked `make update`
  --self-check        Validate this recipe without building or requiring macOS
  -h, --help          Show this help
EOF
}

while (($#)); do
  case "$1" in
    --source-dir|--jobs)
      [[ $# -ge 2 ]] || { echo "missing value for $1" >&2; exit 2; }
      case "$1" in
        --source-dir) SOURCE_DIR="$2" ;;
        --jobs) JOBS="$2" ;;
      esac
      shift 2
      ;;
    --skip-update) SKIP_UPDATE=1; shift ;;
    --self-check) SELF_CHECK=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

PATCH_INPUTS=(
  "$ROOT/blender_patches/apply_narwhal3d_branding.py"
  "$ROOT/blender_patches/apply_app_drawn.py"
  "$ROOT/blender_patches/apply_cad_boundary_wires.py"
  "$ROOT/blender_patches/apply_deferred_first_present.py"
  "$ROOT/blender_patches/narwhal3d_app_icon.svg"
)
for input in "${PATCH_INPUTS[@]}"; do
  [[ -f "$input" && ! -L "$input" ]] || {
    echo "required patch input is missing or symlinked: $input" >&2
    exit 2
  }
done

if ((SELF_CHECK)); then
  python3 - "$REQUIRED_BLENDER_COMMIT" "${PATCH_INPUTS[@]}" <<'PY'
import ast
import re
import sys
from pathlib import Path
commit = sys.argv[1]
if commit != "d13f752e3b9c4f8c261cda552b1021f8bcc0382c" or not re.fullmatch(r"[0-9a-f]{40}", commit):
    raise SystemExit("invalid pinned Blender commit")
for raw in sys.argv[2:-1]:
    path = Path(raw)
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
if Path(sys.argv[-1]).suffix != ".svg":
    raise SystemExit("missing SVG icon input")
PY
  echo "build_macos_source.sh self-check: OK"
  exit 0
fi

[[ "$(uname -s)" == "Darwin" ]] || {
  echo "this build recipe must run on macOS (Darwin)" >&2
  exit 2
}
ARCH="$(uname -m)"
case "$ARCH" in
  arm64|x86_64) ;;
  *) echo "unsupported Mac architecture: $ARCH" >&2; exit 2 ;;
esac
MAJOR="$(sw_vers -productVersion | cut -d. -f1)"
[[ "$MAJOR" =~ ^[0-9]+$ && "$MAJOR" -ge 14 ]] || {
  echo "macOS 14 or newer is required by the exact CPython 3.13 wheels" >&2
  exit 2
}
for command_name in git git-lfs cmake make python3 cargo ditto; do
  command -v "$command_name" >/dev/null || {
    echo "required command is missing: $command_name" >&2
    exit 2
  }
done
[[ -n "$SOURCE_DIR" ]] || { echo "--source-dir is required" >&2; exit 2; }
[[ -z "$JOBS" || "$JOBS" =~ ^[1-9][0-9]*$ ]] || {
  echo "--jobs must be a positive integer" >&2
  exit 2
}
if [[ -z "$JOBS" ]]; then
  JOBS="$(sysctl -n hw.logicalcpu)"
fi
SOURCE_DIR="$(cd "$SOURCE_DIR" && pwd)"
[[ -f "$SOURCE_DIR/CMakeLists.txt" && -d "$SOURCE_DIR/.git" ]] || {
  echo "not a Blender Git checkout: $SOURCE_DIR" >&2
  exit 2
}
ACTUAL="$(git -C "$SOURCE_DIR" rev-parse HEAD)"
[[ "$ACTUAL" == "$REQUIRED_BLENDER_COMMIT" ]] || {
  echo "Blender source must be exactly $REQUIRED_BLENDER_COMMIT (got $ACTUAL)" >&2
  exit 2
}
[[ -z "$(git -C "$SOURCE_DIR" status --porcelain --untracked-files=no)" ]] || {
  echo "Blender checkout has tracked modifications; use a clean checkout" >&2
  exit 2
}

if ((!SKIP_UPDATE)); then
  make -C "$SOURCE_DIR" update
fi

python3 - "$SOURCE_DIR" "${PATCH_INPUTS[@]}" <<'PY'
import importlib.util
import sys
from pathlib import Path
source = Path(sys.argv[1])

def load(name, raw):
    path = Path(raw)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load patch {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

branding = load("narwhal_branding", sys.argv[2])
branding.apply(str(source), sys.argv[6])
for name, raw in (("app_drawn", sys.argv[3]), ("boundary", sys.argv[4]),
                  ("deferred", sys.argv[5])):
    module = load(name, raw)
    module.SRC = str(source / "source" / "blender")
    module.main()
PY

make -C "$SOURCE_DIR" -j"$JOBS"
BUILT_APP="$(cd "$SOURCE_DIR/.." && pwd)/build_darwin/bin/Blender.app"
[[ -x "$BUILT_APP/Contents/MacOS/Blender" ]] || {
  echo "Blender build did not produce $BUILT_APP" >&2
  exit 2
}
INSTALL_APP="$ROOT/tools/blender-patched/Blender.app"
mkdir -p "$(dirname "$INSTALL_APP")"
rm -rf "$INSTALL_APP"
ditto "$BUILT_APP" "$INSTALL_APP"

BUNDLED_PYTHON="$(find "$INSTALL_APP/Contents/Resources" -type f -path '*/python/bin/python3.13' -print -quit)"
[[ -x "$BUNDLED_PYTHON" ]] || {
  echo "built app has no bundled CPython 3.13 interpreter" >&2
  exit 2
}
"$BUNDLED_PYTHON" -m ensurepip --upgrade
"$BUNDLED_PYTHON" -m pip install --only-binary=:all: \
  'rhino3dm==8.35.0' \
  'cadquery-ocp-proxy==8.0.1.0.0' \
  'cadquery-ocp-novtk==8.0.1.0.0'

RUNTIME_BIN="$ROOT/runtime/macos-$ARCH/bin"
mkdir -p "$RUNTIME_BIN"
for crate in pure_core selection_core cdr_core document_core converter_supervisor cdr_multiversion; do
  cargo build --manifest-path "$ROOT/native/$crate/Cargo.toml" --release --locked
 done
cp "$ROOT/native/pure_core/target/release/libnarwhal_pure_core.dylib" "$RUNTIME_BIN/"
cp "$ROOT/native/selection_core/target/release/libnarwhal_selection_core.dylib" "$RUNTIME_BIN/"
cp "$ROOT/native/cdr_core/target/release/libnarwhal_cdr_core.dylib" "$RUNTIME_BIN/"
cp "$ROOT/native/document_core/target/release/narwhal-document-core" "$RUNTIME_BIN/"
cp "$ROOT/native/converter_supervisor/target/release/narwhal-converter-supervisor" "$RUNTIME_BIN/"
cp "$ROOT/native/cdr_multiversion/target/release/narwhal-cdr-multiversion" "$RUNTIME_BIN/"
chmod +x "$RUNTIME_BIN/narwhal-document-core" \
  "$RUNTIME_BIN/narwhal-converter-supervisor" \
  "$RUNTIME_BIN/narwhal-cdr-multiversion"

"$INSTALL_APP/Contents/MacOS/Blender" --version | grep -q '^Narwhal 3D 5\.2\.2' || {
  echo "built runtime does not carry the Narwhal version banner" >&2
  exit 2
}
"$BUNDLED_PYTHON" -c 'import rhino3dm, OCP; print("macOS geometry dependencies: OK")'
cat <<EOF
Experimental macOS source build completed.
Architecture: $ARCH
Application:  $INSTALL_APP
Native tools: $RUNTIME_BIN
Run:          ./narwhal3d.sh

This local build is unsigned, unnotarized, and not a supported release.
EOF
