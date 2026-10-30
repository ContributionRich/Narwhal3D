# Release procedure

Linux/Windows0.4.8 is published from `d818a4328a6286cb377f7fdd108684e0e40e1768`:
AppImage, portable EXE, compatibility ZIP and their checksums passed upload verification.
The Windows EXE passed native launch/cache/normal-close/update/backup acceptance.
Every future candidate still needs its own package gates.

The macOS runtime has built successfully; its complete app/DMG remains pending worker
containment integration and platform qualification. See [the measured Mac continuation
plan](MACOS_048_PLAN.md). The owner authorizes adding a qualified Mac asset to the same
release later. Do not present a runtime archive or primitive test as a finished app.

## 1. Select and freeze source

- Refresh public `main`, tags and releases. Choose the next unused `0.4.x` version;
  never overwrite a published tag or silently replace assets.
- Work in an external detached candidate checkout. Preserve shared working trees,
  compiler caches and existing verified artifacts. Never put scratch worktrees or
  links to downloaded runtime/dependency trees inside the editor's project.
- Review pending integrations before freezing. Record the exact commit, build epoch,
  dependencies/toolchains, eight runtime patch/icon inputs and every native component.
- Use `scripts/create_public_branch.py` and `scripts/audit_public_tree.py` as the
  public-source inventory/audit contract. Stage explicit paths only. Do not publish
  private development history, research, SDKs, credentials or unreviewed logs.

## 2. Reuse by evidence, not by filename

Compare full inputs before reusing an output: source, lockfiles, compiler/SDK,
architecture, native headers/libraries, patches and embedded artwork. New input
files matter even when old recorded files still match. Never rewrite an old
binary's provenance to make it appear current.

Preserve the old install when incrementally rebuilding. The verified portable
Linux runtime environment uses the pinned manylinux2.28 image and cached compiler
RPMs; the host's compiler or missing host Git LFS is not an equivalent environment.
Keep build/install/provenance together and record provenance only after rebuilding.

## 3. Platform entrypoints and obligations

### Linux AppImage

- Runtime tooling: `scripts/build_blender_linux.sh`.
- Assembly/verification: `scripts/assemble_linux_release.py` and
  `scripts/verify_linux_release.py`; inspect their current `--help` before invoking.
  Compression is explicitly bounded to4 workers/512MiB, not the host CPU count.
- Native helper builds: `scripts/build_linux_native.py` uses the pinned portable
  image, offline dependencies, independent preserved targets and source inventories.
  Do not omit the configured `/blender-libs` mount when resuming the runtime build.
- Include all current native components, especially the automation library/server
  and rebuilt welcome. Verify loader precedence; development artifacts must not
  shadow packaged files.
- Scan **every ELF** for architecture, dependencies and GLIBC requirements. The
  current enforced GLIBC ceiling is2.35; historical2.28 results are not certification
  for newly rebuilt artifacts.
- Verify the extracted AppImage as well as the staging directory.

### Windows portable EXE

**Future releases publish only the portable EXE and its checksum, not a Windows
ZIP.** The already-published0.4.8 ZIP remains unchanged. Internal engineering/staging
ZIPs may still be used by the build tools but are not release downloads. Users of
older ZIP installs must manually download the portable EXE once; their legacy ZIP
updater cannot migrate itself to an EXE-only release.

- Preserve MSVC runtime/OCCT ABI compatibility. The independent Rust bootstrap may
  use a GNU toolchain because it launches, rather than links, those applications.
- Existing runtime/ZIP tooling: `scripts/build_windows_preview.ps1`,
  `scripts/assemble_windows_release.py`, `scripts/verify_windows_release.py`.
  `-ResumePackaging` requires verified reusable inputs; `-RebuildRuntime` retains
  compiler objects. The builder's default clean path is not routine maintenance.
- Bootstrap source: `packaging/windows/portable/`. Build a Windows stub and a host
  packer from the same source. `scripts/assemble_windows_exe.py` admits an already
  verified flat Windows package, assembles the EXE and verifies its actual extracted
  payload with the existing Windows gates. A tiny fixture is never a release asset.
- The existing VM has4 virtual CPUs; use4 compiler workers when measured RAM permits.
  Keep logs local to the guest and readable while writing. Losing an RDP monitor
  does not mean compilation stopped; inspect before retrying. Never restart by
  cleaning the cache. Package-only retries use `-ResumePackaging` with valid runtime
  provenance, not another source reset/rebuild.
- Icon fixtures use an isolated pinned Pillow environment selected through
  `NARWHAL3D_TEST_PYTHON`, not the shipped interpreter. Compare full decoded PNG/ICO/
  ICNS frames across zlib implementations, while retaining stored-resource hash checks.
- Prove current-user ownership, protected DACLs, no-follow lock opening, cold-start
  concurrency, verified extraction/reuse, lifetime leases and safe cleanup on real
  Windows. Wine does not establish Windows ACL behavior.
- Prove updates replace the outer executable safely; never run the legacy ZIP
  updater against an immutable cached payload. Publish only the qualified EXE;
  a failing EXE gate is not permission to substitute a ZIP upload.
- The detached updater must inherit **no** handles. Redirecting only standard I/O
  is insufficient: scheduler pipes can otherwise remain open until the application
  exits. Test scheduler exit **and pipe EOF** while the original application remains
  running. Also test an already-exited launcher whose process handle is retained;
  Windows can reject image-name queries for that object with error31.
- For real-EXE acceptance, close only the owned CAD window normally and let Welcome
  supervise shutdown. Do not simultaneously close both windows. Wait for the owned
  updater/backup publication before hashing the replacement: repeated target-file
  opens can conflict with Windows replacement sharing. Normalize extended Windows
  paths when finding the owned helper, and retain exact old/new hashes and backup
  checks. Never substitute a forced process-tree kill for normal-close acceptance.
- Check Unicode paths/arguments, CWD, exit status, read-only runtime behavior,
  interruption, low disk and rollback. Signing requires real credentials and
  Authenticode-aware payload handling; checksums are not publisher signatures.

### macOS app and DMG

- Target ARM64/macOS14+ initially. Do not infer Intel/universal or macOS11 support
  from another application or from a Rust-only compilation proof.
- `.github/workflows/macos-runtime.yml` builds the fully patched ARM64 runtime;
  `macos-native.yml` builds/tests helpers and retains compiler progress on failure.
  Use the authoritative Blender repository for LFS, not GitHub's incomplete LFS
  mirror. Select Xcode16.2 explicitly on macos-14; its default15.4 is too old.
- Run small platform proofs BEFORE a full build: Metal-only presentation compilation,
  native helper tests and actual process-memory containment. Exit0 from the supervisor
  is insufficient: its JSON must also report returncode0, no timeout and no error.
  Native macOS currently refuses the AS limits used by CDR and document workers.
  Do not remove that gate or assume unit tests of argument parsing prove containment.
- `scripts/assemble_macos_release.py` and the Rust launcher in
  `packaging/macos/launcher/` are the new app/DMG pipeline. They still require complete
  native qualification. `build_macos_source.sh` remains an experimental developer
  helper, not the approved release recipe.
- A validated OSXCross installation can build individual components on Linux.
  Preserve SDK licensing restrictions and do not redistribute SDK contents.
- Inspect every Mach-O architecture, minimum OS, install ID, dependency and rpath.
  Eliminate build-machine paths. The OCP wheel's dylib IDs require review before
  linking additional libraries; an apparently successful link is insufficient.
- The `.app` must include the application, runtime, Python packages, native helpers,
  resources and licenses and work after relocation without the source checkout.
- Finish resource/loader edits before signing. Ad-hoc signatures are not Developer
  ID/notarization or proof of Gatekeeper acceptance.
- Produce and independently inspect a real DMG. Linux tooling can validate image
  contents/signature hashes; native macOS checks must cover launch, dialogs, Metal,
  geometry, save/reopen, Undo, relocation and downloaded-file behavior.

## 4. Efficient validation

Use targeted tests and mutation checks during implementation. Do not restart the
entire product suite after each fix. Close all known failures, freeze the candidate,
then run the complete `./run_tests.sh` gate. An interrupted run is not a pass and
partial runs cannot be added together to claim one. Source-development exact-Brep
suites need the explicitly configured local `NARWHAL3D_BREP_WRITER`; record its hash
and never copy it into packages. The package gates must run without that override
and verify exact masters/native document persistence and honest refusals. In
particular, bounded multi-section Sweep1 presently requires that unredistributed
writer and must not be advertised as active in the binary packages.

Do not wrap the entire suite in the heavy/GUI locks: several launchers acquire them
internally and would wait on their own parent. Coordinate resources, and use the
individual drivers' locks rather than creating that self-deadlock.

Package tests must exercise actual extracted bytes with disposable profiles and
models: native window icons/welcome, themes, input/selection, exact analytic geometry,
round trips/export, Undo, Gumball/fillets, AI permission persistence and approved box
creation/cancellation. Use deterministic local model fixtures instead of paid API
calls or private documents. Recheck inventories/hashes after testing.

Bound builds/tests, preserve desktop headroom and honor the shared heavy-build/GUI
locks. A launcher that takes its own lock must not be wrapped in the same lock.
On interruption, stop the exact owned scope/container and verify it stopped; a
closed monitoring terminal is not proof. Keep logs bounded and summarize failures.

## 5. Publish only qualified outputs

1. Prepare release-note bullets describing changes **since the previous release**.
   Include only implemented, qualified features; distinguish limitations/platforms.
2. Tag the exact packaged source. Create a GitHub draft prerelease.
3. Upload each verified application artifact and checksum individually. Never upload
   test stubs, SDK/toolchain archives, private evidence or user files.
4. Compare uploaded sizes and SHA-256 values with final local files.
5. Verify tag/source identity, names, install instructions and update discovery.
6. Publish the draft only after all required gates pass. Record the release URL,
   source/epoch, artifact paths/hashes and actual platform test results.

## 6. Add macOS to an existing release without rewriting history

When the owner authorizes a later platform addition, leave the published Linux/
Windows assets and original release tag unchanged. Qualify the complete Mac app/
DMG first. If Mac portability fixes require a later source commit, explicitly
record that platform's exact commit and publish its matching source archive or
source link and build manifest. Do not pretend it was built from the original
Linux/Windows tag. Restrict that Mac candidate to reviewed portability fixes;
do not accidentally include new overnight feature work from a moving main branch.

Upload the new Mac asset/checksum individually, verify remote sizes/digests, then
append a dated platform-addition note to the same GitHub release. It is the first
Mac release—there was no0.4.7 Mac package. Do not create another release, replace
existing assets or move tags merely to attach Mac later.

## 0.4.8 release-note topics and remaining Mac addition

The published release describes its qualified Linux/Windows changes. These topics
remain a checklist for platform-specific notes; they are not blanket support claims:

- Revised welcome with installed version and real update status.
- Refreshed tool/inspector artwork and clearer AI/Bug Report controls.
- Corrected native window icons, once rebuilt-window checks pass.
- Responses API, workspace context and automatic selected-object metadata.
- Remembered provider-scoped sharing permission, with explicit revocation.
- Separately approved analytic box/cube creation, cancellation and Undo.
- Source-only ordered bounded multi-section Sweep1 and rename-safe hybrid input;
  the binary packages refuse the multi-section route without the optional writer.
- Polygon preview/count-memory improvements and bounded CageEdit safety; do not
  claim full CageEdit deformation parity.
- Windows EXE/macOS DMG availability **only after their production gates pass**.
