#!/usr/bin/env bash
# Build the reviewed Rust CLI from source; never select a stale executable.
set -euo pipefail
ROOT=$(dirname "$(dirname "$(readlink -f "$0")")")
CRATE="$ROOT/native/source_tools"
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/narwhal3d/source-tools"
mkdir -p "$CACHE"
exec 9>"$CACHE/build.lock"
flock 9
PIN=$(find "$CRATE" -type f -not -path '*/target/*' -not -path '*/.git/*' -print0 | sort -z | xargs -0 sha256sum | sha256sum)
if [[ ! -x "$CACHE/target/release/narwhal-source-tools" || ! -f "$CACHE/source.sha256" || "$(cat "$CACHE/source.sha256")" != "$PIN" ]]; then
  BUILD=(cargo +1.95.0 build --manifest-path "$CRATE/Cargo.toml" --target-dir "$CACHE/target" --release --locked --offline -j 2)
  if systemd-run --user --scope -p MemoryMax=3G -p MemorySwapMax=0 -p CPUQuota=200% true >/dev/null 2>&1; then
    systemd-run --user --scope -p MemoryMax=3G -p MemorySwapMax=0 -p CPUQuota=200% "${BUILD[@]}" >&2
  else
    (ulimit -v 3145728; "${BUILD[@]}") >&2
  fi
  printf '%s' "$PIN" > "$CACHE/source.sha256.tmp"
  mv "$CACHE/source.sha256.tmp" "$CACHE/source.sha256"
fi
flock -u 9
exec "$CACHE/target/release/narwhal-source-tools" "$@"
