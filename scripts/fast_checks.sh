#!/usr/bin/env bash
# Fast checks for contributors and AI agents: the suites that run after
# scripts/bootstrap_dev_linux.sh alone, without the patched runtime or the
# document observer that scripts/setup_dev_native.sh stages. ./run_tests.sh
# remains the full gate.
#
# Builds the pure-core library these suites need, exports its path, runs each
# step, prints its name, and exits non-zero if any step failed.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# rustup's cargo, even when the caller's shell has not sourced ~/.cargo/env.
export PATH="${CARGO_HOME:-$HOME/.cargo}/bin:$PATH"
export PYTHONDONTWRITEBYTECODE=1

# The official runtime from scripts/bootstrap_dev_linux.sh, never the patched one.
RUNTIME_ROOT="$ROOT/tools/blender-5.2.2-linux-x64"
PYTHON="$RUNTIME_ROOT/5.2/python/bin/python3.13"
[[ -x "$PYTHON" ]] || {
    echo "error: $PYTHON not found; run scripts/bootstrap_dev_linux.sh first" >&2
    exit 2
}

TOOLCHAIN="$(sed -n 's/^channel = "\(.*\)"$/\1/p' native/pure_core/rust-toolchain.toml)"
command -v cargo >/dev/null 2>&1 || {
    echo "error: cargo not found; install rustup, then:" \
         "rustup toolchain install $TOOLCHAIN --component rustfmt,clippy" >&2
    exit 2
}
cargo "+$TOOLCHAIN" --version >/dev/null 2>&1 || {
    echo "error: Rust $TOOLCHAIN is not installed:" \
         "rustup toolchain install $TOOLCHAIN --component rustfmt,clippy" >&2
    exit 2
}

find narwhal3d spike scripts -type d -name __pycache__ -prune -exec rm -rf {} +

failed=()
step() {
    local label=$1
    shift
    printf '\n==> %s\n' "$label"
    if "$@"; then
        printf 'PASS: %s\n' "$label"
    else
        printf 'FAIL: %s (exit %d)\n' "$label" "$?" >&2
        failed+=("$label")
    fi
}

step "pure-core release build" cargo "+$TOOLCHAIN" build --release --locked \
    --manifest-path native/pure_core/Cargo.toml --target x86_64-unknown-linux-gnu
export NARWHAL_PURE_CORE="$ROOT/native/pure_core/target/x86_64-unknown-linux-gnu/release/libnarwhal_pure_core.so"

step "bootstrap self-check" scripts/bootstrap_dev_linux.sh --self-check
step "public-tree audit self-check" "$PYTHON" scripts/audit_public_tree.py --self-check
step "verify release self-check" "$PYTHON" scripts/verify_linux_release.py --self-check
step "assemble release self-check" "$PYTHON" scripts/assemble_linux_release.py --self-check
step "Rust pure-core source gate" "$PYTHON" scripts/check_pure_core.py

# Each of these runs without the document observer or the patched runtime;
# spike/test_fast_checks.py keeps it that way.
FAST_SUITES=(
    spike/test_public_project.py
    spike/test_macro.py
    spike/test_coords.py
    spike/test_numparse.py
    spike/test_toolbars.py
    spike/test_native_only_policy.py
    spike/test_kernel.py
    spike/test_native_geometry.py
    spike/test_fast_checks.py
)
for suite in "${FAST_SUITES[@]}"; do
    step "$suite" "$PYTHON" "$suite"
done

printf '\n'
if ((${#failed[@]})); then
    printf 'FAST CHECKS FAILED (%d):\n' "${#failed[@]}" >&2
    printf '  - %s\n' "${failed[@]}" >&2
    exit 1
fi
echo "FAST CHECKS PASSED"
