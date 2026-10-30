# Windows portable payload prototype

**Not a release-qualified application.** This Rust crate implements manifest
admission, embedded ZIP integrity checks/extraction, a per-user cache, leases,
and a Windows launch path. The production EXE assembler/verifier and complete
outer-executable update transaction are still required. Never distribute a tiny
fixture executable as the application.

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
heavy-build lock. Current host tests: **20**, with **19 compiled mutations** observed
RED then restored GREEN. Native Windows tests also cover protected current-user
creation and refusal to repair an existing unprotected directory.

A native Windows tiny-fixture probe verified first extraction and subsequent reuse.
It exposed and corrected an elevated-token default-owner issue; Wine behavior was
not treated as Windows certification. These tests do not certify a packaged Blender
runtime, executable updates, signing or distribution.

`mutation_check.py` orchestrates isolated copies and must not modify live source.
Dependencies are pinned in Cargo.toml/Cargo.lock; this is no longer a dependency-free
manifest-only prototype.

## Still required before shipping

- Broader native Windows cold-start concurrency, lock-link/handle-race and permission
  validation, including real runtime writes under sealed-cache permissions.
- Actual Unicode/document/CWD/exit/lifetime and low-disk/interruption acceptance.
- A reviewed outer-EXE update transaction. The legacy welcome ZIP updater must not
  mutate a digest-addressed cache; a refusal guard is not an implemented updater.
- Production assembler/verifier and source/native-input closure, final PE resource
  checks, extracted-package acceptance and post-test hashes.
- Signing/Authenticode work if signed distribution is requested.

Keep the existing ZIP available until the portable EXE and its updater pass these
gates. An installer wizard is not an equivalent portable experience.
