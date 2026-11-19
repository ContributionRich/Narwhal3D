#![cfg_attr(windows, windows_subsystem = "windows")]
use narwhal_portable_payload::{payload::Payload, Limits};
use std::path::Path;

#[cfg(windows)]
mod startup;

#[cfg(windows)]
fn run() -> Result<i32, String> {
    use narwhal_portable_payload::cache::Cache;
    use std::process::Command;
    let mut arguments: Vec<_> = std::env::args_os().skip(1).collect();
    let internal = arguments.first().is_some_and(|arg| {
        [
            "--portable-verify",
            "--portable-check-cache",
            "--portable-schedule-update",
            "--portable-finish-update",
        ]
        .iter()
        .any(|flag| arg == *flag)
    });
    let startup = (!internal && std::env::var_os("NARWHAL3D_LAUNCHER_TEST").is_none())
        .then(startup::Startup::show);
    let stage = |text| {
        if let Some(window) = &startup {
            window.stage(text);
        }
    };
    let executable = std::env::current_exe().map_err(|e| e.to_string())?;
    let payload = Payload::open(&executable, Limits::default()).map_err(|e| e.to_string())?;
    if payload.manifest().identity.version != env!("NARWHAL_PORTABLE_VERSION") {
        return Err("payload version differs from native executable resources".into());
    }

    if arguments.len() == 4 && arguments[0] == "--portable-schedule-update" {
        let hash = arguments[2].to_str().ok_or("invalid update hash")?;
        let pid = arguments[3]
            .to_str()
            .and_then(|s| s.parse::<u32>().ok())
            .ok_or("invalid launcher process")?;
        let message = narwhal_portable_payload::update::schedule(
            &executable,
            &payload,
            Path::new(&arguments[1]),
            hash,
            pid,
        )
        .map_err(|e| e.to_string())?;
        println!("{message}");
        return Ok(0);
    }
    if matches!(arguments.len(), 2 | 3) && arguments[0] == "--portable-finish-update" {
        let message = narwhal_portable_payload::update::finish(
            &executable,
            &payload,
            Path::new(&arguments[1]),
            arguments.get(2).is_some_and(|arg| arg == "--restart"),
        )
        .map_err(|e| e.to_string())?;
        println!("{message}");
        notice("Narwhal 3D update complete", &message);
        return Ok(0);
    }
    if arguments.first().is_some_and(|s| s == "--portable-verify") {
        payload
            .verify_contents(Limits::default())
            .map_err(|e| e.to_string())?;
        println!(
            "{}",
            serde_json::to_string(payload.manifest()).map_err(|e| e.to_string())?
        );
        return Ok(0);
    }
    let cache = Cache::current_user().map_err(|e| e.to_string())?;
    if !internal {
        stage("Checking interrupted updates…");
        for message in narwhal_portable_payload::update::recover_startup(
            &executable,
            &payload.manifest().identity.version,
        )
        .map_err(|e| format!("Could not safely recover an interrupted update: {e}"))?
        {
            notice("Narwhal 3D update recovery", &message);
        }
    }
    if startup.is_some() && !arguments.first().is_some_and(|a| a == "--portable") {
        stage("Checking the per-user installation…");
        let installed = narwhal_portable_payload::install::managed(
            &cache,
            &executable,
            &payload.manifest().identity.version,
        )
        .map_err(|e| format!("Could not prepare the per-user launcher: {e}"))?;
        if std::fs::canonicalize(&executable).map_err(|e| e.to_string())?
            != std::fs::canonicalize(&installed).map_err(|e| e.to_string())?
        {
            let mut child = Command::new(installed)
                .args(&arguments)
                .spawn()
                .map_err(|e| e.to_string())?;
            drop(startup);
            return child
                .wait()
                .map(|s| s.code().unwrap_or(1))
                .map_err(|e| e.to_string());
        }
        stage("Registering shortcuts…");
        if let Err(error) = narwhal_portable_payload::install::migrate(&cache, &installed) {
            notice("Narwhal 3D shortcuts", &format!("Could not register all shortcuts: {error}. Narwhal can still start; shortcut registration will retry next launch."));
        }
    }
    if arguments.first().is_some_and(|a| a == "--portable") {
        arguments.remove(0);
    }
    let lease = cache
        .prepare_with_progress(&payload, Limits::default(), stage)
        .map_err(|e| e.to_string())?;
    if arguments
        .first()
        .is_some_and(|s| s == "--portable-check-cache")
    {
        println!(
            "{}",
            serde_json::json!({"version": payload.manifest().identity.version,
            "reused": lease.reused, "root": lease.directory})
        );
        return Ok(0);
    }
    let runtime = lease.directory.join("runtime/blender.exe");
    let bootstrap = lease.directory.join("app/launch/bootstrap.py");
    let welcome = lease
        .directory
        .join("app/runtime/windows-x86_64/bin/narwhal-welcome.exe");
    let mode = std::env::var("NARWHAL3D_LAUNCHER_TEST").unwrap_or_default();
    let mut command = if mode == "version" || mode == "headless" {
        let mut command = Command::new(runtime);
        if mode == "version" {
            command.arg("--version");
        } else {
            command
                .args([
                    "-b",
                    "--factory-startup",
                    "--python-exit-code",
                    "1",
                    "--python",
                ])
                .arg(bootstrap);
        }
        command
    } else {
        let mut command = Command::new(welcome);
        command
            .arg("--runtime")
            .arg(runtime)
            .arg("--bootstrap")
            .arg(bootstrap)
            .arg("--")
            .args(&arguments);
        command
    };
    // Keep caller CWD and OS strings: relative/Unicode document arguments survive.
    // Welcome supervises the runtime; the cache lease stays held until it exits.
    stage("Starting Narwhal 3D…");
    let mut child = command
        .env("NARWHAL3D_VERSION", &payload.manifest().identity.version)
        .env("NARWHAL3D_WINDOWS", "1")
        .env("NARWHAL3D_PACKAGE_FORMAT", "windows-portable-exe")
        .env("NARWHAL3D_PACKAGE_PATH", &lease.directory)
        .env("NARWHAL3D_PORTABLE_EXE", &executable)
        .env("NARWHAL3D_PORTABLE_PID", std::process::id().to_string())
        .env("PYTHONDONTWRITEBYTECODE", "1")
        .spawn()
        .map_err(|e| format!("could not start the verified payload: {e}"))?;
    drop(startup);
    let status = child
        .wait()
        .map_err(|e| format!("could not wait for the application: {e}"))?;
    let restart = status.code() == Some(75);
    if status.success() || restart {
        narwhal_portable_payload::update::apply_after_session(&executable, restart)
            .map_err(|e| format!("Could not apply staged update: {e}"))?;
    }
    Ok(if restart {
        0
    } else {
        status.code().unwrap_or(1)
    })
}

#[cfg(windows)]
fn notice(title: &str, text: &str) {
    let title: Vec<_> = title.encode_utf16().chain(Some(0)).collect();
    let text: Vec<_> = text.encode_utf16().chain(Some(0)).collect();
    unsafe {
        windows_sys::Win32::UI::WindowsAndMessaging::MessageBoxW(
            std::ptr::null_mut(),
            text.as_ptr(),
            title.as_ptr(),
            0x40,
        );
    }
}

fn main() {
    // Offline verification is also available on Linux, without launching payloads.
    let args: Vec<_> = std::env::args_os().collect();
    if args.len() == 7 && args[1] == "--pack" {
        let result = args[5]
            .to_str()
            .zip(args[6].to_str())
            .ok_or_else(|| std::io::Error::other("version/source must be Unicode"))
            .and_then(|(version, source)| {
                narwhal_portable_payload::pack::assemble(
                    Path::new(&args[2]),
                    Path::new(&args[3]),
                    Path::new(&args[4]),
                    version,
                    source,
                )
            });
        match result {
            Ok(manifest) => println!(
                "{}",
                serde_json::json!({"version":manifest.identity.version,
                "source_revision":manifest.identity.source_revision,"files":manifest.files.len(),
                "archive_size":manifest.archive_size})
            ),
            Err(error) => {
                eprintln!("portable assembly: {error}");
                std::process::exit(2);
            }
        }
        return;
    }
    if args.len() == 4 && args[1] == "--extract-for-verification" {
        let result = (|| -> std::io::Result<()> {
            let payload = Payload::open(Path::new(&args[2]), Limits::default())?;
            let destination = Path::new(&args[3]);
            std::fs::create_dir(destination)?;
            payload.extract(destination, Limits::default())?;
            payload.verify_tree(destination)?;
            println!(
                "{}",
                serde_json::json!({"version": payload.manifest().identity.version,
                "source_revision": payload.manifest().identity.source_revision,
                "files": payload.manifest().files.len()})
            );
            Ok(())
        })();
        if let Err(error) = result {
            eprintln!("portable verification extraction: {error}");
            std::process::exit(2);
        }
        return;
    }
    if args.len() == 3 && args[1] == "--inspect" {
        match Payload::open(Path::new(&args[2]), Limits::default()) {
            Ok(p) => {
                if let Err(error) = p.verify_contents(Limits::default()) {
                    eprintln!("portable payload: {error}");
                    std::process::exit(2);
                }
                println!("{}", serde_json::to_string(p.manifest()).unwrap());
                return;
            }
            Err(e) => {
                eprintln!("portable payload: {e}");
                std::process::exit(2);
            }
        }
    }
    #[cfg(windows)]
    {
        match run() {
            Ok(code) => std::process::exit(code),
            Err(error) => {
                eprintln!("Narwhal 3D portable: {error}");
                if !args.iter().any(|s| {
                    s == "--portable-verify"
                        || s == "--portable-check-cache"
                        || s == "--portable-schedule-update"
                }) {
                    let message: Vec<_> = error.encode_utf16().chain(Some(0)).collect();
                    let title: Vec<_> = "Narwhal 3D could not start"
                        .encode_utf16()
                        .chain(Some(0))
                        .collect();
                    unsafe {
                        windows_sys::Win32::UI::WindowsAndMessaging::MessageBoxW(
                            std::ptr::null_mut(),
                            message.as_ptr(),
                            title.as_ptr(),
                            0x10,
                        );
                    }
                }
                std::process::exit(2);
            }
        }
    }
    #[cfg(not(windows))]
    {
        eprintln!("This host supports offline packing, inspection and verification extraction; application launch requires Windows.");
        std::process::exit(2);
    }
}
