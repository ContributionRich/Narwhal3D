use serde::Serialize;
use std::collections::BTreeSet;
use std::fs::{self, OpenOptions};
use std::io::{Cursor, Read, Write};
use std::path::PathBuf;
use std::sync::Mutex;
use zip::ZipArchive;

const MAX_PACKAGE_BYTES: u64 = 64 * 1024 * 1024;
const MAX_MEMBERS: usize = 4096;
const MAX_MEMBER_BYTES: u64 = 64 * 1024 * 1024;
const MAX_EXPANDED_BYTES: u64 = 256 * 1024 * 1024;
const MAX_RECORDS: usize = 500_000;
const MAX_DEPTH: usize = 256;
const MAX_JSON_BYTES: usize = 128 * 1024 * 1024;
const MEMORY_LIMIT_BYTES: usize = 768 * 1024 * 1024;
const PROTOCOL: &str = "narwhal-cdr-multiversion 1 tracedraw 821ba154 versions 16-18";

struct WarningCapture;
static WARNING_CAPTURE: WarningCapture = WarningCapture;
static CAPTURED_WARNINGS: Mutex<Vec<String>> = Mutex::new(Vec::new());

impl log::Log for WarningCapture {
    fn enabled(&self, metadata: &log::Metadata<'_>) -> bool {
        metadata.level() <= log::Level::Warn
    }

    fn log(&self, record: &log::Record<'_>) {
        if self.enabled(record.metadata()) {
            if let Ok(mut warnings) = CAPTURED_WARNINGS.lock() {
                let message = record.args().to_string();
                if warnings.len() < 200 && !warnings.contains(&message) {
                    warnings.push(message);
                }
            }
        }
    }

    fn flush(&self) {}
}

fn init_warning_capture() -> Result<(), String> {
    log::set_logger(&WARNING_CAPTURE)
        .map_err(|error| format!("cannot initialize warning log: {error}"))?;
    log::set_max_level(log::LevelFilter::Warn);
    Ok(())
}

fn take_warnings() -> Vec<String> {
    CAPTURED_WARNINGS
        .lock()
        .map(|mut warnings| std::mem::take(&mut *warnings))
        .unwrap_or_default()
}

#[cfg(unix)]
fn install_memory_limit() -> Result<(), String> {
    let limit = libc::rlimit {
        rlim_cur: MEMORY_LIMIT_BYTES as libc::rlim_t,
        rlim_max: MEMORY_LIMIT_BYTES as libc::rlim_t,
    };
    // SAFETY: `limit` points to an initialized rlimit structure for this process.
    if unsafe { libc::setrlimit(libc::RLIMIT_AS, &limit) } != 0 {
        return Err(format!(
            "cannot set CDR helper memory limit: {}",
            std::io::Error::last_os_error()
        ));
    }
    Ok(())
}

#[cfg(windows)]
fn install_memory_limit() -> Result<(), String> {
    use std::mem::{size_of, zeroed};
    use windows_sys::Win32::Foundation::{CloseHandle, FALSE};
    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
        SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, JOB_OBJECT_LIMIT_PROCESS_MEMORY,
    };
    use windows_sys::Win32::System::Threading::GetCurrentProcess;
    // SAFETY: handles are checked and the information size matches the Windows structure.
    unsafe {
        let job = CreateJobObjectW(std::ptr::null(), std::ptr::null());
        if job.is_null() {
            return Err(format!(
                "cannot create CDR memory job: {}",
                std::io::Error::last_os_error()
            ));
        }
        let mut information: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = zeroed();
        information.BasicLimitInformation.LimitFlags =
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_PROCESS_MEMORY;
        information.ProcessMemoryLimit = MEMORY_LIMIT_BYTES;
        if SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            &information as *const _ as *const _,
            size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
        ) == FALSE
            || AssignProcessToJobObject(job, GetCurrentProcess()) == FALSE
        {
            let error = std::io::Error::last_os_error();
            CloseHandle(job);
            return Err(format!("cannot apply CDR memory job: {error}"));
        }
        // Raw HANDLE has no Drop implementation; intentionally keep the job open for process life.
    }
    Ok(())
}

#[derive(Serialize)]
struct Decoded {
    version: u16,
    document: serde_json::Value,
    report: Report,
}

#[derive(Serialize)]
struct Report {
    pages: usize,
    layers: usize,
    shapes: usize,
    skipped_objects: usize,
    warnings: Vec<String>,
}

fn u16_at(data: &[u8], offset: usize) -> Result<u16, String> {
    let bytes: [u8; 2] = data
        .get(offset..offset + 2)
        .ok_or("truncated CDR field")?
        .try_into()
        .map_err(|_| "truncated CDR field")?;
    Ok(u16::from_le_bytes(bytes))
}

fn u32_at(data: &[u8], offset: usize) -> Result<u32, String> {
    let bytes: [u8; 4] = data
        .get(offset..offset + 4)
        .ok_or("truncated CDR field")?
        .try_into()
        .map_err(|_| "truncated CDR field")?;
    Ok(u32::from_le_bytes(bytes))
}

fn read_member(package: &[u8], name: &str) -> Result<Vec<u8>, String> {
    let mut archive = ZipArchive::new(Cursor::new(package))
        .map_err(|error| format!("invalid CDR ZIP package: {error}"))?;
    let file = archive
        .by_name(name)
        .map_err(|_| format!("CDR ZIP member is missing: {name}"))?;
    if file.size() > MAX_MEMBER_BYTES {
        return Err(format!("CDR ZIP member exceeds the limit: {name}"));
    }
    let mut output = Vec::with_capacity(file.size() as usize);
    file.take(MAX_MEMBER_BYTES + 1)
        .read_to_end(&mut output)
        .map_err(|error| format!("cannot read CDR ZIP member {name}: {error}"))?;
    if output.len() as u64 > MAX_MEMBER_BYTES {
        return Err(format!("CDR ZIP member expands beyond the limit: {name}"));
    }
    Ok(output)
}

fn preflight(package: &[u8]) -> Result<(), String> {
    let mut archive = ZipArchive::new(Cursor::new(package))
        .map_err(|error| format!("invalid CDR ZIP package: {error}"))?;
    if archive.len() > MAX_MEMBERS {
        return Err("CDR ZIP package has too many members".to_owned());
    }
    let mut expanded = 0u64;
    let mut names = BTreeSet::new();
    for index in 0..archive.len() {
        let file = archive
            .by_index(index)
            .map_err(|error| format!("invalid CDR ZIP member: {error}"))?;
        let path = file
            .enclosed_name()
            .ok_or("CDR ZIP package contains an unsafe member path")?;
        let name = path.to_string_lossy().replace('\\', "/");
        if !names.insert(name) {
            return Err("CDR ZIP package contains duplicate member names".to_owned());
        }
        if file.size() > MAX_MEMBER_BYTES {
            return Err("CDR ZIP member exceeds the expansion limit".to_owned());
        }
        expanded = expanded
            .checked_add(file.size())
            .ok_or("CDR ZIP expanded-size overflow")?;
        if expanded > MAX_EXPANDED_BYTES {
            return Err("CDR ZIP package exceeds the expansion limit".to_owned());
        }
    }
    Ok(())
}

fn validate_vrsn(root: &[u8], expected: u16) -> Result<(), String> {
    let mut position = 12usize;
    let mut found = None;
    while position < root.len() {
        if root.len() - position < 8 {
            return Err("truncated top-level CDR record".to_owned());
        }
        let tag = &root[position..position + 4];
        let size = u32_at(root, position + 4)? as usize;
        let stop = position
            .checked_add(8)
            .and_then(|value| value.checked_add(size))
            .ok_or("CDR top-level record size overflow")?;
        if stop > root.len() {
            return Err("CDR top-level record exceeds root stream".to_owned());
        }
        if tag == b"vrsn" {
            if found.is_some() {
                return Err("CDR root contains duplicate vrsn records".to_owned());
            }
            let body = &root[position + 8..stop];
            let value = if size == 16 && u32_at(body, 0)? == u32::MAX {
                if u32_at(body, 4)? != 2 {
                    return Err("CDR vrsn redirect has the wrong length".to_owned());
                }
                u16_at(body, 8)?
            } else if size == 2 {
                u16_at(body, 0)?
            } else {
                return Err("CDR vrsn record has an unsupported layout".to_owned());
            };
            found = Some(value);
        }
        position = stop
            .checked_add(size & 1)
            .ok_or("CDR top-level padding overflow")?;
    }
    let expected = expected
        .checked_mul(100)
        .ok_or("CDR version multiplication overflow")?;
    if found != Some(expected) {
        return Err("CDR form type and vrsn record disagree".to_owned());
    }
    Ok(())
}

fn validate_records(
    data: &[u8],
    start: usize,
    end: usize,
    external: &[Vec<u8>],
    depth: usize,
    count: &mut usize,
) -> Result<(), String> {
    if depth > MAX_DEPTH {
        return Err("CDR RIFF nesting exceeds the limit".to_owned());
    }
    if start > end || end > data.len() {
        return Err("invalid CDR RIFF range".to_owned());
    }
    let mut position = start;
    while position < end {
        if end - position < 8 {
            return Err("trailing truncated CDR RIFF record".to_owned());
        }
        *count += 1;
        if *count > MAX_RECORDS {
            return Err("CDR RIFF record count exceeds the limit".to_owned());
        }
        let tag = data
            .get(position..position + 4)
            .ok_or("truncated CDR tag")?;
        let size = u32_at(data, position + 4)? as usize;
        let stop = position
            .checked_add(8)
            .and_then(|value| value.checked_add(size))
            .ok_or("CDR RIFF record size overflow")?;
        if stop > end {
            return Err("CDR RIFF record extends beyond its container".to_owned());
        }
        let mut body = data
            .get(position + 8..stop)
            .ok_or("truncated CDR RIFF body")?;
        if size == 16 {
            let stream = u32_at(body, 0)?;
            let length = u32_at(body, 4)? as usize;
            if stream == u32::MAX {
                if length > 8 {
                    return Err("inline CDR redirect exceeds eight bytes".to_owned());
                }
                body = body
                    .get(8..8 + length)
                    .ok_or("truncated inline CDR redirect")?;
            } else {
                let source = external
                    .get(stream as usize)
                    .ok_or("CDR redirect names a missing external stream")?;
                let offset = u32_at(body, 8)? as usize;
                if u32_at(body, 12)? != 0 {
                    return Err("CDR external redirect reserved word is not zero".to_owned());
                }
                let redirected_stop = offset
                    .checked_add(length)
                    .ok_or("CDR redirect size overflow")?;
                body = source
                    .get(offset..redirected_stop)
                    .ok_or("CDR redirect extends beyond its external stream")?;
            }
        }
        if tag == b"LIST" {
            if body.len() < 4 {
                return Err("CDR LIST record has no form type".to_owned());
            }
            if &body[..4] == b"cmpr" {
                return Err(
                    "compressed RIFF lists are not accepted in CDR 16 through 18".to_owned(),
                );
            }
            validate_records(body, 4, body.len(), external, depth + 1, count)?;
        }
        position = stop
            .checked_add(size & 1)
            .ok_or("CDR RIFF padding overflow")?;
        if position > end {
            return Err("CDR RIFF record is missing its padding byte".to_owned());
        }
    }
    Ok(())
}

fn decode(package: &[u8]) -> Result<Decoded, String> {
    preflight(package)?;
    let container = tracedraw_cdr::container::detect(package)
        .map_err(|error| format!("cannot identify CDR package: {error}"))?;
    let version = container.version.0;
    if !(16..=18).contains(&version) {
        return Err(format!("UNSUPPORTED_VERSION:{version}"));
    }
    let (root_name, data_files) = match &container.kind {
        tracedraw_cdr::container::ContainerKind::Zip {
            riff_member,
            data_files,
        } => (riff_member.as_str(), data_files.as_slice()),
        _ => return Err("CDR versions 16 through 18 must use a ZIP container".to_owned()),
    };
    if data_files.len() > MAX_MEMBERS {
        return Err("CDR external stream list exceeds the limit".to_owned());
    }
    let mut seen = BTreeSet::new();
    let mut external = Vec::with_capacity(data_files.len());
    for name in data_files {
        if name.is_empty() || name.contains('/') || name.contains('\\') || !seen.insert(name) {
            return Err("invalid or duplicate CDR external stream name".to_owned());
        }
        let primary = format!("content/data/{name}");
        let data = read_member(package, &primary)
            .or_else(|_| read_member(package, &format!("content/{name}")))
            .or_else(|_| read_member(package, name))?;
        external.push(data);
    }
    let root = read_member(package, root_name)?;
    if root.len() < 12 || &root[..4] != b"RIFF" {
        return Err("CDR root stream is not RIFF".to_owned());
    }
    let declared = u32_at(&root, 4)? as usize;
    if declared != root.len() - 8 {
        return Err("CDR root RIFF size is not exact".to_owned());
    }
    validate_vrsn(&root, version)?;
    let mut records = 0usize;
    validate_records(&root, 12, root.len(), &external, 0, &mut records)?;

    let (document, mut report) = tracedraw_cdr::open_bytes(package, "CorelDRAW import")
        .map_err(|error| format!("cannot decode CDR version {version}: {error}"))?;
    if report.version.map(|value| value.0) != Some(version)
        || report
            .warnings
            .iter()
            .any(|warning| warning.contains("vrsn chunk says version"))
    {
        return Err("CDR form type and vrsn record disagree".to_owned());
    }
    for warning in take_warnings() {
        if report.warnings.len() < 200 && !report.warnings.contains(&warning) {
            report.warnings.push(warning);
        }
    }
    if report.pages == 0 || document.pages.is_empty() || report.shapes == 0 {
        return Err("CDR package contains no readable vector objects".to_owned());
    }
    let document = document
        .to_json()
        .map_err(|error| format!("cannot serialize decoded CDR document: {error}"))?;
    if document.len() > MAX_JSON_BYTES {
        return Err("decoded CDR document exceeds the JSON limit".to_owned());
    }
    let document = serde_json::from_str(&document)
        .map_err(|error| format!("invalid decoded CDR document JSON: {error}"))?;
    Ok(Decoded {
        version,
        document,
        report: Report {
            pages: report.pages,
            layers: report.layers,
            shapes: report.shapes,
            skipped_objects: report.skipped_objects,
            warnings: report.warnings,
        },
    })
}

fn run() -> Result<(), String> {
    let mut arguments = std::env::args_os().skip(1);
    let first = arguments.next().ok_or("missing input CDR path")?;
    if first == "--version" {
        if arguments.next().is_some() {
            return Err("unexpected arguments after --version".to_owned());
        }
        println!("{PROTOCOL}");
        return Ok(());
    }
    let input = PathBuf::from(first);
    let output = PathBuf::from(arguments.next().ok_or("missing output JSON path")?);
    if arguments.next().is_some() {
        return Err("unexpected extra arguments".to_owned());
    }
    let path_metadata = fs::symlink_metadata(&input)
        .map_err(|error| format!("cannot inspect input path: {error}"))?;
    if !path_metadata.file_type().is_file() {
        return Err("input CDR path must name a regular file, not a link".to_owned());
    }
    let mut file = OpenOptions::new()
        .read(true)
        .open(&input)
        .map_err(|error| format!("cannot open input CDR: {error}"))?;
    let metadata = file
        .metadata()
        .map_err(|error| format!("cannot inspect input: {error}"))?;
    if !metadata.file_type().is_file() || metadata.len() > MAX_PACKAGE_BYTES {
        return Err("input CDR must be a regular file no larger than 64 MiB".to_owned());
    }
    let mut package = Vec::with_capacity(metadata.len() as usize);
    (&mut file)
        .take(MAX_PACKAGE_BYTES + 1)
        .read_to_end(&mut package)
        .map_err(|error| format!("cannot read input CDR: {error}"))?;
    if package.len() as u64 > MAX_PACKAGE_BYTES {
        return Err("input CDR grew beyond the 64 MiB limit while being read".to_owned());
    }
    let decoded = decode(&package)?;
    let data = serde_json::to_vec(&decoded)
        .map_err(|error| format!("cannot serialize CDR result: {error}"))?;
    if data.len() > MAX_JSON_BYTES {
        return Err("decoded CDR result exceeds the JSON limit".to_owned());
    }
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&output)
        .map_err(|error| format!("cannot create output JSON: {error}"))?;
    file.write_all(&data)
        .and_then(|_| file.sync_all())
        .map_err(|error| format!("cannot write output JSON: {error}"))?;
    Ok(())
}

fn main() {
    if let Err(error) = install_memory_limit()
        .and_then(|_| init_warning_capture())
        .and_then(|_| run())
    {
        eprintln!("{error}");
        let code = if error.starts_with("UNSUPPORTED_VERSION:") {
            4
        } else {
            2
        };
        std::process::exit(code);
    }
}

#[cfg(test)]
mod tests {
    use super::{decode, init_warning_capture, take_warnings};
    use std::io::{Cursor, Read, Write};
    use tracedraw_core::{BezPath, Document, Shape, ShapeKind, Size};

    fn document() -> Document {
        let mut document = Document::new("Embedded", Size::new(100.0, 80.0));
        let layer = document.pages[0].layers[0].id;
        let mut path = BezPath::new();
        path.move_to((10.0, 20.0));
        path.curve_to((20.0, 35.0), (30.0, 5.0), (40.0, 20.0));
        let shape = Shape::new(
            document.ids_mut().shape(),
            ShapeKind::Path {
                path,
                closed: false,
            },
        );
        document.layer_mut(layer).unwrap().shapes.push(shape);
        document
    }

    #[cfg(unix)]
    #[test]
    fn installs_the_process_memory_ceiling() {
        super::install_memory_limit().unwrap();
        let mut limit = libc::rlimit {
            rlim_cur: 0,
            rlim_max: 0,
        };
        // SAFETY: limit points to writable initialized storage.
        assert_eq!(unsafe { libc::getrlimit(libc::RLIMIT_AS, &mut limit) }, 0);
        assert!(limit.rlim_cur <= super::MEMORY_LIMIT_BYTES as libc::rlim_t);
    }

    #[test]
    fn captures_upstream_log_warnings_for_the_import_report() {
        let _ = init_warning_capture();
        log::warn!("captured warning probe");
        assert!(take_warnings()
            .iter()
            .any(|warning| warning == "captured warning probe"));
    }

    #[test]
    fn generated_versions_16_through_18_are_strictly_readable() {
        let document = document();
        for version in 16..=18 {
            let package = tracedraw_cdr::write::document_to_cdr_version(&document, version);
            let decoded = decode(&package).unwrap();
            assert_eq!(decoded.version, version);
            assert_eq!(decoded.report.shapes, 1);
        }
    }

    #[test]
    fn mismatched_form_and_vrsn_versions_are_rejected() {
        let package = tracedraw_cdr::write::document_to_cdr_version(&document(), 18);
        let mut input = zip::ZipArchive::new(Cursor::new(&package)).unwrap();
        let mut rebuilt = Cursor::new(Vec::new());
        {
            let mut output = zip::ZipWriter::new(&mut rebuilt);
            for index in 0..input.len() {
                let mut member = input.by_index(index).unwrap();
                let name = member.name().to_owned();
                let mut data = Vec::new();
                member.read_to_end(&mut data).unwrap();
                if name == "content/root.dat" {
                    let position = data.windows(4).position(|value| value == b"vrsn").unwrap();
                    data[position + 16..position + 18].copy_from_slice(&1900u16.to_le_bytes());
                }
                output
                    .start_file(name, zip::write::SimpleFileOptions::default())
                    .unwrap();
                output.write_all(&data).unwrap();
            }
            output.finish().unwrap();
        }
        assert!(decode(rebuilt.get_ref()).is_err());
    }

    #[test]
    fn truncated_inner_riff_is_rejected() {
        let package = tracedraw_cdr::write::document_to_cdr_version(&document(), 18);
        let mut input = zip::ZipArchive::new(Cursor::new(&package)).unwrap();
        let mut rebuilt = Cursor::new(Vec::new());
        {
            let mut output = zip::ZipWriter::new(&mut rebuilt);
            for index in 0..input.len() {
                let mut member = input.by_index(index).unwrap();
                let mut data = Vec::new();
                member.read_to_end(&mut data).unwrap();
                if member.name() == "content/root.dat" {
                    data.pop();
                }
                output
                    .start_file(member.name(), zip::write::SimpleFileOptions::default())
                    .unwrap();
                output.write_all(&data).unwrap();
            }
            output.finish().unwrap();
        }
        assert!(decode(rebuilt.get_ref()).is_err());
    }
}
