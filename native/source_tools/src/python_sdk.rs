//! CPython compiler/AST SDK only. No Python audit engine or executable reference.
//! Real compiler diagnostics and byte-source encoding are part of the source contract.
use libloading::Library;
use serde_json::{json, Value};
use std::{
    ffi::{c_char, c_int, c_void, CString},
    path::{Path, PathBuf},
    sync::{Mutex, OnceLock},
};
type Obj = *mut c_void;
type Unary = unsafe extern "C" fn(Obj) -> Obj;
#[repr(C)]
struct Flags {
    flags: c_int,
    feature: c_int,
}
pub struct Python {
    _lib: Library,
    compile: unsafe extern "C" fn(*const c_char, Obj, c_int, *mut Flags, c_int) -> Obj,
    unicode: unsafe extern "C" fn(*const c_char, isize) -> Obj,
    utf8: unsafe extern "C" fn(Obj, *mut isize) -> *const c_char,
    unicode_len: unsafe extern "C" fn(Obj) -> isize,
    unicode_char: unsafe extern "C" fn(Obj, isize) -> u32,
    attr: unsafe extern "C" fn(Obj, *const c_char) -> Obj,
    size: unsafe extern "C" fn(Obj) -> isize,
    item: unsafe extern "C" fn(Obj, isize) -> Obj,
    str_: Unary,
    err: unsafe extern "C" fn() -> Obj,
    clear: unsafe extern "C" fn(),
    dec: unsafe extern "C" fn(Obj),
    gil: unsafe extern "C" fn() -> c_int,
    release: unsafe extern "C" fn(c_int),
    type_: Unary,
}
fn library_paths(root: &Path) -> Vec<(PathBuf, Option<PathBuf>)> {
    if let Some(path) = std::env::var_os("NARWHAL_SOURCE_COMPILER_SDK") {
        return vec![(path.into(), None)];
    }
    let mut result = Vec::new();
    for runtime in [
        "tools/blender-patched/5.2/python",
        "tools/blender-5.2.2-linux-x64/5.2/python",
    ] {
        let p = root.join(runtime);
        for name in [
            "lib/libpython3.13.so.1.0",
            "lib/libpython3.13.dylib",
            "python313.dll",
        ] {
            result.push((p.join(name), Some(p.clone())));
        }
    }
    for name in [
        "libpython3.14.so.1.0",
        "libpython3.13.so.1.0",
        "libpython3.12.so.1.0",
        "python313.dll",
    ] {
        result.push((PathBuf::from(name), None));
    }
    result
}
impl Python {
    unsafe fn open(root: &Path) -> Result<Self, String> {
        let mut loaded = None;
        for (p, home) in library_paths(root) {
            if let Ok(lib) = Library::new(&p) {
                loaded = Some((lib, home));
                break;
            }
        }
        let (lib, home) =
            loaded.ok_or("CPython compiler SDK unavailable; source audit refuses to run")?;
        macro_rules! sym {
            ($s:literal,$t:ty) => {
                *lib.get::<$t>(concat!($s, "\0").as_bytes())
                    .map_err(|e| e.to_string())?
            };
        }
        let init = sym!("Py_InitializeEx", unsafe extern "C" fn(c_int));
        if let Some(home) = home {
            #[cfg(not(windows))]
            {
                let mut wide: Vec<i32> = home.to_string_lossy().chars().map(|c| c as i32).collect();
                wide.push(0);
                let set = sym!("Py_SetPythonHome", unsafe extern "C" fn(*const i32));
                set(Box::leak(wide.into_boxed_slice()).as_ptr());
            }
        }
        init(0);
        let py = Self {
            compile: sym!(
                "Py_CompileStringObject",
                unsafe extern "C" fn(*const c_char, Obj, c_int, *mut Flags, c_int) -> Obj
            ),
            unicode: sym!(
                "PyUnicode_FromStringAndSize",
                unsafe extern "C" fn(*const c_char, isize) -> Obj
            ),
            utf8: sym!(
                "PyUnicode_AsUTF8AndSize",
                unsafe extern "C" fn(Obj, *mut isize) -> *const c_char
            ),
            unicode_len: sym!("PyUnicode_GetLength", unsafe extern "C" fn(Obj) -> isize),
            unicode_char: sym!(
                "PyUnicode_ReadChar",
                unsafe extern "C" fn(Obj, isize) -> u32
            ),
            attr: sym!(
                "PyObject_GetAttrString",
                unsafe extern "C" fn(Obj, *const c_char) -> Obj
            ),
            size: sym!("PySequence_Size", unsafe extern "C" fn(Obj) -> isize),
            item: sym!(
                "PySequence_GetItem",
                unsafe extern "C" fn(Obj, isize) -> Obj
            ),
            str_: sym!("PyObject_Str", Unary),
            err: sym!("PyErr_GetRaisedException", unsafe extern "C" fn() -> Obj),
            clear: sym!("PyErr_Clear", unsafe extern "C" fn()),
            dec: sym!("Py_DecRef", unsafe extern "C" fn(Obj)),
            gil: sym!("PyGILState_Ensure", unsafe extern "C" fn() -> c_int),
            release: sym!("PyGILState_Release", unsafe extern "C" fn(c_int)),
            type_: sym!("PyObject_Type", Unary),
            _lib: lib,
        };
        let save = *py
            ._lib
            .get::<unsafe extern "C" fn() -> Obj>(b"PyEval_SaveThread\0")
            .map_err(|e| e.to_string())?;
        save();
        Ok(py)
    }
    unsafe fn get(&self, o: Obj, k: &str) -> Obj {
        (self.attr)(o, CString::new(k).unwrap().as_ptr())
    }
    unsafe fn text(&self, o: Obj) -> String {
        let mut n = 0;
        let p = (self.utf8)(o, &mut n);
        if p.is_null() {
            (self.clear)();
            // CPython permits lone surrogates in string constants. Retain the
            // surrounding policy text rather than treating encoding refusal as empty.
            self.unicode_with_surrogates(o)
        } else {
            String::from_utf8_lossy(std::slice::from_raw_parts(p.cast(), n as usize)).into_owned()
        }
    }
    unsafe fn unicode_with_surrogates(&self, o: Obj) -> String {
        let n = (self.unicode_len)(o);
        let mut out = String::new();
        for i in 0..n.max(0) {
            let c = (self.unicode_char)(o, i);
            out.push(char::from_u32(c).unwrap_or('\u{fffd}'));
        }
        out
    }
    unsafe fn display(&self, o: Obj) -> String {
        let s = (self.str_)(o);
        if s.is_null() {
            (self.clear)();
            return String::new();
        }
        let t = self.text(s);
        (self.dec)(s);
        t
    }
    unsafe fn convert(&self, o: Obj, depth: usize) -> Result<Value, String> {
        if depth > 512 {
            return Err("compiler AST nesting limit exceeded".into());
        }
        let ty = (self.type_)(o);
        let name = self.get(ty, "__name__");
        let kind = self.text(name);
        (self.dec)(name);
        (self.dec)(ty);
        match kind.as_str() {
            "NoneType" => Ok(Value::Null),
            "str" => Ok(Value::String(self.text(o))),
            "int" => Ok(self
                .display(o)
                .parse::<i64>()
                .map(Value::from)
                .unwrap_or(Value::Null)),
            "float" => Ok(self
                .display(o)
                .parse::<f64>()
                .ok()
                .and_then(serde_json::Number::from_f64)
                .map(Value::Number)
                .unwrap_or(Value::Null)),
            "bool" => Ok(Value::Bool(self.display(o) == "True")),
            "bytes" => {
                let f = *self
                    ._lib
                    .get::<unsafe extern "C" fn(Obj, *mut *mut c_char, *mut isize) -> c_int>(
                        b"PyBytes_AsStringAndSize\0",
                    )
                    .map_err(|e| e.to_string())?;
                let mut data = std::ptr::null_mut();
                let mut len = 0;
                f(o, &mut data, &mut len);
                Ok(json!({"_bytes":std::slice::from_raw_parts(data.cast::<u8>(),len as usize)}))
            }
            "list" | "tuple" => {
                let n = (self.size)(o);
                let mut out = Vec::new();
                for i in 0..n {
                    let x = (self.item)(o, i);
                    let v = self.convert(x, depth + 1);
                    (self.dec)(x);
                    out.push(v?);
                }
                Ok(Value::Array(out))
            }
            _ => {
                let fields = self.get(o, "_fields");
                if fields.is_null() {
                    (self.clear)();
                    return Ok(Value::Null);
                }
                let n = (self.size)(fields);
                let mut result = serde_json::Map::new();
                result.insert("_type".into(), kind.into());
                for i in 0..n {
                    let k = (self.item)(fields, i);
                    let key = self.text(k);
                    (self.dec)(k);
                    let x = self.get(o, &key);
                    if !x.is_null() {
                        let v = self.convert(x, depth + 1);
                        (self.dec)(x);
                        result.insert(key, v?);
                    } else {
                        (self.clear)();
                    }
                }
                (self.dec)(fields);
                for k in ["lineno", "col_offset", "end_lineno", "end_col_offset"] {
                    let x = self.get(o, k);
                    if !x.is_null() {
                        result.insert(k.to_string(), self.convert(x, depth + 1)?);
                        (self.dec)(x);
                    } else {
                        (self.clear)();
                    }
                }
                Ok(Value::Object(result))
            }
        }
    }
    fn compile(&self, blob: &[u8], path: &str, ast: bool) -> Result<Value, String> {
        unsafe {
            let gil = (self.gil)();
            let result = (|| {
                let code = CString::new(blob)
                    .map_err(|_| "source code string cannot contain null bytes".to_string())?;
                let filename = (self.unicode)(path.as_ptr().cast(), path.len() as isize);
                let mut flags = Flags {
                    flags: if ast { 0x400 } else { 0 },
                    feature: 13,
                };
                let obj = (self.compile)(code.as_ptr(), filename, 257, &mut flags, -1);
                (self.dec)(filename);
                if obj.is_null() {
                    let err = (self.err)();
                    let text = self.display(err);
                    (self.dec)(err);
                    return Err(text);
                }
                let value = if ast {
                    self.convert(obj, 0)
                } else {
                    Ok(Value::Null)
                };
                (self.dec)(obj);
                value
            })();
            (self.release)(gil);
            result
        }
    }
}
static SDK: OnceLock<Result<Mutex<Python>, String>> = OnceLock::new();
pub fn parse(root: &Path, data: &[u8], path: &str) -> Result<Value, String> {
    sdk(root)?
        .lock()
        .map_err(|_| "compiler lock poisoned".to_string())?
        .compile(data, path, true)
}
pub fn validate(root: &Path, data: &[u8], path: &str) -> Result<(), String> {
    sdk(root)?
        .lock()
        .map_err(|_| "compiler lock poisoned".to_string())?
        .compile(data, path, false)
        .map(|_| ())
}
fn sdk(root: &Path) -> Result<&'static Mutex<Python>, String> {
    SDK.get_or_init(|| unsafe { Python::open(root).map(Mutex::new) })
        .as_ref()
        .map_err(Clone::clone)
}
pub fn walk<'a>(v: &'a Value, out: &mut Vec<&'a Value>) {
    if v.get("_type").is_some() {
        out.push(v);
    }
    match v {
        Value::Object(m) => {
            for v in m.values() {
                walk(v, out)
            }
        }
        Value::Array(a) => {
            for v in a {
                walk(v, out)
            }
        }
        _ => {}
    }
}
