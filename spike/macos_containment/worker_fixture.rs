//! Bounded native integration fixture. NEVER ship as Blender or an application.
use std::ffi::{c_char, c_void, CString};
extern "C" {
    fn dlopen(path: *const c_char, flags: i32) -> *mut c_void;
    fn dlsym(handle: *mut c_void, name: *const c_char) -> *mut c_void;
    fn fork() -> i32;
    fn waitpid(pid: i32, status: *mut i32, flags: i32) -> i32;
    fn execv(path: *const c_char, args: *const *const c_char) -> i32;
    fn _exit(code: i32) -> !;
}
fn main() {
    let library = CString::new(std::env::var("NARWHAL_POLICY_FIXTURE").unwrap()).unwrap();
    let symbol = CString::new("narwhal_seal_document_worker").unwrap();
    let handle = unsafe { dlopen(library.as_ptr(), 2) };
    assert!(!handle.is_null());
    let address = unsafe { dlsym(handle, symbol.as_ptr()) };
    assert!(!address.is_null());
    let seal: extern "C" fn() -> i32 = unsafe { std::mem::transmute(address) };
    assert_eq!(seal(), 0, "worker was not sealed");
    assert_eq!(std::env::var("PYTHONDONTWRITEBYTECODE").as_deref(), Ok("1"));
    let mode = std::env::args().last().unwrap();
    match mode.as_str() {
        "over" => {
            let mut data = vec![0u8; 128 * 1024 * 1024];
            for i in (0..data.len()).step_by(4096) {
                unsafe {
                    std::ptr::write_volatile(data.as_mut_ptr().add(i), 0x5a);
                }
            }
            std::hint::black_box(&data);
            println!("allocation-survived");
        }
        "fork" => {
            let pid = unsafe { fork() };
            if pid == 0 {
                unsafe { _exit(7) }
            }
            if pid > 0 {
                let mut status = 0;
                unsafe {
                    waitpid(pid, &mut status, 0);
                }
                panic!("fork escaped");
            }
            assert_eq!(std::io::Error::last_os_error().raw_os_error(), Some(1));
            println!("fork-denied");
        }
        "exec" => {
            let path = CString::new("/usr/bin/true").unwrap();
            unsafe {
                execv(path.as_ptr(), [path.as_ptr(), std::ptr::null()].as_ptr());
            }
            assert_eq!(std::io::Error::last_os_error().raw_os_error(), Some(1));
            println!("exec-denied");
        }
        "sleep" => std::thread::sleep(std::time::Duration::from_secs(8)),
        "normal" => println!(
            "contained-{}",
            std::env::var("NARWHAL_MAC_DOCUMENT_LIMIT_MIB").unwrap()
        ),
        _ => panic!("unknown bounded fixture mode"),
    }
}
