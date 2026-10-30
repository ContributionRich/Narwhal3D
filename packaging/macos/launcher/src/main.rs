use std::path::{Path, PathBuf};

#[derive(Debug)]
struct Layout {
    bundle: PathBuf,
    app: PathBuf,
    runtime: PathBuf,
    welcome: PathBuf,
}

fn layout(executable: &Path) -> Result<Layout, &'static str> {
    let macos = executable.parent().ok_or("launcher has no directory")?;
    let contents = macos.parent().ok_or("launcher has no Contents directory")?;
    let bundle = contents
        .parent()
        .ok_or("launcher has no application bundle")?;
    if executable.file_name().and_then(|s| s.to_str()) != Some("Narwhal3D")
        || macos.file_name().and_then(|s| s.to_str()) != Some("MacOS")
        || contents.file_name().and_then(|s| s.to_str()) != Some("Contents")
        || bundle.extension().and_then(|s| s.to_str()) != Some("app")
    {
        return Err("Keep Narwhal 3D.app intact; the launcher cannot run outside its bundle.");
    }
    let app = contents.join("Resources/app");
    Ok(Layout {
        bundle: bundle.to_owned(),
        runtime: contents.join("Resources/runtime/Blender.app/Contents/MacOS/Blender"),
        welcome: app.join("runtime/macos-arm64/bin/narwhal-welcome"),
        app,
    })
}

fn required_file(bundle: &Path, path: &Path) -> Result<(), String> {
    let resolved = path
        .canonicalize()
        .map_err(|e| format!("Missing {}: {e}", path.display()))?;
    if !resolved.starts_with(bundle) || !resolved.is_file() {
        return Err(format!(
            "Bundled file escapes the application: {}",
            path.display()
        ));
    }
    Ok(())
}

#[cfg(target_os = "macos")]
fn launch() -> Result<(), String> {
    use std::os::unix::process::CommandExt;
    use std::process::Command;
    let executable = std::env::current_exe()
        .map_err(|e| e.to_string())?
        .canonicalize()
        .map_err(|e| e.to_string())?;
    let paths = layout(&executable).map_err(str::to_owned)?;
    let bootstrap = paths.app.join("launch/bootstrap.py");
    for path in [&paths.runtime, &paths.welcome, &bootstrap] {
        required_file(&paths.bundle, path)?;
    }
    let native = paths.app.join("runtime/macos-arm64/bin");
    let mode = std::env::var("NARWHAL3D_LAUNCHER_TEST").unwrap_or_default();
    let mut command = if mode == "version" || mode == "headless" {
        let mut command = Command::new(&paths.runtime);
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
                .arg(&bootstrap);
        }
        command
    } else {
        let mut command = Command::new(&paths.welcome);
        command
            .arg("--runtime")
            .arg(&paths.runtime)
            .arg("--bootstrap")
            .arg(&bootstrap)
            .arg("--")
            .args(
                std::env::args_os()
                    .skip(1)
                    .filter(|arg| !arg.to_string_lossy().starts_with("-psn_")),
            );
        command
    };
    for (variable, name) in [
        ("NARWHAL_PURE_CORE", "libnarwhal_pure_core.dylib"),
        ("NARWHAL_SELECTION_CORE", "libnarwhal_selection_core.dylib"),
        ("NARWHAL_CDR_CORE", "libnarwhal_cdr_core.dylib"),
        ("NARWHAL_GEOMETRY_CORE", "libnarwhal_geometry_core.dylib"),
        ("NARWHAL_PLATFORM_CORE", "libnarwhal_platform_core.dylib"),
        ("NARWHAL_DOCUMENT_CORE", "narwhal-document-core"),
    ] {
        let path = native.join(name);
        required_file(&paths.bundle, &path)?;
        command.env(variable, path);
    }
    let mut search = vec![native];
    if let Some(old) = std::env::var_os("PATH") {
        search.extend(std::env::split_paths(&old));
    }
    command
        .env(
            "PATH",
            std::env::join_paths(search).map_err(|e| e.to_string())?,
        )
        .env("NARWHAL3D_VERSION", env!("CARGO_PKG_VERSION"))
        .env("NARWHAL3D_PACKAGE_FORMAT", "macos-app")
        .env("NARWHAL3D_PACKAGE_PATH", &paths.bundle)
        .env("PYTHONDONTWRITEBYTECODE", "1")
        .env("NARWHAL3D_DEFER_FIRST_PRESENT", "1")
        .env("NARWHAL3D_STATUSBAR_HEIGHT", "37")
        .env_remove("NARWHAL3D_UI_READY")
        .env_remove("NARWHAL3D_PRESENT_WATCHDOG");
    Err(format!(
        "Could not start the bundled application: {}",
        command.exec()
    ))
}

fn main() {
    #[cfg(target_os = "macos")]
    if let Err(error) = launch() {
        eprintln!("Narwhal 3D: {error}");
        let _ = std::process::Command::new("/usr/bin/osascript")
            .args([
                "-e",
                "on run argv",
                "-e",
                "display alert \"Narwhal 3D could not start\" message (item 1 of argv) as critical",
                "-e",
                "end run",
                "--",
            ])
            .arg(error)
            .status();
        std::process::exit(1);
    }
    #[cfg(not(target_os = "macos"))]
    {
        eprintln!("This launcher requires macOS ARM64.");
        std::process::exit(2);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn relocated_bundle_uses_only_its_own_payload() {
        let root = Path::new("/Volumes/External Drive/日本語/Narwhal 3D.app");
        let paths = layout(&root.join("Contents/MacOS/Narwhal3D")).unwrap();
        assert_eq!(paths.bundle, root);
        assert_eq!(paths.app, root.join("Contents/Resources/app"));
        assert_eq!(
            paths.runtime,
            root.join("Contents/Resources/runtime/Blender.app/Contents/MacOS/Blender")
        );
        assert_eq!(
            paths.welcome,
            paths.app.join("runtime/macos-arm64/bin/narwhal-welcome")
        );
    }
    #[test]
    fn loose_or_misplaced_executable_is_rejected() {
        for path in [
            "/tmp/Narwhal3D",
            "/tmp/no-bundle/Contents/MacOS/Narwhal3D",
            "/tmp/App.app/MacOS/Narwhal3D",
        ] {
            assert!(layout(Path::new(path)).is_err(), "{path}");
        }
    }
    #[cfg(unix)]
    #[test]
    fn links_cannot_escape_the_bundle() {
        let root = std::env::temp_dir().join(format!("narwhal-mac-launch-{}", std::process::id()));
        std::fs::create_dir_all(root.join("App.app")).unwrap();
        std::fs::write(root.join("outside"), b"not bundled").unwrap();
        std::os::unix::fs::symlink("../outside", root.join("App.app/escape")).unwrap();
        assert!(required_file(&root.join("App.app"), &root.join("App.app/escape")).is_err());
        std::fs::write(root.join("App.app/inside"), b"bundled").unwrap();
        required_file(&root.join("App.app"), &root.join("App.app/inside")).unwrap();
        std::fs::remove_dir_all(root).unwrap();
    }
}
