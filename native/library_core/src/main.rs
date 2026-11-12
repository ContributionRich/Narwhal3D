mod archive;
use archive::validate_zip_directory;
// User library authority. Geometry bytes remain opaque exact host-produced assets.
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::{HashMap, HashSet},
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    path::{Path, PathBuf},
    time::{Duration, SystemTime, UNIX_EPOCH},
};
type Result<T> = std::result::Result<T, String>;
const MAX_FILE: u64 = 256 * 1024 * 1024;
const MAX_TOTAL: u64 = 1024 * 1024 * 1024;
const MAX_ITEMS: usize = 10_000;
#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Category {
    id: String,
    name: String,
}
#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Asset {
    id: String,
    name: String,
    category_id: String,
    source_file: String,
    thumbnail: String,
    tags: Vec<String>,
    created_at: u64,
    source_sha256: String,
    thumbnail_sha256: String,
}
#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Catalog {
    version: u32,
    categories: Vec<Category>,
    assets: Vec<Asset>,
}
impl Default for Catalog {
    fn default() -> Self {
        Self {
            version: 1,
            categories: vec![Category {
                id: "uncategorized".into(),
                name: "Uncategorized".into(),
            }],
            assets: vec![],
        }
    }
}
fn now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}
fn digest(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
fn id() -> String {
    use std::sync::atomic::{AtomicU64, Ordering};
    static N: AtomicU64 = AtomicU64::new(0);
    digest(
        format!(
            "{}:{}:{}",
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap_or_default()
                .as_nanos(),
            std::process::id(),
            N.fetch_add(1, Ordering::Relaxed)
        )
        .as_bytes(),
    )[..32]
        .into()
}
fn name(s: &str) -> Result<String> {
    if s.chars().any(char::is_control) {
        return Err("Names cannot contain control characters".into());
    }
    let s = s.trim();
    if s.is_empty() || s.len() > 256 || s.chars().any(char::is_control) {
        return Err("Names must contain 1–256 UTF-8 bytes without control characters".into());
    }
    Ok(s.into())
}
fn safe_id(s: &str) -> bool {
    !s.is_empty()
        && s.len() <= 64
        && s.bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
}
fn member(s: &str, folder: &str) -> bool {
    let parts: Vec<_> = s.split('/').collect();
    parts.len() == 2
        && parts[0] == folder
        && !parts[1].is_empty()
        && parts[1]
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_' || b == b'.')
        && !parts[1].contains("..")
}
fn read_file(p: &Path) -> Result<Vec<u8>> {
    let m = fs::symlink_metadata(p).map_err(|e| format!("{}: {e}", p.display()))?;
    if !m.is_file() || m.file_type().is_symlink() || m.len() > MAX_FILE {
        return Err("Asset must be a bounded regular file, not a symlink".into());
    }
    let mut b = Vec::new();
    File::open(p)
        .map_err(|e| e.to_string())?
        .take(MAX_FILE + 1)
        .read_to_end(&mut b)
        .map_err(|e| e.to_string())?;
    if b.len() as u64 > MAX_FILE {
        return Err("Asset exceeds file size limit".into());
    }
    Ok(b)
}
fn validate(c: &Catalog) -> Result<()> {
    if c.version != 1 || c.assets.len() > MAX_ITEMS || c.categories.len() > MAX_ITEMS {
        return Err("Unsupported library version or item limit".into());
    }
    let mut cats = HashSet::new();
    let mut labels = HashSet::new();
    for x in &c.categories {
        if !safe_id(&x.id)
            || name(&x.name)? != x.name
            || !cats.insert(x.id.clone())
            || !labels.insert(x.name.to_lowercase())
        {
            return Err("Invalid or duplicate category".into());
        }
    }
    if !cats.contains("uncategorized") {
        return Err("Library lacks Uncategorized category".into());
    }
    let mut ids = HashSet::new();
    let mut paths = HashSet::new();
    for a in &c.assets {
        if !safe_id(&a.id)
            || name(&a.name)? != a.name
            || !ids.insert(a.id.clone())
            || !cats.contains(&a.category_id)
            || !member(&a.source_file, "assets")
            || !member(&a.thumbnail, "thumbnails")
            || !paths.insert(a.source_file.clone())
            || !paths.insert(a.thumbnail.clone())
            || !(a.source_file.ends_with(".narwhal") || a.source_file.ends_with(".3dm"))
            || !a.thumbnail.ends_with(".png")
            || a.tags.len() > 64
            || a.tags.iter().any(|t| name(t).is_err())
            || [&a.source_sha256, &a.thumbnail_sha256]
                .iter()
                .any(|s| s.len() != 64 || !s.bytes().all(|b| b.is_ascii_hexdigit()))
        {
            return Err("Invalid asset record, path or category reference".into());
        }
    }
    Ok(())
}
fn checked_path(root: &Path, s: &str) -> Result<PathBuf> {
    let p = root.join(s);
    let canonical = p.canonicalize().map_err(|e| e.to_string())?;
    if !canonical.starts_with(root)
        || fs::symlink_metadata(&p)
            .map_err(|e| e.to_string())?
            .file_type()
            .is_symlink()
    {
        return Err("Library asset escapes its storage root".into());
    }
    Ok(canonical)
}
fn load(root: &Path) -> Result<Catalog> {
    let p = root.join("manifest.json");
    if !p.exists() {
        return Ok(Catalog::default());
    }
    let c: Catalog = serde_json::from_slice(&read_file(&p)?)
        .map_err(|e| format!("Invalid library manifest: {e}"))?;
    validate(&c)?;
    Ok(c)
}
fn atomic(path: &Path, bytes: &[u8]) -> Result<()> {
    let parent = path.parent().ok_or("Missing parent")?;
    let temp = parent.join(format!(".write-{}", id()));
    let mut f = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&temp)
        .map_err(|e| e.to_string())?;
    f.write_all(bytes)
        .and_then(|_| f.sync_all())
        .map_err(|e| e.to_string())?;
    fs::rename(&temp, path)
        .map_err(|e| format!("Atomic publication failed; previous library retained: {e}"))?;
    Ok(())
}
fn save(root: &Path, c: &Catalog) -> Result<()> {
    validate(c)?;
    atomic(
        &root.join("manifest.json"),
        &serde_json::to_vec(c).map_err(|e| e.to_string())?,
    )
}
// OS locks release on normal exit and process crashes. The inert lock file is
// retained; no unlink race can create two simultaneously authoritative writers.
struct Lock {
    _file: File,
}
fn lock(root: &Path) -> Result<Lock> {
    let p = root.join(".catalog.lock");
    if let Ok(meta) = fs::symlink_metadata(&p) {
        if !meta.is_file() || meta.file_type().is_symlink() {
            return Err("Invalid library lock file".into());
        }
    }
    let f = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .open(&p)
        .map_err(|e| e.to_string())?;
    for _ in 0..100 {
        match f.try_lock() {
            Ok(()) => return Ok(Lock { _file: f }),
            Err(std::fs::TryLockError::WouldBlock) => std::thread::sleep(Duration::from_millis(20)),
            Err(std::fs::TryLockError::Error(e)) => {
                return Err(format!("Cannot lock library: {e}"))
            }
        }
    }
    Err("Library is busy; wait for the other operation to finish".into())
}
fn text<'a>(v: &'a Value, k: &str) -> Result<&'a str> {
    v.get(k)
        .and_then(Value::as_str)
        .ok_or_else(|| format!("Missing string field {k}"))
}
fn category(c: &Catalog, s: &str) -> Result<()> {
    if c.categories.iter().any(|x| x.id == s) {
        Ok(())
    } else {
        Err("Category no longer exists".into())
    }
}
fn asset_mut<'a>(c: &'a mut Catalog, s: &str) -> Result<&'a mut Asset> {
    c.assets
        .iter_mut()
        .find(|a| a.id == s)
        .ok_or_else(|| "Asset no longer exists".into())
}
fn public_asset(root: &Path, a: &Asset) -> Result<Value> {
    let mut v = serde_json::to_value(a).map_err(|e| e.to_string())?;
    v["source_file"] = json!(checked_path(root, &a.source_file)?);
    v["thumbnail"] = json!(checked_path(root, &a.thumbnail)?);
    Ok(v)
}
fn blobs(root: &Path, a: &Asset) -> Result<[(String, Vec<u8>); 2]> {
    let source = read_file(&checked_path(root, &a.source_file)?)?;
    let thumb = read_file(&checked_path(root, &a.thumbnail)?)?;
    if digest(&source) != a.source_sha256 || digest(&thumb) != a.thumbnail_sha256 {
        return Err(format!("Asset integrity check failed: {}", a.name));
    }
    Ok([
        (a.source_file.clone(), source),
        (a.thumbnail.clone(), thumb),
    ])
}
fn thumbnail_valid(bytes: &[u8]) -> bool {
    if bytes.len() < 33
        || bytes.len() > 32 * 1024 * 1024
        || !bytes.starts_with(b"\x89PNG\r\n\x1a\n")
        || &bytes[8..16] != b"\0\0\0\rIHDR"
    {
        return false;
    }
    let w = u32::from_be_bytes(bytes[16..20].try_into().unwrap());
    let h = u32::from_be_bytes(bytes[20..24].try_into().unwrap());
    w > 0 && h > 0 && w <= 1024 && h <= 1024
}
// A single publication plan carries validated metadata and two opaque blobs.
#[allow(clippy::too_many_arguments)]
fn add_bytes(
    root: &Path,
    c: &mut Catalog,
    label: &str,
    cat: &str,
    ext: &str,
    source: &[u8],
    thumb: &[u8],
    tags: Vec<String>,
) -> Result<Asset> {
    category(c, cat)?;
    if c.assets.len() >= MAX_ITEMS
        || !["narwhal", "3dm"].contains(&ext)
        || source.len() as u64 > MAX_FILE
        || !thumbnail_valid(thumb)
    {
        return Err("Invalid source format, thumbnail PNG or library size limit".into());
    }
    let ident = id();
    let a = Asset {
        id: ident.clone(),
        name: name(label)?,
        category_id: cat.into(),
        source_file: format!("assets/{ident}.{ext}"),
        thumbnail: format!("thumbnails/{ident}.png"),
        tags,
        created_at: now(),
        source_sha256: digest(source),
        thumbnail_sha256: digest(thumb),
    };
    // New immutable files precede the single catalogue publication. Old bytes are
    // retained, even on remove/replace, so open readers and failed writes are safe.
    atomic(&root.join(&a.source_file), source)?;
    atomic(&root.join(&a.thumbnail), thumb)?;
    c.assets.push(a.clone());
    validate(c)?;
    Ok(a)
}
fn package_export(root: &Path, c: &Catalog, path: &Path, cat: Option<&str>) -> Result<Value> {
    if !path
        .extension()
        .and_then(|s| s.to_str())
        .is_some_and(|s| s.eq_ignore_ascii_case("narwhallib"))
    {
        return Err("Export destination must have the .narwhallib extension".into());
    }
    if let Some(s) = cat {
        category(c, s)?;
    }
    let mut subset = c.clone();
    if let Some(s) = cat {
        subset.assets.retain(|a| a.category_id == s);
        subset
            .categories
            .retain(|x| x.id == s || x.id == "uncategorized");
    }
    let mut total = 0u64;
    let mut payload = Vec::new();
    for a in &subset.assets {
        for item in blobs(root, a)? {
            total += item.1.len() as u64;
            if total > MAX_TOTAL {
                return Err("Package exceeds 1 GiB limit".into());
            }
            payload.push(item);
        }
    }
    let temp = path.with_file_name(format!(".library-export-{}", id()));
    let f = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&temp)
        .map_err(|e| e.to_string())?;
    let mut z = zip::ZipWriter::new(f);
    let options =
        zip::write::SimpleFileOptions::default().compression_method(zip::CompressionMethod::Stored);
    z.start_file("manifest.json", options)
        .map_err(|e| e.to_string())?;
    z.write_all(&serde_json::to_vec(&subset).map_err(|e| e.to_string())?)
        .map_err(|e| e.to_string())?;
    for (n, b) in payload {
        z.start_file(n, options).map_err(|e| e.to_string())?;
        z.write_all(&b).map_err(|e| e.to_string())?;
    }
    z.finish()
        .and_then(|f| f.sync_all().map_err(zip::result::ZipError::Io))
        .map_err(|e| e.to_string())?;
    fs::rename(temp, path).map_err(|e| e.to_string())?;
    Ok(json!({"assets":subset.assets.len(),"path":path}))
}
fn package_import(root: &Path, c: &mut Catalog, path: &Path, mode: &str) -> Result<Value> {
    if !["merge", "replace"].contains(&mode) {
        return Err("Import mode must be merge or replace".into());
    }
    if fs::metadata(path).map_err(|e| e.to_string())?.len() > MAX_TOTAL {
        return Err("Package exceeds size limit".into());
    }
    validate_zip_directory(path)?;
    let mut z = zip::ZipArchive::new(File::open(path).map_err(|e| e.to_string())?)
        .map_err(|e| e.to_string())?;
    if z.len() > MAX_ITEMS * 2 + 1 {
        return Err("Too many archive members".into());
    }
    let mut files = HashMap::new();
    let mut total = 0u64;
    let mut actual_total = 0u64;
    for i in 0..z.len() {
        let f = z.by_index(i).map_err(|e| e.to_string())?;
        let n = f.name().to_string();
        if (n != "manifest.json" && !member(&n, "assets") && !member(&n, "thumbnails"))
            || f.is_dir()
            || f.unix_mode().is_some_and(|m| m & 0o170000 == 0o120000)
            || f.size() > MAX_FILE
            || files.contains_key(&n)
        {
            return Err("Unsafe, duplicate or oversized package member".into());
        }
        total = total.checked_add(f.size()).ok_or("Package size overflow")?;
        if total > MAX_TOTAL {
            return Err("Expanded package exceeds 1 GiB".into());
        }
        let mut b = Vec::new();
        let remaining = MAX_TOTAL - actual_total;
        f.take(MAX_FILE.min(remaining) + 1)
            .read_to_end(&mut b)
            .map_err(|e| e.to_string())?;
        actual_total = actual_total
            .checked_add(b.len() as u64)
            .ok_or("Package size overflow")?;
        if b.len() as u64 > MAX_FILE || actual_total > MAX_TOTAL {
            return Err("Package expansion limit".into());
        }
        files.insert(n, b);
    }
    let incoming: Catalog =
        serde_json::from_slice(files.get("manifest.json").ok_or("Missing manifest")?)
            .map_err(|e| e.to_string())?;
    validate(&incoming)?;
    let mut expected = HashSet::from(["manifest.json".to_string()]);
    for a in &incoming.assets {
        for (n, h) in [
            (&a.source_file, &a.source_sha256),
            (&a.thumbnail, &a.thumbnail_sha256),
        ] {
            let b = files.get(n).ok_or("Package asset missing")?;
            if digest(b) != *h {
                return Err("Package checksum mismatch".into());
            }
            expected.insert(n.clone());
        }
    }
    if expected.len() != files.len() {
        return Err("Package contains unreferenced files".into());
    }
    for a in &incoming.assets {
        if !thumbnail_valid(&files[&a.thumbnail]) {
            return Err("Invalid or oversized PNG preview".into());
        }
    }
    let mut target = if mode == "replace" {
        Catalog::default()
    } else {
        c.clone()
    };
    let mut mapping = HashMap::new();
    for cat in &incoming.categories {
        let dest = if cat.id == "uncategorized" {
            "uncategorized".into()
        } else if let Some(x) = target
            .categories
            .iter()
            .find(|x| x.name.to_lowercase() == cat.name.to_lowercase())
        {
            x.id.clone()
        } else {
            let ident = id();
            target.categories.push(Category {
                id: ident.clone(),
                name: cat.name.clone(),
            });
            ident
        };
        mapping.insert(cat.id.clone(), dest);
    }
    validate(&target)?;
    if target.assets.len() + incoming.assets.len() > MAX_ITEMS {
        return Err("Merged library exceeds asset limit".into());
    }
    for a in &incoming.assets {
        let ext = Path::new(&a.source_file)
            .extension()
            .and_then(|x| x.to_str())
            .ok_or("Invalid extension")?;
        add_bytes(
            root,
            &mut target,
            &a.name,
            &mapping[&a.category_id],
            ext,
            &files[&a.source_file],
            &files[&a.thumbnail],
            a.tags.clone(),
        )?;
    }
    // Validate and copy everything before replacing the only authoritative index.
    save(root, &target)?;
    *c = target;
    Ok(json!({"assets":incoming.assets.len(),"mode":mode}))
}
fn hover_frames(scale: f64) -> Vec<Value> {
    (0..=9)
        .map(|frame| {
            let progress = frame as f64 / 9.;
            let (mut low, mut high) = (0., 1.);
            for _ in 0..18 {
                let t = (low + high) / 2.;
                let x =
                    3. * (1. - t) * (1. - t) * t * 0.25 + 3. * (1. - t) * t * t * 0.25 + t * t * t;
                if x < progress {
                    low = t;
                } else {
                    high = t;
                }
            }
            let t = (low + high) / 2.;
            let weight = if frame == 0 {
                0.
            } else if frame == 9 {
                1.
            } else {
                3. * (1. - t) * (1. - t) * t * 0.1 + 3. * (1. - t) * t * t + t * t * t
            };
            json!({"weight":weight,"lift":scale*weight,"slide":4.*scale*(1.-weight)})
        })
        .collect()
}

fn tooltip_layout(v: &Value) -> Result<Value> {
    let number = |key: &str, default: f64| -> Result<f64> {
        let value = v.get(key).and_then(Value::as_f64).unwrap_or(default);
        if value.is_finite() {
            Ok(value)
        } else {
            Err("Non-finite tooltip parameter".into())
        }
    };
    let scale = number("scale", 1.)?.clamp(0.5, 4.);
    let width = number("width", 200.)?.clamp(0., 32768.);
    let height = number("height", 500.)?.clamp(0., 32768.);
    let pad = 4. * scale;
    let tip_width = (number("text_width", 100.)?.max(0.) + 18. * scale)
        .max(80. * scale)
        .min((width - 2. * pad).max(0.));
    let tip_height = (number("lines", 1.)?.clamp(1., 100.) * 15. * scale + 14. * scale)
        .min((height - 2. * pad).max(0.));
    let x = (number("mouse_x", 0.)? + 12. * scale).clamp(pad, (width - pad - tip_width).max(pad));
    let y = (number("mouse_y", 0.)? - 20. * scale - tip_height)
        .clamp(pad, (height - pad - tip_height).max(pad));
    Ok(json!({"box":[x,y,x+tip_width,y+tip_height]}))
}

fn layout(v: &Value) -> Result<Value> {
    let number = |k: &str, d: f64| -> Result<f64> {
        let n = v.get(k).and_then(Value::as_f64).unwrap_or(d);
        if !n.is_finite() {
            Err("Non-finite layout parameter".into())
        } else {
            Ok(n)
        }
    };
    let w = number("width", 200.)?.clamp(0., 32768.);
    let h = number("height", 500.)?.clamp(0., 32768.);
    let s = number("scale", 1.)?.clamp(0.5, 4.);
    let count = v
        .get("count")
        .and_then(Value::as_u64)
        .unwrap_or(0)
        .min(MAX_ITEMS as u64) as usize;
    let p = 9. * s;
    let gap = 8. * s;
    let cols = if w >= 180. * s { 2 } else { 1 };
    let tile = ((w - 2. * p - gap * (cols - 1) as f64) / cols as f64).max(1.);
    let top = (h - 110. * s).max(38. * s);
    let bottom = 38. * s;
    let rows = count.div_ceil(cols);
    let max = (rows as f64 * (tile + 30. * s + gap) + 2. * s - (top - bottom)).max(0.);
    let scroll = number("scroll", 0.)?.clamp(0., max);
    let mut tiles = vec![];
    let rect = |x: f64, y: f64, x1: f64, y1: f64| json!([x, y, x1, y1]);
    for i in 0..count {
        let x = p + (i % cols) as f64 * (tile + gap);
        let y1 = top - 2. * s - (i / cols) as f64 * (tile + 30. * s + gap) + scroll;
        let y = y1 - tile - 30. * s;
        if y1 <= bottom || y >= top {
            continue;
        }
        tiles.push(json!({"index":i,"box":rect(x,y,x+tile,y1),"art":rect(x,y+30.*s,x+tile,y1),"name":rect(x+5.*s,y,x+tile-23.*s,y+30.*s),"more":rect(x+tile-23.*s,y,x+tile,y+30.*s)}));
    }
    Ok(
        json!({"search":rect(p,h-40.*s,(w-p).max(p),h-9.*s),"category":rect(p,h-77.*s,(w-p-34.*s).max(p),h-48.*s),"category_add":rect((w-p-29.*s).max(p),h-77.*s,w-p,h-48.*s),"overview":rect(p,h-104.*s,w-p,h-83.*s),"grid":rect(p,bottom,w-p,top),"footer":rect(p,0.,w-p,30.*s),"tiles":tiles,"scroll":scroll,"max_scroll":max,"hover_frames":hover_frames(s),"editor":{
            "head":rect(0.,h-48.*s,w,h),
            "back":rect(p,h-37.*s,p+25.*s,h-12.*s),
            "title":rect(p+31.*s,h-40.*s,w-p,h-9.*s),
            "cancel":rect(p,10.*s,(w-gap)/2.,44.*s),
            "submit":rect((w+gap)/2.,10.*s,w-p,44.*s)
        }}),
    )
}
fn execute(v: Value) -> Result<Value> {
    let op = text(&v, "op")?;
    if op == "layout" {
        return layout(&v);
    }
    if op == "tooltip_layout" {
        return tooltip_layout(&v);
    }
    let given = PathBuf::from(text(&v, "root")?);
    if !given.is_absolute() {
        return Err("Library root must be absolute".into());
    }
    fs::create_dir_all(&given).map_err(|e| e.to_string())?;
    if fs::symlink_metadata(&given)
        .map_err(|e| e.to_string())?
        .file_type()
        .is_symlink()
    {
        return Err("Library root cannot be a symlink".into());
    }
    let root = given.canonicalize().map_err(|e| e.to_string())?;
    if root.parent().is_none() || root.join(".git").exists() {
        return Err(
            "Choose a dedicated library directory, not a filesystem or source repository root"
                .into(),
        );
    }
    let _lock = lock(&root)?;
    for d in ["assets", "thumbnails"] {
        let p = root.join(d);
        fs::create_dir_all(&p).map_err(|e| e.to_string())?;
        if fs::symlink_metadata(&p)
            .map_err(|e| e.to_string())?
            .file_type()
            .is_symlink()
        {
            return Err("Library directories cannot be symlinks".into());
        }
    }
    let mut c = load(&root)?;
    if op == "catalog" {
        let query = v
            .get("query")
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim()
            .to_lowercase();
        let cat = v.get("category_id").and_then(Value::as_str);
        let names: HashMap<_, _> = c.categories.iter().map(|x| (&x.id, &x.name)).collect();
        let mut assets: Vec<_> = c
            .assets
            .iter()
            .filter(|a| {
                cat.is_none_or(|x| a.category_id == x)
                    && (query.is_empty()
                        || format!("{} {} {}", a.name, names[&a.category_id], a.tags.join(" "))
                            .to_lowercase()
                            .contains(&query))
            })
            .collect();
        assets.sort_by_key(|a| (a.name.to_lowercase(), a.id.clone()));
        return Ok(
            json!({"version":1,"categories":c.categories,"assets":assets.into_iter().map(|a|public_asset(&root,a)).collect::<Result<Vec<_>>>()?}),
        );
    }
    let result = match op {
        "create_category" => {
            let label = name(text(&v, "name")?)?;
            if c.categories
                .iter()
                .any(|x| x.name.to_lowercase() == label.to_lowercase())
            {
                return Err("Category name already exists".into());
            }
            let cat = Category {
                id: id(),
                name: label,
            };
            c.categories.push(cat.clone());
            json!(cat)
        }
        "rename_category" => {
            let ident = text(&v, "id")?;
            if ident == "uncategorized" {
                return Err("Uncategorized is protected".into());
            }
            let label = name(text(&v, "name")?)?;
            if c.categories
                .iter()
                .any(|x| x.id != ident && x.name.to_lowercase() == label.to_lowercase())
            {
                return Err("Category name already exists".into());
            }
            let cat = c
                .categories
                .iter_mut()
                .find(|x| x.id == ident)
                .ok_or("Category not found")?;
            cat.name = label;
            json!(cat)
        }
        "delete_category" => {
            let ident = text(&v, "id")?;
            category(&c, ident)?;
            if ident == "uncategorized" {
                return Err("Uncategorized is protected".into());
            }
            c.categories.retain(|x| x.id != ident);
            for a in &mut c.assets {
                if a.category_id == ident {
                    a.category_id = "uncategorized".into();
                }
            }
            json!({"deleted":ident})
        }
        "rename_asset" => {
            let a = asset_mut(&mut c, text(&v, "id")?)?;
            a.name = name(text(&v, "name")?)?;
            public_asset(&root, a)?
        }
        "move_asset" => {
            let cat = text(&v, "category_id")?;
            category(&c, cat)?;
            let a = asset_mut(&mut c, text(&v, "id")?)?;
            a.category_id = cat.into();
            public_asset(&root, a)?
        }
        "refresh_thumbnail" => {
            let a = asset_mut(&mut c, text(&v, "id")?)?;
            let thumb = read_file(Path::new(text(&v, "thumbnail_path")?))?;
            if !thumbnail_valid(&thumb) {
                return Err("Invalid thumbnail PNG".into());
            }
            let path = format!("thumbnails/{}.png", id());
            atomic(&root.join(&path), &thumb)?;
            a.thumbnail = path;
            a.thumbnail_sha256 = digest(&thumb);
            public_asset(&root, a)?
        }
        "remove_asset" => {
            let ident = text(&v, "id")?;
            asset_mut(&mut c, ident)?;
            c.assets.retain(|a| a.id != ident);
            json!({"removed":ident})
        }
        "add_asset" => {
            let p = Path::new(text(&v, "source_path")?);
            let ext = p
                .extension()
                .and_then(|x| x.to_str())
                .ok_or("Source needs extension")?;
            let source = read_file(p)?;
            let thumb = read_file(Path::new(text(&v, "thumbnail_path")?))?;
            let tags = serde_json::from_value(v.get("tags").cloned().unwrap_or(json!([])))
                .map_err(|e| e.to_string())?;
            let a = add_bytes(
                &root,
                &mut c,
                text(&v, "name")?,
                text(&v, "category_id")?,
                ext,
                &source,
                &thumb,
                tags,
            )?;
            public_asset(&root, &a)?
        }
        "export_library" => {
            return package_export(
                &root,
                &c,
                Path::new(text(&v, "path")?),
                v.get("category_id").and_then(Value::as_str),
            )
        }
        "import_library" => {
            return package_import(
                &root,
                &mut c,
                Path::new(text(&v, "path")?),
                v.get("mode").and_then(Value::as_str).unwrap_or("merge"),
            )
        }
        _ => return Err("Unknown library operation".into()),
    };
    save(&root, &c)?;
    Ok(result)
}
fn main() {
    if std::env::args().nth(1).as_deref() == Some("--version") {
        println!("narwhal-library-core 1");
        return;
    }
    let mut input = Vec::new();
    let read = std::io::stdin().take(65537).read_to_end(&mut input);
    let answer = if read.is_err() || input.len() > 65536 {
        Err("Library request exceeds 64 KiB".into())
    } else {
        serde_json::from_slice(&input)
            .map_err(|e| format!("Invalid request: {e}"))
            .and_then(execute)
    };
    let reply = match answer {
        Ok(result) => json!({"ok":true,"result":result}),
        Err(error) => json!({"ok":false,"error":error}),
    };
    println!("{reply}");
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn hover_easing_and_tooltip_bounds() {
        let frames = hover_frames(2.);
        assert_eq!(frames.len(), 10);
        assert_eq!(frames[0]["weight"], 0.);
        assert_eq!(frames[9]["weight"], 1.);
        assert_eq!(frames[9]["lift"], 2.);
        for pair in frames.windows(2) {
            assert!(pair[0]["weight"].as_f64().unwrap() < pair[1]["weight"].as_f64().unwrap());
        }
        let tip = tooltip_layout(&json!({"width":160.,"height":400.,"mouse_x":159.,"mouse_y":2.,"text_width":120.,"lines":4.})).unwrap();
        let bounds = tip["box"].as_array().unwrap();
        assert!(bounds[0].as_f64().unwrap() >= 0.);
        assert!(bounds[1].as_f64().unwrap() >= 0.);
        assert!(bounds[2].as_f64().unwrap() <= 160.);
        assert!(bounds[3].as_f64().unwrap() <= 400.);
    }
    #[test]
    fn paths() {
        assert!(member("assets/a.narwhal", "assets"));
        for s in [
            "../x",
            "assets/../x",
            "/assets/x",
            "assets/x\\y",
            "assets/a..b",
        ] {
            assert!(!member(s, "assets"));
        }
    }
    #[test]
    fn schema() {
        let mut c = Catalog::default();
        validate(&c).unwrap();
        c.categories.push(c.categories[0].clone());
        assert!(validate(&c).is_err());
    }
    #[test]
    fn layout_bounds() {
        let p = layout(&json!({"width":230,"height":500,"count":1000,"scroll":999999})).unwrap();
        assert!(p["max_scroll"].as_f64().unwrap() > 0.);
        assert_eq!(p["scroll"], p["max_scroll"]);
        assert!(p["tiles"].as_array().unwrap().len() < 20);
    }
}
