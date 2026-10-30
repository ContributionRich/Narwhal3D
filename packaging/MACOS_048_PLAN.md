# macOS addition to the published 0.4.8 release

**Completed2026-10-30:** the Apple Silicon DMG and checksum are published in v0.4.8.
Application source is `93dcc8c985926cd6305a5ef75919209a9318e388`, deliberately excluding
next-release Rust ports. Native package run41126680014 and final artifact/launcher
run41128183330 passed. The signed bundle remains unchanged after document, rendering
and Gumball checks; checked-hash Python caches and Metal-aware readiness were required.
The package is ad-hoc-signed, not notarized, and needs explicit Gatekeeper approval.
Corel multiversion conversion remains unavailable. The sections below preserve the
investigation and qualification history, not current blockers.

Linux and Windows0.4.8 are published from `d818a4328a6286cb377f7fdd108684e0e40e1768`.
Keep that tag and its existing assets unchanged. macOS will use an explicitly
identified later portability revision and can be attached to the same release
only after its own package gates pass. There was no0.4.7 Mac release.

## What is already built

- The patched ARM64 runtime completed in Actions run41012973989 with Xcode16.2
  and the eight required patch/icon inputs. Reuse it after inventory verification;
  do not rebuild Blender merely to change a helper.
- Native-helper run41021358892 built the other components, but CDR's standalone
  memory-limit test failed. The complete app/DMG remains unqualified.

## Measured containment problem

The actual converter supervisor fails before launching a limited child on macOS:
`RLIMIT_AS` requests of64,768 and4096MiB produce EINVAL. The supervisor's outer exit
status can still be zero; its JSON reports the failure. Native-document validation
uses the4096MiB route, so this is broader than the optional Corel integration.

The small Rust experiment in `spike/macos_containment/probe.rs` establishes:

1. Apple's `posix_spawnattr_setjetsam_ext` can enforce fatal physical-memory limits
   for an ordinary, non-root user. A128MiB touched allocation succeeds with a768MiB
   limit and is killed with a64MiB limit.
2. A forked child does not inherit that limit (run41079839397). Raw jetsam is not
   a safe generic replacement for the existing supervisor policy.
3. Seatbelt can deny fork, but exec resets the limit (run41080174445). Denying fork
   alone is insufficient.
4. A cooperating trusted worker can deny **both fork and exec** before processing
   data. The sealed-worker experiment includes below-limit success, above-limit
   termination, process-creation refusals and a bounded no-limit control. Its
   mutation runner removes each of the three protections and requires the intended
   assertion to fail. An unexpected successful exec exits42, not the same zero used
   to acknowledge a denied exec.

Run `.github/workflows/macos-containment-proof.yml` for this small native check.
The original unsealed and no-fork-only experiments remain in the source to expose
the rejected designs. They are diagnostics, not production implementations.

**A passing primitive experiment is not an integrated supervisor or a Mac release.**
Private Apple APIs and the deprecated Seatbelt interface also need explicit runtime
availability checks and supported-OS qualification; absence must fail closed.

## Next implementation gate

Choose and review a complete launch/worker contract before changing production:

- Install the physical cap on the final worker image, then establish the no-fork/
  no-exec policy at a trusted entrypoint **before untrusted model bytes are read**.
- Account for the real Blender/Python worker startup, not only a toy executable.
  A wrapper that execs the worker after receiving its own cap loses that cap.
- Do not claim arbitrary converter containment on the strength of cooperation from
  one test worker. Refuse unsupported targets unless their full launch contract
  provides equivalent guarantees. No silently unbounded fallback.
- Keep process/containment logic in Rust where feasible. Python should only bridge
  the Blender/worker entrypoint. Preserve all analytic geometry/master checks.
- Exercise actual document validation, failure envelopes, memory exhaustion, fork/
  exec attempts, timeout and process cleanup. Mutation-test the installed guards.
- CDR also installs its own memory cap. Either qualify that standalone path or defer
  Corel explicitly under the approved platform exception; do not relabel a failing helper
  as supported.

## Then package and qualify

1. Rebuild changed native helpers only, preserving compiler caches and exact source
   inventories. Keep post-release overnight feature work out of this Mac candidate.
2. Use `scripts/assemble_macos_release.py` and the pinned ARM64 wheel lock. Validate
   every Mach-O architecture, minimum OS, dependency and bundle-relative load path.
3. Verify the self-contained, relocatable app and actual read-only DMG, not just a
   staging directory. Retain resources, licenses, inventories and final hashes.
4. Perform native Apple Silicon launch, Metal, dialogs, exact geometry, save/reopen,
   Undo and downloaded-file/Gatekeeper checks. Ad-hoc signing is not notarization.
5. Attach the qualified DMG/checksum to v0.4.8, verify remote sizes/digests, and add a
   dated note identifying the Mac source revision. Never replace Linux/Windows
   assets or move the original release tag.

See [RELEASING.md](RELEASING.md) for the reusable release procedure.
