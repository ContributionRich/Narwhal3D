//! Persistent history ownership. Payloads are opaque IDs, never geometry snapshots.
#[cfg(test)]
#[path = "history_tests.rs"]
mod tests;
use serde::{Deserialize, Deserializer, Serialize};
use std::collections::{BTreeSet, HashMap, VecDeque};

type Result<T> = std::result::Result<T, &'static str>;
// OURS: process-wide admission bounds, including escaped leases and retired payloads.
const MAX_HISTORIES: usize = 256;
const MAX_RECORDS: usize = 100_000;
const MAX_PAYLOADS: usize = 200_000;
const MAX_NAME_BYTES: usize = 16 * 1024 * 1024;
const MAX_NAME: usize = 16_384;
const MAX_LEASES: usize = 1_000_000;

fn nullable<'de, D: Deserializer<'de>>(d: D) -> std::result::Result<Option<u64>, D::Error> {
    Option::deserialize(d)
}
#[derive(Clone, Copy, Debug, Deserialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum Side {
    Undo,
    Redo,
}
#[derive(Debug, Deserialize)]
#[serde(tag = "op", rename_all = "snake_case", deny_unknown_fields)]
pub(crate) enum Request {
    Create {
        limit: u64,
    },
    Close {
        history: u64,
    },
    Info {
        history: u64,
    },
    RegisterPayload {
        history: u64,
    },
    DropPayload {
        history: u64,
        payload: u64,
    },
    Begin {
        history: u64,
        name: String,
    },
    Capture {
        history: u64,
        #[serde(deserialize_with = "nullable")]
        payload: Option<u64>,
    },
    End {
        history: u64,
    },
    Abandon {
        history: u64,
    },
    Enable {
        history: u64,
        value: bool,
    },
    Records {
        history: u64,
        side: Side,
    },
    Names {
        history: u64,
        side: Side,
    },
    Record {
        history: u64,
        record: u64,
    },
    RetainRecord {
        history: u64,
        record: u64,
    },
    ReleaseRecord {
        history: u64,
        record: u64,
    },
    SetBefore {
        history: u64,
        record: u64,
        #[serde(deserialize_with = "nullable")]
        payload: Option<u64>,
    },
    SetAfter {
        history: u64,
        record: u64,
        #[serde(deserialize_with = "nullable")]
        payload: Option<u64>,
    },
    SetName {
        history: u64,
        record: u64,
        name: String,
    },
    PrepareNav {
        history: u64,
        direction: Side,
        #[serde(deserialize_with = "nullable")]
        current: Option<u64>,
    },
    CommitNav {
        history: u64,
        ticket: u64,
    },
    AbortNav {
        history: u64,
        ticket: u64,
        unknown: bool,
    },
    Clear {
        history: u64,
    },
    Garbage {
        history: u64,
        limit: u64,
    },
    AckGarbage {
        history: u64,
        payloads: Vec<u64>,
    },
}
impl Request {
    fn history(&self) -> u64 {
        match *self {
            Self::Create { .. } => unreachable!(),
            Self::Close { history }
            | Self::Info { history }
            | Self::RegisterPayload { history }
            | Self::DropPayload { history, .. }
            | Self::Begin { history, .. }
            | Self::Capture { history, .. }
            | Self::End { history }
            | Self::Abandon { history }
            | Self::Enable { history, .. }
            | Self::Records { history, .. }
            | Self::Names { history, .. }
            | Self::Record { history, .. }
            | Self::RetainRecord { history, .. }
            | Self::ReleaseRecord { history, .. }
            | Self::SetBefore { history, .. }
            | Self::SetAfter { history, .. }
            | Self::SetName { history, .. }
            | Self::PrepareNav { history, .. }
            | Self::CommitNav { history, .. }
            | Self::AbortNav { history, .. }
            | Self::Clear { history }
            | Self::Garbage { history, .. }
            | Self::AckGarbage { history, .. } => history,
        }
    }
    fn edit(&self) -> bool {
        matches!(
            self,
            Self::Begin { .. }
                | Self::Capture { .. }
                | Self::End { .. }
                | Self::Abandon { .. }
                | Self::Enable { .. }
                | Self::SetBefore { .. }
                | Self::SetAfter { .. }
                | Self::SetName { .. }
                | Self::PrepareNav { .. }
                | Self::Clear { .. }
        )
    }
}
#[derive(Serialize)]
pub(crate) struct Reply {
    pub result: Output,
    pub info: Option<Header>,
}
#[derive(Serialize)]
#[serde(untagged)]
pub(crate) enum Output {
    Null,
    Created {
        history: u64,
    },
    Payload {
        payload: u64,
    },
    Serial {
        serial: u64,
    },
    Captured {
        captured: bool,
    },
    Committed {
        committed: bool,
    },
    Records {
        records: Vec<u64>,
    },
    Names {
        names: Vec<String>,
    },
    Record {
        serial: u64,
        name: String,
        before: Option<u64>,
        after: Option<u64>,
    },
    Prepared {
        ticket: u64,
        record: u64,
        target: Option<u64>,
    },
    Navigated {
        record: u64,
    },
    Garbage {
        payloads: Vec<u64>,
    },
}
#[derive(Serialize)]
pub(crate) struct Header {
    generation: u64,
    state_id: u64,
    current_serial: u64,
    top_serial: u64,
    next_serial: u64,
    depth: u64,
    enabled: bool,
    uncertain: bool,
    undo_count: usize,
    redo_count: usize,
    open_record: Option<u64>,
    undo_record: Option<u64>,
    redo_record: Option<u64>,
    captured: bool,
    gc_pending: usize,
    record_count: usize,
    payload_count: usize,
}
struct Record {
    serial: u64,
    name: String,
    before: Option<u64>,
    after: Option<u64>,
    before_state: u64,
    after_state: u64,
    stacked: bool,
    leases: usize,
}
struct Pending {
    id: u64,
    record: u64,
    direction: Side,
    current: Option<u64>,
}
struct History {
    limit: usize,
    generation: u64,
    state_id: u64,
    next_serial: u64,
    depth: u64,
    enabled: bool,
    uncertain: bool,
    open: Option<u64>,
    undo: VecDeque<u64>,
    redo: VecDeque<u64>,
    records: HashMap<u64, Record>,
    payloads: HashMap<u64, usize>,
    garbage: BTreeSet<u64>,
    pending: Option<Pending>,
}
#[derive(Default)]
struct Budget {
    records: usize,
    payloads: usize,
    names: usize,
    leases: usize,
}
#[derive(Default)]
pub(crate) struct Registry {
    next_id: u64,
    histories: HashMap<u64, History>,
    budget: Budget,
}
fn ids(next: &mut u64, count: u64) -> Result<u64> {
    let first = next.checked_add(1).ok_or("id_exhausted")?;
    *next = next.checked_add(count).ok_or("id_exhausted")?;
    Ok(first)
}
fn name_check(name: &str) -> Result<()> {
    if name.len() > MAX_NAME {
        Err("name_limit")
    } else {
        Ok(())
    }
}
impl Registry {
    pub(crate) fn execute(&mut self, request: Request) -> Result<Reply> {
        if let Request::Create { limit } = request {
            if !(1..=100_000).contains(&limit) {
                return Err("invalid_limit");
            }
            if self.histories.len() >= MAX_HISTORIES {
                return Err("history_limit");
            }
            let id = ids(&mut self.next_id, 2)?;
            let h = History {
                limit: limit as usize,
                generation: 0,
                state_id: id + 1,
                next_serial: 1,
                depth: 0,
                enabled: true,
                uncertain: false,
                open: None,
                undo: VecDeque::new(),
                redo: VecDeque::new(),
                records: HashMap::new(),
                payloads: HashMap::new(),
                garbage: BTreeSet::new(),
                pending: None,
            };
            let info = Some(h.header());
            self.histories.insert(id, h);
            return Ok(Reply {
                result: Output::Created { history: id },
                info,
            });
        }
        let id = request.history();
        let h = self.histories.get_mut(&id).ok_or("invalid_history")?;
        if matches!(request, Request::Close { .. }) {
            self.budget.records -= h.records.len();
            self.budget.payloads -= h.payloads.len();
            self.budget.names -= h.records.values().map(|r| r.name.len()).sum::<usize>();
            self.budget.leases -= h.records.values().map(|r| r.leases).sum::<usize>();
            self.histories.remove(&id);
            return Ok(Reply {
                result: Output::Null,
                info: None,
            });
        }
        let result = h.execute(request, &mut self.next_id, &mut self.budget)?;
        Ok(Reply {
            result,
            info: Some(h.header()),
        })
    }
}
impl History {
    fn header(&self) -> Header {
        Header {
            generation: self.generation,
            state_id: self.state_id,
            current_serial: self.open.map_or(0, |id| self.records[&id].serial),
            top_serial: self.undo.back().map_or(0, |id| self.records[id].serial),
            next_serial: self.next_serial,
            depth: self.depth,
            enabled: self.enabled,
            uncertain: self.uncertain,
            undo_count: self.undo.len(),
            redo_count: self.redo.len(),
            open_record: self.open,
            undo_record: self.undo.back().copied(),
            redo_record: self.redo.back().copied(),
            captured: self
                .open
                .is_some_and(|id| self.records[&id].before.is_some()),
            gc_pending: self.garbage.len(),
            record_count: self.records.len(),
            payload_count: self.payloads.len(),
        }
    }
    fn generation_room(&self, count: u64) -> Result<()> {
        self.generation
            .checked_add(count)
            .map(|_| ())
            .ok_or("generation_exhausted")
    }
    fn payload(&self, payload: Option<u64>) -> Result<()> {
        if payload.is_some_and(|id| !self.payloads.contains_key(&id)) {
            Err("invalid_payload")
        } else {
            Ok(())
        }
    }
    fn add_ref(&mut self, payload: Option<u64>) {
        if let Some(id) = payload {
            *self.payloads.get_mut(&id).unwrap() += 1;
            self.garbage.remove(&id);
        }
    }
    fn del_ref(&mut self, payload: Option<u64>) {
        if let Some(id) = payload {
            let refs = self.payloads.get_mut(&id).unwrap();
            *refs -= 1;
            if *refs == 0 {
                self.garbage.insert(id);
            }
        }
    }
    fn reap(&mut self, id: u64, budget: &mut Budget) {
        let r = &self.records[&id];
        if r.stacked || r.leases != 0 {
            return;
        }
        let r = self.records.remove(&id).unwrap();
        budget.records -= 1;
        budget.names -= r.name.len();
        self.del_ref(r.before);
        self.del_ref(r.after);
    }
    fn unstack(&mut self, id: u64, budget: &mut Budget) {
        self.records.get_mut(&id).unwrap().stacked = false;
        self.reap(id, budget);
    }
    fn discard_redo(&mut self, budget: &mut Budget) {
        while let Some(id) = self.redo.pop_front() {
            self.unstack(id, budget);
        }
    }
    fn field(&mut self, id: u64, before: bool, payload: Option<u64>) {
        let r = self.records.get_mut(&id).unwrap();
        let slot = if before { &mut r.before } else { &mut r.after };
        let old = std::mem::replace(slot, payload);
        // Add first: assigning the same ID must not transiently retire it.
        self.add_ref(payload);
        self.del_ref(old);
    }
    fn execute(&mut self, request: Request, next: &mut u64, budget: &mut Budget) -> Result<Output> {
        if request.edit() {
            if self.pending.is_some() {
                return Err("navigation_busy");
            }
            if self.uncertain && !matches!(request, Request::Clear { .. }) {
                return Err("history_uncertain");
            }
        }
        use Request::*;
        Ok(match request {
            Info { .. } => Output::Null,
            RegisterPayload { .. } => {
                if budget.payloads >= MAX_PAYLOADS {
                    return Err("payload_limit");
                }
                let payload = ids(next, 1)?;
                self.payloads.insert(payload, 0);
                budget.payloads += 1;
                Output::Payload { payload }
            }
            DropPayload { payload, .. } => {
                self.payload(Some(payload))?;
                if self.payloads[&payload] != 0 {
                    return Err("payload_in_use");
                }
                self.payloads.remove(&payload);
                self.garbage.remove(&payload);
                budget.payloads -= 1;
                Output::Null
            }
            Begin { name, .. } => {
                name_check(&name)?;
                if !self.enabled {
                    return Ok(Output::Serial { serial: 0 });
                }
                self.generation_room(1)?;
                let serial;
                if let Some(id) = self.open {
                    let depth = self.depth.checked_add(1).ok_or("depth_exhausted")?;
                    serial = self.records[&id].serial;
                    self.depth = depth;
                } else {
                    if budget.records >= MAX_RECORDS {
                        return Err("record_limit");
                    }
                    if budget.names + name.len() > MAX_NAME_BYTES {
                        return Err("name_storage_limit");
                    }
                    let next_serial = self.next_serial.checked_add(1).ok_or("serial_exhausted")?;
                    let id = ids(next, 1)?;
                    serial = self.next_serial;
                    budget.records += 1;
                    budget.names += name.len();
                    self.records.insert(
                        id,
                        self::Record {
                            serial,
                            name,
                            before: None,
                            after: None,
                            before_state: self.state_id,
                            after_state: self.state_id,
                            stacked: true,
                            leases: 0,
                        },
                    );
                    self.open = Some(id);
                    self.depth = 1;
                    self.next_serial = next_serial;
                }
                self.generation += 1;
                Output::Serial { serial }
            }
            Capture { payload, .. } => {
                self.payload(payload)?;
                let eligible = self
                    .open
                    .is_some_and(|id| self.records[&id].before.is_none());
                if eligible && payload.is_some() {
                    self.generation_room(1)?;
                    self.field(self.open.unwrap(), true, payload);
                    self.generation += 1;
                }
                Output::Captured { captured: eligible }
            }
            End { .. } => {
                let Some(id) = self.open else {
                    return Ok(Output::Committed { committed: false });
                };
                self.generation_room(1)?;
                let committed = self.depth == 1 && self.records[&id].before.is_some();
                let state = if committed { Some(ids(next, 1)?) } else { None };
                self.depth -= 1;
                self.generation += 1;
                if self.depth == 0 {
                    self.open = None;
                    if let Some(state) = state {
                        self.state_id = state;
                        self.records.get_mut(&id).unwrap().after_state = state;
                        self.undo.push_back(id);
                        while self.undo.len() > self.limit {
                            let oldest = self.undo.pop_front().unwrap();
                            self.unstack(oldest, budget);
                        }
                        self.discard_redo(budget);
                    } else {
                        self.unstack(id, budget);
                    }
                }
                Output::Committed { committed }
            }
            Abandon { .. } => {
                if let Some(id) = self.open {
                    self.generation_room(1)?;
                    self.open = None;
                    self.depth = 0;
                    self.unstack(id, budget);
                    self.generation += 1;
                }
                Output::Null
            }
            Enable { value, .. } => {
                if self.enabled != value {
                    self.generation_room(1)?;
                    self.enabled = value;
                    self.generation += 1;
                }
                Output::Null
            }
            Records { side, .. } => Output::Records {
                records: match side {
                    Side::Undo => self.undo.iter().copied().collect(),
                    Side::Redo => self.redo.iter().copied().collect(),
                },
            },
            Names { side, .. } => {
                let stack = match side {
                    Side::Undo => &self.undo,
                    Side::Redo => &self.redo,
                };
                let names = stack
                    .iter()
                    .rev()
                    .map(|id| self.records[id].name.clone())
                    .collect();
                Output::Names { names }
            }
            Record { record, .. } => {
                let r = self.records.get(&record).ok_or("invalid_record")?;
                Output::Record {
                    serial: r.serial,
                    name: r.name.clone(),
                    before: r.before,
                    after: r.after,
                }
            }
            RetainRecord { record, .. } => {
                let r = self.records.get_mut(&record).ok_or("invalid_record")?;
                if budget.leases >= MAX_LEASES {
                    return Err("lease_limit");
                }
                r.leases += 1;
                budget.leases += 1;
                Output::Null
            }
            ReleaseRecord { record, .. } => {
                let r = self.records.get_mut(&record).ok_or("invalid_record")?;
                if r.leases == 0 {
                    return Err("record_not_retained");
                }
                r.leases -= 1;
                budget.leases -= 1;
                self.reap(record, budget);
                Output::Null
            }
            SetBefore {
                record, payload, ..
            }
            | SetAfter {
                record, payload, ..
            } => {
                let before = matches!(request, SetBefore { .. });
                let r = self.records.get(&record).ok_or("invalid_record")?;
                self.payload(payload)?;
                if (if before { r.before } else { r.after }) != payload {
                    self.generation_room(1)?;
                    self.field(record, before, payload);
                    self.generation += 1;
                }
                Output::Null
            }
            SetName { record, name, .. } => {
                name_check(&name)?;
                let r = self.records.get(&record).ok_or("invalid_record")?;
                if r.name != name {
                    if budget.names - r.name.len() + name.len() > MAX_NAME_BYTES {
                        return Err("name_storage_limit");
                    }
                    self.generation_room(1)?;
                    budget.names = budget.names - r.name.len() + name.len();
                    self.records.get_mut(&record).unwrap().name = name;
                    self.generation += 1;
                }
                Output::Null
            }
            PrepareNav {
                direction, current, ..
            } => {
                self.payload(current)?;
                let record = match direction {
                    Side::Undo => self.undo.back(),
                    Side::Redo => self.redo.back(),
                }
                .copied();
                if self.open.is_some() || record.is_none() {
                    return Ok(Output::Null);
                }
                // Reserve completion before the host restores anything. Pending
                // operations cannot grow either stack or spend these generations.
                self.generation_room(2)?;
                match direction {
                    Side::Undo => &mut self.redo,
                    Side::Redo => &mut self.undo,
                }
                .try_reserve(1)
                .map_err(|_| "allocation_failed")?;
                let ticket = ids(next, 1)?;
                let record = record.unwrap();
                let r = &self.records[&record];
                let target = match direction {
                    Side::Undo => r.before,
                    Side::Redo => r.after,
                };
                self.add_ref(current);
                self.pending = Some(Pending {
                    id: ticket,
                    record,
                    direction,
                    current,
                });
                self.generation += 1;
                Output::Prepared {
                    ticket,
                    record,
                    target,
                }
            }
            CommitNav { ticket, .. } => {
                let p = self
                    .pending
                    .as_ref()
                    .filter(|p| p.id == ticket)
                    .ok_or("invalid_ticket")?;
                let record = p.record;
                let before = matches!(p.direction, Side::Redo);
                let current = p.current;
                self.field(record, before, current);
                let r = self.records.get_mut(&record).unwrap();
                if before {
                    r.before_state = self.state_id;
                    self.state_id = r.after_state;
                    self.redo.pop_back();
                    self.undo.push_back(record);
                } else {
                    r.after_state = self.state_id;
                    self.state_id = r.before_state;
                    self.undo.pop_back();
                    self.redo.push_back(record);
                }
                self.pending = None;
                self.del_ref(current);
                self.generation += 1;
                Output::Navigated { record }
            }
            AbortNav {
                ticket, unknown, ..
            } => {
                let p = self
                    .pending
                    .as_ref()
                    .filter(|p| p.id == ticket)
                    .ok_or("invalid_ticket")?;
                let current = p.current;
                self.pending = None;
                self.del_ref(current);
                self.uncertain = unknown;
                self.generation += 1;
                Output::Null
            }
            Clear { .. } => {
                if self.open.is_some()
                    || !self.undo.is_empty()
                    || !self.redo.is_empty()
                    || self.uncertain
                {
                    self.generation_room(1)?;
                    let state = if self.uncertain {
                        Some(ids(next, 1)?)
                    } else {
                        None
                    };
                    if let Some(id) = self.open.take() {
                        self.unstack(id, budget);
                    }
                    while let Some(id) = self.undo.pop_front() {
                        self.unstack(id, budget);
                    }
                    self.discard_redo(budget);
                    self.depth = 0;
                    if let Some(state) = state {
                        self.state_id = state;
                    }
                    self.uncertain = false;
                    self.generation += 1;
                }
                Output::Null
            }
            Garbage { limit, .. } => {
                if !(1..=128).contains(&limit) {
                    return Err("invalid_limit");
                }
                Output::Garbage {
                    payloads: self.garbage.iter().take(limit as usize).copied().collect(),
                }
            }
            AckGarbage { payloads, .. } => {
                if payloads.len() > 128 {
                    return Err("garbage_limit");
                }
                let unique: BTreeSet<_> = payloads.iter().copied().collect();
                if unique.len() != payloads.len() {
                    return Err("duplicate_payload");
                }
                if payloads.iter().any(|id| !self.garbage.contains(id)) {
                    return Err("invalid_garbage");
                }
                for id in payloads {
                    self.garbage.remove(&id);
                    self.payloads.remove(&id);
                    budget.payloads -= 1;
                }
                Output::Null
            }
            Create { .. } | Close { .. } => unreachable!(),
        })
    }
}
