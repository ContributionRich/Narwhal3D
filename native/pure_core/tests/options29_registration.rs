//! OPTIONS-ENGINE-29 — public-branch registration guard.
//!
//! Pure-Rust (no Python host, no dynamic discovery) guard that pins the exact
//! registration the public branch must carry for the options engine, so a
//! silent removal looks unimplemented (RED) instead of silently green:
//!
//!  1. `src/lib.rs` declares both option modules (`options_owned`, `options_ffi`),
//!     so the engine and the C ABI are actually part of the compiled crate.
//!  2. `src/options_ffi.rs` exports the two option C ABI symbols by their exact
//!     names (`narwhal_pure_core_options_abi`,
//!     `narwhal_pure_core_options_resolve`) under `#[no_mangle]`.
//!
//! The expected names are EXACT constants — not discovered at runtime. The two
//! source files are read with `include_str!` relative to this file
//! (`pure_core/tests/`), so the guard inspects the SAME source the crate and
//! the public branch actually ship — not a copy that could drift. The semantic
//! behavior of the exports is covered by `options29_engine.rs` (engine) and
//! `options29_ffi.rs` (ABI contract); this guard only pins the registration.

const LIB_RS: &str = include_str!("../src/lib.rs");
const OPTIONS_FFI_RS: &str = include_str!("../src/options_ffi.rs");

/// The two modules `src/lib.rs` must declare for the options engine to be part
/// of the compiled crate. Exact `pub mod` lines, as they must appear.
const REQUIRED_LIB_MODULES: [&str; 2] = ["pub mod options_owned;", "pub mod options_ffi;"];

/// The two option C ABI symbols `src/options_ffi.rs` must export, by exact
/// name, under a `#[no_mangle]` attribute.
const REQUIRED_EXPORT_SYMBOLS: [&str; 3] = [
    "pub extern \"C\" fn narwhal_pure_core_options_abi() -> u32 {",
    "pub unsafe extern \"C\" fn narwhal_pure_core_options_resolve(",
    "pub unsafe extern \"C\" fn narwhal_pure_core_options_prompt(",
];

/// The `#[no_mangle]` marker that must directly precede each exported symbol.
const NO_MANGLE: &str = "#[no_mangle]";

#[test]
fn lib_declares_both_option_modules() {
    for line in REQUIRED_LIB_MODULES {
        assert!(
            LIB_RS.lines().any(|l| l.trim() == line),
            "src/lib.rs must declare `{line}` (options engine missing from the crate)"
        );
    }
}

#[test]
fn ffi_exports_both_option_abi_symbols() {
    for sym in REQUIRED_EXPORT_SYMBOLS {
        assert!(
            OPTIONS_FFI_RS.lines().any(|l| l.trim() == sym.trim()),
            "src/options_ffi.rs must export the exact symbol `{sym}`"
        );
    }
}

#[test]
fn each_export_is_no_mangle() {
    // Each exported symbol must have a `#[no_mangle]` line directly above it,
    // otherwise the C ABI name would be mangled and the host could not bind it.
    for sym in REQUIRED_EXPORT_SYMBOLS {
        let trimmed = sym.trim();
        let mut found = false;
        let mut previous_was_no_mangle = false;
        for line in OPTIONS_FFI_RS.lines() {
            if line.trim() == trimmed {
                assert!(
                    previous_was_no_mangle,
                    "export `{sym}` is missing its `#[no_mangle]` marker"
                );
                found = true;
                break;
            }
            previous_was_no_mangle = line.trim() == NO_MANGLE;
        }
        assert!(found, "export `{sym}` was not found in src/options_ffi.rs");
    }
}
