//! Native macOS API experiment, not a production containment implementation.
//! Each worker allocates at most 128 MiB and has a five-second process-group deadline.
//! Apple implementation: xnu/libsyscall/wrappers/spawn/posix_spawn.c and
//! xnu/bsd/kern/kern_exec.c (posix_spawnattr_setjetsam_ext / fatal active+inactive limits).
#[cfg(target_os = "macos")]
mod mac {
    use std::ffi::{c_char, c_int, c_void, CString};
    use std::time::{Duration, Instant};
    type Attr = *mut c_void;
    type SetJetsam = unsafe extern "C" fn(*mut Attr, i16, c_int, c_int, c_int) -> c_int;
    unsafe extern "C" {
        fn dlsym(handle: *mut c_void, symbol: *const c_char) -> *mut c_void;
        fn posix_spawnattr_init(attr: *mut Attr) -> c_int;
        fn posix_spawnattr_destroy(attr: *mut Attr) -> c_int;
        fn posix_spawnattr_setflags(attr: *mut Attr, flags: i16) -> c_int;
        fn posix_spawnattr_setpgroup(attr: *mut Attr, group: c_int) -> c_int;
        fn posix_spawn(
            pid: *mut c_int,
            path: *const c_char,
            actions: *const c_void,
            attr: *const Attr,
            argv: *const *mut c_char,
            env: *const *mut c_char,
        ) -> c_int;
        fn waitpid(pid: c_int, status: *mut c_int, options: c_int) -> c_int;
        fn kill(pid: c_int, signal: c_int) -> c_int;
        fn fork() -> c_int;
        fn execv(path: *const c_char, argv: *const *const c_char) -> c_int;
        fn getuid() -> u32;
        fn _exit(status: c_int) -> !;
    }
    #[link(name = "sandbox")]
    unsafe extern "C" {
        fn sandbox_init(profile: *const c_char, flags: u64, error: *mut *mut c_char) -> c_int;
        fn sandbox_free_error(error: *mut c_char);
    }
    fn sandbox() {
        let profile = CString::new("(version 1)(allow default)(deny process-fork)").unwrap();
        let mut error = std::ptr::null_mut();
        let result = unsafe { sandbox_init(profile.as_ptr(), 0, &mut error) };
        if !error.is_null() {
            unsafe {
                sandbox_free_error(error);
            }
        }
        assert_eq!(result, 0, "cannot deny worker process creation");
    }
    fn sandbox_worker(mode: &str) -> ! {
        sandbox();
        if mode == "--sandbox-fork" {
            let pid = unsafe { fork() };
            if pid < 0 {
                unsafe {
                    _exit(
                        if std::io::Error::last_os_error().raw_os_error() == Some(1) {
                            0
                        } else {
                            4
                        },
                    )
                }
            }
            if pid == 0 {
                unsafe { _exit(7) }
            }
            let mut status = 0;
            unsafe {
                waitpid(pid, &mut status, 0);
                _exit(5)
            }
        }
        if mode == "--sandbox-exec-allocate" {
            let exe = CString::new(
                std::env::current_exe()
                    .unwrap()
                    .as_os_str()
                    .as_encoded_bytes(),
            )
            .unwrap();
            let arg = CString::new("--allocate").unwrap();
            let argv = [exe.as_ptr(), arg.as_ptr(), std::ptr::null()];
            unsafe {
                execv(exe.as_ptr(), argv.as_ptr());
                _exit(6)
            }
        }
        worker(false)
    }
    fn allocate() {
        let mut bytes = vec![0u8; 128 * 1024 * 1024];
        for i in (0..bytes.len()).step_by(4096) {
            unsafe {
                std::ptr::write_volatile(bytes.as_mut_ptr().add(i), 0x5a);
            }
        }
        std::thread::sleep(Duration::from_millis(200));
        std::hint::black_box(&bytes);
    }
    fn worker(forked: bool) -> ! {
        if forked {
            let pid = unsafe { fork() };
            if pid < 0 {
                unsafe { _exit(2) }
            }
            if pid > 0 {
                let mut status = 0;
                if unsafe { waitpid(pid, &mut status, 0) } != pid {
                    unsafe { _exit(3) }
                }
                let signal = status & 0x7f;
                unsafe {
                    _exit(if signal != 0 {
                        128 + signal
                    } else {
                        (status >> 8) & 0xff
                    })
                }
            }
        }
        allocate();
        unsafe { _exit(0) }
    }
    fn run(set: SetJetsam, limit: Option<i32>, mode: &str) -> Result<i32, String> {
        let exe = CString::new(
            std::env::current_exe()
                .map_err(|e| e.to_string())?
                .as_os_str()
                .as_encoded_bytes(),
        )
        .unwrap();
        let arg = CString::new(mode).unwrap();
        let argv = [
            exe.as_ptr() as *mut c_char,
            arg.as_ptr() as *mut c_char,
            std::ptr::null_mut(),
        ];
        let env = [std::ptr::null_mut()];
        let mut attr: Attr = std::ptr::null_mut();
        let init = unsafe { posix_spawnattr_init(&mut attr) };
        if init != 0 {
            return Err(format!("attr init: {init}"));
        }
        let result = (|| {
            for code in [
                unsafe { posix_spawnattr_setflags(&mut attr, 0x0002) },
                unsafe { posix_spawnattr_setpgroup(&mut attr, 0) },
            ] {
                if code != 0 {
                    return Err(format!("group config: {code}"));
                }
            }
            if let Some(limit) = limit {
                // Fatal memory limits for both active and inactive states; setter adds JETSAM_SET.
                let code = unsafe { set(&mut attr, 0x000c, 0, limit, limit) };
                if code != 0 {
                    return Err(format!("jetsam config: {code}"));
                }
            }
            let mut pid = 0;
            let code = unsafe {
                posix_spawn(
                    &mut pid,
                    exe.as_ptr(),
                    std::ptr::null(),
                    &attr,
                    argv.as_ptr(),
                    env.as_ptr(),
                )
            };
            if code != 0 {
                return Err(format!("spawn: {code}"));
            }
            let start = Instant::now();
            let mut status = 0;
            loop {
                let waited = unsafe { waitpid(pid, &mut status, 1) };
                if waited == pid {
                    return Ok(status);
                }
                if waited < 0 || start.elapsed() > Duration::from_secs(5) {
                    unsafe {
                        kill(-pid, 9);
                        waitpid(pid, &mut status, 0);
                    }
                    return Err("worker wait failed or timed out".into());
                }
                std::thread::sleep(Duration::from_millis(10));
            }
        })();
        unsafe {
            posix_spawnattr_destroy(&mut attr);
        }
        result
    }
    pub fn main() {
        match std::env::args().nth(1).as_deref() {
            Some("--allocate") => worker(false),
            Some("--fork-allocate") => worker(true),
            Some("--empty") => unsafe { _exit(0) },
            Some(mode @ ("--sandbox-allocate" | "--sandbox-fork" | "--sandbox-exec-allocate")) => {
                sandbox_worker(mode)
            }
            _ => {}
        }
        assert_ne!(
            unsafe { getuid() },
            0,
            "must prove ordinary-user containment"
        );
        let symbol = CString::new("posix_spawnattr_setjetsam_ext").unwrap();
        let address = unsafe { dlsym(-2isize as *mut c_void, symbol.as_ptr()) }; // RTLD_DEFAULT on Darwin.
        assert!(!address.is_null(), "jetsam spawn API unavailable");
        let setter: SetJetsam = unsafe { std::mem::transmute(address) };
        let mut passed = true;
        if std::env::args().nth(1).as_deref() == Some("--sandbox-suite") {
            // Raw jetsam fork inheritance failed on native macOS. Keep that baseline
            // experiment below unchanged; test the alternative no-fork contract here.
            for (name, limit, mode, expected) in [
                ("sandbox_below_limit", 768, "--sandbox-allocate", 0),
                ("sandbox_over_limit", 64, "--sandbox-allocate", 9),
                ("sandbox_denies_fork", 64, "--sandbox-fork", 0),
                (
                    "sandbox_exec_preserves_limit",
                    64,
                    "--sandbox-exec-allocate",
                    9,
                ),
            ] {
                let status = run(setter, Some(limit), mode);
                let ok = status.as_ref().is_ok_and(|raw| *raw == expected);
                println!("{name}: result={status:?}; expected_status={expected}; passed={ok}");
                passed &= ok;
            }
            assert!(passed, "sandbox strategy not qualified");
            return;
        }
        for (name, limit, mode, expected) in [
            ("no_limit_control", None, "--allocate", 0),
            ("below_limit", Some(768), "--allocate", 0),
            ("empty_small_limit", Some(64), "--empty", 0),
            ("over_limit", Some(64), "--allocate", 9),
            ("fork_inherits_limit", Some(64), "--fork-allocate", 137 << 8),
        ] {
            let status = run(setter, limit, mode);
            let ok = status.as_ref().is_ok_and(|raw| *raw == expected);
            println!("{name}: result={status:?}; expected_status={expected}; passed={ok}");
            passed &= ok;
        }
        assert!(
            passed,
            "native memory strategy not qualified; do not remove existing containment"
        );
    }
}
#[cfg(target_os = "macos")]
fn main() {
    mac::main();
}
#[cfg(not(target_os = "macos"))]
fn main() {
    eprintln!("This experiment requires native macOS.");
    std::process::exit(2);
}
