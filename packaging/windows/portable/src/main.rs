#![cfg_attr(windows, windows_subsystem = "windows")]
use narwhal_portable_payload::{payload::Payload, Limits};
use std::path::Path;

#[cfg(windows)]
fn run() -> Result<i32, String> {
    use narwhal_portable_payload::cache::Cache;
    use std::process::Command;
    let executable = std::env::current_exe().map_err(|e| e.to_string())?;
    let payload = Payload::open(&executable, Limits::default()).map_err(|e| e.to_string())?;
    if payload.manifest().identity.version != env!("NARWHAL_PORTABLE_VERSION") {
        return Err("payload version differs from native executable resources".into());
    }
    let arguments: Vec<_> = std::env::args_os().skip(1).collect();
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
    if arguments.len() == 2 && arguments[0] == "--portable-finish-update" {
        let message = narwhal_portable_payload::update::finish(
            &executable,
            &payload,
            Path::new(&arguments[1]),
        )
        .map_err(|e| e.to_string())?;
        println!("{message}");
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
    let lease = cache
        .prepare(&payload, Limits::default())
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
    let status = command
        .env("NARWHAL3D_VERSION", &payload.manifest().identity.version)
        .env("NARWHAL3D_WINDOWS", "1")
        .env("NARWHAL3D_PACKAGE_FORMAT", "windows-portable-exe")
        .env("NARWHAL3D_PACKAGE_PATH", &lease.directory)
        .env("NARWHAL3D_PORTABLE_EXE", &executable)
        .env("NARWHAL3D_PORTABLE_PID", std::process::id().to_string())
        .env("PYTHONDONTWRITEBYTECODE", "1")
        .status()
        .map_err(|e| format!("could not start the verified payload: {e}"))?;
    Ok(status.code().unwrap_or(1))
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
