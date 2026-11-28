//! Test-only renderer witness. Never included in a product payload.
#[cfg(target_os = "linux")]
fn main() {
    use serde_json::json;
    let mut limits = serde_json::Map::new();
    for (name, resource, expected) in [
        ("cpu_seconds", libc::RLIMIT_CPU, 12),
        ("address_bytes", libc::RLIMIT_AS, 512 * 1024 * 1024),
        ("file_bytes", libc::RLIMIT_FSIZE, 64 * 1024 * 1024),
        ("descriptors", libc::RLIMIT_NOFILE, 64),
    ] {
        let mut value = libc::rlimit {
            rlim_cur: 0,
            rlim_max: 0,
        };
        assert_eq!(unsafe { libc::getrlimit(resource, &mut value) }, 0);
        assert_eq!(value.rlim_cur, expected);
        assert_eq!(value.rlim_max, expected);
        limits.insert(name.into(), json!(value.rlim_cur));
    }
    assert_eq!(
        unsafe { libc::prctl(libc::PR_GET_NO_NEW_PRIVS, 0, 0, 0, 0) },
        1
    );
    assert_eq!(unsafe { libc::prctl(libc::PR_GET_SECCOMP, 0, 0, 0, 0) }, 2);
    assert_eq!(
        unsafe { libc::socket(libc::AF_INET, libc::SOCK_STREAM, 0) },
        -1
    );
    assert_eq!(
        std::io::Error::last_os_error().raw_os_error(),
        Some(libc::EPERM)
    );
    let environment: std::collections::BTreeMap<_, _> = std::env::vars().collect();
    assert!(!environment.contains_key("PATH"));
    assert!(!environment.contains_key("LD_PRELOAD"));
    assert_eq!(environment["LD_LIBRARY_PATH"], "/package/pdf-libs");
    let proof = json!({"ok":true,"inherited_limits":limits,"no_new_privileges":true,"seccomp_filter":true,"socket_creation_denied":true,"environment":environment});
    std::fs::write(
        "../renderer-containment.json",
        serde_json::to_vec_pretty(&proof).unwrap(),
    )
    .unwrap();
    println!("Pages: 2\nEncrypted: no\nPage 1 size: 144 x 72 pts\nPage 1 rot: 0");
}
#[cfg(not(target_os = "linux"))]
fn main() {
    panic!("Linux-only containment witness; not a renderer implementation");
}
