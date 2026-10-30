# macOS download and historical source-build helper

A published **Apple Silicon DMG** is available in [v0.4.8](https://github.com/ContributionRich/Narwhal3D/releases/tag/v0.4.8): `Narwhal_3D-0.4.8-macOS-arm64.dmg`. Open it and copy **Narwhal 3D.app** to Applications. The self-contained package targets macOS14+; native qualification ran on macOS15 with Apple Paravirtual/Metal graphics. It is not an Intel/universal package.

The Mac preview is **ad-hoc-signed and unnotarized**, not Developer ID-signed. Gatekeeper assessment rejects this unsigned-publisher preview without explicit user approval. Follow Apple’s Privacy & Security instructions for opening a trusted download; do not disable Gatekeeper globally. Interactive downloaded-file approval and broad physical-Mac coverage are not certified. Corel multiversion conversion is unavailable.

The official build/release route is documented in [packaging/RELEASING.md](../packaging/RELEASING.md). The legacy developer helper below is **not a supported release recipe** and does not replace the current worker-containment, dependency, signing, DMG and artifact-acceptance gates. Do not redistribute its output as an official package.

## Feasibility measured on 2026-10-19

- Blender's official build documentation supports macOS 11.2 and newer and produces `build_darwin/bin/Blender.app`.
- `rhino3dm 8.35.0` publishes CPython 3.13 `universal2` wheels for macOS 14 and 15.
- `cadquery-ocp-novtk 8.0.1.0.0` publishes separate CPython 3.13 wheels for macOS 11+ on both `arm64` and `x86_64`.
- Narwhal's Rust cores are ordinary host builds and use Unix process limits on macOS.

Because the exact `rhino3dm` wheel starts at macOS 14, this recipe requires **macOS 14 or newer**. It builds only the Mac's current architecture. It does not create a universal application.

## What the helper does

`scripts/build_macos_source.sh`:

1. refuses non-macOS hosts, macOS older than 14, unsupported architectures, dirty Blender source, and the wrong Blender revision;
2. optionally runs Blender's official networked `make update` dependency step;
3. applies the same Narwhal branding, app-drawn-object, CAD-boundary-wire, and deferred-first-presentation patches used by the Linux build;
4. runs Blender's native macOS `make` build and copies `Blender.app` to `tools/blender-patched/Blender.app`;
5. installs exact CPython 3.13 versions of `rhino3dm`, `cadquery-ocp-proxy`, and `cadquery-ocp-novtk` into that local app;
6. builds the pure, selection, CDR, project-document, converter-supervisor, and multi-version CDR Rust components for the host architecture under `runtime/macos-<arch>/bin`;
7. checks the Narwhal version banner and imports both geometry dependencies.

It does not sign, notarize, create a DMG, install file associations, build a universal binary, or claim the GUI works correctly.

## One-click build and collection

After installing the prerequisites below, Finder can run the complete engineering build by double-clicking:

```text
Build-Narwhal3D-macOS.command
```

macOS may ask which application should open it; choose Terminal. The command uses the dedicated `~/Library/Caches/Narwhal3D-macOS-build` directory for the pinned Blender checkout, verifies that cache's upstream URL before resetting it, invokes `scripts/build_macos_source.sh`, and then runs the exact representation audit and real-window UI report. A failed run is resumable: correct the reported prerequisite or build problem and double-click the command again.

The Desktop receives:

- a **Build ZIP** containing the native-architecture `Narwhal 3D.app`, the six macOS Rust runtime components, and build identity;
- a much smaller **Report ZIP** containing sanitized logs, tool/platform facts, native-component hashes, the UI report and screenshot when available, and the Build ZIP checksum.

The collector uploads nothing. Review `README-BEFORE-SHARING.txt`, `build-report.txt`, and `build.log` before returning the ZIP files. It intentionally does not collect user or computer names, serial numbers, network addresses, environment dumps, personal files, or CAD documents. The Build ZIP can be large because it contains the complete application bundle.

These are engineering return artifacts, not a signed or notarized public release. The returned host-native binaries and acceptance evidence let the maintainer develop and verify macOS release assembly without pretending Linux can produce Apple binaries.

## Requirements

Install Apple's command-line developer tools:

```bash
xcode-select --install
```

Install the tools recommended by Blender plus Rust:

```bash
brew install cmake git git-lfs rustup-init
rustup-init
```

Open a new Terminal after installing Rust. Allow substantial free disk space and build time. Keep the Blender checkout outside Desktop, Documents, or any iCloud-synchronized folder; Blender documents that iCloud file attributes can interfere with builds.

## Build

Clone Narwhal's public source and Blender, then pin Blender to Narwhal's exact runtime revision:

```bash
git clone https://github.com/ContributionRich/Narwhal3D.git
mkdir -p ~/blender-git
git clone https://projects.blender.org/blender/blender.git ~/blender-git/blender
git -C ~/blender-git/blender checkout d13f752e3b9c4f8c261cda552b1021f8bcc0382c
```

From the Narwhal checkout:

```bash
./scripts/build_macos_source.sh \
  --source-dir ~/blender-git/blender
```

The helper runs Blender's `make update`, which downloads Blender's matching libraries and data. To reuse an already completed update without network access:

```bash
./scripts/build_macos_source.sh \
  --source-dir ~/blender-git/blender \
  --skip-update
```

Launch the local build:

```bash
./narwhal3d.sh
```

The launcher uses `tools/blender-patched/Blender.app/Contents/MacOS/Blender` and the host-architecture native components under `runtime/macos-<arch>/bin`.

## Gatekeeper and privacy

A local source build is unsigned and unnotarized. Do not disable Gatekeeper globally. A build produced locally should not carry a downloaded quarantine attribute; if macOS reports one, inspect the checkout and application before removing quarantine from this specific locally built app.

The build runs Blender's upstream `make update` and `pip`, so it downloads source dependencies and exact-version Python wheels from their normal upstream services. Narwhal does not upload models or build logs.

## What testers should report

Open a GitHub issue marked as a **macOS source build** and include:

- Mac model category, Apple Silicon or Intel, macOS version, and GPU family;
- Xcode/command-line-tools, CMake, Git LFS, Rust, and Blender build results;
- whether the app reaches four viewports and the complete UI report passes;
- whether `.narwhal` save/reopen and the exact representation audit pass;
- the first failing command and relevant terminal output.

Do not attach private CAD files unless they are intentionally safe to publish.

## Remaining release work

A real macOS release still needs native Apple Silicon and Intel testing, a supported architecture decision, application-bundle identity/icon review, platform-native file dialogs and file associations, full GUI/GPU acceptance, exact geometry and project round trips, deterministic dependency locks, packaging verification, code signing, notarization, and an update/distribution policy.
