//! ZIP central-directory preflight, before the ZIP reader can coalesce duplicate names.
use std::{
    collections::HashSet,
    fs::File,
    io::{Read, Seek, SeekFrom},
    path::Path,
};
pub fn validate_zip_directory(path: &Path) -> Result<(), String> {
    let mut f = File::open(path).map_err(|e| e.to_string())?;
    let size = f.metadata().map_err(|e| e.to_string())?.len();
    let n = size.min(65557) as usize;
    f.seek(SeekFrom::End(-(n as i64)))
        .map_err(|e| e.to_string())?;
    let mut tail = vec![0; n];
    f.read_exact(&mut tail).map_err(|e| e.to_string())?;
    let u16at = |b: &[u8], i: usize| u16::from_le_bytes([b[i], b[i + 1]]) as usize;
    let u32at =
        |b: &[u8], i: usize| u32::from_le_bytes([b[i], b[i + 1], b[i + 2], b[i + 3]]) as usize;
    let end = (0..n.saturating_sub(21))
        .rev()
        .find(|&i| tail[i..].starts_with(b"PK\x05\x06") && i + 22 + u16at(&tail, i + 20) == n)
        .ok_or("Missing ZIP directory")?;
    if u16at(&tail, end + 4) != 0 || u16at(&tail, end + 6) != 0 {
        return Err("Multi-disk archives are unsupported".into());
    }
    let count = u16at(&tail, end + 10);
    let len = u32at(&tail, end + 12);
    let offset = u32at(&tail, end + 16);
    if count > 20001
        || count != u16at(&tail, end + 8)
        || len > 16 * 1024 * 1024
        || offset as u64 + len as u64 != size - n as u64 + end as u64
    {
        return Err("Invalid, oversized or ZIP64 directory".into());
    }
    f.seek(SeekFrom::Start(offset as u64))
        .map_err(|e| e.to_string())?;
    let mut directory = vec![0; len];
    f.read_exact(&mut directory).map_err(|e| e.to_string())?;
    let mut cursor = 0usize;
    let mut names = HashSet::new();
    for _ in 0..count {
        let header = directory
            .get(cursor..cursor + 46)
            .ok_or("Truncated directory")?;
        if !header.starts_with(b"PK\x01\x02") {
            return Err("Invalid directory entry".into());
        }
        let name_len = u16at(header, 28);
        let extra = u16at(header, 30);
        let comment = u16at(header, 32);
        let end = cursor
            .checked_add(46 + name_len + extra + comment)
            .ok_or("Directory overflow")?;
        let name = directory
            .get(cursor + 46..cursor + 46 + name_len)
            .ok_or("Truncated member name")?;
        if end > len || !names.insert(name.to_vec()) {
            return Err("Duplicate or truncated ZIP member".into());
        }
        cursor = end;
    }
    if cursor != len {
        return Err("Unaccounted ZIP directory entries".into());
    }
    Ok(())
}
