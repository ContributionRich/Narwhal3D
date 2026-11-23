use std::{
    fs::{self, OpenOptions},
    path::Path,
    process::Command,
};
fn cli(args: &[&str]) -> std::process::Output {
    Command::new(env!("CARGO_BIN_EXE_automation-test-driver"))
        .args(args)
        .output()
        .unwrap()
}
#[test]
fn invalid_cli_and_inside_targets() {
    for args in [
        vec![],
        vec!["--suite", "missing"],
        vec!["--suite", "plan", "--test", "v4_"],
        vec!["--wat"],
        vec!["--suite", "v7", "--fixture-only"],
        vec!["--suite", "v8", "--legacy-library", "/missing"],
    ] {
        assert!(!cli(&args).status.success());
    }
    let root = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .canonicalize()
        .unwrap();
    let target = root.join("native/automation_test_driver/never-create");
    let o = cli(&[
        "--suite",
        "box",
        "--target-dir",
        target.to_str().unwrap(),
        "--python",
        "/does/not/exist",
    ]);
    assert!(String::from_utf8_lossy(&o.stderr).contains("outside repository"));
    assert!(!target.exists());
}
#[test]
fn shared_flock_compatibility() {
    let lock = OpenOptions::new()
        .create(true)
        .append(true)
        .open("/tmp/narwhal3d-heavy-build.lock")
        .unwrap();
    lock.try_lock()
        .expect("run test only when heavy build lane is idle");
    let root = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .canonicalize()
        .unwrap();
    let python = root.join("tools/blender-5.2.2-linux-x64/5.2/python/bin/python3.13");
    // Actual fcntl.flock, not a second Rust lock implementation.
    let output=Command::new(&python).args(["-B","-c","import fcntl; f=open('/tmp/narwhal3d-heavy-build.lock','a'); fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)"]).output().unwrap();
    assert!(!output.status.success());
    assert!(String::from_utf8_lossy(&output.stderr).contains("BlockingIOError"));
    let target = std::env::temp_dir().join(format!("ai-driver-lock-{}", std::process::id()));
    let o = cli(&[
        "--suite",
        "box",
        "--target-dir",
        target.to_str().unwrap(),
        "--python",
        python.to_str().unwrap(),
    ]);
    assert!(!o.status.success());
    assert!(!target.exists());
    drop(lock);
    let output=Command::new(python).args(["-B","-c","import fcntl; f=open('/tmp/narwhal3d-heavy-build.lock','a'); fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)"]).output().unwrap();
    assert!(output.status.success());
}
#[test]
fn symlink_escaped_nonexistent_target() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .canonicalize()
        .unwrap();
    let outside = std::env::temp_dir().join(format!("ai-path-{}", std::process::id()));
    fs::create_dir(&outside).unwrap();
    std::os::unix::fs::symlink(&root, outside.join("link")).unwrap();
    let t = outside.join("link/nonexistent/../still-inside");
    assert!(automation_test_driver::external(&root, &t).is_err());
    fs::remove_dir_all(outside).unwrap();
}

#[test]
fn scope_properties_and_hard_kill() {
    use std::time::{Duration, Instant};
    let unit = format!("narwhal-driver-containment-{}", std::process::id());
    let mut args = automation_test_driver::scope_args(1024, true, &unit).unwrap();
    for a in &mut args {
        if a == "170s" {
            *a = "1s".into();
        }
        if a == "RuntimeMaxSec=175s" {
            *a = "RuntimeMaxSec=5s".into();
        }
    }
    let started = Instant::now();
    let output = Command::new("systemd-run")
        .args(args)
        .args(["sh", "-c", "trap '' TERM; sleep 60 & wait"])
        .output()
        .unwrap();
    assert!(!output.status.success());
    assert!(started.elapsed() < Duration::from_secs(10));
    let active = Command::new("systemctl")
        .args(["--user", "is-active", "--quiet", &format!("{unit}.scope")])
        .status()
        .unwrap();
    assert!(
        !active.success(),
        "timeout left descendants in active scope"
    );
}
