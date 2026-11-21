# Windows portable payload prototype

**Not a release-qualified application.** This Rust crate implements manifest
admission, embedded ZIP integrity checks/extraction, a per-user cache, leases,
and a Windows launch path. The assembler and outer-executable updater are source
candidates, not packaged acceptance. Never distribute a tiny fixture executable
as the application.

## Implemented boundaries

- Bounded Windows x86_64 / 0.4.x manifest, path, count, size and disk admission.
- Streaming archive/file SHA-256 checks, bounded ZIP central-directory parsing,
  exact regular-file inventory, and rejection of traversal, links, aliases and
  unexpected members. Packaged names are ASCII; user paths/arguments use OS strings.
- Private LocalAppData cache, explicit current-user ownership on new directories,
  protected user/SYSTEM DACL, and reparse refusal. Existing wrong-ownership or
  unprotected directories are refused, not repaired or taken over.
- Same-volume staging/publication, integrity checks on reuse, shared application
  lifetime leases and explicit verified-unused cleanup. Failed staging is retained.
- Cold-start losers recheck publication under a shared lock; they must not queue
  an exclusive lock behind an application that has already started.
- Lock opening rejects dangling links, uses no-follow/reparse-point flags, validates
  the opened regular-file handle, and shares the same path for extraction and cleanup.
- Native Windows icon/version resources and Unicode argument/CWD forwarding.
  The launch path still needs real application-package acceptance.

The embedded format currently assumes an unsigned PE. Checksums provide integrity,
not publisher authentication. Authenticode/certificate-table handling and signing
must be completed and verified before making signed-EXE claims.

## Focused validation

Use an external target directory, bounded memory, no added swap, and the shared
heavy-build lock. Current Linux-host tests: **47**. This recovery change observed
four additional compiled mutations RED (prepared-file hash, published backup hash,
restore backup hash, approval receipt equality), followed by restored GREEN.
The shortcut follow-up also observed two mutations RED (canonical-prefix conversion
bypassed; SetPath dispatched through Resolve's slot), then restored GREEN.
The unattended-restart follow-up observed three more mutations RED (healthy
relaunch suppression removed; interruption notification written as completion;
absolute-path validation inverted), followed by restored GREEN.
Windows-only tests are cross-checked including test code, not executed on Linux.
They cover native replacement, idempotent publication recovery, verified missing-target
restoration, approved-session reattachment and idempotent completion receipts, as
well as protected current-user creation and refusal to repair an existing directory.

A native Windows tiny-fixture probe verified first extraction and subsequent reuse.
It exposed and corrected an elevated-token default-owner issue; Wine behavior was
not treated as Windows certification. These tests do not certify a packaged Blender
runtime, executable updates, signing or distribution.

`mutation_check.py` orchestrates isolated copies and must not modify live source.
Dependencies are pinned in Cargo.toml/Cargo.lock; this is no longer a dependency-free
manifest-only prototype.

## Fresh-shortcut failure follow-up (native retest passed)

The WinBoat acceptance receipt reported `SetPath` failure and neither link created.
The managed cache is canonicalized, producing a verbatim `\\?\C:\…` target. ShellLink
now receives the equivalent DOS path (or `\\server\share` for verbatim UNC), with
UTF-16 preserved; filesystem validation still uses the original canonical path.
The `IShellLinkW::SetPath` call uses slot 20 with its exact two-argument ABI, and
failures include the HRESULT. Successful nested COM initialization (`S_FALSE`)
now receives its required matching `CoUninitialize`.

Host tests cover the canonical managed drive path, UNC/ordinary paths, surrogate
preservation/device refusal, COM slot dispatch and HRESULT diagnostics. The new
Windows-only `native_canonical_managed_target_roundtrips_through_shelllink` test
creates disposable Unicode/space-containing targets, saves/reloads links via COM,
checks target/working-directory and verifies nested apartment cleanup. It never
writes Desktop/Start Menu. An observed WinBoat retest passed this native COM
test and fresh Desktop/Start Menu publication with unattended cold/warm startup.
This observation is limited to that VM run, not general Windows certification.
That receipt's two other native test failures are corrected here: absolute paths
use platform-appropriate drive/UNC/root semantics, and pending-plan identity is
compared after filesystem canonicalization (8.3 names are aliases, not different
plans). Their native rerun is still required.

## Installation/update lifecycle candidate (OURS)

- A native preparation window precedes cold extraction. Default launch uses a
  stable per-user managed EXE and migrates Start Menu/desktop links once per target;
  `--portable` bypasses managed installation. Shortcut failures are visible and retry.
- A verified download is staged beside the outer EXE. No helper starts while the
  user is deciding Restart/Later; only successful supervised exit (or approved
  restart exit 75) schedules apply. The helper's 30-second wait bounds launcher
  teardown, not the user's decision time. Cache leases remain held through the session.
- Before helper creation, a synced, hash-bound `approved` receipt records that
  normal shutdown authorized apply. Startup inspects bounded, private sibling
  transactions under the same per-target update lock. An interrupted **approved**
  update with exact old/new hashes is rebound to the new session and retries only
  after that session closes normally. A merely staged/crashed session is reported,
  not silently applied. Only one transaction is adopted per session.
- Recovery checks actual target/replacement/backup hashes, not a phase journal.
  An already-published update is recognized without replacing it twice. The helper
  can restore an exact previous EXE if replacement left the target missing; unknown
  files, conflicting backups, links and invalid receipts are refused, not overwritten.
- Helper completion and failure use native dialogs (not invisible stdout only).
  Restart waits up to 180 seconds for the new runtime's version receipt. Failed
  spawn/exit/timeout leaves the backup and reports failure; it does **not** kill a
  possibly running runtime or automatically roll back beneath it. Startup recovery
  reports installation separately from runtime readiness. Before spawning the new
  launcher the helper syncs `relaunch.json` with its PID and creation time. Startup
  recognizes a controlled relaunch only when the expected readiness path and live
  helper image/creation identity match. That path emits no recovery modal and writes
  no completion acknowledgment; the helper owns acknowledgment after readiness.
  If the helper disappeared, startup reports an actual interrupted/unconfirmed
  relaunch once using `recovery-notified`, never `acknowledged`. Notification receipts
  record delivery, not successful runtime startup. Owner acceptance already verified
  real Restart replacement/backup/readiness/completion, but this non-modal follow-up
  and its two corrected native tests still require native retest.
- Transaction directories/backups are retained, not garbage-collected. Superseded
  transactions are ignored only for a later admitted release. More than 128 sibling
  transactions requires manual review. An unapproved interrupted download may be
  reported again until manually reviewed or superseded.

**Missing-target limitation:** if the stable shortcut target itself is absent, it
cannot execute startup recovery. Run the retained `updater.exe` with
`--portable-finish-update <absolute-path-to-plan.json>` to restore a verified backup,
or recover manually from `previous.exe`. This is not an automatic shortcut repair
claim. Never delete transaction files until the installed runtime is qualified.

## Still required before shipping

- Broader native Windows cold-start concurrency, lock-link/handle-race and permission
  validation, including real runtime writes under sealed-cache permissions.
- Actual Unicode/document/CWD/exit/lifetime and low-disk/interruption acceptance.
- Native Windows acceptance of managed shortcuts (fresh/migrated), cold/warm launch,
  Restart/Later, cancel/failing Save, crash before approval, helper creation failure,
  interruption before/after replacement, recovery across sessions and failed readiness.
  Exercise real Windows replacement/locking errors and the manual missing-target path.
  Cross-compilation and filesystem classifier tests are not packaged acceptance.
  The legacy welcome ZIP updater must never mutate a digest-addressed cache.
- Production assembler/verifier and source/native-input closure, final PE resource
  checks, extracted-package acceptance and post-test hashes.
- Signing/Authenticode work if signed distribution is requested.

Keep the existing ZIP available until the portable EXE and its updater pass these
gates. An installer wizard is not an equivalent portable experience.
