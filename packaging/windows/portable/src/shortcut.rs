//! Native Rust publishing of a per-user Windows shortcut (`.lnk`) to the stable
//! launcher, into the per-user Known Folders (Start Menu **Programs** or **Desktop**).
//!
//! OURS — self-contained module. It adds no dependencies and changes no manifest:
//! every COM/Shell symbol it calls is already exposed by the crate's existing
//! `windows-sys` feature set (`Win32_System_Com`, `Win32_UI_Shell`,
//! `Win32_Storage_FileSystem`, `Win32_Foundation`). `IShellLinkW` and `IPersistFile`
//! are not generated as typed structs by `windows-sys`, so they are driven through
//! raw COM vtable calls.
//!
//! The vtable slot indices below are MEASURED from the Windows SDK interface
//! definitions (as generated in the `windows` crate, `crates/libs/windows/src/
//! Windows/Win32/UI/Shell/mod.rs` and `.../System/Com/mod.rs`), not from memory:
//!
//! * `IShellLinkW` (IUnknown occupies 0-2): `GetPath`=3, `GetIDList`=4,
//!   `SetIDList`=5, `GetDescription`=6, `SetDescription`=7, `GetWorkingDirectory`=8,
//!   `SetWorkingDirectory`=9, `GetArguments`=10, `SetArguments`=11, `GetHotkey`=12,
//!   `SetHotkey`=13, `GetShowCmd`=14, `SetShowCmd`=15, `GetIconLocation`=16,
//!   `SetIconLocation`=17, `SetRelativePath`=18, `Resolve`=19, `SetPath`=20.
//! * `IPersistFile` (IUnknown 0-2, then `IPersist::GetClassID`=3): `IsDirty`=4,
//!   `Load`=5, `Save`=6, `SaveCompleted`=7, `GetCurFile`=8.
//!
//! SAFETY CONTRACT — never overwrite an existing file:
//!
//! * The existing target is classified BEFORE anything is written. If it is a file
//!   or a directory (including a shortcut or a junction that merely points elsewhere),
//!   publishing is refused with [`PublishOutcome::SkippedExisting`] and nothing is
//!   created.
//! * The shortcut is STAGED into a uniquely-named temporary file in the same known
//!   folder (same volume, so the final publish is a rename, not a copy).
//! * The staged file is only PUBLISHED with `MoveFileExW` WITHOUT
//!   `MOVEFILE_REPLACE_EXISTING`. The OS therefore guarantees an existing target is
//!   never overwritten; a racing creation between our check and the move fails with
//!   `ERROR_FILE_EXISTS`, is cleaned up, and reported as `SkippedExisting`.
//!
//! The pure decision logic (target classification, launcher validation, naming) is
//! cross-platform and unit-tested on any host; the COM execution is `#[cfg(windows)]`.
//! The outer executable (`main.rs`) is responsible for invoking this and for the
//! managed self-update copy; this module only publishes shortcuts.

use std::io;
use std::path::{Path, PathBuf};

/// Which per-user Known Folder to publish the stable launcher into.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum LinkTarget {
    /// `%USERPROFILE%\Desktop` (per-user, not the public desktop).
    Desktop,
    /// `%APPDATA%\Microsoft\Windows\Start Menu\Programs` (per-user Start Menu).
    Programs,
}

/// The result of a publish attempt. This is the value `main` reports to the user.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum PublishOutcome {
    /// A new `.lnk` was created at `target`, pointing at `launcher`. `staged` names the
    /// temporary file that was used and is now gone (it was moved into `target`).
    Published {
        target: PathBuf,
        launcher: PathBuf,
        staged: PathBuf,
    },
    /// A file or directory already occupied `target`. It was left completely untouched;
    /// nothing was created or replaced.
    SkippedExisting { target: PathBuf },
}

/// Errors that are not simply "the target already exists" (that case is an outcome,
/// not an error). Each variant carries enough to explain itself in a UI string.
#[derive(Debug)]
pub enum ShortcutError {
    /// A `std::io` error surfaced from filesystem or path work.
    Io(io::Error),
    /// The launcher path was empty.
    EmptyLauncher,
    /// The launcher path was relative; a stable shortcut must point at an absolute path.
    RelativeLauncher,
    /// `SHGetKnownFolderPath` failed to resolve the requested per-user folder.
    KnownFolder { target: LinkTarget, code: u32 },
    /// COM could not be initialized on this thread.
    ComInit { code: u32 },
    /// `CoCreateInstance(CLSID_ShellLink, IID_IShellLinkW)` failed.
    ComCreate { code: u32 },
    /// `IShellLinkW::SetPath` failed.
    ComSetPath { code: u32 },
    /// `IShellLinkW::SetDescription` failed.
    ComSetDescription,
    /// `IShellLinkW::SetWorkingDirectory` failed.
    ComSetWorkingDir,
    /// `IShellLinkW::QueryInterface(IID_IPersistFile)` failed.
    ComNoPersistFile { code: u32 },
    /// `IPersistFile::Save` (staging the `.lnk`) failed.
    ComSave { code: u32 },
    /// The staged artifact was not a regular, non-empty file (e.g. a symlink).
    StagedNotRegular { staged: PathBuf },
    /// `MoveFileExW` (no-replace publish) failed for a reason other than "already exists".
    PublishFailed { code: u32 },
    /// The target path existed but could not be inspected, so it must not be touched.
    TargetUnreadable { target: PathBuf },
}

impl std::fmt::Display for ShortcutError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Io(e) => write!(f, "shortcut file operation failed: {e}"),
            Self::EmptyLauncher => write!(f, "no launcher path was supplied"),
            Self::RelativeLauncher => {
                write!(
                    f,
                    "the launcher must be an absolute path for a stable shortcut"
                )
            }
            Self::KnownFolder { target, code } => {
                write!(
                    f,
                    "could not resolve the {target:?} known folder (code {code:#x})"
                )
            }
            Self::ComInit { code } => write!(f, "COM initialization failed (code {code:#x})"),
            Self::ComCreate { code } => {
                write!(f, "could not create the shell-link object (code {code:#x})")
            }
            Self::ComSetPath { code } => write!(
                f,
                "could not set the shortcut target (HRESULT {code:#010x})"
            ),
            Self::ComSetDescription => write!(f, "could not set the shortcut description"),
            Self::ComSetWorkingDir => {
                write!(f, "could not set the shortcut working directory")
            }
            Self::ComNoPersistFile { code } => {
                write!(
                    f,
                    "shell link does not expose IPersistFile (code {code:#x})"
                )
            }
            Self::ComSave { code } => {
                write!(f, "could not write the staged shortcut (code {code:#x})")
            }
            Self::StagedNotRegular { staged } => {
                write!(
                    f,
                    "staged shortcut is not a regular file: {}",
                    staged.display()
                )
            }
            Self::PublishFailed { code } => {
                write!(
                    f,
                    "could not publish the shortcut into place (code {code:#x})"
                )
            }
            Self::TargetUnreadable { target } => {
                write!(
                    f,
                    "the existing shortcut location could not be inspected and was not touched: {}",
                    target.display()
                )
            }
        }
    }
}

impl From<std::io::Error> for ShortcutError {
    fn from(err: std::io::Error) -> Self {
        Self::Io(err)
    }
}

impl std::error::Error for ShortcutError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Io(e) => Some(e),
            _ => None,
        }
    }
}

/// The stable, user-facing shortcut name (without extension). OURS: ASCII-only, matching
/// the crate's packaged-name policy; the `.lnk` extension is added by [`link_path`].
pub const STABLE_NAME: &str = "Narwhal 3D";

/// The final published path for a known folder: `<folder>/Narwhal 3D.lnk`.
pub fn link_path(folder: &Path) -> PathBuf {
    folder.join(format!("{STABLE_NAME}.lnk"))
}

/// A uniquely-named staging file in the SAME folder as the target (so the final
/// publish is a same-volume rename). The name is not the stable name, so a stray staged
/// file can never be mistaken for the published shortcut.
#[cfg_attr(not(windows), allow(dead_code))]
pub(crate) fn stage_name(folder: &Path) -> PathBuf {
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    folder.join(format!("narwhal3d-{}-{nanos}.staging", std::process::id()))
}

/// A stable launcher shortcut must point at an absolute path: a relative target would
/// resolve against whatever working directory the shortcut is later launched from.
#[cfg_attr(not(windows), allow(dead_code))]
pub(crate) fn validate_launcher(launcher: &Path) -> Result<(), ShortcutError> {
    if launcher.as_os_str().is_empty() {
        return Err(ShortcutError::EmptyLauncher);
    }
    if !launcher.is_absolute() {
        return Err(ShortcutError::RelativeLauncher);
    }
    Ok(())
}

/// The fine-grained state of the publish target, inspected via `symlink_metadata` so a
/// symlink/junction is classified by itself, not by what it points at.
// Cross-platform decision logic, exercised by the tests on every host and called
// by the `#[cfg(windows)]` `publish`. Suppressed on non-Windows hosts where it is
// otherwise unreachable, so the module builds warning-free everywhere.
#[cfg_attr(not(windows), allow(dead_code))]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Existing {
    /// Nothing at the path: safe to stage + publish.
    Absent,
    /// A regular file (or symlink/junction to one) occupies the path: never overwrite.
    File,
    /// A directory occupies the path: never overwrite.
    Directory,
    /// The path is present but could not be inspected: refuse rather than risk clobbering.
    Unreadable,
}

/// Pure, cross-platform classification of the target. This is the load-bearing half of
/// the "never overwrite" contract and is what the unit tests pin down on any host.
#[cfg_attr(not(windows), allow(dead_code))]
pub(crate) fn existing_state(target: &Path) -> Existing {
    match std::fs::symlink_metadata(target) {
        Ok(meta) => {
            if meta.is_dir() {
                Existing::Directory
            } else {
                Existing::File
            }
        }
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => Existing::Absent,
        Err(_) => Existing::Unreadable,
    }
}

/// Decide the publish action from the target's state. Returns the skip outcome when a
/// file, directory, or unreadable entry occupies the target (an existing file must never
/// be overwritten); returns `None` only when the target is absent and publishing may
/// proceed.
#[cfg_attr(not(windows), allow(dead_code))]
pub(crate) fn plan(target: &Path) -> Option<PublishOutcome> {
    let skip = PublishOutcome::SkippedExisting {
        target: target.to_path_buf(),
    };
    match existing_state(target) {
        Existing::Absent => None,
        Existing::File | Existing::Directory | Existing::Unreadable => Some(skip),
    }
}

#[cfg(windows)]
fn wide(text: &str) -> Vec<u16> {
    text.encode_utf16().chain(Some(0)).collect()
}

#[cfg(windows)]
fn wide_path(path: &Path) -> Vec<u16> {
    use std::os::windows::ffi::OsStrExt;
    // Encode the path losslessly; `to_string_lossy` would corrupt a non-UTF-8 name.
    path.as_os_str().encode_wide().chain(Some(0)).collect()
}

/// OURS: filesystem canonicalization produces verbatim paths, whereas ShellLink
/// consumes shell/DOS paths. Keep filesystem validation verbatim; convert only
/// at this boundary, losslessly including unpaired UTF-16 surrogates.
fn shell_path(units: &[u16]) -> io::Result<Vec<u16>> {
    let prefix: Vec<u16> = r"\\?\".encode_utf16().collect();
    let unc: Vec<u16> = r"UNC\".encode_utf16().collect();
    let mut result = if let Some(rest) = units.strip_prefix(prefix.as_slice()) {
        if let Some(network) = rest.strip_prefix(unc.as_slice()) {
            let mut path: Vec<u16> = r"\\".encode_utf16().collect();
            path.extend_from_slice(network);
            path
        } else if rest.len() >= 3
            && ((b'A' as u16..=b'Z' as u16).contains(&rest[0])
                || (b'a' as u16..=b'z' as u16).contains(&rest[0]))
            && rest[1] == b':' as u16
            && rest[2] == b'\\' as u16
        {
            rest.to_vec()
        } else {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "shortcut target uses an unsupported Windows device namespace",
            ));
        }
    } else {
        units.to_vec()
    };
    if result.is_empty() || result.contains(&0) {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "empty or NUL-containing shell path",
        ));
    }
    result.push(0);
    Ok(result)
}

#[cfg(windows)]
fn wide_shell_path(path: &Path) -> io::Result<Vec<u16>> {
    use std::os::windows::ffi::OsStrExt;
    shell_path(&path.as_os_str().encode_wide().collect::<Vec<_>>())
}

// Keep the callable ABI available to host tests, without linking Windows APIs.
mod shell_link_abi {
    use core::ffi::c_void;
    pub(super) unsafe fn set_path(obj: *mut c_void, path: &[u16]) -> i32 {
        let vtable = *(obj as *const *const usize);
        let call: unsafe extern "system" fn(*mut c_void, *const u16) -> i32 =
            std::mem::transmute(*vtable.add(20));
        call(obj, path.as_ptr())
    }
}

#[cfg(windows)]
fn text(pointer: *const u16) -> io::Result<PathBuf> {
    use std::os::windows::ffi::OsStringExt;
    if pointer.is_null() {
        return Err(io::Error::other(
            "Windows returned a null known-folder path",
        ));
    }
    let mut len = 0usize;
    while unsafe { *pointer.add(len) } != 0 {
        len += 1;
        if len > 32767 {
            return Err(io::Error::other("known-folder path is too long"));
        }
    }
    Ok(PathBuf::from(std::ffi::OsString::from_wide(unsafe {
        std::slice::from_raw_parts(pointer, len)
    })))
}

/// Resolve the per-user Known Folder for a [`LinkTarget`] (never creates it; the
/// per-user Desktop and Start Menu folders already exist).
#[cfg(windows)]
pub fn known_folder(link: LinkTarget) -> io::Result<PathBuf> {
    use windows_sys::Win32::System::Com::CoTaskMemFree;
    use windows_sys::Win32::UI::Shell::{
        FOLDERID_Desktop, FOLDERID_Programs, SHGetKnownFolderPath,
    };

    let id = match link {
        LinkTarget::Desktop => &FOLDERID_Desktop,
        LinkTarget::Programs => &FOLDERID_Programs,
    };
    unsafe {
        let mut pointer = std::ptr::null_mut();
        let hr = SHGetKnownFolderPath(id, 0, std::ptr::null_mut(), &mut pointer);
        if hr < 0 {
            return Err(io::Error::other(format!(
                "SHGetKnownFolderPath failed: {hr:#x}"
            )));
        }
        let path = text(pointer)?;
        CoTaskMemFree(pointer as *const std::ffi::c_void);
        Ok(path)
    }
}

#[cfg(windows)]
mod com {
    use super::{shell_link_abi::set_path, wide, wide_path, wide_shell_path, ShortcutError};
    use core::ffi::c_void;
    use std::path::Path;
    use windows_sys::core::GUID;
    use windows_sys::Win32::System::Com::{
        CoCreateInstance, CoInitializeEx, CoUninitialize, CLSCTX_INPROC_SERVER,
        COINIT_APARTMENTTHREADED,
    };

    // Standard, well-known COM GUIDs (stable across Windows versions).
    const CLSID_SHELL_LINK: GUID = GUID::from_u128(0x00021401_0000_0000_c000_000000000046);
    const IID_ISHELL_LINK: GUID = GUID::from_u128(0x000214f9_0000_0000_c000_000000000046);
    const IID_IPERSIST_FILE: GUID = GUID::from_u128(0x0000010b_0000_0000_c000_000000000046);

    /// The vtable entry at `index` on a COM object, read as an integer (a function
    /// pointer). A COM vtable's first three slots are the IUnknown methods (QI at 0,
    /// AddRef at 1, Release at 2); the rest follow in interface declaration order.
    /// Reading the slot as a `usize` and then `transmute`-ing it to the specific call
    /// signature is the standard way to obtain a call from a vtable entry — a raw data
    /// pointer cannot be `as`-cast straight to a fn pointer.
    unsafe fn slot(obj: *mut c_void, index: usize) -> usize {
        let vtbl = *(obj as *const *const usize);
        *vtbl.add(index)
    }
    unsafe fn release(obj: *mut c_void) {
        let f: unsafe extern "system" fn(*mut c_void) -> u32 = std::mem::transmute(slot(obj, 2));
        let _ = f(obj);
    }
    unsafe fn query_interface(obj: *mut c_void, iid: &GUID, out: &mut *mut c_void) -> i32 {
        let f: unsafe extern "system" fn(*mut c_void, *const GUID, *mut *mut c_void) -> i32 =
            std::mem::transmute(slot(obj, 0));
        f(obj, iid, out)
    }

    unsafe fn set_description(obj: *mut c_void, d: &Vec<u16>) -> i32 {
        let f: unsafe extern "system" fn(*mut c_void, *const u16) -> i32 =
            std::mem::transmute(slot(obj, 7));
        f(obj, d.as_ptr())
    }
    unsafe fn set_working_dir(obj: *mut c_void, d: &Vec<u16>) -> i32 {
        let f: unsafe extern "system" fn(*mut c_void, *const u16) -> i32 =
            std::mem::transmute(slot(obj, 9));
        f(obj, d.as_ptr())
    }
    unsafe fn persist_save(pf: *mut c_void, p: &Vec<u16>) -> i32 {
        let f: unsafe extern "system" fn(*mut c_void, *const u16, i32) -> i32 =
            std::mem::transmute(slot(pf, 6));
        f(pf, p.as_ptr(), 0)
    }

    #[test]
    fn native_canonical_managed_target_roundtrips_through_shelllink() {
        // Isolate apartment state from the test runner and exercise S_FALSE too.
        std::thread::spawn(|| unsafe {
            let temp = crate::payload_tests::Temp::new();
            let directory = temp.0.join("Managed Narwhal 名字");
            std::fs::create_dir(&directory).unwrap();
            let launcher = directory.join("Narwhal3D.exe");
            std::fs::write(&launcher, b"shortcut target fixture").unwrap();
            let canonical = std::fs::canonicalize(&launcher).unwrap();
            let staged = directory.join("fixture.lnk");
            assert!(CoInitializeEx(std::ptr::null(), COINIT_APARTMENTTHREADED as u32) >= 0);
            build(&staged, &canonical, "Narwhal regression").unwrap();
            build(
                &directory.join("second.lnk"),
                &canonical,
                "Narwhal regression",
            )
            .unwrap();
            let mut link = std::ptr::null_mut();
            assert!(
                CoCreateInstance(
                    &CLSID_SHELL_LINK,
                    std::ptr::null_mut(),
                    CLSCTX_INPROC_SERVER,
                    &IID_ISHELL_LINK,
                    &mut link
                ) >= 0
            );
            let mut persist = std::ptr::null_mut();
            assert!(query_interface(link, &IID_IPERSIST_FILE, &mut persist) >= 0);
            let load: unsafe extern "system" fn(*mut c_void, *const u16, u32) -> i32 =
                std::mem::transmute(slot(persist, 5));
            assert!(load(persist, wide_path(&staged).as_ptr(), 0) >= 0);
            let get_path: unsafe extern "system" fn(
                *mut c_void,
                *mut u16,
                i32,
                *mut c_void,
                u32,
            ) -> i32 = std::mem::transmute(slot(link, 3));
            let mut target = vec![0u16; 32768];
            assert!(
                get_path(
                    link,
                    target.as_mut_ptr(),
                    target.len() as i32,
                    std::ptr::null_mut(),
                    4
                ) >= 0
            );
            target.truncate(target.iter().position(|u| *u == 0).unwrap() + 1);
            assert_eq!(target, wide_shell_path(&canonical).unwrap());
            let get_dir: unsafe extern "system" fn(*mut c_void, *mut u16, i32) -> i32 =
                std::mem::transmute(slot(link, 8));
            let mut working = vec![0u16; 32768];
            assert!(get_dir(link, working.as_mut_ptr(), working.len() as i32) >= 0);
            working.truncate(working.iter().position(|u| *u == 0).unwrap() + 1);
            assert_eq!(
                working,
                wide_shell_path(canonical.parent().unwrap()).unwrap()
            );
            release(persist);
            release(link);
            CoUninitialize();
            // Each successful nested initialization in build must be balanced.
            let hr = CoInitializeEx(
                std::ptr::null(),
                windows_sys::Win32::System::Com::COINIT_MULTITHREADED as u32,
            );
            assert!(hr >= 0, "nested STA initialization leaked: {hr:#x}");
            CoUninitialize();
        })
        .join()
        .unwrap();
    }

    /// Build a shortcut to `launcher` and save it to `staged`, using
    /// `IShellLinkW` (target/description/working directory) and `IPersistFile::Save`.
    pub(super) unsafe fn build(
        staged: &Path,
        launcher: &Path,
        description: &str,
    ) -> Result<(), ShortcutError> {
        // Both S_OK and S_FALSE require a matching CoUninitialize. A negative
        // HRESULT (including RPC_E_CHANGED_MODE) is an initialization failure.
        let hr_init = unsafe { CoInitializeEx(std::ptr::null(), COINIT_APARTMENTTHREADED as u32) };
        let owns_apartment = hr_init >= 0;
        if !owns_apartment {
            return Err(ShortcutError::ComInit {
                code: hr_init as u32,
            });
        }

        let result = (|| -> Result<(), ShortcutError> {
            let mut link: *mut c_void = std::ptr::null_mut();
            let hr = unsafe {
                CoCreateInstance(
                    &CLSID_SHELL_LINK,
                    std::ptr::null_mut(),
                    CLSCTX_INPROC_SERVER,
                    &IID_ISHELL_LINK,
                    &mut link,
                )
            };
            if link.is_null() || hr < 0 {
                return Err(ShortcutError::ComCreate { code: hr as u32 });
            }

            let setup = (|| -> Result<(), ShortcutError> {
                let launcher_w = wide_shell_path(launcher)?;
                let hr_path = unsafe { set_path(link, &launcher_w) };
                if hr_path < 0 {
                    return Err(ShortcutError::ComSetPath {
                        code: hr_path as u32,
                    });
                }
                if !description.is_empty() {
                    let description_w = wide(description);
                    if unsafe { set_description(link, &description_w) } < 0 {
                        return Err(ShortcutError::ComSetDescription);
                    }
                }
                if let Some(parent) = launcher.parent() {
                    if !parent.as_os_str().is_empty() {
                        let parent_w = wide_shell_path(parent)?;
                        if unsafe { set_working_dir(link, &parent_w) } < 0 {
                            return Err(ShortcutError::ComSetWorkingDir);
                        }
                    }
                }

                let mut persist: *mut c_void = std::ptr::null_mut();
                let hr_qi = unsafe { query_interface(link, &IID_IPERSIST_FILE, &mut persist) };
                if persist.is_null() {
                    return Err(ShortcutError::ComNoPersistFile { code: hr_qi as u32 });
                }
                let staged_w = wide_path(staged);
                let hr_save = unsafe { persist_save(persist, &staged_w) };
                unsafe { release(persist) };
                if hr_save < 0 {
                    return Err(ShortcutError::ComSave {
                        code: hr_save as u32,
                    });
                }
                Ok(())
            })();

            unsafe { release(link) };
            setup
        })();

        if owns_apartment {
            unsafe { CoUninitialize() };
        }
        result
    }
}

/// Publish a shortcut to the stable `launcher` into the per-user Known Folder for `link`,
/// honoring the never-overwrite contract. See the module docs for the full safety model.
///
/// `main` is responsible for invoking this and for the managed self-update copy; this
/// function only creates the per-user shortcut.
#[cfg(windows)]
pub fn publish(
    link: LinkTarget,
    launcher: &Path,
    description: &str,
) -> Result<PublishOutcome, ShortcutError> {
    use windows_sys::Win32::Foundation::{GetLastError, ERROR_FILE_EXISTS};
    use windows_sys::Win32::Storage::FileSystem::MoveFileExW;

    validate_launcher(launcher)?;
    let folder = known_folder(link)?;
    let target = link_path(&folder);

    // Never overwrite: classify before writing anything. A present (or unreadable)
    // target is refused here, leaving the existing file completely untouched.
    if let Some(skipped) = plan(&target) {
        return Ok(skipped);
    }

    let staged = stage_name(&folder);
    let best_effort_remove = |path: &Path| {
        let _ = std::fs::remove_file(path);
    };

    if let Err(err) = unsafe { com::build(&staged, launcher, description) } {
        best_effort_remove(&staged);
        return Err(err);
    }

    // Verify the staged artifact is a real, regular, non-empty file before publishing it.
    let meta = std::fs::symlink_metadata(&staged).map_err(ShortcutError::Io)?;
    if meta.file_type().is_symlink() || meta.len() == 0 {
        best_effort_remove(&staged);
        return Err(ShortcutError::StagedNotRegular { staged });
    }

    // Publish no-replace: MOVE without REPLACE_EXISTING. An existing target can therefore
    // never be clobbered; a racing creation fails with ERROR_FILE_EXISTS.
    let moved = unsafe { MoveFileExW(wide_path(&staged).as_ptr(), wide_path(&target).as_ptr(), 0) };
    if moved == 0 {
        let code = unsafe { GetLastError() };
        best_effort_remove(&staged);
        if code == ERROR_FILE_EXISTS {
            return Ok(PublishOutcome::SkippedExisting { target });
        }
        return Err(ShortcutError::PublishFailed { code });
    }

    Ok(PublishOutcome::Published {
        target,
        launcher: launcher.to_path_buf(),
        staged,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn unique_dir(label: &str) -> PathBuf {
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::temp_dir().join(format!(
            "narwhal-shortcut-{label}-{}-{nanos}",
            std::process::id()
        ));
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn utf16(text: &str) -> Vec<u16> {
        text.encode_utf16().collect()
    }
    fn terminated(text: &str) -> Vec<u16> {
        text.encode_utf16().chain(Some(0)).collect()
    }

    #[test]
    fn canonical_managed_drive_path_is_converted_for_shell_not_filesystem() {
        let canonical =
            utf16(r"\\?\C:\Users\Renée\AppData\Local\Narwhal3D-Portable\Install\Narwhal3D.exe");
        let original = canonical.clone();
        assert_eq!(
            shell_path(&canonical).unwrap(),
            terminated(r"C:\Users\Renée\AppData\Local\Narwhal3D-Portable\Install\Narwhal3D.exe")
        );
        assert_eq!(canonical, original);
    }
    #[test]
    fn shell_paths_preserve_unc_and_ordinary_paths() {
        assert_eq!(
            shell_path(&utf16(r"\\?\UNC\server\share\名字\Narwhal3D.exe")).unwrap(),
            terminated(r"\\server\share\名字\Narwhal3D.exe")
        );
        for path in [
            r"C:\Program Files\Narwhal 3D\Narwhal3D.exe",
            r"\\server\share\Narwhal3D.exe",
        ] {
            assert_eq!(shell_path(&utf16(path)).unwrap(), terminated(path));
        }
    }
    #[test]
    fn shell_paths_do_not_lossily_reencode_surrogates_or_accept_device_names() {
        let mut path = utf16(r"\\?\C:\Users\");
        path.push(0xd800);
        path.extend(utf16(r"\Narwhal3D.exe"));
        let mut expected = path[4..].to_vec();
        expected.push(0);
        assert_eq!(shell_path(&path).unwrap(), expected);
        for bad in [
            r"\\?\Volume{abc}\Narwhal3D.exe",
            r"\\?\C:relative",
            "",
            "C:\\bad\0name",
        ] {
            assert!(shell_path(&utf16(bad)).is_err(), "{bad:?}");
        }
    }
    #[test]
    fn set_path_dispatches_slot_twenty_with_this_and_utf16_path() {
        use core::ffi::c_void;
        #[repr(C)]
        struct Fake {
            table: *const usize,
            expected: Vec<u16>,
            calls: usize,
        }
        unsafe extern "system" fn wrong(_: *mut c_void, _: *const u16) -> i32 {
            -1
        }
        unsafe extern "system" fn correct(this: *mut c_void, path: *const u16) -> i32 {
            let fake = &mut *(this as *mut Fake);
            fake.calls += 1;
            if std::slice::from_raw_parts(path, fake.expected.len()) == fake.expected {
                123
            } else {
                -2
            }
        }
        let mut table = [wrong as *const () as usize; 21];
        table[20] = correct as *const () as usize;
        let path = terminated(r"C:\Narwhal 3D\Narwhal3D.exe");
        let mut fake = Fake {
            table: table.as_ptr(),
            expected: path.clone(),
            calls: 0,
        };
        let result =
            unsafe { shell_link_abi::set_path(&mut fake as *mut Fake as *mut c_void, &path) };
        assert_eq!(result, 123);
        assert_eq!(fake.calls, 1);
    }
    #[test]
    fn shortcut_target_error_retains_native_hresult() {
        let message = ShortcutError::ComSetPath { code: 0x80070057 }.to_string();
        assert!(message.contains("0x80070057"));
    }

    #[test]
    fn stable_name_and_link_path_are_deterministic_and_ascii() {
        assert_eq!(STABLE_NAME, "Narwhal 3D");
        assert!(!STABLE_NAME.is_empty());
        assert!(STABLE_NAME.is_ascii());
        let folder = Path::new("/home/user/Desktop");
        assert_eq!(
            link_path(folder),
            Path::new("/home/user/Desktop/Narwhal 3D.lnk")
        );
    }

    #[test]
    fn staged_file_is_same_folder_and_never_the_stable_name() {
        let folder = unique_dir("stage");
        let staged = stage_name(&folder);
        assert_eq!(staged.parent(), Some(folder.as_path()));
        // A stray staged file must not be mistaken for the published shortcut.
        assert_ne!(staged, link_path(&folder));
        fs::remove_dir_all(folder).unwrap();
    }

    #[test]
    fn launcher_must_be_nonempty_and_absolute() {
        assert!(
            matches!(
                validate_launcher(Path::new("")),
                Err(ShortcutError::EmptyLauncher)
            ),
            "an empty launcher must be refused"
        );
        assert!(
            matches!(
                validate_launcher(Path::new("Narwhal3D.exe")),
                Err(ShortcutError::RelativeLauncher)
            ),
            "a relative launcher would resolve against the launch-time CWD"
        );
        let executable = std::env::current_exe().unwrap();
        assert!(
            validate_launcher(&executable).is_ok(),
            "the actual platform-absolute launcher is a stable shortcut target"
        );
        let canonical = fs::canonicalize(&executable).unwrap();
        assert!(
            validate_launcher(&canonical).is_ok(),
            "canonical/verbatim absolute targets must also be accepted"
        );
        #[cfg(unix)]
        assert!(validate_launcher(Path::new("/opt/narwhal/Narwhal3D.exe")).is_ok());
        #[cfg(windows)]
        {
            for absolute in [
                r"C:\Program Files\Narwhal3D.exe",
                r"\\server\share\Narwhal3D.exe",
                r"\\?\C:\Narwhal3D.exe",
            ] {
                assert!(validate_launcher(Path::new(absolute)).is_ok(), "{absolute}");
            }
            for relative in [
                r"\opt\narwhal\Narwhal3D.exe",
                r"/opt/narwhal/Narwhal3D.exe",
                r"C:Narwhal3D.exe",
            ] {
                assert!(matches!(validate_launcher(Path::new(relative)), Err(ShortcutError::RelativeLauncher)),
                    "rooted/drive-relative targets still depend on the launch-time drive/CWD: {relative}");
            }
        }
    }

    #[test]
    fn absent_target_is_the_only_state_that_may_proceed() {
        let dir = unique_dir("plan");
        let absent = dir.join("does-not-exist.lnk");
        assert_eq!(existing_state(&absent), Existing::Absent);
        assert!(
            plan(&absent).is_none(),
            "an absent target must be allowed to proceed"
        );

        let file = dir.join("exists.lnk");
        fs::write(&file, b"existing shortcut bytes").unwrap();
        assert_eq!(existing_state(&file), Existing::File);
        assert!(
            matches!(
                plan(&file),
                Some(PublishOutcome::SkippedExisting { target }) if target == file
            ),
            "an existing file must never be overwritten"
        );

        let sub = dir.join("a-dir");
        fs::create_dir(&sub).unwrap();
        assert_eq!(existing_state(&sub), Existing::Directory);
        assert!(
            matches!(
                plan(&sub),
                Some(PublishOutcome::SkippedExisting { target }) if target == sub
            ),
            "an existing directory must never be overwritten"
        );

        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn error_display_is_actionable_and_mentions_paths() {
        assert!(ShortcutError::RelativeLauncher
            .to_string()
            .contains("absolute"));
        assert!(ShortcutError::TargetUnreadable {
            target: PathBuf::from("/x/y.lnk")
        }
        .to_string()
        .contains("/x/y.lnk"));
    }
}
