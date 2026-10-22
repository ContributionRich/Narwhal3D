// SPDX-License-Identifier: GPL-3.0-or-later
//! Dependency-free native file and confirmation dialogs behind a bounded C ABI.

use std::collections::VecDeque;
#[cfg(windows)]
use std::ffi::c_void;
use std::mem::{align_of, size_of};
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::ptr;
use std::slice;
use std::str;
#[cfg(any(windows, test))]
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Mutex, OnceLock};

pub const ABI_VERSION: u32 = 1;
pub const STATUS_OK: i32 = 0;
pub const STATUS_CANCEL: i32 = 1;
pub const STATUS_UNAVAILABLE: i32 = 2;
pub const STATUS_ERROR: i32 = 3;

const ERROR_NONE: u32 = 0;
const ERROR_INVALID_ARGUMENT: u32 = 1;
const ERROR_LIMIT_EXCEEDED: u32 = 2;
const ERROR_INVALID_UTF8: u32 = 3;
const ERROR_EMBEDDED_NUL: u32 = 4;
#[cfg(windows)]
const ERROR_INVALID_WINDOW: u32 = 5;
#[cfg(windows)]
const ERROR_SYSTEM: u32 = 6;
const ERROR_INVALID_TOKEN: u32 = 7;
#[cfg(any(windows, test))]
const ERROR_PENDING_FULL: u32 = 8;
#[cfg(windows)]
const ERROR_INVALID_PATH_ENCODING: u32 = 9;
const ERROR_PANIC: u32 = 10;

pub const DIALOG_OPEN: u32 = 1;
pub const DIALOG_SAVE: u32 = 2;
pub const CHOICE_NONE: u32 = 0;
pub const CHOICE_YES: u32 = 1;
pub const CHOICE_NO: u32 = 2;
pub const CHOICE_CANCEL: u32 = 3;

const MAX_TITLE_BYTES: usize = 4 * 1024;
const MAX_MESSAGE_BYTES: usize = 64 * 1024;
const MAX_START_PATH_BYTES: usize = 128 * 1024;
const MAX_FILTERS: usize = 64;
const MAX_FILTER_FIELD_BYTES: usize = 4 * 1024;
const MAX_FILTER_UTF16_UNITS: usize = 64 * 1024;
const MAX_OUTPUT_BYTES: usize = 512 * 1024;
#[cfg(any(windows, test))]
const MAX_PENDING_RESULTS: usize = 32;

#[repr(C)]
#[derive(Clone, Copy)]
pub struct NarwhalUtf8Slice {
    pub data: *const u8,
    pub length: usize,
}

#[repr(C)]
#[derive(Clone, Copy)]
pub struct NarwhalFilterPairV1 {
    pub struct_size: u32,
    pub abi_version: u32,
    pub description: NarwhalUtf8Slice,
    pub glob: NarwhalUtf8Slice,
}

#[repr(C)]
#[derive(Clone, Copy)]
pub struct NarwhalFileDialogRequestV1 {
    pub struct_size: u32,
    pub abi_version: u32,
    pub dialog_kind: u32,
    pub reserved: u32,
    pub owner_window: usize,
    pub title: NarwhalUtf8Slice,
    pub start_path: NarwhalUtf8Slice,
    pub filters: *const NarwhalFilterPairV1,
    pub filter_count: usize,
    pub output: *mut u8,
    pub output_capacity: usize,
}

#[repr(C)]
#[derive(Clone, Copy)]
pub struct NarwhalDialogResultV1 {
    pub struct_size: u32,
    pub abi_version: u32,
    pub status: i32,
    pub error_code: u32,
    pub system_error: u32,
    pub selected_filter_index: u32,
    pub required_capacity: usize,
    pub written_length: usize,
    pub retry_token: u64,
}

impl NarwhalDialogResultV1 {
    fn new() -> Self {
        Self {
            struct_size: size_of::<Self>() as u32,
            abi_version: ABI_VERSION,
            status: STATUS_ERROR,
            error_code: ERROR_INVALID_ARGUMENT,
            system_error: 0,
            selected_filter_index: 0,
            required_capacity: 0,
            written_length: 0,
            retry_token: 0,
        }
    }
}

#[repr(C)]
#[derive(Clone, Copy)]
pub struct NarwhalMessageBoxRequestV1 {
    pub struct_size: u32,
    pub abi_version: u32,
    pub reserved0: u32,
    pub reserved1: u32,
    pub owner_window: usize,
    pub title: NarwhalUtf8Slice,
    pub message: NarwhalUtf8Slice,
}

#[repr(C)]
#[derive(Clone, Copy)]
pub struct NarwhalMessageBoxResultV1 {
    pub struct_size: u32,
    pub abi_version: u32,
    pub status: i32,
    pub choice: u32,
    pub error_code: u32,
    pub system_error: u32,
}

impl NarwhalMessageBoxResultV1 {
    fn new() -> Self {
        Self {
            struct_size: size_of::<Self>() as u32,
            abi_version: ABI_VERSION,
            status: STATUS_ERROR,
            choice: CHOICE_NONE,
            error_code: ERROR_INVALID_ARGUMENT,
            system_error: 0,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct AbiError(u32);

fn valid_object_pointer<T>(pointer: *const T) -> bool {
    !pointer.is_null() && (pointer as usize).is_multiple_of(align_of::<T>())
}

unsafe fn utf8_slice<'a>(value: NarwhalUtf8Slice, maximum: usize) -> Result<&'a str, AbiError> {
    if value.length > maximum || value.length > isize::MAX as usize {
        return Err(AbiError(ERROR_LIMIT_EXCEEDED));
    }
    if value.length == 0 {
        return Ok("");
    }
    if value.data.is_null() {
        return Err(AbiError(ERROR_INVALID_ARGUMENT));
    }
    // SAFETY: The C ABI requires `data` to reference `length` readable bytes.
    let bytes = unsafe { slice::from_raw_parts(value.data, value.length) };
    let text = str::from_utf8(bytes).map_err(|_| AbiError(ERROR_INVALID_UTF8))?;
    if text.as_bytes().contains(&0) {
        return Err(AbiError(ERROR_EMBEDDED_NUL));
    }
    Ok(text)
}

#[cfg(any(windows, test))]
fn wide_nul(text: &str) -> Vec<u16> {
    text.encode_utf16().chain([0]).collect()
}

#[cfg(any(windows, test))]
fn common_dialog_status(accepted: bool, extended_error: u32) -> i32 {
    if accepted {
        STATUS_OK
    } else if extended_error == 0 {
        STATUS_CANCEL
    } else {
        STATUS_ERROR
    }
}

#[cfg(any(windows, test))]
fn message_box_status_choice(selected: i32) -> (i32, u32) {
    match selected {
        6 => (STATUS_OK, CHOICE_YES),
        7 => (STATUS_OK, CHOICE_NO),
        2 => (STATUS_CANCEL, CHOICE_CANCEL),
        _ => (STATUS_ERROR, CHOICE_NONE),
    }
}

unsafe fn build_filter_block(
    filters: *const NarwhalFilterPairV1,
    count: usize,
) -> Result<Vec<u16>, AbiError> {
    if count > MAX_FILTERS {
        return Err(AbiError(ERROR_LIMIT_EXCEEDED));
    }
    if count == 0 {
        return Ok(Vec::new());
    }
    if !valid_object_pointer(filters)
        || count > isize::MAX as usize / size_of::<NarwhalFilterPairV1>()
    {
        return Err(AbiError(ERROR_INVALID_ARGUMENT));
    }
    // SAFETY: The C ABI requires `filters` to reference `count` readable records.
    let pairs = unsafe { slice::from_raw_parts(filters, count) };
    let mut block = Vec::new();
    for pair in pairs {
        if pair.struct_size as usize != size_of::<NarwhalFilterPairV1>()
            || pair.abi_version != ABI_VERSION
        {
            return Err(AbiError(ERROR_INVALID_ARGUMENT));
        }
        // SAFETY: Each nested slice is governed by the same C ABI contract.
        let description = unsafe { utf8_slice(pair.description, MAX_FILTER_FIELD_BYTES) }?;
        // SAFETY: Each nested slice is governed by the same C ABI contract.
        let glob = unsafe { utf8_slice(pair.glob, MAX_FILTER_FIELD_BYTES) }?;
        if description.is_empty() || glob.is_empty() {
            return Err(AbiError(ERROR_INVALID_ARGUMENT));
        }
        block.extend(description.encode_utf16());
        block.push(0);
        block.extend(glob.encode_utf16());
        block.push(0);
        if block.len() + 1 > MAX_FILTER_UTF16_UNITS {
            return Err(AbiError(ERROR_LIMIT_EXCEEDED));
        }
    }
    block.push(0);
    Ok(block)
}

#[derive(Clone)]
struct PendingResult {
    token: u64,
    path: Vec<u8>,
}

static PENDING: OnceLock<Mutex<VecDeque<PendingResult>>> = OnceLock::new();
#[cfg(any(windows, test))]
static NEXT_TOKEN: AtomicU64 = AtomicU64::new(1);

fn pending_results() -> &'static Mutex<VecDeque<PendingResult>> {
    PENDING.get_or_init(|| Mutex::new(VecDeque::new()))
}

#[cfg(any(windows, test))]
fn next_token() -> u64 {
    loop {
        let token = NEXT_TOKEN.fetch_add(1, Ordering::Relaxed);
        if token != 0 {
            return token;
        }
    }
}

#[cfg(any(windows, test))]
fn retain_path(path: Vec<u8>) -> Result<u64, AbiError> {
    let mut pending = pending_results()
        .lock()
        .map_err(|_| AbiError(ERROR_PANIC))?;
    if pending.len() >= MAX_PENDING_RESULTS {
        return Err(AbiError(ERROR_PENDING_FULL));
    }
    let token = next_token();
    pending.push_back(PendingResult { token, path });
    Ok(token)
}

unsafe fn copy_bytes(output: *mut u8, capacity: usize, bytes: &[u8]) -> Result<bool, AbiError> {
    let required = bytes
        .len()
        .checked_add(1)
        .ok_or(AbiError(ERROR_LIMIT_EXCEEDED))?;
    if required > MAX_OUTPUT_BYTES || capacity > MAX_OUTPUT_BYTES {
        return Err(AbiError(ERROR_LIMIT_EXCEEDED));
    }
    if capacity < required {
        if capacity != 0 && output.is_null() {
            return Err(AbiError(ERROR_INVALID_ARGUMENT));
        }
        return Ok(false);
    }
    if output.is_null() {
        return Err(AbiError(ERROR_INVALID_ARGUMENT));
    }
    // SAFETY: The caller supplied at least `required` writable bytes.
    unsafe {
        ptr::copy_nonoverlapping(bytes.as_ptr(), output, bytes.len());
        output.add(bytes.len()).write(0);
    }
    Ok(true)
}

#[cfg(any(windows, test))]
unsafe fn finish_selected_path(
    path: String,
    output: *mut u8,
    capacity: usize,
    filter_index: u32,
    result: &mut NarwhalDialogResultV1,
) -> Result<(), AbiError> {
    let bytes = path.into_bytes();
    result.required_capacity = bytes.len() + 1;
    result.selected_filter_index = filter_index;
    // SAFETY: `copy_bytes` validates the caller-owned destination contract.
    if unsafe { copy_bytes(output, capacity, &bytes) }? {
        result.written_length = bytes.len();
    } else {
        result.retry_token = retain_path(bytes)?;
    }
    result.status = STATUS_OK;
    result.error_code = ERROR_NONE;
    Ok(())
}

fn set_dialog_error(result: &mut NarwhalDialogResultV1, error: AbiError) {
    result.status = STATUS_ERROR;
    result.error_code = error.0;
}

fn set_message_error(result: &mut NarwhalMessageBoxResultV1, error: AbiError) {
    result.status = STATUS_ERROR;
    result.error_code = error.0;
}

#[cfg(windows)]
mod windows {
    use super::*;

    type Hwnd = *mut c_void;

    const OFN_OVERWRITEPROMPT: u32 = 0x0000_0002;
    const OFN_NOCHANGEDIR: u32 = 0x0000_0008;
    const OFN_PATHMUSTEXIST: u32 = 0x0000_0800;
    const OFN_FILEMUSTEXIST: u32 = 0x0000_1000;
    const OFN_NOREADONLYRETURN: u32 = 0x0000_8000;
    const OFN_EXPLORER: u32 = 0x0008_0000;
    const FILE_BUFFER_UNITS: usize = 32_768;

    const MB_YESNOCANCEL: u32 = 0x0000_0003;
    const MB_ICONQUESTION: u32 = 0x0000_0020;
    const MB_TASKMODAL: u32 = 0x0000_2000;

    #[repr(C)]
    struct OpenFileNameW {
        struct_size: u32,
        owner_window: Hwnd,
        instance: *mut c_void,
        filter: *const u16,
        custom_filter: *mut u16,
        max_custom_filter: u32,
        filter_index: u32,
        file: *mut u16,
        max_file: u32,
        file_title: *mut u16,
        max_file_title: u32,
        initial_dir: *const u16,
        title: *const u16,
        flags: u32,
        file_offset: u16,
        file_extension: u16,
        default_extension: *const u16,
        custom_data: isize,
        hook: *mut c_void,
        template_name: *const u16,
        reserved: *mut c_void,
        reserved2: u32,
        flags_ex: u32,
    }

    const _: () = assert!(
        size_of::<OpenFileNameW>() == if usize::BITS == 64 { 152 } else { 88 },
        "OPENFILENAMEW layout does not match Win32"
    );

    #[link(name = "comdlg32")]
    extern "system" {
        fn GetOpenFileNameW(value: *mut OpenFileNameW) -> i32;
        fn GetSaveFileNameW(value: *mut OpenFileNameW) -> i32;
        fn CommDlgExtendedError() -> u32;
    }

    #[link(name = "user32")]
    extern "system" {
        fn IsWindow(window: Hwnd) -> i32;
        fn MessageBoxW(owner_window: Hwnd, text: *const u16, caption: *const u16, kind: u32)
            -> i32;
    }

    #[link(name = "kernel32")]
    extern "system" {
        fn GetLastError() -> u32;
    }

    fn owner_handle(value: usize) -> Result<Hwnd, AbiError> {
        let owner = value as Hwnd;
        if value != 0 {
            // SAFETY: IsWindow treats the value as an opaque handle and does not dereference it.
            if unsafe { IsWindow(owner) } == 0 {
                return Err(AbiError(ERROR_INVALID_WINDOW));
            }
        }
        Ok(owner)
    }

    pub(super) fn show_file_dialog(
        kind: u32,
        owner_value: usize,
        title: &str,
        start_path: &str,
        filters: &[u16],
    ) -> Result<(i32, Option<String>, u32, u32), AbiError> {
        let owner = owner_handle(owner_value)?;
        let title = wide_nul(title);
        let start_path = wide_nul(start_path);
        let mut file = vec![0_u16; FILE_BUFFER_UNITS];
        let mut value = OpenFileNameW {
            struct_size: size_of::<OpenFileNameW>() as u32,
            owner_window: owner,
            instance: ptr::null_mut(),
            filter: if filters.is_empty() {
                ptr::null()
            } else {
                filters.as_ptr()
            },
            custom_filter: ptr::null_mut(),
            max_custom_filter: 0,
            filter_index: 1,
            file: file.as_mut_ptr(),
            max_file: FILE_BUFFER_UNITS as u32,
            file_title: ptr::null_mut(),
            max_file_title: 0,
            initial_dir: if start_path.len() == 1 {
                ptr::null()
            } else {
                start_path.as_ptr()
            },
            title: if title.len() == 1 {
                ptr::null()
            } else {
                title.as_ptr()
            },
            flags: OFN_EXPLORER
                | OFN_NOCHANGEDIR
                | OFN_PATHMUSTEXIST
                | if kind == DIALOG_OPEN {
                    OFN_FILEMUSTEXIST
                } else {
                    OFN_OVERWRITEPROMPT | OFN_NOREADONLYRETURN
                },
            file_offset: 0,
            file_extension: 0,
            default_extension: ptr::null(),
            custom_data: 0,
            hook: ptr::null_mut(),
            template_name: ptr::null(),
            reserved: ptr::null_mut(),
            reserved2: 0,
            flags_ex: 0,
        };
        // SAFETY: OPENFILENAMEW and all pointed-to buffers remain live for the synchronous call.
        let accepted = unsafe {
            if kind == DIALOG_OPEN {
                GetOpenFileNameW(&mut value)
            } else {
                GetSaveFileNameW(&mut value)
            }
        };
        if accepted == 0 {
            // SAFETY: CommDlgExtendedError has no preconditions after a common-dialog failure.
            let error = unsafe { CommDlgExtendedError() };
            return Ok((
                common_dialog_status(false, error),
                None,
                value.filter_index,
                error,
            ));
        }
        let end = file
            .iter()
            .position(|unit| *unit == 0)
            .unwrap_or(file.len());
        let path =
            String::from_utf16(&file[..end]).map_err(|_| AbiError(ERROR_INVALID_PATH_ENCODING))?;
        Ok((STATUS_OK, Some(path), value.filter_index, 0))
    }

    pub(super) fn show_message_box(
        owner_value: usize,
        title: &str,
        message: &str,
    ) -> Result<(i32, u32, u32), AbiError> {
        let owner = owner_handle(owner_value)?;
        let title = wide_nul(title);
        let message = wide_nul(message);
        let mut flags = MB_YESNOCANCEL | MB_ICONQUESTION;
        if owner.is_null() {
            flags |= MB_TASKMODAL;
        }
        // SAFETY: The UTF-16 buffers are NUL terminated and remain live for the synchronous call.
        let selected = unsafe { MessageBoxW(owner, message.as_ptr(), title.as_ptr(), flags) };
        let (status, choice) = message_box_status_choice(selected);
        let system_error = if selected == 0 {
            // SAFETY: GetLastError has no preconditions.
            unsafe { GetLastError() }
        } else {
            0
        };
        Ok((status, choice, system_error))
    }
}

unsafe fn file_dialog_impl(
    request: *const NarwhalFileDialogRequestV1,
    result: *mut NarwhalDialogResultV1,
) -> i32 {
    if !valid_object_pointer(result) {
        return STATUS_ERROR;
    }
    let mut output = NarwhalDialogResultV1::new();
    if !valid_object_pointer(request) {
        // SAFETY: `result` was checked for null and alignment; writability is the caller contract.
        unsafe { ptr::write(result, output) };
        return STATUS_ERROR;
    }
    // SAFETY: Readability of a correctly aligned request record is the caller contract.
    let request = unsafe { ptr::read(request) };
    if request.struct_size as usize != size_of::<NarwhalFileDialogRequestV1>()
        || request.abi_version != ABI_VERSION
        || !matches!(request.dialog_kind, DIALOG_OPEN | DIALOG_SAVE)
        || request.reserved != 0
    {
        // SAFETY: Writability of the result record is the caller contract.
        unsafe { ptr::write(result, output) };
        return STATUS_ERROR;
    }
    let prepared = (|| {
        // SAFETY: Nested input slices are governed by the C ABI contract.
        let title = unsafe { utf8_slice(request.title, MAX_TITLE_BYTES) }?;
        // SAFETY: Nested input slices are governed by the C ABI contract.
        let start_path = unsafe { utf8_slice(request.start_path, MAX_START_PATH_BYTES) }?;
        // SAFETY: The filter array is governed by the C ABI contract.
        let filters = unsafe { build_filter_block(request.filters, request.filter_count) }?;
        if request.output_capacity > MAX_OUTPUT_BYTES
            || (request.output_capacity != 0 && request.output.is_null())
        {
            return Err(AbiError(if request.output_capacity > MAX_OUTPUT_BYTES {
                ERROR_LIMIT_EXCEEDED
            } else {
                ERROR_INVALID_ARGUMENT
            }));
        }
        Ok((title, start_path, filters))
    })();
    match prepared {
        Err(error) => set_dialog_error(&mut output, error),
        Ok((title, start_path, filters)) => {
            #[cfg(windows)]
            match windows::show_file_dialog(
                request.dialog_kind,
                request.owner_window,
                title,
                start_path,
                &filters,
            ) {
                Err(error) => set_dialog_error(&mut output, error),
                Ok((STATUS_CANCEL, _, filter_index, _)) => {
                    output.status = STATUS_CANCEL;
                    output.error_code = ERROR_NONE;
                    output.selected_filter_index = filter_index;
                }
                Ok((STATUS_ERROR, _, filter_index, system_error)) => {
                    output.status = STATUS_ERROR;
                    output.error_code = ERROR_SYSTEM;
                    output.system_error = system_error;
                    output.selected_filter_index = filter_index;
                }
                Ok((STATUS_OK, Some(path), filter_index, _)) => {
                    // SAFETY: The caller-owned output contract was validated above.
                    if let Err(error) = unsafe {
                        finish_selected_path(
                            path,
                            request.output,
                            request.output_capacity,
                            filter_index,
                            &mut output,
                        )
                    } {
                        set_dialog_error(&mut output, error);
                    }
                }
                _ => set_dialog_error(&mut output, AbiError(ERROR_SYSTEM)),
            }
            #[cfg(not(windows))]
            {
                let _ = (title, start_path, filters, request.owner_window);
                output.status = STATUS_UNAVAILABLE;
                output.error_code = ERROR_NONE;
            }
        }
    }
    let status = output.status;
    // SAFETY: Writability of the result record is the caller contract.
    unsafe { ptr::write(result, output) };
    status
}

unsafe fn message_box_impl(
    request: *const NarwhalMessageBoxRequestV1,
    result: *mut NarwhalMessageBoxResultV1,
) -> i32 {
    if !valid_object_pointer(result) {
        return STATUS_ERROR;
    }
    let mut output = NarwhalMessageBoxResultV1::new();
    if !valid_object_pointer(request) {
        // SAFETY: Writability of the result record is the caller contract.
        unsafe { ptr::write(result, output) };
        return STATUS_ERROR;
    }
    // SAFETY: Readability of a correctly aligned request record is the caller contract.
    let request = unsafe { ptr::read(request) };
    if request.struct_size as usize != size_of::<NarwhalMessageBoxRequestV1>()
        || request.abi_version != ABI_VERSION
        || request.reserved0 != 0
        || request.reserved1 != 0
    {
        // SAFETY: Writability of the result record is the caller contract.
        unsafe { ptr::write(result, output) };
        return STATUS_ERROR;
    }
    let prepared = (|| {
        // SAFETY: Nested input slices are governed by the C ABI contract.
        let title = unsafe { utf8_slice(request.title, MAX_TITLE_BYTES) }?;
        // SAFETY: Nested input slices are governed by the C ABI contract.
        let message = unsafe { utf8_slice(request.message, MAX_MESSAGE_BYTES) }?;
        Ok((title, message))
    })();
    match prepared {
        Err(error) => set_message_error(&mut output, error),
        Ok((title, message)) => {
            #[cfg(windows)]
            match windows::show_message_box(request.owner_window, title, message) {
                Err(error) => set_message_error(&mut output, error),
                Ok((status, choice, system_error)) => {
                    output.status = status;
                    output.choice = choice;
                    output.error_code = if status == STATUS_ERROR {
                        ERROR_SYSTEM
                    } else {
                        ERROR_NONE
                    };
                    output.system_error = system_error;
                }
            }
            #[cfg(not(windows))]
            {
                let _ = (title, message, request.owner_window);
                output.status = STATUS_UNAVAILABLE;
                output.error_code = ERROR_NONE;
            }
        }
    }
    let status = output.status;
    // SAFETY: Writability of the result record is the caller contract.
    unsafe { ptr::write(result, output) };
    status
}

unsafe fn copy_result_impl(
    token: u64,
    output_pointer: *mut u8,
    capacity: usize,
    result: *mut NarwhalDialogResultV1,
) -> i32 {
    if !valid_object_pointer(result) {
        return STATUS_ERROR;
    }
    let mut output = NarwhalDialogResultV1::new();
    if token == 0 || capacity > MAX_OUTPUT_BYTES || (capacity != 0 && output_pointer.is_null()) {
        output.error_code = if capacity > MAX_OUTPUT_BYTES {
            ERROR_LIMIT_EXCEEDED
        } else if token == 0 {
            ERROR_INVALID_TOKEN
        } else {
            ERROR_INVALID_ARGUMENT
        };
        // SAFETY: Writability of the result record is the caller contract.
        unsafe { ptr::write(result, output) };
        return STATUS_ERROR;
    }
    let mut pending = match pending_results().lock() {
        Ok(value) => value,
        Err(_) => {
            output.error_code = ERROR_PANIC;
            // SAFETY: Writability of the result record is the caller contract.
            unsafe { ptr::write(result, output) };
            return STATUS_ERROR;
        }
    };
    let Some(index) = pending.iter().position(|item| item.token == token) else {
        output.error_code = ERROR_INVALID_TOKEN;
        // SAFETY: Writability of the result record is the caller contract.
        unsafe { ptr::write(result, output) };
        return STATUS_ERROR;
    };
    let bytes = &pending[index].path;
    output.required_capacity = bytes.len() + 1;
    output.retry_token = token;
    // SAFETY: `copy_bytes` validates the caller-owned destination contract.
    match unsafe { copy_bytes(output_pointer, capacity, bytes) } {
        Ok(false) => {
            output.status = STATUS_OK;
            output.error_code = ERROR_NONE;
        }
        Ok(true) => {
            output.status = STATUS_OK;
            output.error_code = ERROR_NONE;
            output.written_length = bytes.len();
            output.retry_token = 0;
            pending.remove(index);
        }
        Err(error) => set_dialog_error(&mut output, error),
    }
    let status = output.status;
    // SAFETY: Writability of the result record is the caller contract.
    unsafe { ptr::write(result, output) };
    status
}

#[no_mangle]
pub extern "C" fn narwhal_platform_core_abi_version() -> u32 {
    ABI_VERSION
}

/// Opens one native file dialog on Windows and writes only to caller-owned storage.
///
/// # Safety
///
/// `request` must reference a readable `NarwhalFileDialogRequestV1` whose nested
/// pointers satisfy their declared lengths. `result` must reference one writable,
/// correctly aligned `NarwhalDialogResultV1`; `request.output`, when non-null,
/// must reference `output_capacity` writable bytes for the synchronous call.
#[no_mangle]
pub unsafe extern "C" fn narwhal_platform_file_dialog_v1(
    request: *const NarwhalFileDialogRequestV1,
    result: *mut NarwhalDialogResultV1,
) -> i32 {
    catch_unwind(AssertUnwindSafe(|| unsafe {
        file_dialog_impl(request, result)
    }))
    .unwrap_or(STATUS_ERROR)
}

/// Copies a retained path into caller-owned storage without reopening its dialog.
///
/// # Safety
///
/// `result` must reference one writable, correctly aligned
/// `NarwhalDialogResultV1`. When `capacity` is nonzero, `output` must reference
/// at least `capacity` writable bytes.
#[no_mangle]
pub unsafe extern "C" fn narwhal_platform_dialog_result_copy_v1(
    token: u64,
    output: *mut u8,
    capacity: usize,
    result: *mut NarwhalDialogResultV1,
) -> i32 {
    catch_unwind(AssertUnwindSafe(|| unsafe {
        copy_result_impl(token, output, capacity, result)
    }))
    .unwrap_or(STATUS_ERROR)
}

#[no_mangle]
pub extern "C" fn narwhal_platform_dialog_result_discard_v1(token: u64) -> i32 {
    catch_unwind(AssertUnwindSafe(|| {
        if token == 0 {
            return STATUS_ERROR;
        }
        let Ok(mut pending) = pending_results().lock() else {
            return STATUS_ERROR;
        };
        let Some(index) = pending.iter().position(|item| item.token == token) else {
            return STATUS_ERROR;
        };
        pending.remove(index);
        STATUS_OK
    }))
    .unwrap_or(STATUS_ERROR)
}

/// Opens one native Yes/No/Cancel message box on Windows.
///
/// # Safety
///
/// `request` must reference a readable `NarwhalMessageBoxRequestV1` whose nested
/// pointers satisfy their declared lengths. `result` must reference one writable,
/// correctly aligned `NarwhalMessageBoxResultV1`.
#[no_mangle]
pub unsafe extern "C" fn narwhal_platform_message_box_v1(
    request: *const NarwhalMessageBoxRequestV1,
    result: *mut NarwhalMessageBoxResultV1,
) -> i32 {
    catch_unwind(AssertUnwindSafe(|| unsafe {
        message_box_impl(request, result)
    }))
    .unwrap_or(STATUS_ERROR)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn slice(text: &[u8]) -> NarwhalUtf8Slice {
        NarwhalUtf8Slice {
            data: text.as_ptr(),
            length: text.len(),
        }
    }

    fn pair(description: &[u8], glob: &[u8]) -> NarwhalFilterPairV1 {
        NarwhalFilterPairV1 {
            struct_size: size_of::<NarwhalFilterPairV1>() as u32,
            abi_version: ABI_VERSION,
            description: slice(description),
            glob: slice(glob),
        }
    }

    #[test]
    fn strict_utf8_and_embedded_nul_are_rejected() {
        let invalid = [0xf0, 0x28, 0x8c, 0x28];
        // SAFETY: Test slices remain live for the calls.
        assert_eq!(
            unsafe { utf8_slice(slice(&invalid), 10) },
            Err(AbiError(ERROR_INVALID_UTF8))
        );
        // SAFETY: Test slices remain live for the calls.
        assert_eq!(
            unsafe { utf8_slice(slice(b"a\0b"), 10) },
            Err(AbiError(ERROR_EMBEDDED_NUL))
        );
        // SAFETY: Test slices remain live for the calls.
        assert_eq!(
            unsafe { utf8_slice(slice("résumé".as_bytes()), 20) },
            Ok("résumé")
        );
    }

    #[test]
    fn utf16_preserves_unicode_and_quotes() {
        let input = "C:\\設計\\'quoted name'\\part.3dm";
        let wide = wide_nul(input);
        assert_eq!(wide.last(), Some(&0));
        assert_eq!(String::from_utf16(&wide[..wide.len() - 1]).unwrap(), input);
    }

    #[test]
    fn filter_block_has_pair_nuls_and_one_final_nul() {
        let filters = [
            pair("3DM files".as_bytes(), b"*.3dm;*.3dmbak"),
            pair("Unicode 図面".as_bytes(), b"*.dwg;*.dxf"),
        ];
        // SAFETY: The filter records and their nested byte slices remain live.
        let block = unsafe { build_filter_block(filters.as_ptr(), filters.len()) }.unwrap();
        let expected: Vec<u16> = "3DM files\0*.3dm;*.3dmbak\0Unicode 図面\0*.dwg;*.dxf\0\0"
            .encode_utf16()
            .collect();
        assert_eq!(block, expected);
        assert_eq!(&block[block.len() - 2..], &[0, 0]);
    }

    #[test]
    fn filter_limits_and_empty_fields_fail_closed() {
        let empty = [pair(b"", b"*.3dm")];
        // SAFETY: The filter record remains live.
        assert_eq!(
            unsafe { build_filter_block(empty.as_ptr(), 1) },
            Err(AbiError(ERROR_INVALID_ARGUMENT))
        );
        let huge = vec![b'x'; MAX_FILTER_FIELD_BYTES + 1];
        let over = [pair(b"Files", &huge)];
        // SAFETY: The filter record and nested data remain live.
        assert_eq!(
            unsafe { build_filter_block(over.as_ptr(), 1) },
            Err(AbiError(ERROR_LIMIT_EXCEEDED))
        );
    }

    #[test]
    fn output_buffer_reports_capacity_and_writes_nul() {
        let mut small = [0xa5_u8; 3];
        // SAFETY: The test output is writable for its declared capacity.
        assert!(!unsafe { copy_bytes(small.as_mut_ptr(), small.len(), "éx".as_bytes()) }.unwrap());
        assert_eq!(small, [0xa5; 3]);
        let mut exact = [0xa5_u8; 4];
        // SAFETY: The test output is writable for its declared capacity.
        assert!(unsafe { copy_bytes(exact.as_mut_ptr(), exact.len(), "éx".as_bytes()) }.unwrap());
        assert_eq!(&exact, &[0xc3, 0xa9, b'x', 0]);
    }

    #[test]
    fn retained_result_is_two_pass_safe_and_one_shot() {
        let token = retain_path("C:\\図面\\part.3dm".as_bytes().to_vec()).unwrap();
        let mut query = NarwhalDialogResultV1::new();
        // SAFETY: The result record is writable; zero capacity permits a null output.
        assert_eq!(
            unsafe { copy_result_impl(token, ptr::null_mut(), 0, &mut query) },
            STATUS_OK
        );
        assert!(query.required_capacity > 1);
        assert_eq!(query.retry_token, token);

        let mut bytes = vec![0_u8; query.required_capacity];
        let mut copied = NarwhalDialogResultV1::new();
        // SAFETY: The destination and result are writable for their declared sizes.
        assert_eq!(
            unsafe { copy_result_impl(token, bytes.as_mut_ptr(), bytes.len(), &mut copied) },
            STATUS_OK
        );
        assert_eq!(copied.retry_token, 0);
        assert_eq!(copied.written_length + 1, copied.required_capacity);
        assert_eq!(
            str::from_utf8(&bytes[..copied.written_length]).unwrap(),
            "C:\\図面\\part.3dm"
        );

        let mut stale = NarwhalDialogResultV1::new();
        // SAFETY: The result record is writable.
        assert_eq!(
            unsafe { copy_result_impl(token, ptr::null_mut(), 0, &mut stale) },
            STATUS_ERROR
        );
        assert_eq!(stale.error_code, ERROR_INVALID_TOKEN);
    }

    #[test]
    fn selected_path_direct_and_retry_results_match() {
        let path = "C:\\work\\résumé.3dm".to_owned();
        let mut direct_bytes = [0_u8; 128];
        let mut direct = NarwhalDialogResultV1::new();
        // SAFETY: The destination is writable for its declared capacity.
        unsafe {
            finish_selected_path(
                path.clone(),
                direct_bytes.as_mut_ptr(),
                direct_bytes.len(),
                2,
                &mut direct,
            )
        }
        .unwrap();
        assert_eq!(direct.status, STATUS_OK);
        assert_eq!(direct.retry_token, 0);
        assert_eq!(direct.selected_filter_index, 2);

        let mut deferred = NarwhalDialogResultV1::new();
        // SAFETY: Zero capacity permits a null output.
        unsafe { finish_selected_path(path, ptr::null_mut(), 0, 2, &mut deferred) }.unwrap();
        assert_ne!(deferred.retry_token, 0);
        assert_eq!(deferred.required_capacity, direct.required_capacity);
        assert_eq!(
            narwhal_platform_dialog_result_discard_v1(deferred.retry_token),
            STATUS_OK
        );
    }

    #[test]
    fn status_and_choice_values_are_stable() {
        assert_eq!(
            [STATUS_OK, STATUS_CANCEL, STATUS_UNAVAILABLE, STATUS_ERROR],
            [0, 1, 2, 3]
        );
        assert_eq!(
            [CHOICE_NONE, CHOICE_YES, CHOICE_NO, CHOICE_CANCEL],
            [0, 1, 2, 3]
        );
    }

    #[test]
    fn native_return_values_map_to_distinct_statuses() {
        assert_eq!(common_dialog_status(true, 0), STATUS_OK);
        assert_eq!(common_dialog_status(false, 0), STATUS_CANCEL);
        assert_eq!(common_dialog_status(false, 0x3002), STATUS_ERROR);
        assert_eq!(message_box_status_choice(6), (STATUS_OK, CHOICE_YES));
        assert_eq!(message_box_status_choice(7), (STATUS_OK, CHOICE_NO));
        assert_eq!(message_box_status_choice(2), (STATUS_CANCEL, CHOICE_CANCEL));
        assert_eq!(message_box_status_choice(0), (STATUS_ERROR, CHOICE_NONE));
    }

    #[cfg(not(windows))]
    #[test]
    fn overlong_start_path_is_rejected_before_platform_dispatch() {
        let path = vec![b'x'; MAX_START_PATH_BYTES + 1];
        let request = NarwhalFileDialogRequestV1 {
            struct_size: size_of::<NarwhalFileDialogRequestV1>() as u32,
            abi_version: ABI_VERSION,
            dialog_kind: DIALOG_SAVE,
            reserved: 0,
            owner_window: 0,
            title: slice(b"Save"),
            start_path: slice(&path),
            filters: ptr::null(),
            filter_count: 0,
            output: ptr::null_mut(),
            output_capacity: 0,
        };
        let mut result = NarwhalDialogResultV1::new();
        // SAFETY: Request and result records remain live for the synchronous call.
        assert_eq!(
            unsafe { narwhal_platform_file_dialog_v1(&request, &mut result) },
            STATUS_ERROR
        );
        assert_eq!(result.error_code, ERROR_LIMIT_EXCEEDED);
    }

    #[cfg(not(windows))]
    #[test]
    fn exported_dialogs_are_unavailable_off_windows() {
        let request = NarwhalFileDialogRequestV1 {
            struct_size: size_of::<NarwhalFileDialogRequestV1>() as u32,
            abi_version: ABI_VERSION,
            dialog_kind: DIALOG_OPEN,
            reserved: 0,
            owner_window: 0,
            title: slice("Open 図面".as_bytes()),
            start_path: slice(b"/tmp"),
            filters: ptr::null(),
            filter_count: 0,
            output: ptr::null_mut(),
            output_capacity: 0,
        };
        let mut result = NarwhalDialogResultV1::new();
        // SAFETY: Request and result records remain live for the synchronous call.
        assert_eq!(
            unsafe { narwhal_platform_file_dialog_v1(&request, &mut result) },
            STATUS_UNAVAILABLE
        );
        assert_eq!(result.status, STATUS_UNAVAILABLE);

        let message = NarwhalMessageBoxRequestV1 {
            struct_size: size_of::<NarwhalMessageBoxRequestV1>() as u32,
            abi_version: ABI_VERSION,
            reserved0: 0,
            reserved1: 0,
            owner_window: 0,
            title: slice(b"Question"),
            message: slice(b"Continue?"),
        };
        let mut message_result = NarwhalMessageBoxResultV1::new();
        // SAFETY: Request and result records remain live for the synchronous call.
        assert_eq!(
            unsafe { narwhal_platform_message_box_v1(&message, &mut message_result) },
            STATUS_UNAVAILABLE
        );
        assert_eq!(message_result.choice, CHOICE_NONE);
    }
}
