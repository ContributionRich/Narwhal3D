//! Owns a private X server and candidate process groups; never connects to the
//! user's display, bus, configuration or recovery directory. GUI API glue is bpy.

#[cfg(target_os = "linux")]
mod linux {
    use std::os::unix::{fs::PermissionsExt, process::CommandExt};
    use std::{
        env, fs,
        io::{BufRead, BufReader},
        path::{Path, PathBuf},
        process::{Child, Command, Stdio},
        sync::mpsc,
        thread,
        time::{Duration, Instant},
    };
    #[link(name = "X11")]
    unsafe extern "C" {
        fn XOpenDisplay(name: *const std::ffi::c_char) -> *mut std::ffi::c_void;
        fn XInternAtom(
            display: *mut std::ffi::c_void,
            name: *const std::ffi::c_char,
            only_if_exists: i32,
        ) -> std::ffi::c_ulong;
        fn XCloseDisplay(display: *mut std::ffi::c_void) -> i32;
    }
    unsafe extern "C" {
        fn kill(pid: i32, signal: i32) -> i32;
    }
    struct Owned(Child);
    impl Drop for Owned {
        fn drop(&mut self) {
            // Negative PID targets ONLY the new process group we created for this child.
            if matches!(self.0.try_wait(), Ok(None)) {
                unsafe {
                    kill(-(self.0.id() as i32), 9);
                }
                let _ = self.0.kill();
            }
            let _ = self.0.wait();
        }
    }
    fn required(name: &str) -> PathBuf {
        env::var_os(name)
            .map(PathBuf::from)
            .unwrap_or_else(|| panic!("set {name}"))
    }
    fn run_case(
        candidate: &Path,
        source: &Path,
        runtime: &Path,
        artifacts: &Path,
        display: &str,
        scenario: (&str, &str, &str),
    ) -> bool {
        let (mode, mutation, name) = scenario;
        let case = artifacts.join(name);
        fs::create_dir_all(&case).unwrap();
        for dir in [
            "config", "scripts", "home", "cache", "data", "runtime", "tmp",
        ] {
            fs::create_dir_all(case.join(dir)).unwrap();
            fs::set_permissions(case.join(dir), fs::Permissions::from_mode(0o700)).unwrap();
        }
        let log = fs::File::create(case.join("host.log")).unwrap();
        let mut command = Command::new(candidate);
        command
            .args([
                "--factory-startup",
                "--disable-autoexec",
                "--gpu-backend",
                "opengl",
                "--window-geometry",
                "0",
                "0",
                "1280",
                "900",
                "-noaudio",
                "--python-exit-code",
                "1",
                "--python",
            ])
            .arg(source.join("spike/preclose_gui_probe.py"))
            .env("DISPLAY", display)
            .env_remove("WAYLAND_DISPLAY")
            .env_remove("XAUTHORITY")
            .env_remove("DBUS_SESSION_BUS_ADDRESS")
            .env("HOME", case.join("home"))
            .env("XDG_CONFIG_HOME", case.join("config"))
            .env("XDG_CACHE_HOME", case.join("cache"))
            .env("XDG_DATA_HOME", case.join("data"))
            .env("XDG_RUNTIME_DIR", case.join("runtime"))
            .env("BLENDER_USER_CONFIG", case.join("config"))
            .env("BLENDER_USER_SCRIPTS", case.join("scripts"))
            .env("BLENDER_SYSTEM_RESOURCES", runtime.join("5.2"))
            .env("TMPDIR", case.join("tmp"))
            .env("BLENDER_SYSTEM_SCRIPTS", runtime.join("5.2/scripts"))
            .env("BLENDER_SYSTEM_DATAFILES", runtime.join("5.2/datafiles"))
            .env("BLENDER_SYSTEM_PYTHON", runtime.join("5.2/python"))
            .env("NARWHAL_RUNTIME_ROOT", runtime)
            .env("NARWHAL3D_DATA_DIR", case.join("data"))
            .env("NARWHAL_GUI_CASE", &case)
            .env("NARWHAL_GUI_MODE", mode)
            .env("NARWHAL_GUI_MUTATION", mutation)
            .env("NARWHAL_GUI_ISOLATED", "1")
            .env("PYTHONDONTWRITEBYTECODE", "1")
            .env("LIBGL_ALWAYS_SOFTWARE", "1")
            .env("MESA_LOADER_DRIVER_OVERRIDE", "llvmpipe")
            .env_remove("NARWHAL3D_WELCOME_REQUEST")
            .env_remove("NARWHAL3D_DEFER_FIRST_PRESENT")
            .env(
                "PYTHONPATH",
                format!(
                    "{}:{}:{}",
                    source.join("vendor/occt").display(),
                    runtime
                        .join("5.2/python/lib/python3.13/site-packages")
                        .display(),
                    source.display()
                ),
            )
            .stdout(Stdio::from(log.try_clone().unwrap()))
            .stderr(Stdio::from(log))
            .process_group(0);
        let mut child = Owned(command.spawn().expect("start owned candidate"));
        let start = Instant::now();
        let status = loop {
            if let Some(status) = child.0.try_wait().unwrap() {
                break status;
            }
            assert!(
                start.elapsed() < Duration::from_secs(180),
                "owned case timed out: {name}; see {}",
                case.display()
            );
            thread::sleep(Duration::from_millis(100));
        };
        let text = fs::read_to_string(case.join("result.json")).unwrap_or_else(|_| {
            panic!(
                "no GUI result for {name}, exit={status}; see {}",
                case.join("host.log").display()
            )
        });
        let report: serde_json::Value = serde_json::from_str(&text).unwrap();
        let ok = status.success() && report["ok"] == true;
        if mutation == "none" {
            assert!(ok, "GUI case {name} failed, exit={status}: {text}");
        } else {
            assert!(
                !ok && report["assertion_failure"] == true,
                "mutation must be assertion RED, not a runtime failure: {text}"
            );
        }
        println!(
            "{}: {name}, elapsed={:.1}s",
            if mutation == "none" {
                "GUI GREEN"
            } else {
                "GUI MUTATION RED"
            },
            start.elapsed().as_secs_f64()
        );
        ok
    }
    pub fn run() {
        let candidate = required("NARWHAL_GUI_CANDIDATE");
        let xvfb = required("NARWHAL_GUI_XVFB");
        let source = required("NARWHAL_GUI_SOURCE");
        let runtime = required("NARWHAL_GUI_RUNTIME");
        let artifact_root = required("NARWHAL_GUI_ARTIFACTS");
        let nonce = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let artifacts = artifact_root.join(format!("run-{}-{nonce}", std::process::id()));
        fs::create_dir_all(&artifacts).unwrap();
        fs::write(
            artifact_root.join("latest-run.txt"),
            artifacts.to_string_lossy().as_bytes(),
        )
        .unwrap();
        let log = fs::File::create(artifacts.join("xvfb.log")).unwrap();
        let mut server = Owned(
            Command::new(xvfb)
                .args([
                    "-displayfd",
                    "1",
                    "-screen",
                    "0",
                    "1600x1000x24",
                    "-nolisten",
                    "tcp",
                    "-ac",
                    "-noreset",
                ])
                .stdout(Stdio::piped())
                .stderr(log)
                .process_group(0)
                .spawn()
                .unwrap(),
        );
        let stdout = server.0.stdout.take().unwrap();
        let (send, recv) = mpsc::channel();
        thread::spawn(move || {
            let mut line = String::new();
            let result = BufReader::new(stdout).read_line(&mut line);
            let _ = send.send((result, line));
        });
        let (result, line) = recv
            .recv_timeout(Duration::from_secs(15))
            .expect("private Xvfb display startup");
        result.unwrap();
        let n: u32 = line.trim().parse().expect("isolated display number");
        let display = format!(":{n}");
        // GHOST interns WM_DELETE_WINDOW only if it exists at startup. A real WM
        // provides it; our otherwise WM-free private X server must initialize it.
        let display_name = std::ffi::CString::new(display.as_str()).unwrap();
        unsafe {
            let connection = XOpenDisplay(display_name.as_ptr());
            assert!(!connection.is_null(), "connect only to our private display");
            for name in [
                "WM_DELETE_WINDOW",
                "WM_TAKE_FOCUS",
                "WM_PROTOCOLS",
                "_NET_WM_PID",
                "UTF8_STRING",
            ] {
                let name = std::ffi::CString::new(name).unwrap();
                assert_ne!(XInternAtom(connection, name.as_ptr(), 0), 0);
            }
            XCloseDisplay(connection);
        }
        println!("Private Xvfb display {display}; no owner display used");
        let modes=env::var("NARWHAL_GUI_MODES").unwrap_or_else(|_|"default-off,save,cancel-dialog,pending-save,latest-edit,updater-save,updater-cancel,updater-escape".into());
        for mode in modes.split(',') {
            run_case(
                &candidate,
                &source,
                &runtime,
                &artifacts,
                &display,
                (mode, "none", mode),
            );
        }
        if env::var_os("NARWHAL_GUI_MUTATIONS").is_some() {
            run_case(
                &candidate,
                &source,
                &runtime,
                &artifacts,
                &display,
                ("save", "bypass-native", "red-native-bypass"),
            );
            run_case(
                &candidate,
                &source,
                &runtime,
                &artifacts,
                &display,
                ("latest-edit", "drop-latest-guard", "red-latest-guard"),
            );
            run_case(
                &candidate,
                &source,
                &runtime,
                &artifacts,
                &display,
                (
                    "updater-save",
                    "premature-update-approval",
                    "red-updater-approval",
                ),
            );
            run_case(
                &candidate,
                &source,
                &runtime,
                &artifacts,
                &display,
                ("save", "none", "restored-save"),
            );
            run_case(
                &candidate,
                &source,
                &runtime,
                &artifacts,
                &display,
                ("latest-edit", "none", "restored-latest-edit"),
            );
        }
        if env::var_os("NARWHAL_GUI_MUTATIONS").is_some() {
            run_case(
                &candidate,
                &source,
                &runtime,
                &artifacts,
                &display,
                ("updater-save", "none", "restored-updater-save"),
            );
        }
        let mut reports = Vec::new();
        for item in fs::read_dir(&artifacts).unwrap() {
            let path = item.unwrap().path().join("result.json");
            if path.is_file() {
                reports.push(
                    serde_json::from_slice::<serde_json::Value>(&fs::read(path).unwrap()).unwrap(),
                );
            }
        }
        fs::write(
            artifacts.join("summary.json"),
            serde_json::to_vec_pretty(&reports).unwrap(),
        )
        .unwrap();
        println!(
            "Owned candidate GUI qualification complete: {}",
            artifacts.display()
        );
    }
}

#[cfg(target_os = "linux")]
fn main() {
    linux::run();
}

#[cfg(not(target_os = "linux"))]
fn main() {
    eprintln!("The isolated Xvfb shutdown qualifier requires Linux");
    std::process::exit(2);
}
