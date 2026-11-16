//! Public-branch registration guard for the SELECTION-REGISTER-26 round.
//!
//! This test is pure Rust (no Python host, no dynamic discovery). It pins the
//! exact registration the public branch must carry for this round:
//!
//!  1. The three selection26 integration targets (static, random, and this
//!     registration guard) are each wired into the mandatory, unconditional
//!     `run_tests.sh` selection gate — the one that links the Rust core and
//!     must not be gated on a built artifact being present.
//!  2. Every test helper and fixture the round owns is listed, explicitly, in
//!     the public branch manifest (`scripts/create_public_branch.py`), so the
//!     public root cannot silently drop any of the 4 `.rs` files, the 10 binary
//!     corpora, or the provenance capture.
//!
//! The expected lists are EXACT constants — not discovered at runtime — so a
//! silent shrink of the manifest or gate would fail this guard instead of
//! passing. A "source gate / run label" that is absent must therefore look
//! unimplemented (RED), never silently green.
//!
//! The two root files are embedded with `include_str!` relative to this file
//! (`selection_core/tests/`), so the guard reads the SAME files the public
//! branch and the test harness actually ship — not a copy that could drift.

/// The three selection26 integration targets the mandatory `run_tests.sh`
/// selection gate must name, in the order the project round registers them.
///
/// These are the `--test` targets the harness passes to `cargo test` in the
/// selection gate. They are NOT discovered; each one is pinned by name so an
/// omission is a failure.
const MANDATORY_SELECTION_TEST_TARGETS: [&str; 3] = [
    "selection26_static",
    "selection26_random",
    "selection26_registration",
];

/// The exact `--test` invocations the mandatory selection gate must contain.
/// Each entry is the `--test <name>` flag form that appears verbatim in the
/// `run_tests.sh` line, so a gate that renames or drops a target fails.
const MANDATORY_SELECTION_TEST_FLAGS: [&str; 3] = [
    "--test selection26_static",
    "--test selection26_random",
    "--test selection26_registration",
];

/// The one mandatory, artifact-independent selection gate the round adds to
/// `run_tests.sh`. It is the `cargo test` line that links the Rust core and
/// runs all three targets. It is NOT conditional on a built `.so` artifact.
const MANDATORY_SELECTION_RUN_LABEL: &str =
    "Rust selection-core tests (static, random, registration)";

/// The exact public-branch manifest entries this round owns, as explicit
/// `native/selection_core/tests/...` paths. Order mirrors the manifest:
/// the four Rust files (2 suites + this guard + the shared fixture), then the
/// ten binary corpora and the provenance capture.
const MANDATORY_PUBLIC_MANIFEST_PATHS: [&str; 15] = [
    "native/selection_core/tests/selection26_static.rs",
    "native/selection_core/tests/selection26_random.rs",
    "native/selection_core/tests/selection26_registration.rs",
    "native/selection_core/tests/support/selection26_fixture.rs",
    "native/selection_core/tests/data/selection26_aabb250.bin",
    "native/selection_core/tests/data/selection26_clip250.bin",
    "native/selection_core/tests/data/selection26_curves100.bin",
    "native/selection_core/tests/data/selection26_near250.bin",
    "native/selection_core/tests/data/selection26_point200.bin",
    "native/selection_core/tests/data/selection26_poly250.bin",
    "native/selection_core/tests/data/selection26_screen500.bin",
    "native/selection_core/tests/data/selection26_snap200.bin",
    "native/selection_core/tests/data/selection26_world250.bin",
    "native/selection_core/tests/data/selection26_worldboxes250.bin",
    "native/selection_core/tests/data/selection26_provenance.json",
];

const RUN_TESTS_SH: &str = include_str!("../../../run_tests.sh");
const CREATE_PUBLIC_BRANCH_PY: &str = include_str!("../../../scripts/create_public_branch.py");

/// A pure guard over the two embedded root files. Returns true only when every
/// required target, flag, run label, and manifest path is present. No I/O, no
/// Python, no discovery: a missing piece makes this return false.
fn selection26_registered(text_run: &str, text_manifest: &str) -> bool {
    // (a) The mandatory run label must be present in run_tests.sh.
    if !text_run.contains(MANDATORY_SELECTION_RUN_LABEL) {
        return false;
    }
    // (b) Every `--test <name>` flag of the three targets must appear.
    for flag in MANDATORY_SELECTION_TEST_FLAGS {
        if !text_run.contains(flag) {
            return false;
        }
    }
    // (c) Every target name must appear (belt-and-suspenders vs flag spelling).
    for target in MANDATORY_SELECTION_TEST_TARGETS {
        if !text_run.contains(target) {
            return false;
        }
    }
    // (d) Every owned manifest path must be listed explicitly in the public
    //     branch manifest. A single omission is a registration failure.
    for path in MANDATORY_PUBLIC_MANIFEST_PATHS {
        if !text_manifest.contains(path) {
            return false;
        }
    }
    true
}

#[test]
fn selection26_mandatory_gate_and_manifest_are_registered() {
    assert!(
        selection26_registered(RUN_TESTS_SH, CREATE_PUBLIC_BRANCH_PY),
        "the selection26 round is not fully registered in the public branch:\n\
         run_tests.sh must carry the mandatory selection gate (label + all three \
         `--test` targets) and scripts/create_public_branch.py must list every \
         owned test/fixture path. See MANDATORY_*_targets/flags/paths above."
    );
}

/// Positive control: the guard MUST accept a manifest + gate text that contains
/// every required piece. This proves the guard is not vacuously failing on a
/// parsing bug (which would make every other test pass for the wrong reason).
#[test]
fn selection26_guard_accepts_a_complete_registration() {
    let mut run = String::new();
    run.push_str("run \"");
    run.push_str(MANDATORY_SELECTION_RUN_LABEL);
    run.push_str("\" cargo test ");
    for flag in MANDATORY_SELECTION_TEST_FLAGS {
        run.push_str(flag);
        run.push(' ');
    }
    let mut manifest = String::from("NATIVE_FILES = (\n");
    for path in MANDATORY_PUBLIC_MANIFEST_PATHS {
        manifest.push_str("    \"");
        manifest.push_str(path);
        manifest.push_str("\",\n");
    }
    manifest.push_str(")\n");
    assert!(
        selection26_registered(&run, &manifest),
        "guard rejected a text that contains every required target, flag, label and path"
    );
}

/// OMISSION MUTATION (run label): strip the mandatory run label and the guard
/// must turn RED. Restoring it must turn it GREEN again (the original test).
#[test]
fn selection26_gate_red_when_run_label_is_omitted() {
    let stripped_run = RUN_TESTS_SH.replace(MANDATORY_SELECTION_RUN_LABEL, "__OMITTED__");
    assert_ne!(
        RUN_TESTS_SH, stripped_run,
        "mutation had no effect: run label not present to strip"
    );
    assert!(
        !selection26_registered(&stripped_run, CREATE_PUBLIC_BRANCH_PY),
        "guard did not fail when the mandatory selection run label was removed"
    );
}

/// OMISSION MUTATION (one `--test` target at a time): removing any single
/// target from the gate must turn the guard RED. All three are mutated here so
/// a silent drop of any one target is caught.
#[test]
fn selection26_gate_red_when_any_test_target_is_omitted() {
    for flag in MANDATORY_SELECTION_TEST_FLAGS {
        let stripped_run = RUN_TESTS_SH.replace(flag, "__OMITTED__");
        assert_ne!(
            RUN_TESTS_SH, stripped_run,
            "mutation had no effect for {flag}"
        );
        assert!(
            !selection26_registered(&stripped_run, CREATE_PUBLIC_BRANCH_PY),
            "guard did not fail when the `--test` target {flag} was removed"
        );
    }
}

/// OMISSION MUTATION (one manifest path at a time): removing any single owned
/// file from the public manifest must turn the guard RED. This is the test that
/// prevents the public branch from silently shrinking the selection26 payload.
#[test]
fn selection26_manifest_red_when_any_path_is_omitted() {
    for path in MANDATORY_PUBLIC_MANIFEST_PATHS {
        let stripped_manifest = CREATE_PUBLIC_BRANCH_PY.replace(path, "__OMITTED__");
        assert_ne!(
            CREATE_PUBLIC_BRANCH_PY, stripped_manifest,
            "mutation had no effect for {path}"
        );
        assert!(
            !selection26_registered(RUN_TESTS_SH, &stripped_manifest),
            "guard did not fail when the manifest path {path} was removed"
        );
    }
}

/// Positive + omission round-trip: after every single-piece omission is restored
/// (i.e. the original embedded files), the guard is GREEN again. This is the
/// "original GREEN" half of the mutation contract.
#[test]
fn selection26_registration_is_stable_when_restored() {
    assert!(
        selection26_registered(RUN_TESTS_SH, CREATE_PUBLIC_BRANCH_PY),
        "restored (original) files must satisfy the guard"
    );
}
