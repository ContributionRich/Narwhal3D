# Picture tracer (`narwhal-picture-trace`)

The Rust helper behind the `TracePicture` command. Given an image and its size, it returns
closed cubic Bezier cut lines around the artwork, offset outward (or inward), as JSON.
`narwhal3d/trace_picture.py` runs it and maps each loop onto the Picture's frame as one closed PolyCurve of exact cubic NURBS
segments, preserving its pieces for Explode.

- `crates/narwhal-trace-core`, `crates/narwhal-picture-trace`: contributed by their author under this project's
  license. `narwhal-picture-trace` adds `--sample-pixel X Y` for the command's PickColor option.
- `crates/vectorcraft-*`: VectorCraft 0.10.0, MIT OR Apache-2.0 (see `LICENSES/` and
  `VECTORCRAFT-REVISION`).

Build: `cargo +1.95.0 build --release --locked --manifest-path native/picture_trace/Cargo.toml`.
`scripts/setup_dev_native.sh` builds it and stages it in `artifacts/picture-trace/<platform>/`.
Test: `cargo +1.95.0 test --release --locked --manifest-path native/picture_trace/Cargo.toml` and
`spike/test_trace_picture.py`.

The Linux AppImage and the Windows EXE ship it as `runtime/<platform>/bin/narwhal-picture-trace`
(`.exe` on Windows) from 0.4.12. The release assemblers run it on a real image and refuse a
package whose tracer does not trace; its notices are `licenses/PictureTrace-Rust-Dependencies.txt`
and `licenses/VectorCraft-*.txt`. Without the helper, `TracePicture` refuses by name.
