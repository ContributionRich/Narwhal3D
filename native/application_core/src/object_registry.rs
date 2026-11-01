//! Object membership/order authority. Geometry stays in owner-scoped host payloads.
//! Transactions deliberately do not implement an Undo stack. See OBJECT_REGISTRY.md.
use serde::{Deserialize, Deserializer, Serialize, de};
use serde_json::{Value, json};
use std::{
    collections::{HashMap, HashSet},
    fmt,
    sync::{
        Arc,
        atomic::{AtomicUsize, Ordering},
    },
};

type Result<T> = std::result::Result<T, &'static str>;
#[path = "object_witness.rs"]
mod object_witness;
use object_witness::Witness;
pub(crate) const MAX_REQUEST: usize = 32 * 1024 * 1024;
const MAX_ROWS: usize = 100_000;
const MAX_PAGE: usize = 1024;
const MAX_TEXT: usize = 4096;
const MAX_STATE: usize = 64 * 1024 * 1024;
const MAX_RETAINED: usize = 256 * 1024 * 1024;
const MAX_OWNERS: usize = 64;
const MAX_SNAPSHOTS: usize = 4096;

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub(crate) struct Row {
    id: String,
    name: String,
    display: Option<String>,
    payload: Option<u64>,
}
fn bounded_items<'de, D: Deserializer<'de>, T: Deserialize<'de>>(
    d: D,
) -> std::result::Result<Vec<T>, D::Error> {
    struct Visitor<T>(std::marker::PhantomData<T>);
    impl<'de, T: Deserialize<'de>> de::Visitor<'de> for Visitor<T> {
        type Value = Vec<T>;
        fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
            f.write_str("bounded object rows")
        }
        fn visit_seq<A: de::SeqAccess<'de>>(
            self,
            mut seq: A,
        ) -> std::result::Result<Vec<T>, A::Error> {
            let mut rows = Vec::new();
            while let Some(row) = seq.next_element()? {
                if rows.len() == MAX_ROWS {
                    return Err(de::Error::custom("row_limit"));
                }
                rows.push(row);
            }
            Ok(rows)
        }
    }
    d.deserialize_seq(Visitor(std::marker::PhantomData))
}
#[derive(Debug, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub(crate) enum Edit {
    Add { row: Row },
    Replace { row: Row, append: bool },
    Delete { id: String },
    Bind { id: String, display: Option<String> },
}

#[derive(Debug, Deserialize)]
#[serde(tag = "op", rename_all = "snake_case", deny_unknown_fields)]
pub(crate) enum Request {
    Create {
        #[serde(deserialize_with = "bounded_items")]
        rows: Vec<Row>,
    },
    Close {
        owner: u64,
    },
    Info {
        owner: u64,
    },
    ReservePayloads {
        owner: u64,
        count: u64,
    },
    Track {
        owner: u64,
    },
    Mark {
        owner: u64,
        #[serde(deserialize_with = "bounded_items")]
        ids: Vec<String>,
        moved: bool,
    },
    MarkAll {
        owner: u64,
    },
    Dirty {
        owner: u64,
    },
    OrderPending {
        owner: u64,
    },
    NormalizePlan {
        owner: u64,
    },
    BindDisplay {
        owner: u64,
        id: String,
        display: Option<String>,
    },
    TicketDiff {
        owner: u64,
        ticket: u64,
    },
    TicketRows {
        owner: u64,
        ticket: u64,
        offset: usize,
        limit: usize,
    },
    Rows {
        owner: u64,
        offset: usize,
        limit: usize,
    },
    FindName {
        owner: u64,
        name: String,
    },
    Get {
        owner: u64,
        id: String,
    },
    Snapshot {
        owner: u64,
    },
    ReleaseSnapshot {
        owner: u64,
        snapshot: u64,
    },
    Prepare {
        owner: u64,
        revision: u64,
        #[serde(deserialize_with = "bounded_items")]
        rows: Vec<Row>,
        rebuild: bool,
    },
    PrepareRestore {
        owner: u64,
        revision: u64,
        snapshot: u64,
    },
    PrepareEdits {
        owner: u64,
        revision: u64,
        #[serde(deserialize_with = "bounded_items")]
        edits: Vec<Edit>,
    },
    BeginApply {
        owner: u64,
        ticket: u64,
    },
    Commit {
        owner: u64,
        ticket: u64,
    },
    Abort {
        owner: u64,
        ticket: u64,
    },
    Invalidate {
        owner: u64,
    },
}

fn text(s: &str) -> Result<()> {
    if s.len() > MAX_TEXT {
        Err("text_limit")
    } else if s.contains('\0') {
        Err("invalid_text")
    } else {
        Ok(())
    }
}
pub(crate) fn uuid(s: &str) -> Result<()> {
    let b = s.as_bytes();
    if b.len() != 36
        || b.iter().enumerate().any(|(i, c)| {
            if [8, 13, 18, 23].contains(&i) {
                *c != b'-'
            } else {
                !c.is_ascii_digit() && !(b'a'..=b'f').contains(c)
            }
        })
        || !b.iter().any(|c| *c != b'0' && *c != b'-')
    {
        Err("invalid_uuid")
    } else {
        Ok(())
    }
}
#[derive(Debug, Deserialize)]
#[serde(tag = "op", rename_all = "snake_case", deny_unknown_fields)]
pub(crate) enum ObservationRequest {
    Prepare {
        owner: u64,
        revision: u64,
        #[serde(deserialize_with = "bounded_items")]
        rows: Vec<Row>,
        rebuild: bool,
    },
    Index {
        owner: u64,
        revision: u64,
    },
    Capture {
        owner: u64,
        revision: u64,
        rebuild: bool,
    },
    Check {
        owner: u64,
        revision: u64,
    },
    Commit {
        owner: u64,
        ticket: u64,
    },
}

struct State {
    observation: Option<Arc<Witness>>,
    rows: Vec<Row>,
    index: HashMap<String, usize>,
    names: HashMap<String, usize>,
    bytes: usize,
    retained: Arc<AtomicUsize>,
}
impl Drop for State {
    fn drop(&mut self) {
        self.retained.fetch_sub(self.bytes, Ordering::Relaxed);
    }
}
impl State {
    fn edited(&self, edits: Vec<Edit>) -> Result<Vec<Row>> {
        if edits.len() > MAX_ROWS {
            return Err("edit_limit");
        }
        let mut rows: Vec<Option<Row>> = self.rows.iter().cloned().map(Some).collect();
        let mut index = self.index.clone();
        for edit in edits {
            match edit {
                Edit::Add { row } => {
                    uuid(&row.id)?;
                    if index.contains_key(&row.id) {
                        return Err("duplicate_uuid");
                    }
                    if index.len() == MAX_ROWS {
                        return Err("row_limit");
                    }
                    index.insert(row.id.clone(), rows.len());
                    rows.push(Some(row));
                }
                Edit::Replace { row, append } => {
                    uuid(&row.id)?;
                    let position = *index.get(&row.id).ok_or("missing_uuid")?;
                    if append {
                        rows[position] = None;
                        index.insert(row.id.clone(), rows.len());
                        rows.push(Some(row));
                    } else {
                        rows[position] = Some(row);
                    }
                }
                Edit::Delete { id } => {
                    uuid(&id)?;
                    if let Some(position) = index.remove(&id) {
                        rows[position] = None;
                    }
                }
                Edit::Bind { id, display } => {
                    uuid(&id)?;
                    let position = *index.get(&id).ok_or("missing_uuid")?;
                    rows[position].as_mut().expect("live index").display = display;
                }
            }
        }
        Ok(rows.into_iter().flatten().collect())
    }

    fn build(rows: Vec<Row>, retained: &Arc<AtomicUsize>) -> Result<Arc<Self>> {
        if rows.len() > MAX_ROWS {
            return Err("row_limit");
        }
        let mut bytes = 256usize;
        let mut index = HashMap::new();
        let mut names = HashMap::new();
        for (i, row) in rows.iter().enumerate() {
            uuid(&row.id)?;
            text(&row.name)?;
            if let Some(name) = &row.display {
                text(name)?;
            }
            if row.payload == Some(0) {
                return Err("invalid_payload");
            }
            // Conservative admission accounting, including duplicate index keys and
            // allocator/hash-table overhead. Shared states are charged only once.
            bytes = bytes
                .checked_add(
                    256 + row.id.len() * 2
                        + row.name.len() * 4
                        + row.display.as_ref().map_or(0, String::len),
                )
                .ok_or("state_limit")?;
            if bytes > MAX_STATE {
                return Err("state_limit");
            }
            names.entry(row.name.to_lowercase()).or_insert(i);
            if index.insert(row.id.clone(), i).is_some() {
                return Err("duplicate_uuid");
            }
        }
        retained
            .fetch_update(Ordering::Relaxed, Ordering::Relaxed, |n| {
                n.checked_add(bytes).filter(|n| *n <= MAX_RETAINED)
            })
            .map_err(|_| "retained_limit")?;
        Ok(Arc::new(Self {
            observation: None,
            rows,
            index,
            names,
            bytes,
            retained: retained.clone(),
        }))
    }
}
fn observation_matches(before: &Witness, after: &[u8]) -> Result<bool> {
    before.matches(after)
}

struct Ticket {
    id: u64,
    state: Arc<State>,
    needs_confirmation: bool,
    applying: bool,
    rebuild: bool,
}
struct Owner {
    physical_order: Vec<String>,
    moved_since_index: HashSet<String>,
    live: bool,
    initialized: bool,
    dirty: HashMap<String, bool>,
    dirty_all: bool,
    baseline: Option<Arc<State>>,
    revision: u64,
    valid: bool,
    state: Arc<State>,
    ticket: Option<Ticket>,
    snapshots: HashMap<u64, Arc<State>>,
}
impl Owner {
    fn info(&self) -> Value {
        json!({"revision":self.revision,"count":self.state.rows.len(),
            "authoritative":self.valid && (!self.live || (self.dirty.is_empty() && !self.dirty_all && self.state.observation.is_some())) && !self.ticket.as_ref().is_some_and(|t|t.applying),
            "pending":self.ticket.is_some()})
    }
    fn readable(&self) -> Result<()> {
        if !self.valid {
            Err("authority_invalid")
        } else if self.ticket.as_ref().is_some_and(|t| t.applying) {
            Err("transaction_pending")
        } else {
            Ok(())
        }
    }
    fn admit(&self, revision: u64, rebuild: bool) -> Result<()> {
        if self.revision != revision {
            return Err("stale_revision");
        }
        if self.ticket.is_some() {
            return Err("transaction_pending");
        }
        if rebuild == self.valid {
            return Err(if rebuild {
                "rebuild_not_required"
            } else {
                "authority_invalid"
            });
        }
        if self.revision == u64::MAX {
            return Err("revision_exhausted");
        }
        Ok(())
    }
}
#[derive(Default)]
pub(crate) struct Registry {
    owners: HashMap<u64, Owner>,
    next: u64,
    retained: Arc<AtomicUsize>,
}
impl Registry {
    fn allocate(&mut self) -> Result<u64> {
        self.next = self.next.checked_add(1).ok_or("handle_exhausted")?;
        Ok(self.next)
    }
    fn owner(&self, id: u64) -> Result<&Owner> {
        self.owners.get(&id).ok_or("invalid_owner")
    }
    fn owner_mut(&mut self, id: u64) -> Result<&mut Owner> {
        self.owners.get_mut(&id).ok_or("invalid_owner")
    }
    fn prepare(&mut self, owner: u64, state: Arc<State>, rebuild: bool) -> Result<Value> {
        let ticket = self.allocate()?;
        self.owner_mut(owner)?.ticket = Some(Ticket {
            id: ticket,
            needs_confirmation: state.observation.is_some(),
            state,
            applying: false,
            rebuild,
        });
        Ok(json!({"ticket":ticket}))
    }
    pub(crate) fn observe(&mut self, request: ObservationRequest, data: &[u8]) -> Result<Value> {
        use crate::observation::decode;
        match request {
            ObservationRequest::Index { owner, revision } => self.live_index(owner, revision, data),
            ObservationRequest::Capture {
                owner,
                revision,
                rebuild,
            } => self.live_capture(owner, revision, data, rebuild),
            ObservationRequest::Prepare {
                owner,
                revision,
                rows,
                rebuild,
            } => {
                let old = self.owner(owner)?;
                old.admit(revision, rebuild)?;
                let observed = decode(data)?;
                let index: HashMap<_, _> = observed.iter().map(|row| (row.id, row)).collect();
                if index.len() != rows.len() {
                    return Err("observation_membership_mismatch");
                }
                let previous = old
                    .baseline
                    .as_ref()
                    .unwrap_or(&old.state)
                    .observation
                    .clone();
                let mut payloads = std::collections::HashSet::new();
                for row in &rows {
                    let item = index
                        .get(row.id.as_str())
                        .ok_or("observation_membership_mismatch")?;
                    if row.name != item.name {
                        return Err("observation_name_mismatch");
                    }
                    let payload = row.payload.ok_or("observation_payload_required")?;
                    if payload == 0 || !payloads.insert(payload) {
                        return Err("invalid_payload");
                    }
                    if let Some(before) = previous.as_ref().and_then(|w| w.get(row.id.as_str()))
                        && !before.matches(item)
                        && let Some(position) = old.state.index.get(&row.id)
                        && old.state.rows[*position].payload == row.payload
                    {
                        return Err("changed_object_reused_payload");
                    }
                }
                let mut state = State::build(rows, &self.retained)?;
                let state_mut = Arc::get_mut(&mut state).expect("new unshared state");
                let witness = Witness::build(data, previous.as_deref(), &self.retained)?;
                if state_mut
                    .bytes
                    .checked_add(witness.logical_bytes)
                    .ok_or("state_limit")?
                    > MAX_STATE
                {
                    return Err("state_limit");
                }
                state_mut.observation = Some(witness);
                self.prepare(owner, state, rebuild)
            }
            ObservationRequest::Check {
                owner: owner_id,
                revision,
            } => {
                let owner = self.owner(owner_id)?;
                owner.readable()?;
                if revision != owner.revision {
                    return Err("stale_revision");
                }
                let before = owner
                    .state
                    .observation
                    .as_deref()
                    .ok_or("observation_required")?;
                let matched = observation_matches(before, data)?;
                let revision = owner.revision;

                let revision = if matched {
                    revision
                } else {
                    self.execute(Request::Invalidate { owner: owner_id })?["revision"]
                        .as_u64()
                        .expect("revision")
                };
                Ok(json!({"revision":revision,"matches":matched}))
            }
            ObservationRequest::Commit { owner, ticket } => {
                let state = {
                    let owner = self.owner(owner)?;
                    let pending = owner
                        .ticket
                        .as_ref()
                        .filter(|t| t.id == ticket)
                        .ok_or("invalid_ticket")?;
                    if !pending.applying {
                        return Err("apply_not_started");
                    }
                    pending.state.clone()
                };
                let before = state.observation.as_deref().ok_or("observation_required")?;
                // Failed confirmation is a possibly-applied host operation, not an
                // abort. Consume its ticket and invalidate before reporting failure.
                let matched = observation_matches(before, data);
                if matched != Ok(true) {
                    self.execute(Request::Invalidate { owner })?;
                    return Err(matched.err().unwrap_or("observation_commit_mismatch"));
                }
                self.owner_mut(owner)?
                    .ticket
                    .as_mut()
                    .expect("checked ticket")
                    .needs_confirmation = false;
                let physical = if self.owner(owner)?.live {
                    Some(decode(data)?.iter().map(|row| row.id.to_owned()).collect())
                } else {
                    None
                };
                let reply = self.execute(Request::Commit { owner, ticket })?;
                if let Some(physical) = physical {
                    self.owner_mut(owner)?.physical_order = physical;
                }
                Ok(reply)
            }
        }
    }

    pub(crate) fn execute(&mut self, request: Request) -> Result<Value> {
        match request {
            Request::Create { rows } => {
                if self.owners.len() == MAX_OWNERS {
                    return Err("owner_limit");
                }
                let state = State::build(rows, &self.retained)?;
                let owner = self.allocate()?;
                self.owners.insert(
                    owner,
                    Owner {
                        physical_order: Vec::new(),
                        moved_since_index: HashSet::new(),
                        live: false,
                        initialized: false,
                        dirty: HashMap::new(),
                        dirty_all: false,
                        baseline: None,
                        revision: 0,
                        valid: true,
                        state,
                        ticket: None,
                        snapshots: HashMap::new(),
                    },
                );
                let mut result = self.owner(owner)?.info();
                result["owner"] = json!(owner);
                Ok(result)
            }
            Request::Close { owner } => {
                self.owners.remove(&owner).ok_or("invalid_owner")?;
                Ok(json!({}))
            }
            Request::Info { owner } => Ok(self.owner(owner)?.info()),
            Request::Track { owner } => self.live_track(owner),
            Request::Mark { owner, ids, moved } => self.live_mark(owner, ids, moved),
            Request::MarkAll { owner } => self.live_mark_all(owner),
            Request::Dirty { owner } => self.live_dirty(owner),
            Request::NormalizePlan { owner } => {
                let o = self.owner(owner)?;
                o.readable()?;
                if !o.live {
                    return Err("live_owner_required");
                }
                let prefix = o
                    .physical_order
                    .iter()
                    .zip(&o.state.rows)
                    .take_while(|(physical, row)| **physical == row.id)
                    .count();
                let ids: Vec<_> = o.state.rows[prefix..].iter().map(|row| &row.id).collect();
                Ok(json!({"ids":ids}))
            }
            Request::OrderPending { owner } => {
                let o = self.owner(owner)?;
                o.readable()?;
                if !o.live {
                    return Err("live_owner_required");
                }
                Ok(json!({"pending":o.physical_order.iter().map(String::as_str)
                    .ne(o.state.rows.iter().map(|row|row.id.as_str()))}))
            }
            Request::BindDisplay { owner, id, display } => self.live_bind(owner, id, display),
            Request::TicketDiff { owner, ticket } => self.live_ticket_diff(owner, ticket),
            Request::TicketRows {
                owner,
                ticket,
                offset,
                limit,
            } => self.live_ticket_rows(owner, ticket, offset, limit),
            Request::ReservePayloads { owner, count } => {
                self.owner(owner)?;
                if count == 0 || count > MAX_ROWS as u64 {
                    return Err("payload_reservation_limit");
                }
                let first = self.next.checked_add(1).ok_or("handle_exhausted")?;
                let last = self.next.checked_add(count).ok_or("handle_exhausted")?;
                self.next = last;
                Ok(json!({"first":first,"count":count}))
            }
            Request::Rows {
                owner,
                offset,
                limit,
            } => {
                if limit > MAX_PAGE {
                    return Err("page_limit");
                }
                let o = self.owner(owner)?;
                o.readable()?;
                if offset > o.state.rows.len() {
                    return Err("invalid_offset");
                }
                let end = offset.saturating_add(limit).min(o.state.rows.len());
                Ok(json!({"revision":o.revision,"rows":&o.state.rows[offset..end],"next":end}))
            }
            Request::FindName { owner, name } => {
                text(&name)?;
                let o = self.owner(owner)?;
                o.readable()?;
                let key = name.to_lowercase();
                let id = o.state.names.get(&key).map(|i| &o.state.rows[*i].id);
                Ok(json!({"revision":o.revision,"id":id}))
            }
            Request::Get { owner, id } => {
                uuid(&id)?;
                let o = self.owner(owner)?;
                o.readable()?;
                let row = o.state.index.get(&id).map(|i| &o.state.rows[*i]);
                Ok(json!({"revision":o.revision,"row":row}))
            }
            Request::Snapshot { owner } => {
                let o = self.owner(owner)?;
                o.readable()?;
                if o.live && (!o.dirty.is_empty() || o.dirty_all || o.state.observation.is_none()) {
                    return Err("observation_required");
                }
                if o.ticket.is_some() {
                    return Err("transaction_pending");
                }
                if self
                    .owners
                    .values()
                    .map(|o| o.snapshots.len())
                    .sum::<usize>()
                    >= MAX_SNAPSHOTS
                {
                    return Err("snapshot_limit");
                }
                let snapshot = self.allocate()?;
                let o = self.owner_mut(owner)?;
                o.snapshots.insert(snapshot, o.state.clone());
                Ok(json!({"snapshot":snapshot,"revision":o.revision}))
            }
            Request::ReleaseSnapshot { owner, snapshot } => {
                self.owner_mut(owner)?
                    .snapshots
                    .remove(&snapshot)
                    .ok_or("invalid_snapshot")?;
                Ok(json!({}))
            }
            Request::Prepare {
                owner,
                revision,
                rows,
                rebuild,
            } => {
                self.owner(owner)?.admit(revision, rebuild)?;
                if self.owner(owner)?.state.observation.is_some() {
                    return Err("observation_required");
                }
                let state = State::build(rows, &self.retained)?;
                self.prepare(owner, state, rebuild)
            }
            Request::PrepareEdits {
                owner,
                revision,
                edits,
            } => {
                let o = self.owner(owner)?;
                o.admit(revision, false)?;
                if o.state.observation.is_some() {
                    return Err("observation_required");
                }
                let rows = o.state.edited(edits)?;
                let state = State::build(rows, &self.retained)?;
                self.prepare(owner, state, false)
            }
            Request::PrepareRestore {
                owner,
                revision,
                snapshot,
            } => {
                let o = self.owner(owner)?;
                o.admit(revision, false)?;
                let state = o
                    .snapshots
                    .get(&snapshot)
                    .ok_or("invalid_snapshot")?
                    .clone();
                if o.state.observation.is_some() && state.observation.is_none() {
                    return Err("observation_required");
                }
                self.prepare(owner, state, false)
            }
            Request::BeginApply { owner, ticket } => {
                let o = self.owner_mut(owner)?;
                let t = o
                    .ticket
                    .as_mut()
                    .filter(|t| t.id == ticket)
                    .ok_or("invalid_ticket")?;
                if t.applying {
                    return Err("possibly_applied");
                }
                t.applying = true;
                Ok(json!({}))
            }
            Request::Commit { owner, ticket } => {
                let o = self.owner_mut(owner)?;
                let t = o
                    .ticket
                    .as_ref()
                    .filter(|t| t.id == ticket)
                    .ok_or("invalid_ticket")?;
                if !t.applying {
                    return Err("apply_not_started");
                }
                if t.needs_confirmation {
                    return Err("observation_confirmation_required");
                }
                let changed = t.rebuild
                    || o.state.rows != t.state.rows
                    || (o.state.observation != t.state.observation && !o.live);
                let revision = if changed {
                    o.revision.checked_add(1).ok_or("revision_exhausted")?
                } else {
                    o.revision
                };
                let t = o.ticket.take().expect("validated ticket");
                if changed || o.state.observation != t.state.observation {
                    o.state = t.state;
                }
                if o.state.observation.is_some() {
                    o.baseline = Some(o.state.clone());
                    o.dirty.clear();
                    o.moved_since_index.clear();
                    o.dirty_all = false;
                }
                o.revision = revision;
                o.valid = true;
                Ok(o.info())
            }
            Request::Abort { owner, ticket } => {
                let o = self.owner_mut(owner)?;
                let t = o
                    .ticket
                    .as_ref()
                    .filter(|t| t.id == ticket)
                    .ok_or("invalid_ticket")?;
                if t.applying {
                    return Err("possibly_applied");
                }
                o.ticket = None;
                Ok(json!({}))
            }
            Request::Invalidate { owner } => {
                let o = self.owner_mut(owner)?;
                // Invalidate consumes a pending ticket even if host publication is
                // unknown. Repeated invalidation cannot resurrect/replay a ticket.
                if o.valid {
                    o.revision = o.revision.checked_add(1).ok_or("revision_exhausted")?;
                }
                o.valid = false;
                o.ticket = None;
                Ok(o.info())
            }
        }
    }
}

#[path = "live_objects.rs"]
mod live_objects;

#[cfg(test)]
#[path = "object_registry_tests.rs"]
mod tests;
