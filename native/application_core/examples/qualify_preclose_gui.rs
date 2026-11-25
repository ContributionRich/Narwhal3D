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
    #[repr(C)]
    struct Attributes {
        x: i32,
        y: i32,
        width: i32,
        height: i32,
        border: i32,
        depth: i32,
        visual: *mut std::ffi::c_void,
        root: std::ffi::c_ulong,
        class: i32,
        bit_gravity: i32,
        win_gravity: i32,
        backing_store: i32,
        backing_planes: std::ffi::c_ulong,
        backing_pixel: std::ffi::c_ulong,
        save_under: i32,
        colormap: std::ffi::c_ulong,
        map_installed: i32,
        map_state: i32,
        all_masks: std::ffi::c_long,
        your_mask: std::ffi::c_long,
        do_not_propagate: std::ffi::c_long,
        override_redirect: i32,
        screen: *mut std::ffi::c_void,
    }
    #[repr(C)]
    struct Timespec {
        sec: std::ffi::c_long,
        ns: std::ffi::c_long,
    }
    unsafe extern "C" {
        fn clock_gettime(clock: i32, value: *mut Timespec) -> i32;
        fn XDefaultRootWindow(display: *mut std::ffi::c_void) -> std::ffi::c_ulong;
        fn XQueryTree(
            display: *mut std::ffi::c_void,
            window: std::ffi::c_ulong,
            root: *mut std::ffi::c_ulong,
            parent: *mut std::ffi::c_ulong,
            children: *mut *mut std::ffi::c_ulong,
            count: *mut u32,
        ) -> i32;
        fn XGetWindowProperty(
            display: *mut std::ffi::c_void,
            window: std::ffi::c_ulong,
            property: std::ffi::c_ulong,
            offset: std::ffi::c_long,
            length: std::ffi::c_long,
            delete: i32,
            requested: std::ffi::c_ulong,
            actual: *mut std::ffi::c_ulong,
            format: *mut i32,
            count: *mut std::ffi::c_ulong,
            after: *mut std::ffi::c_ulong,
            data: *mut *mut u8,
        ) -> i32;
        fn XGetWindowAttributes(
            display: *mut std::ffi::c_void,
            window: std::ffi::c_ulong,
            value: *mut Attributes,
        ) -> i32;
        fn XFree(value: *mut std::ffi::c_void) -> i32;
        fn XSetErrorHandler(
            handler: Option<
                unsafe extern "C" fn(*mut std::ffi::c_void, *mut std::ffi::c_void) -> i32,
            >,
        ) -> *mut std::ffi::c_void;
    }
    unsafe extern "C" fn disappearing_window_error(
        _: *mut std::ffi::c_void,
        _: *mut std::ffi::c_void,
    ) -> i32 {
        0
    }
    fn monotonic_ns() -> u64 {
        let mut t = Timespec { sec: 0, ns: 0 };
        assert_eq!(unsafe { clock_gettime(1, &mut t) }, 0);
        t.sec as u64 * 1_000_000_000 + t.ns as u64
    }
    struct Visibility(*mut std::ffi::c_void);
    impl Visibility {
        fn open(display: &str) -> Self {
            let name = std::ffi::CString::new(display).unwrap();
            let d = unsafe { XOpenDisplay(name.as_ptr()) };
            assert!(!d.is_null());
            unsafe {
                XSetErrorHandler(Some(disappearing_window_error));
            }
            Self(d)
        }
        fn visible(&self, pid: u32) -> bool {
            unsafe {
                let mut root = 0;
                let mut parent = 0;
                let mut children = std::ptr::null_mut();
                let mut count = 0;
                if XQueryTree(
                    self.0,
                    XDefaultRootWindow(self.0),
                    &mut root,
                    &mut parent,
                    &mut children,
                    &mut count,
                ) == 0
                {
                    return false;
                }
                let atom = XInternAtom(self.0, c"_NET_WM_PID".as_ptr(), 0);
                let mut visible = false;
                for i in 0..count {
                    let w = *children.add(i as usize);
                    let mut actual = 0;
                    let mut fmt = 0;
                    let mut items = 0;
                    let mut after = 0;
                    let mut data = std::ptr::null_mut();
                    XGetWindowProperty(
                        self.0,
                        w,
                        atom,
                        0,
                        1,
                        0,
                        6,
                        &mut actual,
                        &mut fmt,
                        &mut items,
                        &mut after,
                        &mut data,
                    );
                    if !data.is_null() {
                        if fmt == 32
                            && items == 1
                            && *data.cast::<std::ffi::c_ulong>() == pid as std::ffi::c_ulong
                        {
                            let mut a: Attributes = std::mem::zeroed();
                            if XGetWindowAttributes(self.0, w, &mut a) != 0 {
                                if let Ok(path) = env::var("NARWHAL_GUI_TRACE_VISIBILITY") {
                                    use std::io::Write;
                                    if let Ok(mut file) =
                                        fs::OpenOptions::new().create(true).append(true).open(path)
                                    {
                                        let _ = writeln!(
                                            file,
                                            "{} {} {} {} {}",
                                            monotonic_ns(),
                                            w,
                                            a.width,
                                            a.height,
                                            a.map_state
                                        );
                                    }
                                }
                                if a.map_state == 2 {
                                    visible = true;
                                }
                            }
                        }
                        XFree(data.cast());
                    }
                }
                XFree(children.cast());
                visible
            }
        }
    }
    impl Drop for Visibility {
        fn drop(&mut self) {
            unsafe {
                XCloseDisplay(self.0);
            }
        }
    }
    fn close_reference(case: &Path) -> Option<(u64, bool)> {
        ["close-request.json", "decision.json"]
            .into_iter()
            .filter_map(|f| {
                let v: serde_json::Value =
                    serde_json::from_slice(&fs::read(case.join(f)).ok()?).ok()?;
                Some((
                    v["at_ns"].as_u64()?,
                    v["expect_fast"].as_bool().unwrap_or(false),
                ))
            })
            .max_by_key(|v| v.0)
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
                    runtime
                        .join("5.2/python/lib/python3.13/site-packages")
                        .display(),
                    source.join("vendor/occt").display(),
                    source.display()
                ),
            )
            .stdout(Stdio::from(log.try_clone().unwrap()))
            .stderr(Stdio::from(log))
            .process_group(0);
        let mut child = Owned(command.spawn().expect("start owned candidate"));
        let start = Instant::now();
        let observer = Visibility::open(display);
        let mut seen_visible = false;
        let mut reference = None;
        let mut disappeared = None;
        let status = loop {
            let visible = observer.visible(child.0.id());
            seen_visible |= visible;
            let current = close_reference(&case);
            if current != reference {
                reference = current;
                disappeared = None;
            }
            if seen_visible && reference.is_some() && !visible && disappeared.is_none() {
                disappeared = Some(monotonic_ns());
            }
            if let Some(status) = child.0.try_wait().unwrap() {
                break status;
            }
            assert!(
                start.elapsed() < Duration::from_secs(240),
                "owned case timed out: {name}; see {}",
                case.display()
            );
            thread::sleep(Duration::from_millis(5));
        };
        let text = fs::read_to_string(case.join("result.json")).unwrap_or_else(|_| {
            panic!(
                "no GUI result for {name}, exit={status}; see {}",
                case.join("host.log").display()
            )
        });
        let report: serde_json::Value = serde_json::from_str(&text).unwrap();
        if disappeared.is_none() && reference.is_some() && !observer.visible(child.0.id()) {
            disappeared = Some(monotonic_ns());
        }
        let latency_ms = reference
            .and_then(|(t, _)| disappeared.map(|d| (d.saturating_sub(t)) as f64 / 1_000_000.0));
        let fast = reference.is_some_and(|v| v.1);
        let required_fast = matches!(
            mode,
            "clean" | "discard" | "private-discard" | "updater-discard"
        );
        // Standard Exit must hand control to native shutdown promptly, without
        // document I/O. Physical GPU/window teardown is measured separately;
        // the canceled hidden-runtime design's 250 ms disappearance target is
        // retained as a diagnostic, not misreported as a passing measurement.
        let handoff_ns = report["state"]["native_quit_request_ns"]
            .as_u64()
            .or_else(|| report["state"]["exit_handler_start_ns"].as_u64())
            .or_else(|| report["state"]["exit_observer_ns"].as_u64());
        let handoff_ms = reference
            .and_then(|(t, _)| handoff_ns.map(|h| h.saturating_sub(t) as f64 / 1_000_000.0));
        let latency_ok = seen_visible
            && disappeared.is_some()
            && (!required_fast || fast)
            && (!fast || handoff_ms.is_some_and(|ms| ms < 250.0));
        let latency = serde_json::json!({"seen_visible":seen_visible,"reference":reference,"disappeared_ns":disappeared,"latency_ms":latency_ms,"native_handoff_ms":handoff_ms,"physical_target_250ms_met":latency_ms.is_some_and(|ms| ms < 250.0),"fast_required":fast||required_fast,"gate":"standard_exit_native_handoff_and_no_document_io","ok":latency_ok});
        fs::write(
            case.join("latency.json"),
            serde_json::to_vec_pretty(&latency).unwrap(),
        )
        .unwrap();
        let ok = status.success() && report["ok"] == true && latency_ok;
        if let Some(files) = report["state"]["expected_files"]
            .as_array()
            .filter(|files| ok && !files.is_empty())
        {
            let script = "import sys,json;sys.path.insert(0,sys.argv[1]);from narwhal3d import narwhal_document as doc; rows=json.loads(sys.argv[2]);[(print(p,len(doc.load_model(p).file.Objects)), (_ for _ in ()).throw(AssertionError(p)) if len(doc.load_model(p).file.Objects)!=n else None) for p,n in rows]";
            let verify = Command::new(runtime.join("5.2/python/bin/python3.13"))
                .args(["-B", "-c", script])
                .arg(source)
                .arg(serde_json::to_string(files).unwrap())
                .env(
                    "PYTHONPATH",
                    format!(
                        "{}:{}",
                        source.join("vendor/occt").display(),
                        runtime
                            .join("5.2/python/lib/python3.13/site-packages")
                            .display()
                    ),
                )
                .env("HOME", case.join("home"))
                .env("XDG_CACHE_HOME", case.join("cache"))
                .env("NARWHAL3D_DATA_DIR", case.join("data"))
                .output()
                .unwrap();
            let mut bytes = verify.stdout;
            bytes.extend(verify.stderr);
            fs::write(case.join("verify.log"), &bytes).unwrap();
            assert!(
                verify.status.success(),
                "saved files failed post-window verification: {}",
                String::from_utf8_lossy(&bytes)
            );
        }
        if mutation == "none" {
            assert!(
                ok,
                "GUI case {name} failed, exit={status}, latency={latency}: {text}"
            );
        } else {
            assert!(
                !ok && report["assertion_failure"] == true,
                "mutation must be assertion RED, not a runtime failure: {text}"
            );
        }
        println!("X visibility {name}: {latency}");
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
        let modes=env::var("NARWHAL_GUI_MODES").unwrap_or_else(|_|"default-off,clean,discard,private-discard,save,cancel-dialog,save-as-cancel,pending-save,pending-discard,pending-timeout,latest-edit,save-failure,quit-failure,updater-save,updater-discard,updater-cancel,updater-escape,updater-failure,updater-quit-failure".into());
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
                ("private-discard", "stage-on-discard", "red-private-stage"),
            );
            run_case(
                &candidate,
                &source,
                &runtime,
                &artifacts,
                &display,
                ("private-discard", "none", "restored-private-discard"),
            );
            run_case(
                &candidate,
                &source,
                &runtime,
                &artifacts,
                &display,
                ("discard", "stage-on-discard", "red-stage-on-discard"),
            );
            run_case(
                &candidate,
                &source,
                &runtime,
                &artifacts,
                &display,
                ("discard", "none", "restored-discard"),
            );
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
