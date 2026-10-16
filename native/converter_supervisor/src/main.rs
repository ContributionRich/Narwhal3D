use serde::Serialize;
use std::collections::VecDeque;
use std::io::{self, Read};
use std::process::{Child, Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};

const MAX_CAPTURE_LIMIT: usize = 1024 * 1024;

#[derive(Debug)]
struct Config {
    timeout: Duration,
    max_output: usize,
    environment: Vec<(String, String)>,
    program: String,
    arguments: Vec<String>,
}

#[derive(Serialize)]
struct Report {
    returncode: i32,
    stdout: String,
    stderr: String,
    timed_out: bool,
    error: Option<String>,
}

fn parse_args() -> Result<Config, String> {
    let mut args = std::env::args().skip(1);
    let mut timeout_ms = None;
    let mut max_output = 8192usize;
    let mut environment = Vec::new();
    let mut command = Vec::new();
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--timeout-ms" => {
                timeout_ms = Some(
                    args.next()
                        .ok_or("--timeout-ms needs a value")?
                        .parse::<u64>()
                        .map_err(|_| "--timeout-ms must be an integer")?,
                );
            }
            "--max-output" => {
                max_output = args
                    .next()
                    .ok_or("--max-output needs a value")?
                    .parse::<usize>()
                    .map_err(|_| "--max-output must be an integer")?;
            }
            "--env" => {
                let item = args.next().ok_or("--env needs KEY=VALUE")?;
                let (key, value) = item.split_once('=').ok_or("--env needs KEY=VALUE")?;
                if key.is_empty() || key.contains('\0') || value.contains('\0') {
                    return Err("invalid environment entry".to_owned());
                }
                environment.push((key.to_owned(), value.to_owned()));
            }
            "--" => {
                command.extend(args);
                break;
            }
            _ => return Err(format!("unknown supervisor argument: {arg}")),
        }
    }
    if max_output == 0 || max_output > MAX_CAPTURE_LIMIT {
        return Err(format!(
            "--max-output must be from 1 through {MAX_CAPTURE_LIMIT}"
        ));
    }
    let timeout_ms = timeout_ms.ok_or("--timeout-ms is required")?;
    if timeout_ms == 0 {
        return Err("--timeout-ms must be positive".to_owned());
    }
    let program = command
        .first()
        .cloned()
        .ok_or("converter command is required")?;
    Ok(Config {
        timeout: Duration::from_millis(timeout_ms),
        max_output,
        environment,
        program,
        arguments: command.into_iter().skip(1).collect(),
    })
}

fn read_tail(mut input: impl Read, limit: usize) -> io::Result<Vec<u8>> {
    let mut tail = VecDeque::with_capacity(limit);
    let mut chunk = [0u8; 8192];
    loop {
        let count = input.read(&mut chunk)?;
        if count == 0 {
            break;
        }
        for byte in &chunk[..count] {
            if tail.len() == limit {
                tail.pop_front();
            }
            tail.push_back(*byte);
        }
    }
    Ok(tail.into_iter().collect())
}

#[cfg(unix)]
fn configure_command(command: &mut Command) {
    use std::os::unix::process::CommandExt as _;
    // SAFETY: setpgid is async-signal-safe and touches no Rust-owned state in the child.
    unsafe {
        command.pre_exec(|| {
            if libc::setpgid(0, 0) == 0 {
                Ok(())
            } else {
                Err(io::Error::last_os_error())
            }
        });
    }
}

#[cfg(windows)]
fn configure_command(command: &mut Command) {
    use std::os::windows::process::CommandExt as _;
    use windows_sys::Win32::System::Threading::{CREATE_NEW_PROCESS_GROUP, CREATE_SUSPENDED};
    command.creation_flags(CREATE_NEW_PROCESS_GROUP | CREATE_SUSPENDED);
}

#[cfg(not(any(unix, windows)))]
fn configure_command(_: &mut Command) {}

#[cfg(unix)]
struct PlatformGuard {
    group: i32,
}

#[cfg(unix)]
impl PlatformGuard {
    fn attach(child: &Child) -> io::Result<Self> {
        Ok(Self {
            group: i32::try_from(child.id())
                .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "child PID overflow"))?,
        })
    }

    fn terminate(&self) {
        // SAFETY: a negative PID targets only the process group created above.
        unsafe {
            libc::kill(-self.group, libc::SIGKILL);
        }
    }
}

#[cfg(windows)]
struct PlatformGuard {
    job: windows_sys::Win32::Foundation::HANDLE,
}

#[cfg(windows)]
impl PlatformGuard {
    fn attach(child: &Child) -> io::Result<Self> {
        use std::mem::{size_of, zeroed};
        use std::os::windows::io::AsRawHandle as _;
        use windows_sys::Win32::Foundation::{CloseHandle, FALSE};
        use windows_sys::Win32::System::JobObjects::{
            AssignProcessToJobObject, JobObjectExtendedLimitInformation, SetInformationJobObject,
            JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
        };
        // SAFETY: handles are checked and the information size matches its C structure.
        unsafe {
            let job = windows_sys::Win32::System::JobObjects::CreateJobObjectW(
                std::ptr::null(),
                std::ptr::null(),
            );
            if job.is_null() {
                return Err(io::Error::last_os_error());
            }
            let mut information: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = zeroed();
            information.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            if SetInformationJobObject(
                job,
                JobObjectExtendedLimitInformation,
                &information as *const _ as *const _,
                size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            ) == FALSE
                || AssignProcessToJobObject(job, child.as_raw_handle() as _) == FALSE
            {
                let error = io::Error::last_os_error();
                CloseHandle(job);
                return Err(error);
            }
            #[link(name = "ntdll")]
            extern "system" {
                fn NtResumeProcess(process: windows_sys::Win32::Foundation::HANDLE) -> i32;
            }
            if NtResumeProcess(child.as_raw_handle() as _) != 0 {
                let error = io::Error::last_os_error();
                CloseHandle(job);
                return Err(error);
            }
            Ok(Self { job })
        }
    }

    fn terminate(&self) {
        // SAFETY: this process owns the job handle until Drop.
        unsafe {
            windows_sys::Win32::System::JobObjects::TerminateJobObject(self.job, 1);
        }
    }
}

#[cfg(windows)]
impl Drop for PlatformGuard {
    fn drop(&mut self) {
        // SAFETY: the handle is valid and closed exactly once.
        unsafe {
            windows_sys::Win32::Foundation::CloseHandle(self.job);
        }
    }
}

#[cfg(not(any(unix, windows)))]
struct PlatformGuard;

#[cfg(not(any(unix, windows)))]
impl PlatformGuard {
    fn attach(_: &Child) -> io::Result<Self> {
        Ok(Self)
    }
    fn terminate(&self) {}
}

fn lossy_tail(bytes: &[u8], limit: usize) -> String {
    let text = String::from_utf8_lossy(bytes);
    if text.len() <= limit {
        return text.into_owned();
    }
    let mut start = text.len() - limit;
    while !text.is_char_boundary(start) {
        start += 1;
    }
    text[start..].to_owned()
}

fn supervise(config: Config) -> Report {
    let mut command = Command::new(&config.program);
    command
        .args(&config.arguments)
        .env_clear()
        .envs(config.environment)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    configure_command(&mut command);
    let mut child = match command.spawn() {
        Ok(child) => child,
        Err(error) => {
            return Report {
                returncode: -1,
                stdout: String::new(),
                stderr: String::new(),
                timed_out: false,
                error: Some(format!("could not start converter: {error}")),
            };
        }
    };
    let guard = match PlatformGuard::attach(&child) {
        Ok(guard) => guard,
        Err(error) => {
            let _ = child.kill();
            let _ = child.wait();
            return Report {
                returncode: -1,
                stdout: String::new(),
                stderr: String::new(),
                timed_out: false,
                error: Some(format!("could not contain converter process: {error}")),
            };
        }
    };
    let stdout = child.stdout.take().expect("piped stdout");
    let stderr = child.stderr.take().expect("piped stderr");
    let output_limit = config.max_output;
    let stdout_thread = thread::spawn(move || read_tail(stdout, output_limit));
    let stderr_thread = thread::spawn(move || read_tail(stderr, output_limit));

    let started = Instant::now();
    let mut timed_out = false;
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break Ok(status),
            Ok(None) if started.elapsed() < config.timeout => {
                thread::sleep(Duration::from_millis(10));
            }
            Ok(None) => {
                timed_out = true;
                guard.terminate();
                let _ = child.kill();
                break child.wait();
            }
            Err(error) => {
                guard.terminate();
                let _ = child.kill();
                let _ = child.wait();
                break Err(error);
            }
        }
    };
    // On ordinary exit, kill the group only if a descendant still holds a pipe. Avoid sending a
    // signal to an already-empty numeric PGID, which could theoretically be reused.
    if !timed_out {
        thread::sleep(Duration::from_millis(20));
        if !stdout_thread.is_finished() || !stderr_thread.is_finished() {
            guard.terminate();
        }
    }
    let stdout = stdout_thread
        .join()
        .ok()
        .and_then(Result::ok)
        .unwrap_or_default();
    let stderr = stderr_thread
        .join()
        .ok()
        .and_then(Result::ok)
        .unwrap_or_default();
    match status {
        Ok(status) => Report {
            returncode: status.code().unwrap_or(1),
            stdout: lossy_tail(&stdout, config.max_output),
            stderr: lossy_tail(&stderr, config.max_output),
            timed_out,
            error: None,
        },
        Err(error) => Report {
            returncode: -1,
            stdout: lossy_tail(&stdout, config.max_output),
            stderr: lossy_tail(&stderr, config.max_output),
            timed_out,
            error: Some(format!("converter wait failed: {error}")),
        },
    }
}

fn main() {
    let config = match parse_args() {
        Ok(config) => config,
        Err(error) => {
            eprintln!("narwhal-converter-supervisor: {error}");
            std::process::exit(2);
        }
    };
    let report = supervise(config);
    match serde_json::to_string(&report) {
        Ok(json) => println!("{json}"),
        Err(error) => {
            eprintln!("narwhal-converter-supervisor: could not encode report: {error}");
            std::process::exit(3);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{lossy_tail, read_tail};

    #[test]
    fn tail_keeps_only_the_latest_bytes() {
        assert_eq!(read_tail(&b"0123456789"[..], 4).unwrap(), b"6789");
    }

    #[test]
    fn lossy_utf8_tail_stays_inside_the_byte_limit() {
        let text = lossy_tail(&[0xff, 0xff, b'o', b'k'], 4);
        assert!(text.ends_with("ok"));
        assert!(text.len() <= 4);
    }
}
