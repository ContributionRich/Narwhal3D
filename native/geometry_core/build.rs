use std::collections::BTreeMap;
use std::env;
use std::ffi::OsStr;
use std::fmt::Write as _;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

const EXPECTED_VERSION: (u32, u32, u32) = (8, 0, 1);
const EXPECTED_COMMIT: &str = "b8f597c677811d1f9f4d8a97f5ae2825c0353a42";
const SOURCE_MARKERS: &[(&str, &str)] = &[
    (
        "adm/cmake/version.cmake",
        "e2c38b918e40bdeedeac86d505471fb92a73461d45242b621304c951a931b63c",
    ),
    (
        "adm/templates/Standard_Version.hxx.in",
        "6a26704341ca9870d91ec0daced4ee5d0049c4bbed1800f7399952c0057c9557",
    ),
    (
        "src/ModelingData/TKBRep/BinTools/BinTools.hxx",
        "ad18702f1a6f241f4665c5c9b7161a5603de5708ea1a8ce04c8a85211bea13ff",
    ),
    (
        "OCCT_LGPL_EXCEPTION.txt",
        "04580a884ea6cea294402649ff7b5cbb167d47462d1340a4ed33e550db10a81b",
    ),
];

const REQUIRED_HEADERS: &[&str] = &[
    "BinTools.hxx",
    "BRepAdaptor_Surface.hxx",
    "BRepCheck_Analyzer.hxx",
    "BRepGProp.hxx",
    "BRepPrimAPI_MakeBox.hxx",
    "BRep_Tool.hxx",
    "GProp_GProps.hxx",
    "GeomAbs_SurfaceType.hxx",
    "Standard_Failure.hxx",
    "Standard_Version.hxx",
    "TopAbs_ShapeEnum.hxx",
    "TopExp.hxx",
    "NCollection_IndexedMap.hxx",
    "TopTools_ShapeMapHasher.hxx",
    "TopoDS.hxx",
];

const REQUIRED_LIBRARIES: &[&str] = &[
    "TKBin",
    "TKFillet",
    "TKBO",
    "TKShHealing",
    "TKGeomAlgo",
    "TKPrim",
    "TKTopAlgo",
    "TKBRep",
    "TKGeomBase",
    "TKG3d",
    "TKG2d",
    "TKMath",
    "TKernel",
];

const SHA256_INITIAL: [u32; 8] = [
    0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
];
const SHA256_ROUND: [u32; 64] = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
];

fn required_directory(variable: &str) -> PathBuf {
    let value = env::var_os(variable).unwrap_or_else(|| {
        panic!("{variable} must be set explicitly; implicit OCCT discovery is forbidden")
    });
    let path = fs::canonicalize(&value).unwrap_or_else(|error| {
        panic!(
            "{variable}={} is not usable: {error}",
            Path::new(&value).display()
        )
    });
    assert!(
        path.is_dir(),
        "{variable}={} is not a directory",
        path.display()
    );
    path
}

fn sha256_hex(input: &[u8]) -> String {
    let bit_length = (input.len() as u64).wrapping_mul(8);
    let mut padded = input.to_vec();
    padded.push(0x80);
    while padded.len() % 64 != 56 {
        padded.push(0);
    }
    padded.extend_from_slice(&bit_length.to_be_bytes());

    let mut state = SHA256_INITIAL;
    for chunk in padded.chunks_exact(64) {
        let mut words = [0_u32; 64];
        for (index, bytes) in chunk.chunks_exact(4).enumerate() {
            words[index] = u32::from_be_bytes([bytes[0], bytes[1], bytes[2], bytes[3]]);
        }
        for index in 16..64 {
            let s0 = words[index - 15].rotate_right(7)
                ^ words[index - 15].rotate_right(18)
                ^ (words[index - 15] >> 3);
            let s1 = words[index - 2].rotate_right(17)
                ^ words[index - 2].rotate_right(19)
                ^ (words[index - 2] >> 10);
            words[index] = words[index - 16]
                .wrapping_add(s0)
                .wrapping_add(words[index - 7])
                .wrapping_add(s1);
        }

        let [mut a, mut b, mut c, mut d, mut e, mut f, mut g, mut h] = state;
        for index in 0..64 {
            let sum1 = h
                .wrapping_add(e.rotate_right(6) ^ e.rotate_right(11) ^ e.rotate_right(25))
                .wrapping_add((e & f) ^ ((!e) & g))
                .wrapping_add(SHA256_ROUND[index])
                .wrapping_add(words[index]);
            let sum0 = (a.rotate_right(2) ^ a.rotate_right(13) ^ a.rotate_right(22))
                .wrapping_add((a & b) ^ (a & c) ^ (b & c));
            h = g;
            g = f;
            f = e;
            e = d.wrapping_add(sum1);
            d = c;
            c = b;
            b = a;
            a = sum0.wrapping_add(sum1);
        }
        for (slot, value) in state.iter_mut().zip([a, b, c, d, e, f, g, h]) {
            *slot = slot.wrapping_add(value);
        }
    }

    let mut output = String::with_capacity(64);
    for word in state {
        write!(&mut output, "{word:08x}").expect("writing to String cannot fail");
    }
    output
}

fn read_checked(path: &Path) -> Vec<u8> {
    fs::read(path).unwrap_or_else(|error| panic!("cannot read {}: {error}", path.display()))
}

fn normalized_source_marker(bytes: &[u8]) -> Vec<u8> {
    let mut normalized = Vec::with_capacity(bytes.len());
    let mut index = 0;
    while index < bytes.len() {
        if bytes[index] == b'\r' && bytes.get(index + 1) == Some(&b'\n') {
            normalized.push(b'\n');
            index += 2;
        } else {
            normalized.push(bytes[index]);
            index += 1;
        }
    }
    normalized
}

fn verify_source_markers(source_dir: &Path) {
    for (relative, expected_hash) in SOURCE_MARKERS {
        let path = source_dir.join(relative);
        let actual_hash = sha256_hex(&normalized_source_marker(&read_checked(&path)));
        assert_eq!(
            &actual_hash,
            expected_hash,
            "OCCT source marker mismatch for {}; expected official V8_0_1 peeled commit {EXPECTED_COMMIT}",
            path.display()
        );
    }
}

fn resolve_head(git_dir: &Path) -> Option<String> {
    let head = fs::read_to_string(git_dir.join("HEAD")).ok()?;
    let head = head.trim();
    let Some(reference) = head.strip_prefix("ref: ") else {
        return Some(head.to_owned());
    };
    if let Ok(value) = fs::read_to_string(git_dir.join(reference)) {
        return Some(value.trim().to_owned());
    }
    let packed = fs::read_to_string(git_dir.join("packed-refs")).ok()?;
    packed.lines().find_map(|line| {
        let (hash, name) = line.split_once(' ')?;
        (name == reference).then(|| hash.to_owned())
    })
}

fn verify_optional_checkout_identity(source_dir: &Path) {
    let git_path = source_dir.join(".git");
    if !git_path.is_dir() {
        return;
    }
    if let Some(head) = resolve_head(&git_path) {
        assert_eq!(
            head, EXPECTED_COMMIT,
            "OCCT checkout HEAD is not official V8_0_1 peeled commit {EXPECTED_COMMIT}"
        );
    }
}

fn cmake_version_value(contents: &str, key: &str) -> Option<u32> {
    for line in contents.lines() {
        let tokens = line
            .split(|character: char| {
                character.is_whitespace() || character == '(' || character == ')'
            })
            .filter(|token| !token.is_empty())
            .collect::<Vec<_>>();
        if tokens.len() == 3 && tokens[0] == "set" && tokens[1] == key {
            return tokens[2].parse().ok();
        }
    }
    None
}

fn generate_version_header(source_dir: &Path, include_dir: &Path) {
    let version_path = source_dir.join("adm/cmake/version.cmake");
    let version_text = fs::read_to_string(&version_path)
        .unwrap_or_else(|error| panic!("cannot read {}: {error}", version_path.display()));
    let actual = (
        cmake_version_value(&version_text, "OCC_VERSION_MAJOR"),
        cmake_version_value(&version_text, "OCC_VERSION_MINOR"),
        cmake_version_value(&version_text, "OCC_VERSION_MAINTENANCE"),
    );
    assert_eq!(
        actual,
        (
            Some(EXPECTED_VERSION.0),
            Some(EXPECTED_VERSION.1),
            Some(EXPECTED_VERSION.2)
        ),
        "official OCCT version source must say 8.0.1"
    );

    let template_path = source_dir.join("adm/templates/Standard_Version.hxx.in");
    let mut generated = fs::read_to_string(&template_path)
        .unwrap_or_else(|error| panic!("cannot read {}: {error}", template_path.display()));
    for (placeholder, value) in [
        ("@OCCT_VERSION_DATE@", "official V8_0_1"),
        ("@OCC_VERSION_MAJOR@", "8"),
        ("@OCC_VERSION_MINOR@", "0"),
        ("@OCC_VERSION_MAINTENANCE@", "1"),
        (
            "@SET_OCC_VERSION_DEVELOPMENT@",
            "// Official release: OCC_VERSION_DEVELOPMENT is intentionally undefined.",
        ),
    ] {
        generated = generated.replace(placeholder, value);
    }
    assert!(
        !generated.contains('@')
            && generated.contains("#define OCC_VERSION_MAJOR 8")
            && generated.contains("#define OCC_VERSION_MINOR 0")
            && generated.contains("#define OCC_VERSION_MAINTENANCE 1")
            && generated.contains("#define OCC_VERSION_COMPLETE \"8.0.1\""),
        "generated Standard_Version.hxx does not identify OCCT 8.0.1"
    );
    fs::write(include_dir.join("Standard_Version.hxx"), generated)
        .expect("cannot write generated Standard_Version.hxx");
}

fn collect_headers(directory: &Path, output: &mut Vec<PathBuf>) {
    let mut entries = fs::read_dir(directory)
        .unwrap_or_else(|error| panic!("cannot traverse {}: {error}", directory.display()))
        .map(|entry| entry.expect("cannot read OCCT source directory entry"))
        .collect::<Vec<_>>();
    entries.sort_by_key(|entry| entry.file_name());
    for entry in entries {
        let path = entry.path();
        let file_type = entry
            .file_type()
            .unwrap_or_else(|error| panic!("cannot inspect {}: {error}", path.display()));
        if file_type.is_dir() {
            collect_headers(&path, output);
        } else if file_type.is_file()
            && matches!(
                path.extension().and_then(OsStr::to_str),
                Some("hxx" | "lxx" | "gxx")
            )
        {
            output.push(path);
        }
    }
}

fn install_header(source: &Path, destination: &Path) {
    #[cfg(unix)]
    {
        if std::os::unix::fs::symlink(source, destination).is_ok() {
            return;
        }
    }
    fs::copy(source, destination).unwrap_or_else(|error| {
        panic!(
            "cannot copy OCCT header {} to {}: {error}",
            source.display(),
            destination.display()
        )
    });
}

fn flatten_headers(source_dir: &Path, out_dir: &Path) -> PathBuf {
    let source_tree = source_dir.join("src");
    assert!(
        source_tree.is_dir(),
        "OCCT source root has no src directory"
    );
    let include_dir = out_dir.join("occt-include");
    if include_dir.exists() {
        fs::remove_dir_all(&include_dir).expect("cannot replace flattened OCCT include directory");
    }
    fs::create_dir(&include_dir).expect("cannot create flattened OCCT include directory");

    let mut discovered = Vec::new();
    collect_headers(&source_tree, &mut discovered);
    assert!(
        !discovered.is_empty(),
        "OCCT source tree contains no headers"
    );
    let mut by_basename = BTreeMap::<String, PathBuf>::new();
    for source in discovered {
        let basename = source
            .file_name()
            .and_then(OsStr::to_str)
            .unwrap_or_else(|| panic!("non-UTF-8 OCCT header basename: {}", source.display()))
            .to_owned();
        if let Some(previous) = by_basename.get(&basename) {
            assert!(
                read_checked(previous) == read_checked(&source),
                "OCCT flattening collision: {} and {} have basename {basename} but different contents",
                previous.display(),
                source.display()
            );
            continue;
        }
        by_basename.insert(basename, source);
    }
    assert!(
        !by_basename.contains_key("Standard_Version.hxx"),
        "source unexpectedly contains generated Standard_Version.hxx"
    );
    for (basename, source) in by_basename {
        install_header(&source, &include_dir.join(basename));
    }
    generate_version_header(source_dir, &include_dir);
    include_dir
}

fn is_shared_library_name(name: &str, stable_name: &str, msvc: bool) -> bool {
    if msvc {
        let prefix = format!("{stable_name}-");
        return name.starts_with(&prefix) && name.to_ascii_lowercase().ends_with(".dll");
    }
    let prefix = format!("lib{stable_name}-");
    let plain_prefix = format!("lib{stable_name}.so");
    (name.starts_with(&prefix) && name.contains(".so")) || name.starts_with(&plain_prefix)
}

fn is_macos_library_name(name: &str, stable_name: &str) -> bool {
    if name == format!("lib{stable_name}.dylib") {
        return true;
    }
    name.strip_prefix(&format!("lib{stable_name}."))
        .and_then(|n| n.strip_suffix(".dylib"))
        .is_some_and(|version| {
            !version.is_empty()
                && version
                    .split('.')
                    .all(|part| !part.is_empty() && part.bytes().all(|c| c.is_ascii_digit()))
        })
}

fn discover_shared_library(directory: &Path, stable_name: &str, msvc: bool) -> PathBuf {
    let macos = env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("macos");
    let mut matches = fs::read_dir(directory)
        .unwrap_or_else(|error| {
            panic!(
                "cannot read OCCT library directory {}: {error}",
                directory.display()
            )
        })
        .filter_map(Result::ok)
        .map(|entry| entry.path())
        .filter(|path| path.is_file())
        .filter(|path| {
            let Some(name) = path.file_name().and_then(OsStr::to_str) else {
                return false;
            };
            if macos {
                is_macos_library_name(name, stable_name)
            } else {
                is_shared_library_name(name, stable_name, msvc)
            }
        })
        .collect::<Vec<_>>();
    matches.sort();
    match matches.as_slice() {
        [only] => only.clone(),
        [] => panic!(
            "missing OCCT shared library {stable_name}: expected exactly one {} in {}",
            if msvc {
                format!("{stable_name}-*.dll")
            } else {
                format!("lib{stable_name}-*.so* or lib{stable_name}.so*")
            },
            directory.display()
        ),
        many => panic!(
            "ambiguous OCCT shared library {stable_name} in {}: {}",
            directory.display(),
            many.iter()
                .map(|path| path.display().to_string())
                .collect::<Vec<_>>()
                .join(", ")
        ),
    }
}

fn run(command: &mut Command, description: &str) {
    let status = command
        .status()
        .unwrap_or_else(|error| panic!("failed to invoke {description}: {error}"));
    assert!(status.success(), "{description} failed with {status}");
}

fn run_header_smoke_gnu(out_dir: &Path) {
    const CONSUMER: &str = r#"#include <narwhal_geometry_core.h>
int main(void) {
#ifdef __cplusplus
  NarwhalBrepInspectionV1 inspection{};
  NarwhalLocalMoveCertificateV3 certificate{};
  NarwhalEditSessionHandle handle{};
  unsigned char buffer[8]{};
#else
  NarwhalBrepInspectionV1 inspection = {0};
  NarwhalLocalMoveCertificateV3 certificate = {0};
  NarwhalEditSessionHandle handle = 0;
  unsigned char buffer[8] = {0};
#endif
  const uint32_t abi = narwhal_geometry_core_abi_version();
  const uint64_t caps = narwhal_geometry_core_capabilities();
  (void)narwhal_geometry_core_make_box_brep(1.0, 2.0, 3.0, buffer, sizeof(buffer));
  (void)narwhal_geometry_core_inspect_brep(buffer, sizeof(buffer), &inspection);
  (void)narwhal_geometry_core_move_component_brep(
      buffer, sizeof(buffer), NARWHAL_GEOMETRY_COMPONENT_FACE, 1,
      1.0, 0.0, 0.0, buffer, sizeof(buffer), &certificate);
  (void)narwhal_geometry_core_edit_session_import(buffer, sizeof(buffer), &handle);
  (void)narwhal_geometry_core_edit_session_move_mode(
      handle, NARWHAL_GEOMETRY_VALIDATION_LIVE,
      NARWHAL_GEOMETRY_COMPONENT_FACE, 1, 1.0, 0.0, 0.0,
      buffer, sizeof(buffer), &certificate);
  (void)narwhal_geometry_core_edit_session_move(
      handle, NARWHAL_GEOMETRY_COMPONENT_FACE, 1, 1.0, 0.0, 0.0,
      buffer, sizeof(buffer), &certificate);
  (void)narwhal_geometry_core_edit_session_release(handle);
  return (abi == NARWHAL_GEOMETRY_CORE_ABI_VERSION && caps != 0) ? 0 : 1;
}
"#;
    const PROVIDER: &str = r#"#include <narwhal_geometry_core.h>
uint32_t narwhal_geometry_core_abi_version(void) { return NARWHAL_GEOMETRY_CORE_ABI_VERSION; }
uint64_t narwhal_geometry_core_capabilities(void) { return NARWHAL_GEOMETRY_CAP_TOPOLOGY_LOCAL_MOVE; }
int64_t narwhal_geometry_core_make_box_brep(double a, double b, double c, uint8_t *o, size_t n) {
  (void)a; (void)b; (void)c; (void)o; (void)n; return NARWHAL_GEOMETRY_ERROR_BUFFER_TOO_SMALL;
}
int32_t narwhal_geometry_core_inspect_brep(const uint8_t *i, size_t n, NarwhalBrepInspectionV1 *o) {
  (void)i; (void)n; (void)o; return NARWHAL_GEOMETRY_ERROR_MALFORMED_BREP;
}
int64_t narwhal_geometry_core_move_component_brep(
    const uint8_t *i, size_t n, uint32_t k, uint64_t x, double dx, double dy, double dz,
    uint8_t *o, size_t c, NarwhalLocalMoveCertificateV3 *q) {
  (void)i; (void)n; (void)k; (void)x; (void)dx; (void)dy; (void)dz; (void)o; (void)c; (void)q;
  return NARWHAL_GEOMETRY_ERROR_POLICY_REFUSED;
}
int32_t narwhal_geometry_core_edit_session_import(
    const uint8_t *i, size_t n, NarwhalEditSessionHandle *h) {
  (void)i; (void)n; *h = UINT64_C(0x100000001); return NARWHAL_GEOMETRY_OK;
}
int64_t narwhal_geometry_core_edit_session_move_mode(
    NarwhalEditSessionHandle h, uint32_t m, uint32_t k, uint64_t x,
    double dx, double dy, double dz, uint8_t *o, size_t c,
    NarwhalLocalMoveCertificateV3 *q) {
  (void)h; (void)m; (void)k; (void)x; (void)dx; (void)dy; (void)dz; (void)o; (void)c; (void)q;
  return NARWHAL_GEOMETRY_ERROR_BUFFER_TOO_SMALL;
}
int64_t narwhal_geometry_core_edit_session_move(
    NarwhalEditSessionHandle h, uint32_t k, uint64_t x,
    double dx, double dy, double dz, uint8_t *o, size_t c,
    NarwhalLocalMoveCertificateV3 *q) {
  return narwhal_geometry_core_edit_session_move_mode(
      h, NARWHAL_GEOMETRY_VALIDATION_COMMIT, k, x, dx, dy, dz, o, c, q);
}
int32_t narwhal_geometry_core_edit_session_release(NarwhalEditSessionHandle h) {
  (void)h; return NARWHAL_GEOMETRY_OK;
}
"#;
    let include_dir = fs::canonicalize("include").expect("cannot locate public include directory");
    let consumer_c = out_dir.join("header_smoke.c");
    let consumer_cpp = out_dir.join("header_smoke.cpp");
    let provider_c = out_dir.join("header_smoke_provider.c");
    fs::write(&consumer_c, CONSUMER).expect("cannot write C header smoke consumer");
    fs::write(&consumer_cpp, CONSUMER).expect("cannot write C++ header smoke consumer");
    fs::write(&provider_c, PROVIDER).expect("cannot write header smoke provider");

    let cc = env::var_os("CC").unwrap_or_else(|| "cc".into());
    let cxx = env::var_os("CXX").unwrap_or_else(|| "c++".into());
    let ar = env::var_os("AR").unwrap_or_else(|| "ar".into());
    let provider_object = out_dir.join("header_smoke_provider.o");
    run(
        Command::new(&cc)
            .arg("-std=c11")
            .arg("-Wall")
            .arg("-Wextra")
            .arg("-Werror")
            .arg("-fPIC")
            .arg("-I")
            .arg(&include_dir)
            .arg("-c")
            .arg(&provider_c)
            .arg("-o")
            .arg(&provider_object),
        "C public-header smoke provider compile",
    );
    let static_provider = out_dir.join("libnarwhal_header_smoke.a");
    run(
        Command::new(&ar)
            .arg("crs")
            .arg(&static_provider)
            .arg(&provider_object),
        "public-header smoke static archive",
    );
    let macos = env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("macos");
    let shared_provider = out_dir.join(if macos {
        "libnarwhal_header_smoke_shared.dylib"
    } else {
        "libnarwhal_header_smoke_shared.so"
    });
    let mut shared_command = Command::new(&cc);
    shared_command.arg(if macos { "-dynamiclib" } else { "-shared" });
    if macos {
        shared_command.arg("-Wl,-install_name,@rpath/libnarwhal_header_smoke_shared.dylib");
    }
    run(
        shared_command
            .arg(&provider_object)
            .arg("-o")
            .arg(&shared_provider),
        "public-header smoke shared library",
    );

    for (language, compiler, source) in [("c", &cc, &consumer_c), ("cpp", &cxx, &consumer_cpp)] {
        let standard = if language == "c" {
            "-std=c11"
        } else {
            "-std=c++17"
        };
        let static_executable = out_dir.join(format!("header_smoke_{language}_static"));
        run(
            Command::new(compiler)
                .arg(standard)
                .arg("-Wall")
                .arg("-Wextra")
                .arg("-Werror")
                .arg("-I")
                .arg(&include_dir)
                .arg(source)
                .arg(&static_provider)
                .arg("-o")
                .arg(&static_executable),
            "public-header static consumer link",
        );
        run(
            &mut Command::new(&static_executable),
            "public-header static consumer run",
        );

        let shared_executable = out_dir.join(format!("header_smoke_{language}_shared"));
        run(
            Command::new(compiler)
                .arg(standard)
                .arg("-Wall")
                .arg("-Wextra")
                .arg("-Werror")
                .arg("-I")
                .arg(&include_dir)
                .arg(source)
                .arg(&shared_provider)
                .arg(format!("-Wl,-rpath,{}", out_dir.display()))
                .arg("-o")
                .arg(&shared_executable),
            "public-header shared consumer link",
        );
        run(
            &mut Command::new(&shared_executable),
            "public-header shared consumer run",
        );
    }
}

fn parse_dumpbin_exports(output: &str) -> Vec<String> {
    let mut exports = Vec::new();
    let mut in_table = false;
    for line in output.lines() {
        let trimmed = line.trim();
        if trimmed.starts_with("ordinal") && trimmed.contains("hint") && trimmed.contains("name") {
            in_table = true;
            continue;
        }
        if !in_table {
            continue;
        }
        let fields = trimmed.split_whitespace().collect::<Vec<_>>();
        if fields.len() < 4
            || !fields[0].bytes().all(|byte| byte.is_ascii_digit())
            || !fields[1].bytes().all(|byte| byte.is_ascii_hexdigit())
            || !fields[2].bytes().all(|byte| byte.is_ascii_hexdigit())
        {
            continue;
        }
        let name = fields[3];
        if name != "[NONAME]" && name.is_ascii() && !name.contains(char::is_whitespace) {
            exports.push(name.to_owned());
        }
    }
    exports.sort();
    exports.dedup();
    exports
}

fn command_output(command: &mut Command, description: &str) -> String {
    let output = command
        .output()
        .unwrap_or_else(|error| panic!("failed to invoke {description}: {error}"));
    assert!(
        output.status.success(),
        "{description} failed with {}: {}",
        output.status,
        String::from_utf8_lossy(&output.stderr)
    );
    String::from_utf8(output.stdout)
        .unwrap_or_else(|error| panic!("{description} emitted non-UTF-8 output: {error}"))
}

fn msvc_import_library(dll: &Path, stable_name: &str, out_dir: &Path) -> PathBuf {
    let dumpbin = env::var_os("DUMPBIN").unwrap_or_else(|| "dumpbin.exe".into());
    let output = command_output(
        Command::new(dumpbin)
            .arg("/nologo")
            .arg("/exports")
            .arg(dll),
        "MSVC dumpbin export inspection",
    );
    let exports = parse_dumpbin_exports(&output);
    assert!(
        !exports.is_empty(),
        "OCCT DLL {} exposes no named exports",
        dll.display()
    );
    let dll_name = dll
        .file_name()
        .and_then(OsStr::to_str)
        .unwrap_or_else(|| panic!("non-UTF-8 OCCT DLL basename: {}", dll.display()));
    let def = out_dir.join(format!("{stable_name}.def"));
    let mut contents = format!("LIBRARY \"{dll_name}\"\r\nEXPORTS\r\n");
    for symbol in exports {
        writeln!(&mut contents, "    {symbol}").expect("writing import definition cannot fail");
    }
    fs::write(&def, contents).expect("cannot write deterministic OCCT import definition");
    let library = out_dir.join(format!("{stable_name}.lib"));
    let librarian = env::var_os("NARWHAL_MSVC_LIB").unwrap_or_else(|| "lib.exe".into());
    run(
        Command::new(librarian)
            .arg("/nologo")
            .arg("/BREPRO")
            .arg("/MACHINE:X64")
            .arg(format!("/DEF:{}", def.display()))
            .arg(format!("/OUT:{}", library.display())),
        "deterministic MSVC OCCT import-library generation",
    );
    library
}

fn run_header_smoke_msvc(out_dir: &Path) {
    const CONSUMER: &str = r#"#include <narwhal_geometry_core.h>
int main(void) {
#ifdef __cplusplus
  NarwhalBrepInspectionV1 inspection{};
  NarwhalLocalMoveCertificateV3 certificate{};
  NarwhalEditSessionHandle handle{};
  unsigned char buffer[8]{};
#else
  NarwhalBrepInspectionV1 inspection = {0};
  NarwhalLocalMoveCertificateV3 certificate = {0};
  NarwhalEditSessionHandle handle = 0;
  unsigned char buffer[8] = {0};
#endif
  const uint32_t abi = narwhal_geometry_core_abi_version();
  const uint64_t caps = narwhal_geometry_core_capabilities();
  (void)narwhal_geometry_core_make_box_brep(1.0, 2.0, 3.0, buffer, sizeof(buffer));
  (void)narwhal_geometry_core_inspect_brep(buffer, sizeof(buffer), &inspection);
  (void)narwhal_geometry_core_move_component_brep(buffer, sizeof(buffer),
      NARWHAL_GEOMETRY_COMPONENT_FACE, 1, 1.0, 0.0, 0.0,
      buffer, sizeof(buffer), &certificate);
  (void)narwhal_geometry_core_edit_session_import(buffer, sizeof(buffer), &handle);
  (void)narwhal_geometry_core_edit_session_move_mode(handle,
      NARWHAL_GEOMETRY_VALIDATION_LIVE, NARWHAL_GEOMETRY_COMPONENT_FACE, 1,
      1.0, 0.0, 0.0, buffer, sizeof(buffer), &certificate);
  (void)narwhal_geometry_core_edit_session_move(handle,
      NARWHAL_GEOMETRY_COMPONENT_FACE, 1, 1.0, 0.0, 0.0,
      buffer, sizeof(buffer), &certificate);
  (void)narwhal_geometry_core_edit_session_release(handle);
  return (abi == NARWHAL_GEOMETRY_CORE_ABI_VERSION && caps != 0) ? 0 : 1;
}
"#;
    const PROVIDER: &str = r#"#include <narwhal_geometry_core.h>
uint32_t narwhal_geometry_core_abi_version(void) { return NARWHAL_GEOMETRY_CORE_ABI_VERSION; }
uint64_t narwhal_geometry_core_capabilities(void) { return NARWHAL_GEOMETRY_CAP_TOPOLOGY_LOCAL_MOVE; }
int64_t narwhal_geometry_core_make_box_brep(double a, double b, double c, uint8_t *o, size_t n) {
  (void)a; (void)b; (void)c; (void)o; (void)n; return NARWHAL_GEOMETRY_ERROR_BUFFER_TOO_SMALL;
}
int32_t narwhal_geometry_core_inspect_brep(const uint8_t *i, size_t n, NarwhalBrepInspectionV1 *o) {
  (void)i; (void)n; (void)o; return NARWHAL_GEOMETRY_ERROR_MALFORMED_BREP;
}
int64_t narwhal_geometry_core_move_component_brep(const uint8_t *i, size_t n, uint32_t k,
    uint64_t x, double dx, double dy, double dz, uint8_t *o, size_t c,
    NarwhalLocalMoveCertificateV3 *q) {
  (void)i; (void)n; (void)k; (void)x; (void)dx; (void)dy; (void)dz; (void)o; (void)c; (void)q;
  return NARWHAL_GEOMETRY_ERROR_POLICY_REFUSED;
}
int32_t narwhal_geometry_core_edit_session_import(const uint8_t *i, size_t n,
    NarwhalEditSessionHandle *h) {
  (void)i; (void)n; *h = UINT64_C(0x100000001); return NARWHAL_GEOMETRY_OK;
}
int64_t narwhal_geometry_core_edit_session_move_mode(NarwhalEditSessionHandle h, uint32_t m,
    uint32_t k, uint64_t x, double dx, double dy, double dz, uint8_t *o, size_t c,
    NarwhalLocalMoveCertificateV3 *q) {
  (void)h; (void)m; (void)k; (void)x; (void)dx; (void)dy; (void)dz; (void)o; (void)c; (void)q;
  return NARWHAL_GEOMETRY_ERROR_BUFFER_TOO_SMALL;
}
int64_t narwhal_geometry_core_edit_session_move(NarwhalEditSessionHandle h, uint32_t k,
    uint64_t x, double dx, double dy, double dz, uint8_t *o, size_t c,
    NarwhalLocalMoveCertificateV3 *q) {
  return narwhal_geometry_core_edit_session_move_mode(h, NARWHAL_GEOMETRY_VALIDATION_COMMIT,
      k, x, dx, dy, dz, o, c, q);
}
int32_t narwhal_geometry_core_edit_session_release(NarwhalEditSessionHandle h) {
  (void)h; return NARWHAL_GEOMETRY_OK;
}
"#;
    // `canonicalize` adds a `\\?\` prefix on Windows. `cl.exe /I` does not
    // consistently accept that extended form, and Cargo already runs this
    // build script from the crate root.
    let include_dir = env::current_dir()
        .expect("cannot locate geometry-core crate directory")
        .join("include");
    assert!(
        include_dir.is_dir(),
        "cannot locate public include directory"
    );
    let consumer_c = out_dir.join("header_smoke.c");
    let consumer_cpp = out_dir.join("header_smoke.cpp");
    let provider_c = out_dir.join("header_smoke_provider.c");
    fs::write(&consumer_c, CONSUMER).expect("cannot write C header smoke consumer");
    fs::write(&consumer_cpp, CONSUMER).expect("cannot write C++ header smoke consumer");
    fs::write(&provider_c, PROVIDER).expect("cannot write header smoke provider");
    let compiler = env::var_os("CC").unwrap_or_else(|| "cl.exe".into());
    let cxx = env::var_os("CXX").unwrap_or_else(|| "cl.exe".into());
    let librarian = env::var_os("NARWHAL_MSVC_LIB").unwrap_or_else(|| "lib.exe".into());
    let provider_object = out_dir.join("header_smoke_provider.obj");
    run(
        Command::new(&compiler)
            .arg("/nologo")
            .arg("/std:c11")
            .arg("/W4")
            .arg("/WX")
            .arg("/MD")
            .arg(format!("/I{}", include_dir.display()))
            .arg("/c")
            .arg(&provider_c)
            .arg(format!("/Fo{}", provider_object.display())),
        "MSVC C public-header provider compile",
    );
    let static_provider = out_dir.join("narwhal_header_smoke.lib");
    run(
        Command::new(&librarian)
            .arg("/nologo")
            .arg("/BREPRO")
            .arg(format!("/OUT:{}", static_provider.display()))
            .arg(&provider_object),
        "MSVC public-header static archive",
    );
    let shared_provider = out_dir.join("narwhal_header_smoke_shared.dll");
    let shared_import = out_dir.join("narwhal_header_smoke_shared.lib");
    let mut shared = Command::new(&compiler);
    shared
        .arg("/nologo")
        .arg("/std:c11")
        .arg("/W4")
        .arg("/WX")
        .arg("/MD")
        .arg("/LD")
        .arg(format!("/I{}", include_dir.display()))
        .arg(&provider_c)
        .arg(format!("/Fe{}", shared_provider.display()))
        .arg("/link")
        .arg("/BREPRO")
        .arg(format!("/IMPLIB:{}", shared_import.display()));
    for symbol in [
        "narwhal_geometry_core_abi_version",
        "narwhal_geometry_core_capabilities",
        "narwhal_geometry_core_make_box_brep",
        "narwhal_geometry_core_inspect_brep",
        "narwhal_geometry_core_move_component_brep",
        "narwhal_geometry_core_edit_session_import",
        "narwhal_geometry_core_edit_session_move_mode",
        "narwhal_geometry_core_edit_session_move",
        "narwhal_geometry_core_edit_session_release",
    ] {
        shared.arg(format!("/EXPORT:{symbol}"));
    }
    run(&mut shared, "MSVC public-header shared provider link");
    for (language, compiler, source) in
        [("c", &compiler, &consumer_c), ("cpp", &cxx, &consumer_cpp)]
    {
        let standard = if language == "c" {
            "/std:c11"
        } else {
            "/std:c++17"
        };
        for (kind, provider) in [("static", &static_provider), ("shared", &shared_import)] {
            let executable = out_dir.join(format!("header_smoke_{language}_{kind}.exe"));
            run(
                Command::new(compiler)
                    .arg("/nologo")
                    .arg(standard)
                    .arg("/W4")
                    .arg("/WX")
                    .arg("/MD")
                    .arg(format!("/I{}", include_dir.display()))
                    .arg(source)
                    .arg(provider)
                    .arg(format!("/Fe{}", executable.display()))
                    .arg("/link")
                    .arg("/BREPRO"),
                "MSVC public-header consumer link",
            );
            run(
                &mut Command::new(&executable),
                "MSVC public-header consumer run",
            );
        }
    }
}

fn main() {
    println!("cargo:rerun-if-changed=cpp/occt_adapter.cpp");
    println!("cargo:rerun-if-changed=cpp/sweep_adapter.inc");
    println!("cargo:rerun-if-changed=cpp/freeform_adapter.inc");
    println!("cargo:rerun-if-changed=cpp/preparation_adapter.inc");
    println!("cargo:rerun-if-changed=include/narwhal_geometry_core.h");
    println!("cargo:rerun-if-env-changed=NARWHAL_OCCT_SOURCE_DIR");
    println!("cargo:rerun-if-env-changed=NARWHAL_OCCT_LIB_DIR");

    let source_dir = required_directory("NARWHAL_OCCT_SOURCE_DIR");
    let library_dir = required_directory("NARWHAL_OCCT_LIB_DIR");
    println!(
        "cargo:rerun-if-changed={}",
        source_dir.join("src").display()
    );
    for (relative, _) in SOURCE_MARKERS {
        println!(
            "cargo:rerun-if-changed={}",
            source_dir.join(relative).display()
        );
    }

    verify_source_markers(&source_dir);
    verify_optional_checkout_identity(&source_dir);

    let out_dir = PathBuf::from(env::var_os("OUT_DIR").expect("Cargo did not provide OUT_DIR"));
    let msvc = env::var("CARGO_CFG_TARGET_ENV").as_deref() == Ok("msvc");
    if msvc {
        run_header_smoke_msvc(&out_dir);
    } else {
        run_header_smoke_gnu(&out_dir);
    }
    let include_dir = flatten_headers(&source_dir, &out_dir);
    for header in REQUIRED_HEADERS {
        let path = include_dir.join(header);
        assert!(
            path.is_file(),
            "required OCCT header is missing after flattening: {}",
            path.display()
        );
    }

    let libraries = REQUIRED_LIBRARIES
        .iter()
        .map(|name| discover_shared_library(&library_dir, name, msvc))
        .collect::<Vec<_>>();
    println!("cargo:rustc-link-search=native={}", out_dir.display());
    println!("cargo:rustc-link-lib=static=narwhal_occt_adapter");
    if msvc {
        let object = out_dir.join("narwhal_occt_adapter.obj");
        let archive = out_dir.join("narwhal_occt_adapter.lib");
        let compiler = env::var_os("CXX").unwrap_or_else(|| "cl.exe".into());
        let librarian = env::var_os("NARWHAL_MSVC_LIB").unwrap_or_else(|| "lib.exe".into());
        run(
            Command::new(compiler)
                .arg("/nologo")
                .arg("/std:c++17")
                .arg("/O2")
                .arg("/EHsc")
                .arg("/MD")
                .arg("/W4")
                .arg("/permissive-")
                .arg(format!("/I{}", include_dir.display()))
                .arg("/c")
                .arg("cpp/occt_adapter.cpp")
                .arg(format!("/Fo{}", object.display())),
            "MSVC OCCT adapter compile",
        );
        run(
            Command::new(librarian)
                .arg("/nologo")
                .arg("/BREPRO")
                .arg(format!("/OUT:{}", archive.display()))
                .arg(&object),
            "deterministic MSVC OCCT adapter archive",
        );
        for (stable_name, dll) in REQUIRED_LIBRARIES.iter().zip(&libraries) {
            let _ = msvc_import_library(dll, stable_name, &out_dir);
            println!("cargo:rustc-link-lib=dylib={stable_name}");
        }
    } else {
        let object = out_dir.join("occt_adapter.o");
        let archive = out_dir.join("libnarwhal_occt_adapter.a");
        let compiler = env::var_os("CXX").unwrap_or_else(|| "c++".into());
        let archiver = env::var_os("AR").unwrap_or_else(|| "ar".into());
        run(
            Command::new(compiler)
                .arg("-std=c++17")
                .arg("-O2")
                .arg("-fPIC")
                .arg("-fvisibility=hidden")
                .arg("-Wall")
                .arg("-Wextra")
                .arg("-Wpedantic")
                .arg("-I")
                .arg(&include_dir)
                .arg("-c")
                .arg("cpp/occt_adapter.cpp")
                .arg("-o")
                .arg(&object),
            "system C++ compiler",
        );
        run(
            Command::new(archiver).arg("crs").arg(&archive).arg(&object),
            "system archiver",
        );
        let macos = env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("macos");
        println!(
            "cargo:rustc-link-lib=dylib={}",
            if macos { "c++" } else { "stdc++" }
        );
        for library in libraries {
            println!("cargo:rustc-link-arg={}", library.display());
        }
        println!(
            "cargo:rustc-link-arg=-Wl,-rpath,{}",
            if macos {
                "@loader_path/occt"
            } else {
                "$ORIGIN/occt"
            }
        );
    }
}

#[cfg(test)]
mod build_tests {
    use super::{
        is_macos_library_name, is_shared_library_name, normalized_source_marker,
        parse_dumpbin_exports,
    };

    #[test]
    fn macos_library_names_are_exact_and_versioned() {
        assert!(is_macos_library_name("libTKBin.8.0.1.dylib", "TKBin"));
        assert!(is_macos_library_name("libTKBin.dylib", "TKBin"));
        for wrong in [
            "libTKBinL.8.0.1.dylib",
            "libTKBin.8..1.dylib",
            "libTKBin.so",
            "libTKBin-malicious.dylib",
            "TKBin.8.0.1.dylib",
        ] {
            assert!(!is_macos_library_name(wrong, "TKBin"), "{wrong}");
        }
    }

    #[test]
    fn platform_library_names_are_strict() {
        assert!(is_shared_library_name("libTKBin-abc.so.8", "TKBin", false));
        assert!(is_shared_library_name("libTKBin.so", "TKBin", false));
        assert!(!is_shared_library_name("TKBin-abc.dll", "TKBin", false));
        assert!(is_shared_library_name("TKBin-42544f47.dll", "TKBin", true));
        assert!(is_shared_library_name("TKBin-42544f47.DLL", "TKBin", true));
        assert!(!is_shared_library_name("TKBin.dll", "TKBin", true));
        assert!(!is_shared_library_name(
            "TKBinL-42544f47.dll",
            "TKBin",
            true
        ));
    }

    #[test]
    fn source_markers_are_identical_across_git_line_endings() {
        assert_eq!(normalized_source_marker(b"one\ntwo\n"), b"one\ntwo\n");
        assert_eq!(normalized_source_marker(b"one\r\ntwo\r\n"), b"one\ntwo\n");
        assert_eq!(normalized_source_marker(b"one\rtwo\n"), b"one\rtwo\n");
    }

    #[test]
    fn dumpbin_parser_keeps_named_decorated_exports_deterministically() {
        let fixture = r#"
    ordinal hint RVA      name
          2    1 00002A80 ??0BinDrivers_DocumentStorageDriver@@QEAA@XZ
          1    0 00001D40 PLUGINFACTORY
          3    2 00000000 [NONAME]
    Summary
        1000 .data
"#;
        assert_eq!(
            parse_dumpbin_exports(fixture),
            vec![
                "??0BinDrivers_DocumentStorageDriver@@QEAA@XZ".to_owned(),
                "PLUGINFACTORY".to_owned(),
            ]
        );
    }
}
