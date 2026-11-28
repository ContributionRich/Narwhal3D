#!/usr/bin/env bash
# Build the reviewed Rust CLI from source; never select a stale executable.
set -euo pipefail
ROOT=$(dirname "$(dirname "$(readlink -f "$0")")")
CRATE="$ROOT/native/source_tools"
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/narwhal3d/source-tools"
mkdir -p "$CACHE"
exec 9>"$CACHE/build.lock"
flock 9
source_pin() {
  find "$CRATE" -type f -not -path '*/target/*' -not -path '*/.git/*' -print0 | sort -z | xargs -0 sha256sum | sha256sum
}
PIN=$(source_pin)
KEY=${PIN%% *}
# Cargo mtime fingerprints in a shared target are not proof of source identity
# across checkouts. Immutable source-keyed targets also stay safe after unlock.
TARGET="$CACHE/targets/$KEY"
if [[ ! -x "$TARGET/release/narwhal-source-tools" ]]; then
  BUILD=(cargo +1.95.0 build --manifest-path "$CRATE/Cargo.toml" --target-dir "$TARGET" --release --locked --offline -j 2)
  if systemd-run --user --scope -p MemoryMax=3G -p MemorySwapMax=0 -p CPUQuota=200% true >/dev/null 2>&1; then
    systemd-run --user --scope -p MemoryMax=3G -p MemorySwapMax=0 -p CPUQuota=200% "${BUILD[@]}" >&2
  else
    (ulimit -v 3145728; "${BUILD[@]}") >&2
  fi
fi
if [[ "$(source_pin)" != "$PIN" ]]; then
  echo 'source-tools inputs changed during build/admission; refusing cached executable' >&2
  exit 2
fi
flock -u 9
exec "$TARGET/release/narwhal-source-tools" "$@"
