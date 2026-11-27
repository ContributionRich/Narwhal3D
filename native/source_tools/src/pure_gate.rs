//! Pure-core qualification: Rust owns process bounds, build order, evidence and reports.
use crate::*;
use std::{
    io::{Read, Write},
    process::{Command, Stdio},
    thread,
    time::{Duration, Instant},
};
pub fn metadata() -> Value {
    serde_json::from_str(include_str!("pure_gate_config.json")).expect("pure gate metadata")
}
pub fn project_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .parent()
        .unwrap()
        .to_path_buf()
}
fn environment(v: &Value) -> Option<BTreeMap<String, String>> {
    v.as_object().map(|m| {
        m.iter()
            .filter_map(|(k, v)| v.as_str().map(|v| (k.clone(), v.into())))
            .collect()
    })
}
/// Bounded process group, drained pipes, bounded diagnostics; never waits for interactive input.
fn terminate(child: &mut std::process::Child) {
    #[cfg(unix)]
    unsafe {
        unsafe extern "C" {
            fn kill(pid: i32, signal: i32) -> i32;
        }
        kill(-(child.id() as i32), 9);
    }
    #[cfg(windows)]
    {
        let _ = Command::new("taskkill")
            .args(["/F", "/T", "/PID", &child.id().to_string()])
            .status();
    }
    let _ = child.kill();
    let _ = child.wait();
}
pub fn execute(
    args: &[String],
    cwd: &Path,
    env: Option<&BTreeMap<String, String>>,
    input: Option<&[u8]>,
    seconds: u64,
) -> Result<(i32, String, String), String> {
    if args.is_empty() {
        return Err("empty command".into());
    }
    let mut command = Command::new(&args[0]);
    command
        .args(&args[1..])
        .current_dir(cwd)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    if let Some(env) = env {
        command.env_clear().envs(env);
    }
    command.env("GIT_EDITOR", "true");
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        command.process_group(0);
    }
    let mut child = command
        .spawn()
        .map_err(|e| format!("cannot run {:?}: {e}", args[0]))?;
    let (send, receive) = std::sync::mpsc::channel();
    for (kind, pipe) in [
        (
            0,
            Box::new(child.stdout.take().unwrap()) as Box<dyn Read + Send>,
        ),
        (
            1,
            Box::new(child.stderr.take().unwrap()) as Box<dyn Read + Send>,
        ),
    ] {
        let send = send.clone();
        thread::spawn(move || {
            let mut b = Vec::new();
            let result = pipe
                .take(8 * 1024 * 1024 + 1)
                .read_to_end(&mut b)
                .map_err(|e| e.to_string());
            let _ = send.send((kind, b, result));
        });
    }
    let mut stdin = child.stdin.take().unwrap();
    let data = input.unwrap_or_default().to_vec();
    let send = send.clone();
    thread::spawn(move || {
        let result = stdin
            .write_all(&data)
            .map(|()| 0)
            .map_err(|e| format!("child stdin write failed: {e}"));
        drop(stdin);
        let _ = send.send((2, Vec::new(), result));
    });
    let begin = Instant::now();
    let mut status = None;
    let mut streams = [None, None, None];
    loop {
        while let Ok((kind, b, result)) = receive.try_recv() {
            if let Err(e) = result {
                terminate(&mut child);
                return Err(e);
            }
            if b.len() > 8 * 1024 * 1024 {
                terminate(&mut child);
                return Err("process output limit exceeded".into());
            }
            streams[kind] = Some(b);
        }
        if status.is_none() {
            status = child.try_wait().map_err(|e| e.to_string())?;
        }
        if status.is_some() && streams.iter().all(Option::is_some) {
            break;
        }
        if begin.elapsed() > Duration::from_secs(seconds) {
            terminate(&mut child);
            return Err(format!(
                "process timed out after {seconds}s: {}",
                args.join(" ")
            ));
        }
        thread::sleep(Duration::from_millis(10));
    }
    Ok((
        status.unwrap().code().unwrap_or(-1),
        String::from_utf8_lossy(streams[0].as_ref().unwrap()).into_owned(),
        String::from_utf8_lossy(streams[1].as_ref().unwrap()).into_owned(),
    ))
}

fn read_u64(v: &Value, key: &str, default: u64) -> u64 {
    v[key].as_u64().unwrap_or(default)
}
pub fn probe(request: &Value) -> Result<Value, String> {
    let library = request["library"].as_str().ok_or("library missing")?;
    let name = request["name"].as_str().ok_or("probe name missing")?;
    let selected = Path::new(library);
    if !selected.is_file() || selected.is_symlink() {
        return Err(format!(
            "missing regular pure-core {} library: {library}",
            if name == "PURE_CORE_RETIREMENT_PROBE" {
                "retirement"
            } else {
                "ABI"
            }
        ));
    }

    let root = request["root"]
        .as_str()
        .map(PathBuf::from)
        .unwrap_or_else(project_root);
    let child = json!({"op":"pure_abi_probe","root":root,"library":library,"name":name});
    let executable = std::env::current_exe().map_err(|e| e.to_string())?;
    let args = vec![executable.to_string_lossy().into_owned(), "rpc".into()];
    let env = environment(&request["environment"]);
    let (status, out, err) = execute(
        &args,
        &root,
        env.as_ref(),
        Some(&serde_json::to_vec(&child).unwrap()),
        read_u64(
            request,
            "timeout_seconds",
            if matches!(name, "PURE_CORE_PARSE_PROBE" | "PURE_CORE_RETIREMENT_PROBE") {
                30
            } else {
                60
            },
        )
        .clamp(1, 60),
    )?;
    let expected = crate::pure_abi::identity(&root, name)?;
    let identity = decode_probe_reply(status, &out, &err, &expected).map_err(|e| {
        format!(
            "required {} probe refused: {e}",
            if name == "PURE_CORE_RETIREMENT_PROBE" {
                "retirement"
            } else {
                "pure-core"
            }
        )
    })?;
    if request
        .get("expected_identity")
        .is_some_and(|v| v != &identity)
    {
        return Err(format!(
            "pure ABI evidence differs: expected {}, got {identity}",
            request["expected_identity"]
        ));
    }
    Ok(identity)
}
fn cargo(c: &Value, crate_: &Path, flags: &[&str]) -> Vec<String> {
    let mut args = vec![
        "cargo".into(),
        format!("+{}", c["RUST_TOOLCHAIN"].as_str().unwrap()),
    ];
    args.extend(flags.iter().map(|s| s.to_string()));
    args.extend([
        "--manifest-path".into(),
        crate_.join("Cargo.toml").to_string_lossy().into_owned(),
    ]);
    args
}
fn run_checked(
    root: &Path,
    args: &[String],
    label: &str,
    request: &Value,
) -> Result<String, String> {
    let (code, out, err) = execute(
        args,
        root,
        environment(&request["environment"]).as_ref(),
        None,
        1800,
    )?;
    if code != 0 {
        return Err(format!("{label} failed:\n{out}{err}"));
    }
    Ok(format!("{out}{err}"))
}
fn executable_file(path: &Path) -> bool {
    if !path.is_file() {
        return false;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::metadata(path).is_ok_and(|m| m.permissions().mode() & 0o111 != 0)
    }
    #[cfg(not(unix))]
    {
        true
    }
}
pub fn python(root: &Path, fallback: &str) -> String {
    let runtime =
        if let Some(s) = std::env::var_os("NARWHAL_RUNTIME_ROOT").filter(|s| !s.is_empty()) {
            PathBuf::from(s)
        } else if executable_file(&root.join("tools/blender-patched/blender")) {
            root.join("tools/blender-patched")
        } else {
            root.join("tools/blender-5.2.2-linux-x64")
        };
    let path = runtime.join("5.2/python/bin/python3.13");
    if path.is_file() {
        path.to_string_lossy().into_owned()
    } else {
        fallback.into()
    }
}
fn files(root: &Path) -> Result<BTreeSet<String>, String> {
    fn walk(root: &Path, path: &Path, out: &mut BTreeSet<String>) -> Result<(), String> {
        for e in fs::read_dir(path).map_err(|e| e.to_string())? {
            let p = e.map_err(|e| e.to_string())?.path();
            if p.is_symlink() {
                return Err(format!("symlink in Rust fixture closure: {}", p.display()));
            }
            if p.is_dir() {
                walk(root, &p, out)?
            } else if p.is_file() {
                out.insert(format!(
                    "native/pure_core/{}",
                    p.strip_prefix(root)
                        .unwrap()
                        .to_string_lossy()
                        .replace('\\', "/")
                ));
            }
        }
        Ok(())
    }
    let mut out = BTreeSet::new();
    walk(root, &root.join("tests"), &mut out)?;
    Ok(out)
}
pub fn closure(
    _root: &Path,
    crate_: &Path,
    c: &Value,
    public: &Value,
) -> Result<Vec<String>, String> {
    let expected: BTreeSet<_> = strings(&c["RUST_INTEGRATION_FILES"]).into_iter().collect();
    let actual = files(crate_)?;
    let modules: BTreeSet<_> = strings(&c["RUST_REQUIRED_MODULE_FILES"])
        .into_iter()
        .collect();
    let selected: BTreeSet<_> = strings(&public["NATIVE_FILES"]).into_iter().collect();
    let missing: BTreeSet<_> = expected
        .union(&modules)
        .filter(|p| !selected.contains(*p))
        .cloned()
        .chain(
            modules
                .iter()
                .filter(|p| {
                    !crate_
                        .join(p.strip_prefix("native/pure_core/").unwrap_or(p))
                        .is_file()
                })
                .cloned(),
        )
        .chain(expected.symmetric_difference(&actual).cloned())
        .collect();
    if !missing.is_empty() {
        return Err(format!(
            "Rust integration source/fixture public closure differs: {missing:?}"
        ));
    }
    Ok(expected.into_iter().collect())
}
/// Each required integration target needs its actual compiler artifact and an unfiltered pass.
pub fn clippy_evidence(result: &Value) -> Result<(), String> {
    if result["status"] != "ok" || result["ran"] != true {
        Err("clippy did not run with -D warnings; a skipped clippy must fail the gate".into())
    } else {
        Ok(())
    }
}
pub fn execution_evidence(text: &str, suite: &str) -> Result<u64, String> {
    let artifact = text
        .lines()
        .filter_map(|l| serde_json::from_str::<Value>(l).ok())
        .any(|a| {
            a["reason"] == "compiler-artifact"
                && a["target"]["name"] == suite
                && a["target"]["kind"] == json!(["test"])
                && a["profile"]["test"] == true
                && a["executable"].as_str().is_some_and(|s| !s.is_empty())
        });
    let summary = Regex::new(
        r"test result: ok\.\s+(\d+) passed; 0 failed; 0 ignored; 0 measured; 0 filtered out",
    )
    .unwrap()
    .captures(text)
    .and_then(|c| c[1].parse::<u64>().ok())
    .filter(|n| *n > 0);
    if !artifact || summary.is_none() {
        return Err(format!(
            "required Rust integration suite {suite} lacks compiled/executed evidence:\n{text}"
        ));
    }
    Ok(summary.unwrap())
}
pub fn step(
    root: &Path,
    crate_: &Path,
    c: &Value,
    public: &Value,
    name: &str,
    library: &Path,
    request: &Value,
) -> Result<Value, String> {
    Ok(match name {
        "fmt" => {
            run_checked(
                root,
                &cargo(c, crate_, &["fmt", "--check"]),
                "cargo fmt --check",
                request,
            )?;
            json!({"status":"ok","command":"cargo fmt --check"})
        }
        "clippy" => {
            let mut args = cargo(
                c,
                crate_,
                &["clippy", "--all-targets", "--locked", "--offline"],
            );
            args.extend(["--".into(), "-D".into(), "warnings".into()]);
            run_checked(root, &args, "cargo clippy -D warnings", request)?;
            json!({"status":"ok","ran":true,"command":"cargo clippy -D warnings"})
        }
        "tests" => {
            let closure = closure(root, crate_, c, public)?;
            let text = run_checked(
                root,
                &cargo(c, crate_, &["test", "--offline", "--locked"]),
                "cargo test",
                request,
            )?;
            let total = Regex::new(r"test result: ok\.\s+(\d+) passed")
                .unwrap()
                .captures(&text)
                .and_then(|v| v[1].parse::<u64>().ok())
                .filter(|n| *n > 0)
                .ok_or("cargo test ran but reported no passing tests")?;
            let mut evidence = serde_json::Map::new();
            for suite in strings(&c["RUST_INTEGRATION_SUITES"]) {
                let text = run_checked(
                    root,
                    &cargo(
                        c,
                        crate_,
                        &[
                            "test",
                            "--offline",
                            "--locked",
                            "--test",
                            &suite,
                            "--message-format=json",
                        ],
                    ),
                    "cargo integration test",
                    request,
                )?;
                let n = execution_evidence(&text, &suite)?;
                evidence.insert(suite, json!({"compiled":true,"passed":n}));
            }
            json!({"status":"ok","passed":total,"command":"cargo test","integration_suites":evidence,"public_fixture_closure":closure})
        }
        "release_build" => {
            let target = c["LINUX_TARGET"].as_str().unwrap();
            run_checked(
                root,
                &cargo(
                    c,
                    crate_,
                    &[
                        "build",
                        "--release",
                        "--locked",
                        "--offline",
                        "--target",
                        target,
                    ],
                ),
                "cargo build --release (Linux)",
                request,
            )?;
            if !library.is_file() {
                return Err(format!(
                    "release build produced no library at {}",
                    library.display()
                ));
            }
            json!({"status":"ok","artifact":library,"command":"cargo build --release"})
        }
        "direct_abi" => {
            for key in ["PURE_CORE_PARSE_PROBE", "PURE_CORE_RETIREMENT_PROBE"] {
                probe(&json!({"library":library,"name":key}))?;
            }
            let mut report = probe(&json!({"library":library,"name":"PURE_CORE_DIRECT_ABI"}))?;
            report["command"] = "Rust typed C ABI probe".into();
            report
        }
        "adapter" => {
            let py = python(root, request["python"].as_str().unwrap_or("python3"));
            let mut env: BTreeMap<String, String> = std::env::vars().collect();
            env.insert(
                "NARWHAL_PURE_CORE".into(),
                library.to_string_lossy().into_owned(),
            );
            let mut done = Vec::new();
            for suite in strings(&c["PURE_CORE_PYTESTS"]) {
                let (code, out, err) =
                    execute(&[py.clone(), suite.clone()], root, Some(&env), None, 1800)?;
                if code != 0 {
                    return Err(format!("pure-core suite {suite} failed:\n{out}{err}"));
                }
                done.push(suite);
            }
            json!({"status":"ok","suites":done.len(),"manifest":done,"command":"dedicated pure-core Python suites"})
        }
        "windows_check" => {
            let target = c["WINDOWS_TARGET"].as_str().unwrap();
            let tool = c["RUST_TOOLCHAIN"].as_str().unwrap();
            let installed = execute(
                &[
                    "rustup".into(),
                    "target".into(),
                    "list".into(),
                    "--toolchain".into(),
                    tool.into(),
                    "--installed".into(),
                ],
                root,
                environment(&request["environment"]).as_ref(),
                None,
                60,
            )
            .ok()
            .filter(|v| v.0 == 0)
            .map(|v| v.1.split_whitespace().any(|s| s == target));
            if installed == Some(false) {
                json!({"status":"skipped","reason":"Windows GNU target not installed"})
            } else {
                let (code, out, err) = execute(
                    &cargo(
                        c,
                        crate_,
                        &["check", "--locked", "--offline", "--target", target],
                    ),
                    root,
                    environment(&request["environment"]).as_ref(),
                    None,
                    1800,
                )?;
                if code != 0 {
                    if installed.is_none()
                        && (out.contains("target not installed")
                            || err.contains("target not installed")
                            || out.contains("component not installed")
                            || err.contains("component not installed"))
                    {
                        json!({"status":"skipped","reason":"Windows GNU target not installed"})
                    } else {
                        return Err(format!("cargo check (Windows GNU) failed:\n{out}{err}"));
                    }
                } else {
                    json!({"status":"ok","command":"cargo check --target x86_64-pc-windows-gnu"})
                }
            }
        }
        key => {
            let key = match key {
                "nearest_batch" => "PURE_CORE_NEAREST_BATCH_PROBE",
                "polyline_nearest" => "PURE_CORE_POLYLINE_NEAREST_PROBE",
                "curve_hits" => "PURE_CORE_CURVE_HITS_PROBE",
                "curve_record_hits" => "PURE_CORE_CURVE_RECORD_HITS_PROBE",
                "nurbs_record" => "PURE_CORE_NURBS_RECORD_PROBE",
                "line_samples" => "PURE_CORE_LINE_SAMPLES_PROBE",
                "polyline_samples" => "PURE_CORE_POLYLINE_SAMPLES_PROBE",
                "polycurve_samples" => "PURE_CORE_POLYCURVE_SAMPLES_PROBE",
                "remove_join_cv" => "PURE_CORE_REMOVE_JOIN_PROBE",
                "offset_fit" => "PURE_CORE_OFFSET_FIT_PROBE",
                "surface_offset" => "PURE_CORE_SURFACE_OFFSET_PROBE",
                "surface_grid" => "PURE_CORE_SURFACE_GRID_PROBE",
                "surface_display" => "PURE_CORE_SURFACE_DISPLAY_PROBE",
                "svg_unit" => "PURE_CORE_SVG_UNIT_PROBE",
                "dxf_approx" => "PURE_CORE_DXF_APPROX_PROBE",
                "planar_area" => "PURE_CORE_PLANAR_AREA_PROBE",
                _ => return Err(format!("unknown pure-core step: {key}")),
            };
            let identity = probe(&json!({"library":library,"name":key}))?;
            json!({"status":"ok",name:identity,"command":format!("Rust typed C ABI {name} probe")})
        }
    })
}
pub fn dispatch(request: &Value, public: &Value) -> Result<Value, String> {
    let root = PathBuf::from(request["root"].as_str().unwrap_or("."));
    let mut c = metadata();
    if let Some(m) = request["pure_config"].as_object() {
        c.as_object_mut().unwrap().extend(m.clone());
    }
    let crate_ = PathBuf::from(
        request["crate"]
            .as_str()
            .map(str::to_owned)
            .unwrap_or_else(|| {
                root.join(c["DEFAULT_CRATE"].as_str().unwrap())
                    .to_string_lossy()
                    .into_owned()
            }),
    );
    let library = request["library"]
        .as_str()
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            crate_
                .join("target")
                .join(c["LINUX_TARGET"].as_str().unwrap())
                .join("release")
                .join(c["LIB_FILENAME"].as_str().unwrap())
        });
    match request["action"].as_str().unwrap_or("run") {
        "closure" => Ok(json!(closure(&root, &crate_, &c, public)?)),
        "step" => step(
            &root,
            &crate_,
            &c,
            public,
            request["step"].as_str().ok_or("step missing")?,
            &library,
            request,
        ),
        "python" => Ok(json!(python(
            &root,
            request["fallback"].as_str().unwrap_or("python3")
        ))),
        "run" => {
            if !crate_.join("Cargo.toml").is_file() {
                return Err(format!(
                    "not a pure-core crate (no Cargo.toml): {}",
                    crate_.display()
                ));
            }
            let mut steps = serde_json::Map::new();
            for name in [
                "fmt",
                "clippy",
                "tests",
                "release_build",
                "direct_abi",
                "nearest_batch",
                "polyline_nearest",
                "curve_hits",
                "curve_record_hits",
                "nurbs_record",
                "line_samples",
                "polyline_samples",
                "polycurve_samples",
                "remove_join_cv",
                "offset_fit",
                "dxf_approx",
                "planar_area",
                "surface_offset",
                "surface_grid",
                "surface_display",
                "svg_unit",
                "adapter",
                "windows_check",
            ] {
                let result = step(&root, &crate_, &c, public, name, &library, request)?;
                if name == "clippy" {
                    clippy_evidence(&result)?;
                }
                steps.insert(name.into(), result);
            }
            Ok(
                json!({"gate":"pure-core-source","crate":crate_,"abi_version":c["ABI_VERSION"],"rust_toolchain":c["RUST_TOOLCHAIN"],"python_manifest":c["PURE_CORE_PYTESTS"],"steps":steps,"ok":true}),
            )
        }
        _ => Err("unknown pure-core gate action".into()),
    }
}

pub fn decode_probe_reply(
    status: i32,
    out: &str,
    err: &str,
    expected: &Value,
) -> Result<Value, String> {
    if status != 0 {
        return Err(format!("pure ABI child failed ({status}): {err}"));
    }
    let reply: Value =
        serde_json::from_str(out).map_err(|e| format!("pure ABI child JSON: {e}"))?;
    if reply["ok"] != true {
        return Err(reply["error"]
            .as_str()
            .unwrap_or("pure ABI child refused")
            .into());
    }
    if &reply["value"] != expected {
        return Err(format!(
            "pure ABI evidence differs: expected {expected}, got {}",
            reply["value"]
        ));
    }
    Ok(reply["value"].clone())
}
