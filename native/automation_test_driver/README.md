# Rust automation mutation driver

Linux-only test orchestration, licensed GPL-3.0-or-later under the repository
LICENSE. Dependencies are pinned to existing serde_json 1.0.154 and sha2 0.10.9.
This tool replaces six retired Python orchestration drivers, not SDK clients or
production logic. It is not shipped in application packages and has no Windows
or macOS execution qualification.

## Invocation and required source gate

Requires Rust 1.95, systemd user scopes, GNU timeout, cached Cargo dependencies,
and an explicit bundled interpreter for retained SDK clients. Build outside
the repository, then invoke the resulting debug/automation-test-driver:

    cargo +1.95.0 build --locked --offline --manifest-path native/automation_test_driver/Cargo.toml --target-dir /absolute/outside/driver-build -j 1
    /absolute/outside/driver-build/debug/automation-test-driver --suite box --target-dir /absolute/outside/runs --python /absolute/bundled/python3.13
    /absolute/outside/driver-build/debug/automation-test-driver --suite plan --mutations --target-dir /absolute/outside/runs --python /absolute/bundled/python3.13

The shared run_tests.sh requires format, strict all-target Clippy, Rust/CLI/source
registration tests and all six suites with all 71 actual mutations. The driver
owns /tmp/narwhal3d-heavy-build.lock; never wrap it in a second holder of that
lock. It does not start a provider or modeling window.

| Retired orchestration | Rust suite | Entries | Default Rust filter |
|---|---|---:|---|
| test_ai_box_native.py | box | 4 | box_action::tests |
| test_ai_plan_native.py | plan --mutations | 18 | library + full original Rust tests |
| test_ai_v4_native.py | v4 --mutations | 42 | v4_ |
| test_ai_v6_review.py | v6 --mutations | 4 | v6_ |
| test_ai_v7_native.py | v7 | 1 | v7_review_clock |
| test_ai_v8_native.py | v8 | 2 | v8_display_projection |

Box/V7/V8 always mutate. Plan/V4/V6 require --mutations. --mutation-filter selects
a label substring. V4 --offset/--limit apply after filtering; empty selections
fail. --test is available for versioned Rust suites. V6/V7/V8 force 1024 MiB and
retain their historical external default target; prefer an explicit owned target.

The actual SDK clients test_ai_box.py, test_ai_plan.py, test_ai_primitives.py,
test_ai_accepted_review.py and test_ai_accepted_review_native.py remain public
and unchanged. Python remains necessary for these actual SDK/host ABI checks;
mutation planning and policy are not duplicated in Python.

## Immutable catalogs and historical anchors

All original labels, files, old/new replacements, ordering and test filters were
independently compared against the six originals before removal. Catalogs are
compiled into the executable, with immutable SHA-256 guards:

| Input | SHA-256 |
|---|---|
| Original 64-entry catalog | 17e701223652104d639fa219305d415eac6bebd4e3aa8286b6efa67734b44845 |
| Additional 7-entry catalog | bc47b79ce8148c6e183ac0e50c56915b546bbaab69a099955561e46beac7dd12 |
| Additional provenance | 4d9ce750aa65f1ac13a5b69f2c006b6b45abdbe751bad73e0adbecdc06ec7343 |
| Anchor overrides | 7ce8c84275afd01897cc7f2acdebc78798e56ff93aee63299f1408601b1e22b1 |
| Applied 64-entry catalog | 7f2d5151d8e3265bf7fc3e178cbcca45ffac44f0da79e56c8bb263fbe6fc975e |
| Combined execution catalog | 6629b3957fdd7e46af51a88cfdb304d87304f10592eb5425f0633d89a0e145aa |

Exactly two explicit V4 historical repairs preserve mutation meaning and test
filters: production_catalog selects tools before frame_schema, not the obsolete
adjacency to schema; canonical_result selects the original review-result guard,
not the newer modeling-result guard. Original anchors measured zero and two
matches. Override code checks historical counts and exact repaired scope; it
never silently retargets future source drift.

## Bounds and RED semantics

Each child has a validated systemd scope, no swap, 1024/1536 MiB compiler cap,
175-second scope lifetime, control-group termination and two-second stop timeout.
GNU timeout independently limits native operations to 170 seconds and SDK clients
to 30 seconds. Available memory must exceed the cap plus 4 GiB desktop headroom.
Missing resource/lock admission fails closed. Output tails are at most 512 KiB
per stream.

Targets are canonicalized outside the compile-time repository root; rebuild
when that checkout moves. Every mutation has a unique source snapshot and Cargo
target. Snapshots refuse symlinks, build hooks and path dependencies and copy only
source/manifests. Live source is never mutated. Pre/post baselines require
nonempty Rust tests, a library build and actual SDK assertions. Plan retains the
full original crate integration-test baseline.

Rust RED requires exit 101, a failed test and assertion panic. SDK RED requires
exit 1, positive unittest count, named failure and AssertionError. Compile errors,
startup errors, timeouts and zero-test selections are infrastructure failures,
never kills. Bounded logs, mutation JSONL and summaries distinguish KILLED,
SURVIVED and INFRA_FAILURE.

## V6 retained fixture controls

V6 validates the retained SDK publisher discovery receipt, exact test identity
and source/library/executable hashes. An absent receipt invokes the existing
explicit SDK fixture builder after releasing the heavy lock. An invalid existing
receipt fails closed without rebuilding or selecting a different backend.

Without --legacy-library, optional old-library behavior is explicitly not
qualified. An explicitly supplied library must not skip; CLI, environment and
CLI-precedence selection remain supported. The cached old library measured
for this conversion is rejected by the current SDK with
native_modeling_update_required. It is not reported as a passing legacy fixture;
the retained SDK client enforces the same behavior.
