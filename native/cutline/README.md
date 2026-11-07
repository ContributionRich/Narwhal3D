# Picture tracer (`coco-cutline`)

The Rust helper behind the `TracePicture` command. Given an image and its size, it returns
closed cubic Bezier cut lines around the artwork, offset outward (or inward), as JSON.
`narwhal3d/trace_picture.py` runs it and maps each loop onto the Picture's frame as one closed PolyCurve of exact cubic NURBS
segments, preserving its pieces for Explode.

- `crates/coco-cut`, `crates/coco-cutline`: contributed by their author under this project's
  license. `coco-cutline` adds `--sample-pixel X Y` for the command's PickColor option.
- `crates/vectorcraft-*`: VectorCraft 0.10.0, MIT OR Apache-2.0 (see `LICENSES/` and
  `VECTORCRAFT-REVISION`).

Build: `cargo +1.95.0 build --release --locked --manifest-path native/cutline/Cargo.toml`.
`scripts/setup_dev_native.sh` builds it and stages it in `artifacts/cutline/<platform>/`.
Test: `cargo +1.95.0 test --release --locked --manifest-path native/cutline/Cargo.toml` and
`spike/test_trace_picture.py`.

Not yet in the release packages (AppImage, Windows): until it is, `TracePicture` refuses by
name when the helper is missing.
