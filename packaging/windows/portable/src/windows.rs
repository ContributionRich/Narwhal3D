//! Win32 known-folder and protected-DACL helpers. No elevation or shared temp path.
use std::ffi::OsString;
use std::fs::{File, OpenOptions};
use std::io;
use std::os::windows::ffi::{OsStrExt, OsStringExt};
use std::os::windows::fs::OpenOptionsExt;
use std::path::{Path, PathBuf};
use std::ptr;
use windows_sys::Win32::{
    Foundation::{CloseHandle, LocalFree, ERROR_ALREADY_EXISTS},
    Security::Authorization::{
        ConvertSecurityDescriptorToStringSecurityDescriptorW, ConvertSidToStringSidW,
        ConvertStringSecurityDescriptorToSecurityDescriptorW, GetNamedSecurityInfoW,
        SE_FILE_OBJECT,
    },
    Security::{
        GetTokenInformation, SetFileSecurityW, TokenUser, DACL_SECURITY_INFORMATION,
        OWNER_SECURITY_INFORMATION, PROTECTED_DACL_SECURITY_INFORMATION, SECURITY_ATTRIBUTES,
        TOKEN_QUERY, TOKEN_USER,
    },
    Storage::FileSystem::{
        CreateDirectoryW, FILE_FLAG_BACKUP_SEMANTICS, FILE_FLAG_OPEN_REPARSE_POINT,
    },
    System::Com::CoTaskMemFree,
    System::Threading::{GetCurrentProcess, OpenProcessToken},
    UI::Shell::{FOLDERID_LocalAppData, SHGetKnownFolderPath},
};

fn wide(path: &Path) -> Vec<u16> {
    path.as_os_str().encode_wide().chain(Some(0)).collect()
}
unsafe fn text(pointer: *const u16) -> io::Result<OsString> {
    if pointer.is_null() {
        return Err(io::Error::other("Windows returned a null string"));
    }
    let mut length = 0;
    while unsafe { *pointer.add(length) } != 0 {
        length += 1;
        if length > 32767 {
            return Err(io::Error::other("Windows path/SID is too long"));
        }
    }
    Ok(OsString::from_wide(unsafe {
        std::slice::from_raw_parts(pointer, length)
    }))
}
fn user_sid() -> io::Result<String> {
    unsafe {
        let mut token = ptr::null_mut();
        if OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &mut token) == 0 {
            return Err(io::Error::last_os_error());
        }
        let result = (|| {
            let mut size = 0;
            GetTokenInformation(token, TokenUser, ptr::null_mut(), 0, &mut size);
            if size == 0 || size > 65536 {
                return Err(io::Error::other("invalid Windows token size"));
            }
            let mut buffer = vec![0usize; (size as usize).div_ceil(std::mem::size_of::<usize>())];
            if GetTokenInformation(
                token,
                TokenUser,
                buffer.as_mut_ptr().cast(),
                size,
                &mut size,
            ) == 0
            {
                return Err(io::Error::last_os_error());
            }
            let user = &*buffer.as_ptr().cast::<TOKEN_USER>();
            sid_string(user.User.Sid)
        })();
        CloseHandle(token);
        result
    }
}
unsafe fn sid_string(sid: *mut std::ffi::c_void) -> io::Result<String> {
    let mut pointer = ptr::null_mut();
    if unsafe { ConvertSidToStringSidW(sid, &mut pointer) } == 0 {
        return Err(io::Error::last_os_error());
    }
    let result = unsafe { text(pointer) }.and_then(|s| {
        s.into_string()
            .map_err(|_| io::Error::other("non-Unicode SID"))
    });
    unsafe {
        LocalFree(pointer.cast());
    }
    result
}
fn dacl(sid: &str) -> String {
    format!("D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;{sid})")
}

/// Seal runtime data against normal writes (including interpreter bytecode).
/// Owners retain WRITE_DAC, allowing explicit verified/leased cleanup to restore it.
pub fn runtime_permissions(root: &Path, writable: bool) -> io::Result<()> {
    let sid = user_sid()?;
    let access = if writable { "FA" } else { "GRGX" };
    let policy: Vec<_> = format!("D:P(A;OICI;FA;;;SY)(A;OICI;{access};;;{sid})")
        .encode_utf16()
        .chain(Some(0))
        .collect();
    let mut paths = vec![root.to_path_buf()];
    let mut index = 0;
    while index < paths.len() {
        let metadata = std::fs::symlink_metadata(&paths[index])?;
        crate::payload::no_link(&metadata)?;
        if metadata.is_dir() {
            let children: Result<Vec<_>, _> = std::fs::read_dir(&paths[index])?
                .map(|e| e.map(|entry| entry.path()))
                .collect();
            paths.extend(children?);
        }
        index += 1;
    }
    unsafe {
        let mut descriptor = ptr::null_mut();
        if ConvertStringSecurityDescriptorToSecurityDescriptorW(
            policy.as_ptr(),
            1,
            &mut descriptor,
            ptr::null_mut(),
        ) == 0
        {
            return Err(io::Error::last_os_error());
        }
        let result = (|| {
            for path in paths.iter().rev() {
                let absolute = std::fs::canonicalize(path)?;
                if SetFileSecurityW(
                    wide(&absolute).as_ptr(),
                    DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION,
                    descriptor,
                ) == 0
                {
                    return Err(io::Error::last_os_error());
                }
            }
            Ok(())
        })();
        LocalFree(descriptor);
        result
    }
}

pub fn local_app_data() -> io::Result<PathBuf> {
    unsafe {
        let mut pointer = ptr::null_mut();
        let result = SHGetKnownFolderPath(&FOLDERID_LocalAppData, 0, ptr::null_mut(), &mut pointer);
        if result < 0 {
            return Err(io::Error::other(format!(
                "LocalAppData lookup failed: {result:#x}"
            )));
        }
        let path = text(pointer).map(PathBuf::from);
        CoTaskMemFree(pointer.cast());
        path
    }
}
pub fn hold_directory(path: &Path) -> io::Result<File> {
    crate::payload::regular_directory(path)?;
    let file = OpenOptions::new()
        .read(true)
        .share_mode(3)
        .custom_flags(FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT)
        .open(path)?;
    crate::payload::no_link(&file.metadata()?)?;
    Ok(file)
}
pub fn private_directory(path: &Path) -> io::Result<()> {
    let sid = user_sid()?;
    // An elevated token can default new objects to the Administrators group.
    // Bind ownership at creation; never take ownership of an existing cache.
    let descriptor: Vec<_> = format!("O:{sid}{}", dacl(&sid))
        .encode_utf16()
        .chain(Some(0))
        .collect();
    unsafe {
        let mut security = ptr::null_mut();
        if ConvertStringSecurityDescriptorToSecurityDescriptorW(
            descriptor.as_ptr(),
            1,
            &mut security,
            ptr::null_mut(),
        ) == 0
        {
            return Err(io::Error::last_os_error());
        }
        let attrs = SECURITY_ATTRIBUTES {
            nLength: std::mem::size_of::<SECURITY_ATTRIBUTES>() as u32,
            lpSecurityDescriptor: security,
            bInheritHandle: 0,
        };
        let result = CreateDirectoryW(wide(path).as_ptr(), &attrs);
        let error = io::Error::last_os_error();
        if result != 0 {
            // Explicitly set the protection control bit on our newly created
            // directory. Never repair a pre-existing directory's permissions.
            let held = hold_directory(path)?;
            let protected = SetFileSecurityW(
                wide(path).as_ptr(),
                DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION,
                security,
            );
            let protect_error = io::Error::last_os_error();
            LocalFree(security);
            drop(held);
            if protected == 0 {
                return Err(protect_error);
            }
        } else {
            LocalFree(security);
            if error.raw_os_error() != Some(ERROR_ALREADY_EXISTS as i32) {
                return Err(error);
            }
        }
    }
    verify_private_directory(path)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn path(label: &str) -> PathBuf {
        std::env::temp_dir().join(format!(
            "narwhal-acl-{label}-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ))
    }
    #[test]
    fn creation_binds_current_user_and_protected_dacl() {
        let root = path("create");
        private_directory(&root).unwrap();
        verify_private_directory(&root).unwrap();
        // Reuse must validate rather than reset permissions.
        private_directory(&root).unwrap();
        std::fs::remove_dir(root).unwrap();
    }
    #[test]
    fn existing_unprotected_directory_is_not_repaired() {
        let root = path("existing");
        std::fs::create_dir(&root).unwrap();
        assert!(verify_private_directory(&root).is_err());
        assert!(private_directory(&root).is_err());
        assert!(verify_private_directory(&root).is_err());
        std::fs::remove_dir(root).unwrap();
    }
}

pub fn verify_private_directory(path: &Path) -> io::Result<()> {
    crate::payload::regular_directory(path)?;
    let current = user_sid()?;
    unsafe {
        let mut descriptor = ptr::null_mut();
        let mut owner = ptr::null_mut();
        let code = GetNamedSecurityInfoW(
            wide(path).as_ptr(),
            SE_FILE_OBJECT,
            OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
            &mut owner,
            ptr::null_mut(),
            ptr::null_mut(),
            ptr::null_mut(),
            &mut descriptor,
        );
        if code != 0 {
            return Err(io::Error::from_raw_os_error(code as i32));
        }
        let result = (|| {
            if sid_string(owner)? != current {
                return Err(io::Error::other(
                    "portable cache belongs to another Windows account",
                ));
            }
            let mut rendered = ptr::null_mut();
            if ConvertSecurityDescriptorToStringSecurityDescriptorW(
                descriptor,
                1,
                DACL_SECURITY_INFORMATION,
                &mut rendered,
                ptr::null_mut(),
            ) == 0
            {
                return Err(io::Error::last_os_error());
            }
            let value = text(rendered)?;
            LocalFree(rendered.cast());
            if value != OsString::from(dacl(&current)) {
                return Err(io::Error::other(format!(
                    "portable cache ACL differs from protected user/SYSTEM policy: {}; cache was not used",
                    value.to_string_lossy().replace(&current, "<current-user>"))));
            }
            Ok(())
        })();
        LocalFree(descriptor);
        result
    }
}
