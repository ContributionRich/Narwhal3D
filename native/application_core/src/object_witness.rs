//! Immutable per-object observation storage. Unchanged records share their archive
//! bytes across snapshots; charges follow allocation lifetimes, not snapshot count.
use super::{MAX_RETAINED, MAX_STATE, Result};
use crate::observation::{Object, decode};
use std::{
    collections::HashMap,
    sync::{
        Arc,
        atomic::{AtomicUsize, Ordering},
    },
};

struct Charge {
    bytes: usize,
    retained: Arc<AtomicUsize>,
}
impl Charge {
    fn new(bytes: usize, retained: &Arc<AtomicUsize>) -> Result<Self> {
        retained
            .fetch_update(Ordering::Relaxed, Ordering::Relaxed, |n| {
                n.checked_add(bytes).filter(|n| *n <= MAX_RETAINED)
            })
            .map_err(|_| "retained_limit")?;
        Ok(Self {
            bytes,
            retained: retained.clone(),
        })
    }
}
impl Drop for Charge {
    fn drop(&mut self) {
        self.retained.fetch_sub(self.bytes, Ordering::Relaxed);
    }
}

pub(super) struct Record {
    name: String,
    geometry: Vec<u8>,
    attributes: Vec<u8>,
    _charge: Charge,
}
impl Record {
    pub(super) fn matches(&self, other: &Object<'_>) -> bool {
        self.name == other.name
            && self.geometry == other.geometry
            && self.attributes == other.attributes
    }
    fn new(object: &Object<'_>, retained: &Arc<AtomicUsize>) -> Result<Arc<Self>> {
        let bytes = 256usize
            .checked_add(object.name.len())
            .and_then(|n| n.checked_add(object.geometry.len()))
            .and_then(|n| n.checked_add(object.attributes.len()))
            .ok_or("state_limit")?;
        let charge = Charge::new(bytes, retained)?;
        Ok(Arc::new(Self {
            name: object.name.to_owned(),
            geometry: object.geometry.to_vec(),
            attributes: object.attributes.to_vec(),
            _charge: charge,
        }))
    }
}
impl PartialEq for Record {
    fn eq(&self, other: &Self) -> bool {
        self.name == other.name
            && self.geometry == other.geometry
            && self.attributes == other.attributes
    }
}
impl Eq for Record {}

pub(super) struct Witness {
    records: HashMap<String, Arc<Record>>,
    pub(super) logical_bytes: usize,
    _charge: Charge,
}
impl Witness {
    pub(super) fn get(&self, id: &str) -> Option<&Record> {
        self.records.get(id).map(Arc::as_ref)
    }

    pub(super) fn build(
        data: &[u8],
        previous: Option<&Self>,
        retained: &Arc<AtomicUsize>,
    ) -> Result<Arc<Self>> {
        let objects = decode(data)?;
        let frame_bytes = objects
            .len()
            .checked_mul(192)
            .and_then(|n| n.checked_add(128))
            .ok_or("state_limit")?;
        let records_overhead = objects.len().checked_mul(256).ok_or("state_limit")?;
        let logical_bytes = data
            .len()
            .checked_add(frame_bytes)
            .and_then(|n| n.checked_add(records_overhead))
            .ok_or("state_limit")?;
        if logical_bytes > MAX_STATE {
            return Err("state_limit");
        }
        let charge = Charge::new(frame_bytes, retained)?;
        let mut records = HashMap::with_capacity(objects.len());
        for object in objects {
            let shared = previous
                .and_then(|p| p.records.get(object.id))
                .filter(|record| record.matches(&object));
            let record = match shared {
                Some(record) => record.clone(),
                None => Record::new(&object, retained)?,
            };
            records.insert(object.id.to_owned(), record);
        }
        Ok(Arc::new(Self {
            records,
            logical_bytes,
            _charge: charge,
        }))
    }

    pub(super) fn matches(&self, data: &[u8]) -> Result<bool> {
        let objects = decode(data)?;
        Ok(objects.len() == self.records.len()
            && objects.iter().all(|object| {
                self.get(object.id)
                    .is_some_and(|record| record.matches(object))
            }))
    }
}
impl PartialEq for Witness {
    fn eq(&self, other: &Self) -> bool {
        self.records == other.records
    }
}
impl Eq for Witness {}
