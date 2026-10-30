//! The macOS document-worker launch contract. Not a generic converter sandbox.
use std::path::{Path, PathBuf};

pub const LIMIT_ENV: &str = "NARWHAL_MAC_DOCUMENT_LIMIT_MIB";

/// Only the packaged, cooperating document worker may use macOS memory limits.
/// Executable and script are trusted package inputs, never document-supplied code.
pub fn validate_document_worker(supervisor: &Path, program: &Path, args: &[String]) -> Result<PathBuf, String> {
    let supervisor = supervisor.canonicalize().map_err(|e| e.to_string())?;
    let bin = supervisor.parent().ok_or("missing supervisor directory")?;
    let app = bin.ancestors().nth(3).ok_or("missing application root")?;
    if bin.strip_prefix(app).ok() != Some(Path::new("runtime/macos-arm64/bin")) {
        return Err("macOS memory limits require the installed document-worker contract".into());
    }
    let script = app.join("narwhal3d/document_worker.py").canonicalize().map_err(|e| e.to_string())?;
    if !script.starts_with(app) { return Err("document worker escapes its package".into()); }
    let runtime = app.parent().ok_or("missing Resources directory")?.join("runtime/Blender.app").canonicalize().map_err(|e| e.to_string())?;
    let program = program.canonicalize().map_err(|e| e.to_string())?;
    let script_arg = if program == runtime.join("Contents/MacOS/Blender") {
        if args.len() != 8 || args[..5] != ["-b", "--factory-startup", "--python-exit-code", "1", "--python"] || args[6] != "--" {
            return Err("unsupported macOS document-worker arguments".into());
        }
        &args[5]
    } else if program.starts_with(runtime.join("Contents/Resources")) && program.ends_with("python/bin/python3.13") {
        if args.len() != 3 || args[1] != "--" { return Err("unsupported Python worker arguments".into()); }
        &args[0]
    } else {
        return Err("memory-limited arbitrary converters are not implemented on macOS".into());
    };
    if Path::new(script_arg).canonicalize().map_err(|e| e.to_string())? != script {
        return Err("only the packaged document worker may use this memory contract".into());
    }
    Ok(program)
}

#[cfg(target_os = "macos")]
pub mod macos {
    use std::ffi::{c_char, c_int, c_void, CString};
    use std::path::Path;
    type SetJetsam = unsafe extern "C" fn(*mut libc::posix_spawnattr_t, i16, c_int, c_int, c_int) -> c_int;
    #[link(name = "sandbox")]
    extern "C" {
        fn sandbox_init(profile: *const c_char, flags: u64, error: *mut *mut c_char) -> c_int;
        fn sandbox_free_error(error: *mut c_char);
    }
    /// Replace the trusted bootstrap in-place. The cap is applied to the FINAL
    /// executable: a subsequent ordinary exec would otherwise reset it on Darwin.
    pub fn exec_limited(supervisor: &Path, program: &Path, args: &[String], limit: u64) -> Result<(), String> {
        let program = super::validate_document_worker(supervisor, program, args)?;
        if !(1..=1024 * 1024).contains(&limit) { return Err("invalid worker memory limit".into()); }
        std::env::set_var(super::LIMIT_ENV, limit.to_string());
        let strings: Vec<CString> = std::iter::once(program.to_string_lossy().into_owned())
            .chain(args.iter().cloned()).map(|s| CString::new(s).map_err(|_| "NUL in worker argument".to_owned())).collect::<Result<_,_>>()?;
        let argv: Vec<*mut c_char> = strings.iter().map(|s| s.as_ptr() as *mut c_char).chain(std::iter::once(std::ptr::null_mut())).collect();
        let env_strings: Vec<CString> = std::env::vars().map(|(k,v)| CString::new(format!("{k}={v}")).map_err(|_| "NUL in worker environment".to_owned())).collect::<Result<_,_>>()?;
        let env: Vec<*mut c_char> = env_strings.iter().map(|s| s.as_ptr() as *mut c_char).chain(std::iter::once(std::ptr::null_mut())).collect();
        let name = CString::new("posix_spawnattr_setjetsam_ext").unwrap();
        // Availability is checked explicitly; no unbounded fallback.
        let symbol = unsafe { libc::dlsym(libc::RTLD_DEFAULT, name.as_ptr()) };
        if symbol.is_null() { return Err("macOS physical-memory limit API unavailable".into()); }
        let set: SetJetsam = unsafe { std::mem::transmute::<*mut c_void, SetJetsam>(symbol) };
        let mut attr = unsafe { std::mem::zeroed() };
        let code = unsafe { libc::posix_spawnattr_init(&mut attr) };
        if code != 0 { return Err(format!("spawn attributes: {code}")); }
        let result = (|| {
            for code in [unsafe { libc::posix_spawnattr_setflags(&mut attr, libc::POSIX_SPAWN_SETEXEC as i16) },
                         unsafe { set(&mut attr, 0x000c, 0, limit as i32, limit as i32) }] {
                if code != 0 { return Err(format!("worker memory attributes: {code}")); }
            }
            let mut pid = 0;
            let code = unsafe { libc::posix_spawn(&mut pid, strings[0].as_ptr(), std::ptr::null(), &attr, argv.as_ptr(), env.as_ptr()) };
            Err(format!("limited worker exec failed: {code}"))
        })();
        unsafe { libc::posix_spawnattr_destroy(&mut attr); }
        result
    }

    pub fn seal() -> Result<(), String> {
        let limit = std::env::var(super::LIMIT_ENV).ok().and_then(|s| s.parse::<u64>().ok());
        if !limit.is_some_and(|v| (1..=1024 * 1024).contains(&v)) {
            return Err("document worker was not launched through the memory supervisor".into());
        }
        let profile = CString::new("(version 1)(allow default)(deny process-fork)(deny process-exec)").unwrap();
        let mut error = std::ptr::null_mut();
        let code = unsafe { sandbox_init(profile.as_ptr(), 0, &mut error) };
        if !error.is_null() { unsafe { sandbox_free_error(error); } }
        if code != 0 { return Err("cannot seal document-worker process creation".into()); }
        Ok(())
    }
}

/// Called by the trusted Python/Blender worker before reading any model data.
/// Returns nonzero on unsupported platforms or missing/failed launch containment.
#[no_mangle]
pub extern "C" fn narwhal_seal_document_worker() -> i32 {
    #[cfg(target_os = "macos")]
    { if macos::seal().is_ok() { 0 } else { -1 } }
    #[cfg(not(target_os = "macos"))]
    { -1 }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn worker_contract_rejects_external_programs_scripts_and_extra_flags() {
        let root = std::env::temp_dir().join(format!("nw-worker-contract-{}", std::process::id()));
        let app = root.join("app");
        let supervisor = app.join("runtime/macos-arm64/bin/narwhal-converter-supervisor");
        let program = root.join("runtime/Blender.app/Contents/MacOS/Blender");
        let script = app.join("narwhal3d/document_worker.py");
        for p in [&supervisor, &program, &script] { std::fs::create_dir_all(p.parent().unwrap()).unwrap(); std::fs::write(p, b"fixture").unwrap(); }
        let args: Vec<String> = ["-b", "--factory-startup", "--python-exit-code", "1", "--python", script.to_str().unwrap(), "--", "/model"].into_iter().map(str::to_owned).collect();
        assert_eq!(validate_document_worker(&supervisor, &program, &args).unwrap(), program.canonicalize().unwrap());
        let mut extra = args.clone(); extra.push("--python-expr=unsafe".into());
        assert!(validate_document_worker(&supervisor, &program, &extra).is_err());
        let mut other = args.clone(); other[5] = program.to_string_lossy().into();
        assert!(validate_document_worker(&supervisor, &program, &other).is_err());
        assert!(validate_document_worker(&supervisor, &script, &args).is_err());
        std::fs::remove_dir_all(root).unwrap();
    }
}
