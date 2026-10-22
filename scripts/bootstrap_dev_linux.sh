#!/usr/bin/env bash
# Bootstrap the official Blender runtime for development only; never for release packaging.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCK_FILE="$ROOT_DIR/packaging/linux-x86_64.lock.json"
FETCHER="$ROOT_DIR/scripts/fetch_locked.py"
CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/narwhal3d/artifacts"
RUNTIME_DIR="$ROOT_DIR/tools/blender-5.2.2-linux-x64"
OFFLINE=0
SELF_CHECK=0

usage() {
  cat <<'EOF'
Usage: scripts/bootstrap_dev_linux.sh [OPTIONS]

Fetch the locked official Blender 5.2.2 Linux x86_64 runtime for local
DEVELOPMENT ONLY, then install the locked CPython 3.13 wheels. Publishable
AppImages must use the separately built native Narwhal runtime and will reject
this official runtime and its provenance markers.

Options:
  --cache-dir PATH    Artifact cache (default: XDG cache outside repository)
  --runtime-dir PATH  Extracted runtime destination
  --offline           Refuse network access; require a complete verified cache
  --self-check        Validate local lock/bootstrap configuration without downloads
  -h, --help          Show this help
EOF
}

while (($#)); do
  case "$1" in
    --cache-dir)
      [[ $# -ge 2 ]] || { echo "missing value for --cache-dir" >&2; exit 2; }
      CACHE_DIR="$2"
      shift 2
      ;;
    --runtime-dir)
      [[ $# -ge 2 ]] || { echo "missing value for --runtime-dir" >&2; exit 2; }
      RUNTIME_DIR="$2"
      shift 2
      ;;
    --offline)
      OFFLINE=1
      shift
      ;;
    --self-check)
      SELF_CHECK=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

[[ "$(uname -s)" == "Linux" ]] || { echo "bootstrap requires Linux" >&2; exit 2; }
[[ "$(uname -m)" == "x86_64" ]] || { echo "bootstrap requires x86_64" >&2; exit 2; }
[[ -f "$LOCK_FILE" && -f "$FETCHER" ]] || { echo "packaging lock or fetcher is missing" >&2; exit 2; }

if ((SELF_CHECK)); then
  python3 "$FETCHER" --self-check
  echo "bootstrap_dev_linux.sh self-check: OK"
  exit 0
fi

mkdir -p "$CACHE_DIR"
fetch_args=(
  "$FETCHER"
  --lock "$LOCK_FILE"
  --cache-dir "$CACHE_DIR"
  blender-runtime rhino3dm cadquery-ocp-novtk cadquery-ocp-proxy
  --extract "blender-runtime=$RUNTIME_DIR"
)
if ((OFFLINE)); then
  fetch_args+=(--offline)
fi
python3 "${fetch_args[@]}"

BLENDER="$RUNTIME_DIR/blender"
PYTHON="$RUNTIME_DIR/5.2/python/bin/python3.13"
[[ -x "$BLENDER" ]] || { echo "locked runtime has no executable Blender binary: $BLENDER" >&2; exit 2; }
[[ -x "$PYTHON" ]] || { echo "locked runtime has no CPython 3.13 interpreter: $PYTHON" >&2; exit 2; }

"$PYTHON" -m ensurepip --upgrade
"$PYTHON" -m pip --disable-pip-version-check install \
  --no-index --no-deps --no-compile \
  "$CACHE_DIR/cadquery_ocp_proxy-8.0.1.0.0-py3-none-any.whl" \
  "$CACHE_DIR/cadquery_ocp_novtk-8.0.1.0.0-cp313-cp313-manylinux_2_28_x86_64.whl" \
  "$CACHE_DIR/rhino3dm-8.35.0-cp313-cp313-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl"

"$PYTHON" - <<'PY'
import importlib.metadata
import sys

if sys.version_info[:2] != (3, 13):
    raise SystemExit(f"expected CPython 3.13, got {sys.version}")
import rhino3dm  # noqa: F401
import OCP  # noqa: F401
expected = {
    "rhino3dm": "8.35.0",
    "cadquery-ocp-novtk": "8.0.1.0.0",
    "cadquery-ocp-proxy": "8.0.1.0.0",
}
for distribution, version in expected.items():
    actual = importlib.metadata.version(distribution)
    if actual != version:
        raise SystemExit(f"{distribution}: expected {version}, got {actual}")
PY

"$PYTHON" - "$LOCK_FILE" "$RUNTIME_DIR/.narwhal3d-runtime.json" <<'PY'
import hashlib
import json
from pathlib import Path
import sys

lock_path = Path(sys.argv[1])
marker_path = Path(sys.argv[2])
raw = lock_path.read_bytes()
lock = json.loads(raw)
marker = {
    "schema": 1,
    "product": lock["product"],
    "platform": lock["platform"],
    "runtime_flavor": lock["runtime"]["flavor"],
    "blender_version": lock["runtime"]["blender_version"],
    "lock_sha256": hashlib.sha256(raw).hexdigest(),
    "artifacts": {item["id"]: item["sha256"] for item in lock["artifacts"]},
    "python_distributions": {
        "rhino3dm": "8.35.0",
        "cadquery-ocp-novtk": "8.0.1.0.0",
        "cadquery-ocp-proxy": "8.0.1.0.0",
    },
}
temporary = marker_path.with_suffix(marker_path.suffix + ".tmp")
temporary.write_text(json.dumps(marker, indent=2, sort_keys=True) + "\n", encoding="utf-8")
temporary.replace(marker_path)
PY

echo "Narwhal 3D development-only official runtime is ready: $RUNTIME_DIR"
