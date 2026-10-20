// SPDX-License-Identifier: GPL-3.0-or-later

pub mod container;
pub mod sha256;

use std::ffi::OsString;
use std::io::{self, Write};
use std::path::PathBuf;

pub const PROGRAM_NAME: &str = "narwhal-document-core";
pub const PROTOCOL_VERSION: &str = "narwhal-document-core 1";

pub fn run<I>(args: I, stdout: &mut dyn Write, stderr: &mut dyn Write) -> i32
where
    I: IntoIterator<Item = OsString>,
{
    let args: Vec<OsString> = args.into_iter().collect();
    if args.len() == 1 && args[0] == "--version" {
        if writeln!(stdout, "{PROTOCOL_VERSION}").is_err() {
            return 1;
        }
        return 0;
    }

    let result = match args.as_slice() {
        [command, staging, target] if command == "pack" => {
            container::pack(&PathBuf::from(staging), &PathBuf::from(target))
        }
        [command, source, output] if command == "unpack" => {
            container::unpack(&PathBuf::from(source), &PathBuf::from(output))
        }
        [command, source] if command == "inspect" => {
            container::inspect(&PathBuf::from(source), stdout)
        }
        _ => {
            let _ = writeln!(
                stderr,
                "usage: {PROGRAM_NAME} --version | pack <staging-dir> <target-file> | unpack <source-file> <output-dir> | inspect <source-file>"
            );
            return 2;
        }
    };

    match result {
        Ok(()) => 0,
        Err(error) => {
            let _ = writeln!(stderr, "{PROGRAM_NAME}: {error}");
            1
        }
    }
}

pub fn process_main() -> i32 {
    let mut stdout = io::stdout().lock();
    let mut stderr = io::stderr().lock();
    run(std::env::args_os().skip(1), &mut stdout, &mut stderr)
}
