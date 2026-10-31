//! Isolated history source-copy mutations; never edits the working crate.
//! Usage: history_mutations CRATE_DIR SCRATCH_DIR TARGET_DIR [FIRST [COUNT]]
//! All compilations take the shared heavy lock, check 4GiB+1GiB admission inside
//! that lock, then use one compiler job and a 1GiB/no-extra-swap systemd scope.
use std::{env, fs, path::Path, process::Command};
struct Mutant {
    name: &'static str,
    file: &'static str,
    old: &'static str,
    new: &'static str,
    test: &'static str,
}
fn copy_tree(from: &Path, to: &Path) {
    fs::create_dir_all(to).unwrap();
    for entry in fs::read_dir(from).unwrap() {
        let entry = entry.unwrap();
        assert!(!entry.file_type().unwrap().is_symlink());
        if entry.file_type().unwrap().is_dir() {
            copy_tree(&entry.path(), &to.join(entry.file_name()));
        } else {
            fs::copy(entry.path(), to.join(entry.file_name())).unwrap();
        }
    }
}
fn main() {
    let args: Vec<_> = env::args().collect();
    assert!((4..=6).contains(&args.len()));
    let source = fs::canonicalize(&args[1]).unwrap();
    fs::create_dir_all(&args[2]).unwrap();
    fs::create_dir_all(&args[3]).unwrap();
    let scratch = fs::canonicalize(&args[2]).unwrap();
    let target = fs::canonicalize(&args[3]).unwrap();
    let project = source.parent().unwrap().parent().unwrap();
    assert!(!scratch.starts_with(project) && !target.starts_with(project));
    let mutants = [
        Mutant {
            name: "disabled_begin_nests",
            file: "history.rs",
            old: "if !self.enabled {",
            new: "if false && !self.enabled {",
            test: "history_lazy_nested_none_capture_and_disabled_open",
        },
        Mutant {
            name: "capture_overwrites_first",
            file: "history.rs",
            old: ".is_some_and(|id| self.records[&id].before.is_none());",
            new: ".is_some_and(|_| true);",
            test: "history_lazy_nested_none_capture_and_disabled_open",
        },
        Mutant {
            name: "null_capture_reports_false",
            file: "history.rs",
            old: "Output::Captured { captured: eligible }",
            new: "Output::Captured { captured: eligible && payload.is_some() }",
            test: "history_lazy_nested_none_capture_and_disabled_open",
        },
        Mutant {
            name: "nested_depth_lost",
            file: "history.rs",
            old: "let depth = self.depth.checked_add(1).ok_or(\"depth_exhausted\")?;",
            new: "let depth = self.depth.checked_add(0).ok_or(\"depth_exhausted\")?;",
            test: "history_lazy_nested_none_capture_and_disabled_open",
        },
        Mutant {
            name: "empty_end_clears_redo",
            file: "history.rs",
            old: "End { .. } => {\n                let Some(id)",
            new: "End { .. } => {\n                self.discard_redo(budget);\n                let Some(id)",
            test: "history_noops_abandon_preserve_redo_and_state",
        },
        Mutant {
            name: "pruning_off_by_one",
            file: "history.rs",
            old: "while self.undo.len() > self.limit {",
            new: "while self.undo.len() > self.limit + 1 {",
            test: "history_saved_state_survives_trim_clear_and_navigation",
        },
        Mutant {
            name: "lease_ignored_on_prune",
            file: "history.rs",
            old: "if r.stacked || r.leases != 0 {",
            new: "if r.stacked {",
            test: "history_escaped_leases_and_multiple_payload_fields",
        },
        Mutant {
            name: "gc_while_one_field_live",
            file: "history.rs",
            old: "if *refs == 0 {",
            new: "if *refs <= 1 {",
            test: "history_escaped_leases_and_multiple_payload_fields",
        },
        Mutant {
            name: "reattach_keeps_garbage",
            file: "history.rs",
            old: "*self.payloads.get_mut(&id).unwrap() += 1;\n            self.garbage.remove(&id);",
            new: "*self.payloads.get_mut(&id).unwrap() += 1;",
            test: "history_escaped_leases_and_multiple_payload_fields",
        },
        Mutant {
            name: "pending_does_not_pin_current",
            file: "history.rs",
            old: "self.add_ref(current);",
            new: "/* mutation: pending current is unpinned */",
            test: "history_navigation_busy_abort_and_uncertain_recovery",
        },
        Mutant {
            name: "busy_guard_removed",
            file: "history.rs",
            old: "if self.pending.is_some() {",
            new: "if false && self.pending.is_some() {",
            test: "history_navigation_busy_abort_and_uncertain_recovery",
        },
        Mutant {
            name: "partial_abort_not_uncertain",
            file: "history.rs",
            old: "self.uncertain = unknown;",
            new: "self.uncertain = false;",
            test: "history_navigation_busy_abort_and_uncertain_recovery",
        },
        Mutant {
            name: "uncertain_clear_reuses_identity",
            file: "history.rs",
            old: "let state = if self.uncertain {",
            new: "let state = if false && self.uncertain {",
            test: "history_navigation_busy_abort_and_uncertain_recovery",
        },
        Mutant {
            name: "undo_wrong_state_identity",
            file: "history.rs",
            old: "self.state_id = r.before_state;",
            new: "self.state_id = r.after_state;",
            test: "history_navigation_refreshes_opposite_and_restores_state",
        },
        Mutant {
            name: "redo_wrong_opposite_field",
            file: "history.rs",
            old: "let before = matches!(p.direction, Side::Redo);",
            new: "let before = false;",
            test: "history_navigation_refreshes_opposite_and_restores_state",
        },
        Mutant {
            name: "clear_changes_saved_identity",
            file: "history.rs",
            old: "let state = if self.uncertain {",
            new: "let state = if true {",
            test: "history_saved_state_survives_trim_clear_and_navigation",
        },
        Mutant {
            name: "record_order_reversed",
            file: "history.rs",
            old: "Side::Undo => self.undo.iter().copied().collect(),",
            new: "Side::Undo => self.undo.iter().rev().copied().collect(),",
            test: "history_stack_order_and_redo_pruning_with_retained_views",
        },
        Mutant {
            name: "payload_owner_guard_removed",
            file: "history.rs",
            old: "if payload.is_some_and(|id| !self.payloads.contains_key(&id)) {",
            new: "if false && payload.is_some_and(|id| !self.payloads.contains_key(&id)) {",
            test: "history_cross_owner_handles_nonreuse_and_close",
        },
        Mutant {
            name: "commit_accepts_wrong_ticket",
            file: "history.rs",
            old: "CommitNav { ticket, .. } => {\n                let p = self\n                    .pending\n                    .as_ref()\n                    .filter(|p| p.id == ticket)",
            new: "CommitNav { ticket, .. } => {\n                let p = self\n                    .pending\n                    .as_ref()",
            test: "history_navigation_busy_abort_and_uncertain_recovery",
        },
        Mutant {
            name: "commit_keeps_ticket",
            file: "history.rs",
            old: "self.pending = None;\n                self.del_ref(current);\n                self.generation += 1;\n                Output::Navigated",
            new: "self.del_ref(current);\n                self.generation += 1;\n                Output::Navigated",
            test: "history_navigation_racing_consumption_is_once",
        },
        Mutant {
            name: "ack_accepts_duplicate",
            file: "history.rs",
            old: "if unique.len() != payloads.len() {",
            new: "if false && unique.len() != payloads.len() {",
            test: "history_gc_bounded_atomic_ack_and_unattached_registration",
        },
        Mutant {
            name: "ack_nonatomic_validation",
            file: "history.rs",
            old: "if payloads.iter().any(|id| !self.garbage.contains(id)) {",
            new: "if payloads.iter().any(|id| !self.garbage.contains(id)) {\n                    if let Some(id) = payloads.first() { self.garbage.remove(id); }",
            test: "history_gc_bounded_atomic_ack_and_unattached_registration",
        },
        Mutant {
            name: "mutation_capacity_bypass",
            file: "lib.rs",
            old: "if MIN_OUTPUT_CAPACITY > capacity {",
            new: "if false && MIN_OUTPUT_CAPACITY > capacity {",
            test: "history_mutation_descriptor_preflight_and_pure_large_reads",
        },
        Mutant {
            name: "required_nullable_omitted",
            file: "history.rs",
            old: "#[serde(deserialize_with = \"nullable\")]",
            new: "#[serde(default, deserialize_with = \"nullable\")]",
            test: "history_strict_schema_names_and_exact_header",
        },
        Mutant {
            name: "name_byte_limit_plus_one",
            file: "history.rs",
            old: "const MAX_NAME: usize = 16_384;",
            new: "const MAX_NAME: usize = 16_385;",
            test: "history_strict_schema_names_and_exact_header",
        },
        Mutant {
            name: "record_budget_bypass",
            file: "history.rs",
            old: "if budget.records >= MAX_RECORDS {",
            new: "if false && budget.records >= MAX_RECORDS {",
            test: "history_admission_checks_are_transactional",
        },
        Mutant {
            name: "payload_budget_bypass",
            file: "history.rs",
            old: "if budget.payloads >= MAX_PAYLOADS {",
            new: "if false && budget.payloads >= MAX_PAYLOADS {",
            test: "history_admission_checks_are_transactional",
        },
        Mutant {
            name: "lease_budget_bypass",
            file: "history.rs",
            old: "if budget.leases >= MAX_LEASES {",
            new: "if false && budget.leases >= MAX_LEASES {",
            test: "history_admission_checks_are_transactional",
        },
        Mutant {
            name: "name_storage_budget_bypass",
            file: "history.rs",
            old: "if budget.names + name.len() > MAX_NAME_BYTES {",
            new: "if false && budget.names + name.len() > MAX_NAME_BYTES {",
            test: "history_admission_checks_are_transactional",
        },
        Mutant {
            name: "history_budget_bypass",
            file: "history.rs",
            old: "if self.histories.len() >= MAX_HISTORIES {",
            new: "if false && self.histories.len() >= MAX_HISTORIES {",
            test: "history_global_admission_and_close_release_all_charges",
        },
        Mutant {
            name: "generation_not_reserved",
            file: "history.rs",
            old: "self.generation_room(2)?;",
            new: "self.generation_room(1)?;",
            test: "history_navigation_reserves_completion_generation",
        },
        Mutant {
            name: "close_leaks_payload_charge",
            file: "history.rs",
            old: "self.budget.payloads -= h.payloads.len();",
            new: "/* mutation: leak payload charge */",
            test: "history_global_admission_and_close_release_all_charges",
        },
        Mutant {
            name: "names_wrong_order",
            file: "history.rs",
            old: ".rev()",
            new: ".skip(0)",
            test: "history_names_order_empty_and_retained_scope",
        },
    ];
    let first = args
        .get(4)
        .map(|s| s.parse::<usize>().unwrap())
        .unwrap_or(0);
    let count = args
        .get(5)
        .map(|s| s.parse::<usize>().unwrap())
        .unwrap_or(mutants.len() - first);
    for mutant in &mutants[first..first + count] {
        let dir = scratch.join(mutant.name);
        assert!(!dir.exists(), "Use a fresh scratch directory");
        fs::create_dir_all(&dir).unwrap();
        for name in ["Cargo.toml", "Cargo.lock"] {
            fs::copy(source.join(name), dir.join(name)).unwrap();
        }
        copy_tree(&source.join("src"), &dir.join("src"));
        copy_tree(&source.join("tests"), &dir.join("tests"));
        let file = dir.join("src").join(mutant.file);
        let text = fs::read_to_string(&file).unwrap();
        if mutant.name == "required_nullable_omitted" {
            assert_eq!(text.matches(mutant.old).count(), 4);
        } else {
            assert_eq!(
                text.matches(mutant.old).count(),
                1,
                "Unique anchor: {}",
                mutant.name
            );
        }
        fs::write(file, text.replacen(mutant.old, mutant.new, 1)).unwrap();
        // Cargo's package fingerprints can otherwise reuse a different source
        // copy's executable, including on the subsequent production baseline.
        let mutant_target = target
            .join("history-mutants")
            .join(scratch.file_name().unwrap())
            .join(mutant.name);
        assert!(
            !mutant_target.exists(),
            "Use a fresh target for each mutant"
        );
        let target_env = format!("CARGO_TARGET_DIR={}", mutant_target.display());
        let output = Command::new("flock")
            .args([
                "-w",
                "30",
                "/tmp/narwhal3d-heavy-build.lock",
                "sh",
                "-c",
                "awk '/MemAvailable:/ { if ($2 < 5242880) exit 1 }' /proc/meminfo && exec \"$@\"",
                "history-build",
                "systemd-run",
                "--user",
                "--scope",
                "-p",
                "MemoryMax=1024M",
                "-p",
                "MemorySwapMax=0",
                "timeout",
                "--kill-after=5s",
                "90s",
                "env",
                &target_env,
                "cargo",
                "test",
                "--locked",
                "--offline",
                "--manifest-path",
            ])
            .arg(dir.join("Cargo.toml"))
            .args(["-j", "1", mutant.test, "--", "--test-threads=1"])
            .output()
            .unwrap();
        let log = format!(
            "{}{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        fs::write(dir.join("observed.log"), &log).unwrap();
        assert!(
            !output.status.success()
                && log.contains("test result: FAILED")
                && log.contains(mutant.test),
            "Mutant survived or failed before assertion: {}\n{}",
            mutant.name,
            log
        );
        println!("RED {}: {}", mutant.name, mutant.test);
    }
    println!(
        "{count} isolated history mutants killed; rerun production baseline to confirm restored GREEN."
    );
}
