#[cfg(not(target_os = "linux"))]
compile_error!("Linux only: requires systemd user scopes and flock");
use automation_test_driver::*;
use std::{
    collections::HashMap,
    fs::{self, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
    process::{Command, Stdio},
};
fn err(e: impl std::fmt::Display) -> String {
    e.to_string()
}
struct Runner {
    root: PathBuf,
    out: PathBuf,
    python: PathBuf,
    cap: u64,
    serial: usize,
}
impl Runner {
    fn run(&mut self, args: Vec<String>, build: bool) -> Result<(i32, String), String> {
        let mem = fs::read_to_string("/proc/meminfo")
            .map_err(err)?
            .lines()
            .find(|s| s.starts_with("MemAvailable:"))
            .and_then(|s| s.split_whitespace().nth(1))
            .and_then(|s| s.parse::<u64>().ok())
            .ok_or("MemAvailable unavailable")?;
        if !headroom(mem, self.cap) {
            return Err("insufficient 4 GiB desktop headroom".into());
        }
        self.serial += 1;
        let log = self.out.join(format!("command-{:04}.log", self.serial));
        // The scope owns the entire process tree; RuntimeMaxSec and stop timeout bound
        // descendants even if this driver dies. timeout additionally reports expiry.
        let unit = format!("narwhal-ai-{}-{}", std::process::id(), self.serial);
        let mut c = Command::new("systemd-run");
        c.current_dir(&self.out)
            .args(scope_args(self.cap, build, &unit)?)
            .args(&args);
        let mut child = c
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(err)?;
        let stdout = child.stdout.take().unwrap();
        let stderr = child.stderr.take().unwrap();
        let a = std::thread::spawn(move || output_tail(stdout));
        let b = std::thread::spawn(move || output_tail(stderr));
        let status = child.wait().map_err(err)?;
        let mut bytes = a.join().map_err(|_| "output reader panic")?.map_err(err)?;
        bytes.extend(b.join().map_err(|_| "output reader panic")?.map_err(err)?);
        fs::write(log, &bytes).map_err(err)?;
        let out = String::from_utf8_lossy(&bytes).into_owned();
        Ok((status.code().unwrap_or(-1), out))
    }
    fn cargo(
        &mut self,
        crate_: &Path,
        target: &Path,
        action: &str,
        filter: Option<&str>,
    ) -> Result<(i32, String), String> {
        let mut a = vec![
            "env".into(),
            format!("CARGO_TARGET_DIR={}", target.display()),
            "cargo".into(),
            action.into(),
            "--locked".into(),
            "--offline".into(),
            "--manifest-path".into(),
            crate_.join("Cargo.toml").display().to_string(),
            "-j".into(),
            "1".into(),
        ];
        if let Some(f) = filter {
            a.extend([
                "--lib".into(),
                f.into(),
                "--".into(),
                "--nocapture".into(),
                "--test-threads=1".into(),
            ]);
        }
        self.run(a, true)
    }
    fn host(
        &mut self,
        suite: &str,
        target: &Path,
        test: Option<&str>,
    ) -> Result<(i32, String), String> {
        let script = match suite {
            "box" => "test_ai_box.py",
            "plan" => "test_ai_plan.py",
            _ => "test_ai_primitives.py",
        };
        let mut a = vec![
            self.python.display().to_string(),
            "-B".into(),
            self.root.join("spike").join(script).display().to_string(),
            "--library".into(),
            target
                .join("debug/libnarwhal_automation.so")
                .display()
                .to_string(),
        ];
        if let Some(t) = test {
            a.extend(["--test".into(), t.into()]);
        }
        self.run(a, false)
    }
    fn fixture_controls(&mut self, target: &Path, legacy: Option<&Path>) -> Result<(), String> {
        let record = target.join("v4-publisher-bridge.json");
        if !record.exists() {
            let (code, out) = self.run(
                vec![
                    self.python.display().to_string(),
                    "-B".into(),
                    self.root
                        .join("spike/test_ai_accepted_review_native.py")
                        .display()
                        .to_string(),
                    "--target-dir".into(),
                    target.display().to_string(),
                    "--build".into(),
                ],
                true,
            )?;
            if code != 0 {
                return Err(format!("explicit retained SDK fixture build failed: {out}"));
            }
        }
        let executable = validate_discovery(&self.root, target)?;
        let (code, out) = self.run(
            vec![
                "timeout".into(),
                "--kill-after=2s".into(),
                "5s".into(),
                executable.display().to_string(),
                "--list".into(),
                "--exact".into(),
                BRIDGE_TEST.into(),
            ],
            false,
        )?;
        if code != 0 || !out.contains(&format!("{BRIDGE_TEST}: test")) {
            return Err("not the V4 publisher library test executable".into());
        }
        let current = target.join("debug/libnarwhal_automation.so");
        let legacy = legacy.map(fs::canonicalize).transpose().map_err(err)?;
        let mut cases = vec![
            ("absent", FixtureExpectation::Absent, None, None),
            (
                "current",
                FixtureExpectation::Current,
                Some(current.as_path()),
                None,
            ),
        ];
        if let Some(l) = legacy.as_deref() {
            cases.extend([
                ("legacy_cli", FixtureExpectation::Legacy, Some(l), None),
                ("legacy_env", FixtureExpectation::Legacy, None, Some(l)),
                (
                    "legacy_cli_precedence",
                    FixtureExpectation::Legacy,
                    Some(l),
                    Some(current.as_path()),
                ),
            ]);
        }
        let mut receipts = Vec::new();
        let mut fixture_failures = Vec::new();
        for (label, expected, cli, environment) in cases {
            let command = fixture_command(&self.python, &self.root, &executable, cli, environment);
            let (code, out) = self.run(command, false)?;
            let passed = fixture_result(expected, code, &out);
            receipts.push(serde_json::json!({"case":label,"passed":passed,"returncode":code,"log":format!("command-{:04}.log",self.serial)}));
            fs::write(self.out.join("fixture-controls.json"),serde_json::to_vec_pretty(&serde_json::json!({"cases":receipts,"optional_legacy":if legacy.is_some(){if receipts.iter().all(|r:&serde_json::Value|r["passed"]==true){"QUALIFIED"}else{"NOT_QUALIFIED_FAILED_CASES"}}else{"NOT_QUALIFIED_NO_EXPLICIT_LIBRARY"},"executable":executable,"legacy_library":legacy,"legacy_sha256":legacy.as_deref().map(file_hash).transpose()?})).map_err(err)?).map_err(err)?;
            if !passed {
                fixture_failures.push(format!("fixture control {label} failed: {out}"));
                println!("fixture {label}: NOT QUALIFIED");
            } else {
                println!("fixture {label}: PASS");
            }
        }
        if legacy.is_none() {
            println!(
                "optional legacy CLI/env/precedence: NOT QUALIFIED (no --legacy-library supplied)"
            );
        }
        if !fixture_failures.is_empty() {
            return Err(fixture_failures.join("\n"));
        }
        Ok(())
    }
    fn baseline(
        &mut self,
        crate_: &Path,
        target: &Path,
        suite: &str,
        filter: &str,
    ) -> Result<(), String> {
        let (c, o) = self.cargo(crate_, target, "test", Some(filter))?;
        if c != 0 || !positive(&o, true) {
            return Err(format!("baseline Rust failed: {o}"));
        }
        if suite == "plan" {
            // Preserve the original driver's integration tests without copying
            // anything except src/manifests into private mutant crates.
            let original = self.root.join("native/automation_core");
            let (c, o) = self.cargo(&original, &target.join("full-tests"), "test", None)?;
            if c != 0 || !positive(&o, true) {
                return Err(format!("plan full Rust baseline failed: {o}"));
            }
        }
        let (c, o) = self.cargo(crate_, target, "build", None)?;
        if c != 0 {
            return Err(format!("baseline build failed: {o}"));
        }
        let (c, o) = self.host(
            suite,
            target,
            if suite != "box" && suite != "plan" {
                Some("test_live_catalog_unchanged_and_one_use_approval")
            } else {
                None
            },
        )?;
        if c != 0 || !positive(&o, false) {
            return Err(format!("baseline SDK failed: {o}"));
        }
        Ok(())
    }
}
fn main() {
    if std::env::args().any(|a| a == "--help") {
        println!(
            "automation-test-driver --suite box|plan|v4|v6|v7|v8 [--target-dir ABSOLUTE_OUTSIDE_REPO] --python ABSOLUTE_INTERPRETER [--mutations] [--mutation-filter LABEL_SUBSTRING] [--test V4_FILTER] [--offset N] [--limit N] [--fixture-only] [--legacy-library ABSOLUTE_FILE]\nLinux/systemd only. Box/v7/v8 always mutate. v6/v7/v8 force 1024 MiB and default to /tmp/narwhal3d-ai-sustained-v4. Fixture flags apply only to v6; --offset/--limit only to v4."
        );
        return;
    }
    if let Err(e) = go() {
        eprintln!("INFRA_FAILURE: {e}");
        std::process::exit(1)
    }
}
fn go() -> Result<(), String> {
    let mut opts = HashMap::new();
    let mut mutations = false;
    let mut fixture_only = false;
    let mut args = std::env::args().skip(1);
    while let Some(a) = args.next() {
        if a == "--fixture-only" {
            fixture_only = true;
        } else if a == "--mutations" {
            mutations = true;
        } else if [
            "--suite",
            "--target-dir",
            "--python",
            "--test",
            "--mutation-filter",
            "--offset",
            "--limit",
            "--legacy-library",
        ]
        .contains(&a.as_str())
        {
            if opts
                .insert(a, args.next().ok_or("missing option value")?)
                .is_some()
            {
                return Err("duplicate option".into());
            }
        } else {
            return Err(format!("unknown option {a}"));
        }
    }
    let suite = opts
        .get("--suite")
        .ok_or("--suite box|plan|v4|v6|v7|v8 required")?
        .clone();
    if !["box", "plan", "v4", "v6", "v7", "v8"].contains(&suite.as_str()) {
        return Err("invalid suite".into());
    }
    if suite != "v6" && (fixture_only || opts.contains_key("--legacy-library")) {
        return Err("fixture options apply only to v6".into());
    }
    if opts.contains_key("--test") && !matches!(suite.as_str(), "v4" | "v6" | "v7" | "v8") {
        return Err("--test only applies to versioned Rust suites".into());
    }
    for key in ["--offset", "--limit"] {
        if suite != "v4" && opts.contains_key(key) {
            return Err(format!("{key} only applies to v4"));
        }
    }
    let root =
        fs::canonicalize(Path::new(env!("CARGO_MANIFEST_DIR")).join("../..")).map_err(err)?;
    let target = external(
        &root,
        Path::new(
            opts.get("--target-dir")
                .map(String::as_str)
                .or_else(|| extended_suite(&suite).then_some(DEFAULT_FIXTURE_TARGET))
                .ok_or("--target-dir required")?,
        ),
    )?;
    let python =
        fs::canonicalize(opts.get("--python").ok_or("explicit --python required")?).map_err(err)?;
    let cap = if extended_suite(&suite) {
        1024
    } else {
        std::env::var("NARWHAL_AI_BUILD_MIB")
            .unwrap_or("1536".into())
            .parse::<u64>()
            .map_err(err)?
    };
    if ![1024, 1536].contains(&cap) {
        return Err("cap must be 1024 or 1536 MiB".into());
    }
    let lock = OpenOptions::new()
        .create(true)
        .append(true)
        .open("/tmp/narwhal3d-heavy-build.lock")
        .map_err(err)?;
    lock.try_lock().map_err(err)?;
    // Fail closed: a real scope must start successfully before any build.
    if !Command::new("timeout")
        .args(["--kill-after=2s", "10s", "systemd-run"])
        .args([
            "--user",
            "--scope",
            "--quiet",
            "-p",
            "MemoryMax=1024M",
            "-p",
            "MemorySwapMax=0",
            "true",
        ])
        .status()
        .map_err(err)?
        .success()
    {
        return Err("systemd scope unavailable".into());
    }
    fs::create_dir_all(&target).map_err(err)?;
    if fs::canonicalize(&target).map_err(err)? != target {
        return Err("target changed during validation".into());
    }
    let out = target.join(format!("run-{}", std::process::id()));
    fs::create_dir(&out).map_err(err)?;
    let mut r = Runner {
        root: root.clone(),
        out: out.clone(),
        python,
        cap,
        serial: 0,
    };
    let original = root.join("native/automation_core");
    let sha = source_hash(&original);
    if fixture_only {
        drop(lock);
        r.fixture_controls(&target, opts.get("--legacy-library").map(Path::new))?;
        println!("fixture controls/logs: {}", out.display());
        return Ok(());
    }
    let base = out.join("baseline-source");
    snapshot(&original, &base)?;
    let filter = opts
        .get("--test")
        .map(String::as_str)
        .unwrap_or_else(|| default_filter(&suite));
    let data = execution_catalog();
    let execution_sha = hash(&serde_json::to_vec(&data).map_err(err)?);
    let applied_sha = hash(&serde_json::to_vec(&applied_catalog()).map_err(err)?);
    let rows = data[&suite].as_array().unwrap();
    let offset = opts
        .get("--offset")
        .map(|s| s.parse::<usize>().map_err(err))
        .transpose()?
        .unwrap_or(0);
    let limit = opts
        .get("--limit")
        .map(|s| s.parse::<usize>().map_err(err))
        .transpose()?
        .unwrap_or(usize::MAX);
    let selected: Vec<_> = rows
        .iter()
        .filter(|m| {
            opts.get("--mutation-filter")
                .is_none_or(|f| m[0].as_str().unwrap().contains(f))
        })
        .skip(offset)
        .take(limit)
        .collect();
    if (mutations || always_mutates(&suite)) && selected.is_empty() {
        return Err("no matching mutations".into());
    }
    let baseline_target = out.join("baseline-target");
    r.baseline(&base, &baseline_target, &suite, filter)?;
    let mut failures = 0;
    let mut attempted = 0;
    let mut killed = 0;
    let mut survived = 0;
    if mutations || always_mutates(&suite) {
        for m in selected {
            attempted += 1;
            let fields: Vec<_> = m
                .as_array()
                .unwrap()
                .iter()
                .map(|s| s.as_str().unwrap())
                .collect();
            let (label, file, test) = (fields[0], fields[1], fields[4]);
            let mutant = out.join(format!("source-{label}"));
            let mt = out.join(format!("target-{label}"));
            let mut receipt = serde_json::json!({"label":label,"test":test,"source_sha256":sha,"mutated_file":file,"mutant_file_sha256":null,"anchor_override_version":if suite=="v4" && matches!(label,"production_catalog"|"canonical_result"){Some(1)}else{None},"applied_catalog_sha256":applied_sha,"anchor_overrides_sha256":OVERRIDE_SHA,"additional_catalog_sha256":ADDITIONAL_SHA,"additional_provenance_sha256":ADDITIONAL_PROVENANCE_SHA,"execution_catalog_sha256":execution_sha,"status":"INFRA_FAILURE"});
            let attempt = (|| -> Result<Status, String> {
                snapshot(&original, &mutant)?;
                let p = mutant.join("src").join(file);
                let text = fs::read_to_string(&p).map_err(err)?;
                let changed = apply_variant(&suite, m, &text)?;
                fs::write(&p, &changed).map_err(err)?;
                receipt["mutant_file_sha256"] = hash(changed.as_bytes()).into();
                let (c, o) = if suite == "plan" {
                    let (c, o) = r.cargo(&mutant, &mt, "build", None)?;
                    if c != 0 {
                        return Err(format!("mutant compile failed: {o}"));
                    }
                    r.host("plan", &mt, Some(test))?
                } else {
                    r.cargo(&mutant, &mt, "test", Some(test))?
                };
                Ok(classify(c, &o, suite != "plan"))
            })();
            let status = match attempt_status(&attempt) {
                Status::Killed => {
                    killed += 1;
                    "KILLED"
                }
                Status::Survived => {
                    survived += 1;
                    failures += 1;
                    "SURVIVED"
                }
                Status::InfraFailure => {
                    receipt["error"] = format!("{attempt:?}").into();
                    eprintln!("{label}: {attempt:?}");
                    failures += 1;
                    "INFRA_FAILURE"
                }
            };
            receipt["status"] = status.into();
            let mut f = OpenOptions::new()
                .create(true)
                .append(true)
                .open(out.join("mutations.jsonl"))
                .map_err(err)?;
            writeln!(f, "{receipt}").map_err(err)?;
            println!("{label}: {status}");
        }
    }
    r.baseline(&base, &baseline_target, &suite, filter)?;
    if source_hash(&original) != sha {
        return Err("live source changed".into());
    }
    let summary = serde_json::json!({"suite":suite,"compiler_cap_mib":cap,"rust_filter":filter,"catalog_sha256":hash(include_bytes!("catalog.json")),"applied_catalog_sha256":applied_sha,"anchor_overrides_sha256":OVERRIDE_SHA,"additional_catalog_sha256":ADDITIONAL_SHA,"additional_provenance_sha256":ADDITIONAL_PROVENANCE_SHA,"execution_catalog_sha256":execution_sha,"source_sha256":sha,"attempted":attempted,"killed":killed,"survived":survived,"infra_failure":failures-survived,"baseline_before":"GREEN","baseline_after":"GREEN","live_source_unchanged":true});
    fs::write(
        out.join("summary.json"),
        serde_json::to_vec_pretty(&summary).map_err(err)?,
    )
    .map_err(err)?;
    drop(lock);
    if suite == "v6" {
        r.fixture_controls(&target, opts.get("--legacy-library").map(Path::new))?;
    }
    println!("{summary}");
    println!("receipts/logs: {}", out.display());
    if failures > 0 {
        return Err(format!("{failures} variants not killed"));
    }
    Ok(())
}
