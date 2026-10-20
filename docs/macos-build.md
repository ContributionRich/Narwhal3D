# Experimental macOS source build

Narwhal 3D does not currently publish a macOS application. This page provides a community build path for people who have a Mac and are willing to compile and test it themselves.

This is **not a supported release recipe**. No macOS build has yet passed Narwhal's real-window UI, exact-manufacturing-geometry, save/reload, signing, notarization, or distribution gates. Do not redistribute the resulting application as an official Narwhal package.

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
- whether the app reaches four viewports and UI report 23/23;
- whether `.narwhal` save/reopen and the exact representation audit pass;
- the first failing command and relevant terminal output.

Do not attach private CAD files unless they are intentionally safe to publish.

## Remaining release work

A real macOS release still needs native Apple Silicon and Intel testing, a supported architecture decision, application-bundle identity/icon review, platform-native file dialogs and file associations, full GUI/GPU acceptance, exact geometry and project round trips, deterministic dependency locks, packaging verification, code signing, notarization, and an update/distribution policy.
