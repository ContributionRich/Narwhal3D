//! Isolated source-copy mutation checks. Never rewrites the production crate.
//! Usage: mutation_check CRATE_DIR EXTERNAL_SCRATCH_DIR
//! Each actual Cargo invocation takes the shared lock and a 1 GiB/no-swap scope.
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
        assert!(
            !entry.file_type().unwrap().is_symlink(),
            "No source-copy symlinks"
        );
        if entry.file_type().unwrap().is_dir() {
            copy_tree(&entry.path(), &to.join(entry.file_name()));
        } else {
            fs::copy(entry.path(), to.join(entry.file_name())).unwrap();
        }
    }
}
fn main() {
    let args: Vec<_> = env::args().collect();
    assert_eq!(args.len(), 3, "CRATE_DIR EXTERNAL_SCRATCH_DIR");
    let source = fs::canonicalize(&args[1]).unwrap();
    let scratch = Path::new(&args[2]);
    fs::create_dir_all(scratch).unwrap();
    let scratch = fs::canonicalize(scratch).unwrap();
    let project = source.parent().unwrap().parent().unwrap();
    assert!(
        !scratch.starts_with(project),
        "Artifacts must be outside the project"
    );
    let mutants = [
        Mutant { name:"document_count_all_rows", file:"table.rs", old:r".filter(|(key, _)| key.contains('\\'))", new:".filter(|_| true)", test:"document_user_text_count_matches_native_and_is_read_only" },
        Mutant { name:"document_count_forward_slash", file:"table.rs", old:r".filter(|(key, _)| key.contains('\\'))", new:".filter(|(key, _)| key.contains('/'))", test:"document_user_text_count_matches_native_and_is_read_only" },
        Mutant { name:"document_count_exactly_one_separator", file:"table.rs", old:r".filter(|(key, _)| key.contains('\\'))", new:r".filter(|(key, _)| key.matches('\\').count() == 1)", test:"document_user_text_count_matches_native_and_is_read_only" },
        Mutant { name:"unicode_lowercase", file:"table.rs", old:"let cp = c as u32;", new:"let cp = c.to_lowercase().next().unwrap() as u32;", test:"exact_pinned_ordinal_not_unicode_casefold" },
        Mutant { name:"exact_lookup_ignored", file:"table.rs", old:"(!exact || row.0.as_ref() == key).then_some(row)", new:"Some(row)", test:"native_replay_order_spelling_empty_and_exact_lookup" },
        Mutant { name:"replay_overwrites_first_spelling", file:"table.rs", old:"*previous = value.into();", new:"*previous = value.into(); builder.get_mut(&norm).unwrap().1.0 = key.into();", test:"native_replay_order_spelling_empty_and_exact_lookup" },
        Mutant { name:"empty_value_is_stored", file:"table.rs", old:"let value = value.filter(|v| !v.is_empty());", new:"let value = value;", test:"no_op_generation_and_identity_never_change" },
        Mutant { name:"same_value_creates_ticket", file:"table.rs", old:"(Some(i), Some(v)) if self.rows[i].1.as_ref() == v => Ok(None),", new:"(Some(i), Some(v)) if false && self.rows[i].1.as_ref() == v => Ok(None),", test:"no_op_generation_and_identity_never_change" },
        Mutant { name:"empty_key_writes", file:"table.rs", old:"if key.is_empty() {\n            return Ok(None);", new:"if false && key.is_empty() {\n            return Ok(None);", test:"no_op_generation_and_identity_never_change" },
        Mutant { name:"prepare_ignores_generation", file:"session.rs", old:"if s.generation != generation {", new:"if false && s.generation != generation {", test:"staged_abort_conflict_and_single_use_tickets" },
        Mutant { name:"commit_ignores_generation", file:"session.rs", old:"if staged.generation != s.generation {", new:"if false && staged.generation != s.generation {", test:"staged_abort_conflict_and_single_use_tickets" },
        Mutant { name:"stale_ticket_not_discarded", file:"session.rs", old:"self.tickets.remove(&ticket);\n                    return Err(\"generation_conflict\");", new:"return Err(\"generation_conflict\");", test:"staged_abort_conflict_and_single_use_tickets" },
        Mutant { name:"abort_keeps_ticket", file:"session.rs", old:"self.ticket(session, ticket)?;\n                self.tickets.remove(&ticket);", new:"self.ticket(session, ticket)?;", test:"staged_abort_conflict_and_single_use_tickets" },
        Mutant { name:"restore_keeps_generation", file:"session.rs", old:".generation\n                    .checked_add(1)", new:".generation\n                    .checked_add(0)", test:"restore_identity_equality_and_conflicts" },
        Mutant { name:"restore_keeps_current_state", file:"session.rs", old:"let state = self.snapshot(session, snapshot)?.state.clone();", new:"let state = self.session(session)?.current.clone();", test:"snapshots_are_constant_time_arc_ownership" },
        Mutant { name:"snapshot_owner_bypass", file:"session.rs", old:"if snap.session_id != session_id {", new:"if false && snap.session_id != session_id {", test:"cross_session_and_closed_handles_cannot_be_reused" },
        Mutant { name:"ticket_owner_bypass", file:"session.rs", old:"if ticket.session_id != session_id {", new:"if false && ticket.session_id != session_id {", test:"cross_session_and_closed_handles_cannot_be_reused" },
        Mutant { name:"equal_identity_only", file:"session.rs", old:"equal: Arc::ptr_eq(a, b) || a.table.rows == b.table.rows,", new:"equal: Arc::ptr_eq(a, b),", test:"restore_identity_equality_and_conflicts" },
        Mutant { name:"close_leaks_snapshots", file:"session.rs", old:"self.snapshots.retain(|_, s| s.session_id != session);", new:"/* mutation: snapshots survive close */", test:"cross_session_and_closed_handles_cannot_be_reused" },
        Mutant { name:"clone_snapshot_table", file:"session.rs", old:"let snapshot = self.ids(1)?;", new:"let state = Arc::new(State { id: state.id, table: state.table.clone(), _lease: self.lease(state.table.bytes, state.table.rows.len())? }); let snapshot = self.ids(1)?;", test:"snapshots_are_constant_time_arc_ownership" },
        Mutant { name:"output_capacity_guard_bypassed", file:"lib.rs", old:"if capacity < MIN_OUTPUT_CAPACITY {", new:"if false && capacity < MIN_OUTPUT_CAPACITY {", test:"descriptors_reject_before_mutation_and_compact_replies_fit" },
        Mutant { name:"string_limit_off_by_one", file:"table.rs", old:"const MAX_STRING: usize = 8 * 1024 * 1024;", new:"const MAX_STRING: usize = 8 * 1024 * 1024 + 1;", test:"strict_json_and_named_string_limits" },
        Mutant { name:"row_limit_off_by_one", file:"table.rs", old:"const MAX_ROWS: usize = 100_000;", new:"const MAX_ROWS: usize = 100_001;", test:"row_limits_reject_import_and_prepare_without_truncation" },
        Mutant { name:"table_bytes_limit_off_by_one", file:"table.rs", old:"const MAX_TABLE_BYTES: usize = 64 * 1024 * 1024;", new:"const MAX_TABLE_BYTES: usize = 64 * 1024 * 1024 + 1;", test:"table_byte_bound_and_first_spelling_accounting" },
        Mutant { name:"retained_budget_ignored", file:"session.rs", old:".filter(|total| *total <= MAX_RETAINED)", new:".filter(|_| true)", test:"overflow_and_admission_are_transactional" },
        Mutant { name:"session_admission_ignored", file:"session.rs", old:"if self.sessions.len() >= MAX_SESSIONS {", new:"if false && self.sessions.len() >= MAX_SESSIONS {", test:"global_handle_limits_and_close_release_admission" },
        Mutant { name:"snapshot_admission_ignored", file:"session.rs", old:"if self.snapshots.len() >= MAX_SNAPSHOTS {", new:"if false && self.snapshots.len() >= MAX_SNAPSHOTS {", test:"global_handle_limits_and_close_release_admission" },
        Mutant { name:"ticket_admission_ignored", file:"session.rs", old:"if self.tickets.len() >= MAX_TICKETS {", new:"if false && self.tickets.len() >= MAX_TICKETS {", test:"global_handle_limits_and_close_release_admission" },
    ];
    for mutant in &mutants {
        let dir = scratch.join(mutant.name);
        assert!(!dir.exists(), "Use a fresh scratch directory for each run");
        fs::create_dir_all(&dir).unwrap();
        for name in ["Cargo.toml", "Cargo.lock"] {
            fs::copy(source.join(name), dir.join(name)).unwrap();
        }
        copy_tree(&source.join("src"), &dir.join("src"));
        copy_tree(&source.join("tests"), &dir.join("tests"));
        let file = dir.join("src").join(mutant.file);
        let text = fs::read_to_string(&file).unwrap();
        assert_eq!(
            text.matches(mutant.old).count(),
            1,
            "Unique anchor: {}",
            mutant.name
        );
        fs::write(file, text.replacen(mutant.old, mutant.new, 1)).unwrap();
        let mem = fs::read_to_string("/proc/meminfo").unwrap();
        let available: u64 = mem
            .lines()
            .find(|l| l.starts_with("MemAvailable:"))
            .unwrap()
            .split_whitespace()
            .nth(1)
            .unwrap()
            .parse()
            .unwrap();
        assert!(
            available >= 5 * 1024 * 1024,
            "Reserve 4 GiB desktop + 1 GiB build"
        );
        let target = format!("CARGO_TARGET_DIR={}", scratch.join("target").display());
        let output = Command::new("flock")
            .args([
                "-w",
                "30",
                "/tmp/narwhal3d-heavy-build.lock",
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
                &target,
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
            "Mutant survived or failed before a test assertion: {}\n{}",
            mutant.name,
            log
        );
        println!("RED {}: {}", mutant.name, mutant.test);
    }
    println!(
        "{} isolated mutants killed; production source untouched. Rerun baseline tests separately.",
        mutants.len()
    );
}
