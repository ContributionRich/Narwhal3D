# Narwhal platform core

`narwhal-platform-core` is a dependency-free Rust 2021 `cdylib`/`staticlib` for
native desktop dialogs. It does not launch Explorer, a shell, or a helper
process.

On Windows it imports the Unicode Win32 APIs directly:

- `GetOpenFileNameW` and `GetSaveFileNameW` from `comdlg32` for real
  Explorer-style file pickers;
- `MessageBoxW` and `IsWindow` from `user32` for Yes/No/Cancel questions and
  optional owner validation.

The same exports are present on other platforms and return
`NARWHAL_PLATFORM_UNAVAILABLE` after validating the versioned request. No Rust
pointer or allocation crosses the public ABI: all input strings are bounded
caller-owned UTF-8 slices and all output bytes are caller-owned.

## ABI summary

Include `include/narwhal_platform_core.h`. Every request record has
`struct_size` and `abi_version`; initialize both to `sizeof(record)` and
`NARWHAL_PLATFORM_CORE_ABI_VERSION`. Reserved fields must be zero.

File filters are an array of description/glob pairs. A glob may contain multiple
patterns separated by semicolons, for example `*.3dm;*.3dmbak`. Rust converts
pairs to Win32's exact
`description\0glob\0description\0glob\0\0` UTF-16 representation. UTF-8 is
strict: malformed input and embedded NULs are errors rather than lossy
conversion.

A file-dialog result distinguishes `OK`, `CANCEL`, `UNAVAILABLE`, and `ERROR`.
On `OK`, `required_capacity` includes the UTF-8 trailing NUL. Callers may either:

1. provide a fixed output buffer (the maximum accepted capacity is 512 KiB); or
2. pass `NULL, 0`, receive a nonzero `retry_token`, allocate the reported size,
   and call `narwhal_platform_dialog_result_copy_v1`.

The second call only copies the already selected path; it never opens the dialog
again. A successful copy consumes the token. A size query or undersized copy
retains it. Call `narwhal_platform_dialog_result_discard_v1` if abandoning it.
The bounded store holds at most 32 pending results.

`owner_window` defaults to zero. A nonzero Windows value must be a live HWND
accepted by `IsWindow`. Paths are returned exactly as Unicode text from the
picker and are never shell-split, unquoted, opened, or otherwise interpreted.

## Build and test

Run from `native/platform_core`:

```text
cargo fmt --check
cargo clippy --all-targets -- -D warnings
cargo test
cargo build --release
```

The Linux release products are under `target/release/` (including
`libnarwhal_platform_core.so` and `libnarwhal_platform_core.a`). Linux tests also
prove that both dialog exports return `UNAVAILABLE` without displaying UI.

If the GNU Windows target is installed:

```text
rustup target list --installed
cargo build --release --target x86_64-pc-windows-gnu
objdump -p target/x86_64-pc-windows-gnu/release/narwhal_platform_core.dll
```

The PE import table must include `comdlg32.dll` and `user32.dll`, with
`GetOpenFileNameW`, `GetSaveFileNameW`, `CommDlgExtendedError`, `MessageBoxW`,
`IsWindow`, and no shell-launch API. The Windows code is exercised
noninteractively through pure tests for UTF conversion, filter layout, status
constants, bounds, and fixed/two-pass output behavior; automated tests never
open a modal window.
