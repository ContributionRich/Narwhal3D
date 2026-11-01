//! Candidate qualification utility, not a live model hook or geometry decoder.
use narwhal_application_core::observation::{MAX_BATCH, decode};
use std::{fs::File, io::Read};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut args = std::env::args_os().skip(1);
    let path = args.next().ok_or("one observation file is required")?;
    if args.next().is_some() {
        return Err("one observation file is required".into());
    }
    let mut bytes = Vec::new();
    File::open(path)?
        .take((MAX_BATCH + 1) as u64)
        .read_to_end(&mut bytes)?;
    let objects = decode(&bytes)?;
    let rows: Vec<_> = objects
        .iter()
        .map(|object| {
            serde_json::json!({
                "id":object.id, "name":object.name,
                "geometry_bytes":object.geometry.len(), "attribute_bytes":object.attributes.len()
            })
        })
        .collect();
    println!("{}", serde_json::json!({"count":rows.len(),"rows":rows}));
    Ok(())
}
