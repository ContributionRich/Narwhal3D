# Pinned TraceDraw Rust crates

This directory contains the `tracedraw-core` and `tracedraw-cdr` source crates from
[TraceDraw](https://github.com/igtoth/tracedraw) revision
`821ba1545e3d3f75843fe7d3f912214800929821` (`0.9.0-dev.821ba15`).

Only the Rust document model and CDR codec crate are retained. The bundled helper uses
the reader; the writer remains part of that crate and generates versioned regression files.
TraceDraw's GUI, CLI, renderer, and unrelated import/export crates are not vendored. The manifests
are adapted from workspace-relative dependencies to standalone pinned path crates.
`Cargo.lock` pins the remaining crates.io build dependencies. A clean build machine needs
network access once to fetch those exact packages; subsequent `--locked --offline` builds
work from that cache. The packaged helper itself needs no network or TraceDraw runtime.

TraceDraw is dual-licensed under MIT or Apache-2.0. The unmodified upstream license
texts are `LICENSE-MIT` and `LICENSE-APACHE`. `CLEANROOM.md` is retained as upstream's
format-research provenance record.
