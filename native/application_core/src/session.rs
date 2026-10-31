use crate::table::{Result, Table, Text, deserialize_rows};
use serde::{Deserialize, Deserializer, Serialize, Serializer};
use std::{
    collections::HashMap,
    sync::{
        Arc,
        atomic::{AtomicUsize, Ordering},
    },
};

// OURS: conservative global admission budget, counting shared data again per
// distinct table version, not per snapshot. This bounds retained state, not RSS.
const MAX_RETAINED: usize = 256 * 1024 * 1024;
const MAX_SESSIONS: usize = 256;
const MAX_SNAPSHOTS: usize = 100_000;
const MAX_TICKETS: usize = 1024;

fn required_nullable<'de, D: Deserializer<'de>>(
    d: D,
) -> std::result::Result<Option<Text>, D::Error> {
    Option::<Text>::deserialize(d)
}
#[derive(Deserialize, Debug)]
#[serde(tag = "op", rename_all = "snake_case", deny_unknown_fields)]
pub(crate) enum Request {
    Create {
        #[serde(deserialize_with = "deserialize_rows")]
        rows: Vec<(Text, Text)>,
    },
    Close {
        session: u64,
    },
    Info {
        session: u64,
    },
    Rows {
        session: u64,
    },
    DocumentUserTextCount {
        session: u64,
    },
    Get {
        session: u64,
        key: Text,
        exact: bool,
    },
    At {
        session: u64,
        index: u64,
    },
    Snapshot {
        session: u64,
    },
    ReleaseSnapshot {
        session: u64,
        snapshot: u64,
    },
    SnapshotRows {
        session: u64,
        snapshot: u64,
    },
    Equal {
        session: u64,
        left: u64,
        right: u64,
    },
    Prepare {
        session: u64,
        generation: u64,
        key: Text,
        #[serde(deserialize_with = "required_nullable")]
        value: Option<Text>,
    },
    Commit {
        session: u64,
        ticket: u64,
    },
    Abort {
        session: u64,
        ticket: u64,
    },
    Restore {
        session: u64,
        snapshot: u64,
    },
}

#[derive(Serialize)]
#[serde(untagged)]
pub(crate) enum Reply {
    Empty {},
    Created {
        session: u64,
        generation: u64,
        state_id: u64,
        count: usize,
    },
    Info {
        generation: u64,
        state_id: u64,
        count: usize,
    },
    Rows {
        rows: RowsReply,
    },
    Count {
        count: u64,
    },
    Get {
        found: bool,
        value: Option<Arc<str>>,
        key: Option<Arc<str>>,
    },
    At {
        found: bool,
        row: Option<crate::table::Row>,
    },
    Snapshot {
        snapshot: u64,
        state_id: u64,
    },
    Equal {
        equal: bool,
    },
    Prepared {
        changed: bool,
        ticket: Option<u64>,
    },
    Version {
        generation: u64,
        state_id: u64,
    },
}
// Serialize shared rows without copying their contents or rebuilding a table.
pub(crate) struct RowsReply(Arc<State>);
impl Serialize for RowsReply {
    fn serialize<S: Serializer>(&self, s: S) -> std::result::Result<S::Ok, S::Error> {
        self.0.table.rows.serialize(s)
    }
}

struct Lease {
    budget: Arc<AtomicUsize>,
    charge: usize,
}
impl Drop for Lease {
    fn drop(&mut self) {
        self.budget.fetch_sub(self.charge, Ordering::Relaxed);
    }
}
struct State {
    id: u64,
    table: Table,
    _lease: Lease,
}
struct Session {
    generation: u64,
    current: Arc<State>,
}
struct Snapshot {
    session_id: u64,
    state: Arc<State>,
}
struct Ticket {
    session_id: u64,
    generation: u64,
    state: Arc<State>,
}

#[derive(Default)]
pub(crate) struct Registry {
    last_id: u64,
    sessions: HashMap<u64, Session>,
    snapshots: HashMap<u64, Snapshot>,
    tickets: HashMap<u64, Ticket>,
    budget: Arc<AtomicUsize>,
}
impl Registry {
    // One ID namespace for sessions, snapshots, tickets AND immutable states.
    // Reserve every needed ID together so overflow cannot partially publish.
    fn ids(&mut self, count: u64) -> Result<u64> {
        let end = self.last_id.checked_add(count).ok_or("id_overflow")?;
        let first = self.last_id + 1;
        self.last_id = end;
        Ok(first)
    }
    fn lease(&self, bytes: usize, rows: usize) -> Result<Lease> {
        let charge = 2 * bytes + 192 * rows + 128;
        self.budget
            .fetch_update(Ordering::Relaxed, Ordering::Relaxed, |used| {
                used.checked_add(charge)
                    .filter(|total| *total <= MAX_RETAINED)
            })
            .map_err(|_| "retained_state_limit")?;
        Ok(Lease {
            budget: self.budget.clone(),
            charge,
        })
    }
    fn session(&self, id: u64) -> Result<&Session> {
        self.sessions.get(&id).ok_or("invalid_session")
    }
    fn snapshot(&self, session_id: u64, id: u64) -> Result<&Snapshot> {
        let snap = self.snapshots.get(&id).ok_or("invalid_snapshot")?;
        if snap.session_id != session_id {
            return Err("snapshot_owner_mismatch");
        }
        Ok(snap)
    }
    fn ticket(&self, session_id: u64, id: u64) -> Result<&Ticket> {
        let ticket = self.tickets.get(&id).ok_or("invalid_ticket")?;
        if ticket.session_id != session_id {
            return Err("ticket_owner_mismatch");
        }
        Ok(ticket)
    }
    pub fn execute(&mut self, request: Request) -> Result<Reply> {
        match request {
            Request::Create { rows } => {
                if self.sessions.len() >= MAX_SESSIONS {
                    return Err("session_limit");
                }
                let table = Table::replay(rows);
                let lease = self.lease(table.bytes, table.rows.len())?;
                let session = self.ids(2)?;
                let state_id = session + 1;
                let count = table.rows.len();
                self.sessions.insert(
                    session,
                    Session {
                        generation: 0,
                        current: Arc::new(State {
                            id: state_id,
                            table,
                            _lease: lease,
                        }),
                    },
                );
                Ok(Reply::Created {
                    session,
                    generation: 0,
                    state_id,
                    count,
                })
            }
            Request::Close { session } => {
                self.session(session)?;
                self.tickets.retain(|_, t| t.session_id != session);
                self.snapshots.retain(|_, s| s.session_id != session);
                self.sessions.remove(&session);
                Ok(Reply::Empty {})
            }
            Request::Info { session } => {
                let s = self.session(session)?;
                Ok(Reply::Info {
                    generation: s.generation,
                    state_id: s.current.id,
                    count: s.current.table.rows.len(),
                })
            }
            Request::DocumentUserTextCount { session } => Ok(Reply::Count {
                count: self
                    .session(session)?
                    .current
                    .table
                    .document_user_text_count(),
            }),
            Request::Rows { session } => Ok(Reply::Rows {
                rows: RowsReply(self.session(session)?.current.clone()),
            }),
            Request::Get {
                session,
                key: Text(key),
                exact,
            } => {
                let row = self.session(session)?.current.table.find(&key, exact);
                Ok(Reply::Get {
                    found: row.is_some(),
                    key: row.map(|r| r.0.clone()),
                    value: row.map(|r| r.1.clone()),
                })
            }
            Request::At { session, index } => {
                let row = usize::try_from(index)
                    .ok()
                    .and_then(|i| self.sessions.get(&session)?.current.table.rows.get(i))
                    .cloned();
                self.session(session)?;
                Ok(Reply::At {
                    found: row.is_some(),
                    row,
                })
            }
            Request::Snapshot { session } => {
                let state = self.session(session)?.current.clone();
                if self.snapshots.len() >= MAX_SNAPSHOTS {
                    return Err("snapshot_limit");
                }
                let snapshot = self.ids(1)?;
                let state_id = state.id;
                self.snapshots.insert(
                    snapshot,
                    Snapshot {
                        session_id: session,
                        state,
                    },
                );
                Ok(Reply::Snapshot { snapshot, state_id })
            }
            Request::ReleaseSnapshot { session, snapshot } => {
                self.session(session)?;
                self.snapshot(session, snapshot)?;
                self.snapshots.remove(&snapshot);
                Ok(Reply::Empty {})
            }
            Request::SnapshotRows { session, snapshot } => {
                self.session(session)?;
                Ok(Reply::Rows {
                    rows: RowsReply(self.snapshot(session, snapshot)?.state.clone()),
                })
            }
            Request::Equal {
                session,
                left,
                right,
            } => {
                self.session(session)?;
                let a = &self.snapshot(session, left)?.state;
                let b = &self.snapshot(session, right)?.state;
                Ok(Reply::Equal {
                    equal: Arc::ptr_eq(a, b) || a.table.rows == b.table.rows,
                })
            }
            Request::Prepare {
                session,
                generation,
                key: Text(key),
                value,
            } => {
                let s = self.session(session)?;
                if s.generation != generation {
                    return Err("generation_conflict");
                }
                let Some(change) = s
                    .current
                    .table
                    .change(&key, value.as_ref().map(|v| v.0.as_str()))?
                else {
                    return Ok(Reply::Prepared {
                        changed: false,
                        ticket: None,
                    });
                };
                if self.tickets.len() >= MAX_TICKETS {
                    return Err("ticket_limit");
                }
                let lease = self.lease(change.bytes, change.count)?;
                let original = s.current.clone();
                let ticket = self.ids(2)?;
                let state = Arc::new(State {
                    id: ticket + 1,
                    table: original.table.changed(change),
                    _lease: lease,
                });
                self.tickets.insert(
                    ticket,
                    Ticket {
                        session_id: session,
                        generation,
                        state,
                    },
                );
                Ok(Reply::Prepared {
                    changed: true,
                    ticket: Some(ticket),
                })
            }
            Request::Commit { session, ticket } => {
                let s = self.session(session)?;
                let staged = self.ticket(session, ticket)?;
                if staged.generation != s.generation {
                    self.tickets.remove(&ticket);
                    return Err("generation_conflict");
                }
                let generation = s.generation.checked_add(1).ok_or("generation_overflow")?;
                let staged = self.tickets.remove(&ticket).expect("validated ticket");
                let s = self.sessions.get_mut(&session).expect("validated session");
                s.current = staged.state;
                s.generation = generation;
                Ok(Reply::Version {
                    generation,
                    state_id: s.current.id,
                })
            }
            Request::Abort { session, ticket } => {
                self.session(session)?;
                self.ticket(session, ticket)?;
                self.tickets.remove(&ticket);
                Ok(Reply::Empty {})
            }
            Request::Restore { session, snapshot } => {
                let generation = self
                    .session(session)?
                    .generation
                    .checked_add(1)
                    .ok_or("generation_overflow")?;
                let state = self.snapshot(session, snapshot)?.state.clone();
                let state_id = state.id;
                let s = self.sessions.get_mut(&session).expect("validated session");
                s.current = state;
                s.generation = generation;
                Ok(Reply::Version {
                    generation,
                    state_id,
                })
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn request(r: &mut Registry, text: &str) -> Reply {
        r.execute(serde_json::from_str(text).unwrap()).unwrap()
    }
    #[test]
    fn snapshots_are_constant_time_arc_ownership() {
        let mut r = Registry::default();
        request(&mut r, r#"{"op":"create","rows":[["K","v"]]}"#);
        let original = r.sessions[&1].current.clone();
        request(&mut r, r#"{"op":"snapshot","session":1}"#);
        assert!(Arc::ptr_eq(&original, &r.snapshots[&3].state));
        let used = r.budget.load(Ordering::Relaxed);
        request(
            &mut r,
            r#"{"op":"prepare","session":1,"generation":0,"key":"k","value":"v"}"#,
        );
        assert_eq!(used, r.budget.load(Ordering::Relaxed));
        request(&mut r, r#"{"op":"restore","session":1,"snapshot":3}"#);
        assert!(Arc::ptr_eq(&original, &r.sessions[&1].current));
        assert_eq!(r.sessions[&1].generation, 1);
        request(
            &mut r,
            r#"{"op":"prepare","session":1,"generation":1,"key":"k","value":"new"}"#,
        );
        assert!(!Arc::ptr_eq(&original, &r.tickets[&4].state));
        assert!(Arc::ptr_eq(
            &original.table.rows[0].0,
            &r.tickets[&4].state.table.rows[0].0
        ));
        request(&mut r, r#"{"op":"commit","session":1,"ticket":4}"#);
        assert!(!Arc::ptr_eq(&original, &r.sessions[&1].current));
        request(&mut r, r#"{"op":"restore","session":1,"snapshot":3}"#);
        assert!(Arc::ptr_eq(&original, &r.sessions[&1].current));
        assert_eq!(r.sessions[&1].generation, 3);
        request(&mut r, r#"{"op":"close","session":1}"#);
        assert!(r.snapshots.is_empty() && r.tickets.is_empty());
        drop(original);
        assert_eq!(r.budget.load(Ordering::Relaxed), 0);
    }
    #[test]
    fn global_handle_limits_and_close_release_admission() {
        let mut r = Registry::default();
        for _ in 0..MAX_SESSIONS {
            r.execute(Request::Create { rows: vec![] }).unwrap();
        }
        assert!(matches!(
            r.execute(Request::Create { rows: vec![] }),
            Err("session_limit")
        ));
        for _ in 0..MAX_SNAPSHOTS {
            r.execute(Request::Snapshot { session: 1 }).unwrap();
        }
        assert!(matches!(
            r.execute(Request::Snapshot { session: 1 }),
            Err("snapshot_limit")
        ));
        for _ in 0..MAX_TICKETS {
            r.execute(Request::Prepare {
                session: 1,
                generation: 0,
                key: Text("k".into()),
                value: Some(Text("v".into())),
            })
            .unwrap();
        }
        assert!(matches!(
            r.execute(Request::Prepare {
                session: 1,
                generation: 0,
                key: Text("k".into()),
                value: Some(Text("v".into()))
            }),
            Err("ticket_limit")
        ));
        let last = r.last_id;
        r.execute(Request::Close { session: 1 }).unwrap();
        assert!(r.snapshots.is_empty() && r.tickets.is_empty());
        let Reply::Created { session, .. } = r.execute(Request::Create { rows: vec![] }).unwrap()
        else {
            panic!("create reply");
        };
        assert!(session > last);
        assert_eq!(r.budget.load(Ordering::Relaxed), MAX_SESSIONS * 128);
    }
    #[test]
    fn overflow_and_admission_are_transactional() {
        let mut r = Registry {
            last_id: u64::MAX - 1,
            ..Registry::default()
        };
        let create = || serde_json::from_str(r#"{"op":"create","rows":[]}"#).unwrap();
        assert!(matches!(r.execute(create()), Err("id_overflow")));
        assert_eq!(r.last_id, u64::MAX - 1);
        assert!(r.sessions.is_empty());
        assert_eq!(r.budget.load(Ordering::Relaxed), 0);
        r.last_id = 0;
        r.execute(create()).unwrap();
        request(&mut r, r#"{"op":"snapshot","session":1}"#);
        request(
            &mut r,
            r#"{"op":"prepare","session":1,"generation":0,"key":"k","value":"v"}"#,
        );
        r.sessions.get_mut(&1).unwrap().generation = u64::MAX;
        r.tickets.get_mut(&4).unwrap().generation = u64::MAX;
        assert!(matches!(
            r.execute(Request::Commit {
                session: 1,
                ticket: 4
            }),
            Err("generation_overflow")
        ));
        assert!(r.tickets.contains_key(&4));
        assert!(matches!(
            r.execute(Request::Restore {
                session: 1,
                snapshot: 3
            }),
            Err("generation_overflow")
        ));
        assert!(r.sessions[&1].current.table.rows.is_empty());
        let remaining = MAX_RETAINED - r.budget.load(Ordering::Relaxed);
        r.budget.fetch_add(remaining, Ordering::Relaxed);
        assert!(matches!(r.lease(1, 1), Err("retained_state_limit")));
        r.budget.fetch_sub(remaining, Ordering::Relaxed);
    }
}
