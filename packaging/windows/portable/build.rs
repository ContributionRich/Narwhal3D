use std::{env, fs, path::PathBuf, process::Command};
fn main() {
    println!("cargo:rerun-if-changed=../narwhal3d.ico");
    println!("cargo:rerun-if-env-changed=NARWHAL_PORTABLE_VERSION");
    if env::var("CARGO_CFG_TARGET_OS").as_deref() != Ok("windows") {
        return;
    }
    let root = PathBuf::from(env::var_os("CARGO_MANIFEST_DIR").unwrap());
    let icon = root
        .join("../narwhal3d.ico")
        .canonicalize()
        .expect("Windows icon input missing");
    let version = env::var("NARWHAL_PORTABLE_VERSION").unwrap_or_else(|_| "0.4.8".into());
    let parts: Vec<_> = version.split('.').collect();
    assert!(
        parts.len() == 3
            && parts[0] == "0"
            && parts[1] == "4"
            && parts
                .iter()
                .all(|p| !p.is_empty() && p.bytes().all(|c| c.is_ascii_digit())),
        "invalid portable version"
    );
    let patch: u16 = parts[2]
        .parse()
        .expect("version patch exceeds PE resource range");
    let output = PathBuf::from(env::var_os("OUT_DIR").unwrap());
    let resource = output.join("portable.rc");
    let source = format!(
        r#"#include <windows.h>
1 ICON "{}"
1 24
BEGIN
 "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>"
 "<assembly xmlns='urn:schemas-microsoft-com:asm.v1' manifestVersion='1.0'>"
 "<trustInfo xmlns='urn:schemas-microsoft-com:asm.v3'><security><requestedPrivileges>"
 "<requestedExecutionLevel level='asInvoker' uiAccess='false'/>"
 "</requestedPrivileges></security></trustInfo>"
 "<application xmlns='urn:schemas-microsoft-com:asm.v3'><windowsSettings>"
 "<longPathAware xmlns='http://schemas.microsoft.com/SMI/2016/WindowsSettings'>true</longPathAware>"
 "</windowsSettings></application></assembly>"
END
1 VERSIONINFO
 FILEVERSION 0,4,{},0
 PRODUCTVERSION 0,4,{},0
 FILEOS VOS_NT_WINDOWS32
 FILETYPE VFT_APP
BEGIN
 BLOCK "StringFileInfo"
 BEGIN
  BLOCK "040904b0"
  BEGIN
   VALUE "CompanyName", "Narwhal 3D Contributors\0"
   VALUE "FileDescription", "Narwhal 3D portable application\0"
   VALUE "ProductName", "Narwhal 3D\0"
   VALUE "FileVersion", "{}\0"
   VALUE "ProductVersion", "{}\0"
  END
 END
 BLOCK "VarFileInfo"
 BEGIN
  VALUE "Translation", 0x409, 1200
 END
END
"#,
        icon.to_string_lossy().replace('\\', "\\\\"),
        patch,
        patch,
        version,
        version
    );
    fs::write(&resource, source).unwrap();
    let gnu = env::var("CARGO_CFG_TARGET_ENV").as_deref() == Ok("gnu");
    let object = output.join(if gnu { "portable.o" } else { "portable.res" });
    let status = if gnu {
        Command::new("x86_64-w64-mingw32-windres")
            .arg("-i")
            .arg(&resource)
            .args(["-O", "coff", "-o"])
            .arg(&object)
            .status()
    } else {
        Command::new("rc.exe")
            .arg("/nologo")
            .arg(format!("/fo{}", object.display()))
            .arg(&resource)
            .status()
    }
    .expect("native Windows resource compiler is required");
    assert!(
        status.success(),
        "native Windows resource compilation failed"
    );
    println!(
        "cargo:rustc-link-arg-bin=narwhal-portable-payload={}",
        object.display()
    );
    println!("cargo:rustc-env=NARWHAL_PORTABLE_VERSION={version}");
}
