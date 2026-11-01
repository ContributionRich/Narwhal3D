//! Live index/journal coordination. Exact payloads remain host-owned; immutable
//! witnesses certify snapshots, not a second undo stack or full document revision.
use super::*;
use crate::observation;
use std::collections::HashSet;

impl Registry {
    pub(super) fn live_track(&mut self, owner: u64) -> Result<Value> {
        let o = self.owner_mut(owner)?;
        if o.live || !o.state.rows.is_empty() || !o.snapshots.is_empty() || o.ticket.is_some() {
            return Err("tracking_requires_fresh_owner");
        }
        o.live = true;
        Ok(json!({}))
    }
    pub(super) fn live_mark(&mut self, owner: u64, ids: Vec<String>, moved: bool) -> Result<Value> {
        for id in &ids {
            uuid(id)?;
        }
        let o = self.owner_mut(owner)?;
        if !o.live {
            return Err("live_owner_required");
        }
        if o.dirty.len().saturating_add(ids.len()) > MAX_ROWS {
            o.dirty_all = true;
            o.dirty.clear();
            o.moved_since_index.clear();
        } else {
            for id in ids {
                if moved {
                    o.moved_since_index.insert(id.clone());
                }
                *o.dirty.entry(id).or_default() |= moved;
            }
        }
        Ok(json!({}))
    }
    pub(super) fn live_mark_all(&mut self, owner: u64) -> Result<Value> {
        let o = self.owner_mut(owner)?;
        if !o.live {
            return Err("live_owner_required");
        }
        o.dirty_all = true;
        Ok(json!({}))
    }
    pub(super) fn live_dirty(&self, owner: u64) -> Result<Value> {
        let o = self.owner(owner)?;
        let mut rows: Vec<_> = o.dirty.iter().collect();
        rows.sort_unstable_by_key(|(id, _)| *id);
        Ok(json!({"all":o.dirty_all,"rows":rows}))
    }
    pub(super) fn live_ticket_diff(&self, owner: u64, ticket: u64) -> Result<Value> {
        let o = self.owner(owner)?;
        if !o.live {
            return Err("live_owner_required");
        }
        let target = &o
            .ticket
            .as_ref()
            .filter(|t| t.id == ticket)
            .ok_or("invalid_ticket")?
            .state;
        let removed: Vec<_> = o
            .state
            .rows
            .iter()
            .filter(|row| {
                target
                    .index
                    .get(&row.id)
                    .is_none_or(|i| target.rows[*i].payload != row.payload)
            })
            .collect();
        let added: Vec<_> = target
            .rows
            .iter()
            .filter(|row| {
                o.state
                    .index
                    .get(&row.id)
                    .is_none_or(|i| o.state.rows[*i].payload != row.payload)
            })
            .map(|row| &row.id)
            .collect();
        Ok(json!({"removed":removed,"added":added}))
    }

    pub(super) fn live_ticket_rows(
        &self,
        owner: u64,
        ticket: u64,
        offset: usize,
        limit: usize,
    ) -> Result<Value> {
        if limit > MAX_PAGE {
            return Err("page_limit");
        }
        let o = self.owner(owner)?;
        let pending = o
            .ticket
            .as_ref()
            .filter(|t| t.id == ticket)
            .ok_or("invalid_ticket")?;
        if offset > pending.state.rows.len() {
            return Err("invalid_offset");
        }
        let end = offset.saturating_add(limit).min(pending.state.rows.len());
        Ok(json!({"revision":o.revision,"rows":&pending.state.rows[offset..end],"next":end}))
    }
    pub(super) fn live_bind(
        &mut self,
        owner: u64,
        id: String,
        display: Option<String>,
    ) -> Result<Value> {
        let o = self.owner(owner)?;
        o.readable()?;
        if !o.live {
            return Err("live_owner_required");
        }
        if o.ticket.is_some() {
            return Err("transaction_pending");
        }
        uuid(&id)?;
        let position = o.state.index.get(&id).ok_or("missing_uuid")?;
        if o.state.rows[*position].display == display {
            return Ok(o.info());
        }
        let rows = o.state.edited(vec![Edit::Bind { id, display }])?;
        let changed = rows != o.state.rows;
        if changed {
            let revision = o.revision.checked_add(1).ok_or("revision_exhausted")?;
            let state = State::build(rows, &self.retained)?;
            let o = self.owner_mut(owner)?;
            o.state = state;
            o.revision = revision;
        }
        Ok(self.owner(owner)?.info())
    }
    pub(super) fn live_index(&mut self, owner: u64, revision: u64, data: &[u8]) -> Result<Value> {
        let observed = observation::decode_index(data)?;
        self.index_objects(owner, revision, &observed)?;
        Ok(self.owner(owner)?.info())
    }
    fn index_objects(
        &mut self,
        owner: u64,
        revision: u64,
        observed: &[observation::Object<'_>],
    ) -> Result<()> {
        let o = self.owner(owner)?;
        o.readable()?;
        if !o.live {
            return Err("live_owner_required");
        }
        if o.revision != revision {
            return Err("stale_revision");
        }
        if o.ticket.is_some() {
            return Err("transaction_pending");
        }
        let live: HashMap<_, _> = observed.iter().map(|row| (row.id, row)).collect();
        let actual_order: Vec<_> = observed.iter().map(|row| row.id).collect();
        let physical_changed = actual_order
            .iter()
            .copied()
            .ne(o.physical_order.iter().map(String::as_str));
        let mut unknown =
            o.initialized
                && !o.dirty_all
                && (o.state.rows.iter().any(|row| {
                    !live.contains_key(row.id.as_str()) && !o.dirty.contains_key(&row.id)
                }) || observed.iter().any(|row| {
                    let changed = o
                        .state
                        .index
                        .get(row.id)
                        .is_none_or(|i| o.state.rows[*i].name != row.name);
                    changed && !o.dirty.contains_key(row.id)
                }));
        if o.initialized && !o.dirty_all && physical_changed {
            let previous: HashSet<_> = o.physical_order.iter().map(String::as_str).collect();
            let mut expected: Vec<_> = o
                .physical_order
                .iter()
                .map(String::as_str)
                .filter(|id| live.contains_key(id) && !o.moved_since_index.contains(*id))
                .collect();
            expected.extend(
                actual_order
                    .iter()
                    .copied()
                    .filter(|id| !previous.contains(id) || o.moved_since_index.contains(*id)),
            );
            let normalized = actual_order.iter().copied().eq(o
                .state
                .rows
                .iter()
                .map(|row| row.id.as_str()));
            unknown |= expected != actual_order && !normalized;
        }
        if unknown {
            self.execute(Request::Invalidate { owner })?;
            return Err("unmediated_object_index_write");
        }
        let force_physical = o.dirty_all && physical_changed;
        let unchanged = observed.len() == o.state.rows.len()
            && observed.iter().all(|row| {
                o.state
                    .index
                    .get(row.id)
                    .is_some_and(|i| o.state.rows[*i].name == row.name)
            });
        if unchanged && o.moved_since_index.is_empty() && !force_physical {
            let o = self.owner_mut(owner)?;
            if physical_changed {
                o.physical_order = actual_order.iter().map(|id| (*id).to_owned()).collect();
            }
            o.initialized = true;
            return Ok(());
        }
        let mut rows = Vec::with_capacity(observed.len());
        let mut kept = HashSet::new();
        for row in &o.state.rows {
            if let Some(current) = live.get(row.id.as_str())
                && !o.moved_since_index.contains(&row.id)
                && !force_physical
            {
                let mut row = row.clone();
                row.name = current.name.to_owned();
                kept.insert(row.id.clone());
                rows.push(row);
            }
        }
        for current in observed {
            if kept.contains(current.id) {
                continue;
            }
            let old = o.state.index.get(current.id).map(|i| &o.state.rows[*i]);
            rows.push(Row {
                id: current.id.to_owned(),
                name: current.name.to_owned(),
                display: old.and_then(|row| row.display.clone()),
                payload: old.and_then(|row| row.payload),
            });
        }
        if rows != o.state.rows {
            let revision = o.revision.checked_add(1).ok_or("revision_exhausted")?;
            let state = State::build(rows, &self.retained)?;
            let o = self.owner_mut(owner)?;
            o.state = state;
            o.revision = revision;
        }
        let o = self.owner_mut(owner)?;
        o.initialized = true;
        o.physical_order = actual_order.into_iter().map(str::to_owned).collect();
        o.moved_since_index.clear();
        Ok(())
    }
    pub(super) fn live_capture(
        &mut self,
        owner: u64,
        revision: u64,
        data: &[u8],
        rebuild: bool,
    ) -> Result<Value> {
        let observed = observation::decode(data)?;
        if rebuild {
            let o = self.owner(owner)?;
            o.admit(revision, true)?;
            if !o.live {
                return Err("live_owner_required");
            }
        } else {
            self.index_objects(owner, revision, &observed)?;
        }
        let (mut rows, unknown, revision) = {
            let o = self.owner(owner)?;
            let prior = o.baseline.as_ref();
            let before = prior.and_then(|p| p.observation.as_deref());
            let live: HashMap<_, _> = observed.iter().map(|row| (row.id, row)).collect();
            let mut rows = if rebuild {
                observed
                    .iter()
                    .map(|row| Row {
                        id: row.id.to_owned(),
                        name: row.name.to_owned(),
                        display: o
                            .state
                            .index
                            .get(row.id)
                            .and_then(|i| o.state.rows[*i].display.clone()),
                        payload: None,
                    })
                    .collect()
            } else {
                o.state.rows.clone()
            };
            let mut unknown = false;
            for row in &mut rows {
                let current = live
                    .get(row.id.as_str())
                    .ok_or("observation_membership_mismatch")?;
                let same = before
                    .and_then(|w| w.get(row.id.as_str()))
                    .is_some_and(|previous| previous.matches(current));
                if same {
                    row.payload =
                        prior.and_then(|p| p.index.get(&row.id).and_then(|i| p.rows[*i].payload));
                } else {
                    if !rebuild && prior.is_some() && !o.dirty_all && !o.dirty.contains_key(&row.id)
                    {
                        unknown = true;
                    }
                    row.payload = None;
                }
            }
            (rows, unknown, o.revision)
        };
        if unknown {
            self.execute(Request::Invalidate { owner })?;
            return Err("unmediated_object_payload_write");
        }
        for row in &mut rows {
            if row.payload.is_none() {
                row.payload = Some(self.allocate()?);
            }
        }
        self.observe(
            ObservationRequest::Prepare {
                owner,
                revision,
                rows,
                rebuild,
            },
            data,
        )
    }
}
