//! Bounded background-load coordination. Host payloads never cross this ABI.
//! Rust owns ordering, newest-request replacement, cancellation and commit guards.
use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeMap,
    sync::{Mutex, OnceLock},
};

#[derive(Clone, Debug, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
struct Guard {
    model: u64,
    context: u64,
    serial: u64,
    state: u64,
    generation: Option<u64>,
    path: Option<String>,
}
impl Guard {
    fn valid(&self) -> bool {
        self.model != 0 && self.context != 0 && self.path.as_ref().is_none_or(|p| p.len() <= 8192)
    }
}
#[derive(Deserialize)]
#[serde(tag = "op", rename_all = "snake_case", deny_unknown_fields)]
enum Request {
    Create,
    #[serde(rename = "request")]
    Enqueue {
        queue: u64,
        guard: Guard,
        strict: bool,
    },
    Tick {
        queue: u64,
        guard: Guard,
        paused: bool,
    },
    Complete {
        queue: u64,
        job: u64,
        success: bool,
    },
    Hydrated {
        queue: u64,
        job: u64,
        success: bool,
    },
    Cancel {
        queue: u64,
    },
    Close {
        queue: u64,
    },
}
#[derive(Default, Serialize, Debug)]
struct Reply {
    action: &'static str,
    queue: u64,
    job: Option<u64>,
    replaced: Option<u64>,
}
#[derive(Debug)]
struct Job {
    id: u64,
    guard: Guard,
    strict: bool,
    cancelled: bool,
    finished: Option<bool>,
    hydrating: bool,
    admitted: bool,
}
#[derive(Default)]
struct Queue {
    active: Option<Job>,
    pending: Option<Job>,
    closed: bool,
}
#[derive(Default)]
struct Registry {
    queues: BTreeMap<u64, Queue>,
    next_queue: u64,
    next_job: u64,
}
impl Registry {
    fn execute(&mut self, request: Request) -> Result<Reply, &'static str> {
        if matches!(request, Request::Create) {
            if self.queues.len() >= 64 {
                return Err("load_queue_limit");
            }
            let id = self.next_queue.checked_add(1).ok_or("load_id_exhausted")?;
            self.next_queue = id;
            self.queues.insert(id, Queue::default());
            return Ok(Reply {
                action: "created",
                queue: id,
                ..Reply::default()
            });
        }
        let id = match &request {
            Request::Create => unreachable!(),
            Request::Enqueue { queue, .. }
            | Request::Tick { queue, .. }
            | Request::Complete { queue, .. }
            | Request::Hydrated { queue, .. }
            | Request::Cancel { queue }
            | Request::Close { queue } => *queue,
        };
        let queue = self.queues.get_mut(&id).ok_or("invalid_load_queue")?;
        let mut reply = Reply {
            queue: id,
            ..Reply::default()
        };
        match request {
            Request::Create => unreachable!(),
            Request::Enqueue { guard, strict, .. } => {
                if queue.closed {
                    return Err("load_queue_closed");
                }
                if !guard.valid() {
                    return Err("invalid_load_guard");
                }
                let job = self.next_job.checked_add(1).ok_or("load_id_exhausted")?;
                self.next_job = job;
                if let Some(active) = &mut queue.active {
                    active.cancelled = true;
                }
                reply.replaced = queue
                    .pending
                    .replace(Job {
                        id: job,
                        guard,
                        strict,
                        cancelled: false,
                        finished: None,
                        hydrating: false,
                        admitted: false,
                    })
                    .map(|j| j.id);
                reply.job = Some(job);
                reply.action = "queued";
            }
            Request::Tick { guard, paused, .. } => {
                if !guard.valid() {
                    return Err("invalid_load_guard");
                }
                if let Some(active) = &mut queue.active {
                    if active.guard.model != guard.model
                        || active.guard.context != guard.context
                        || (active.strict && active.guard != guard)
                    {
                        active.cancelled = true;
                    }
                    reply.job = Some(active.id);
                    if let Some(success) = active.finished {
                        if active.cancelled || queue.closed {
                            reply.action = "cancel";
                        } else if !success {
                            reply.action = "error";
                        } else if paused {
                            reply.action = "wait";
                            return Ok(reply);
                        } else if !active.admitted {
                            active.hydrating = true;
                            reply.action = "hydrate";
                            return Ok(reply);
                        } else {
                            reply.action = "commit";
                        }
                        queue.active = None;
                    } else {
                        reply.action = "wait";
                    }
                } else if let Some(mut job) = queue.pending.take() {
                    reply.job = Some(job.id);
                    if queue.closed
                        || job.guard.model != guard.model
                        || job.guard.context != guard.context
                        || (job.strict && job.guard != guard)
                    {
                        reply.action = "cancel";
                    } else {
                        job.finished = None;
                        queue.active = Some(job);
                        reply.action = "start";
                    }
                } else {
                    reply.action = "idle";
                }
            }
            Request::Complete { job, success, .. } => {
                let active = queue.active.as_mut().ok_or("invalid_load_completion")?;
                if active.id != job || active.finished.is_some() {
                    return Err("invalid_load_completion");
                }
                active.finished = Some(success);
                reply.job = Some(job);
                if queue.closed {
                    self.queues.remove(&id);
                    reply.action = "discard";
                } else {
                    reply.action = "completed";
                }
            }
            Request::Hydrated { job, success, .. } => {
                let active = queue.active.as_mut().ok_or("invalid_load_hydration")?;
                if active.id != job
                    || active.finished != Some(true)
                    || !active.hydrating
                    || active.admitted
                {
                    return Err("invalid_load_hydration");
                }
                active.admitted = success;
                active.finished = Some(success);
                reply.job = Some(job);
                reply.action = "hydrated";
            }
            Request::Cancel { .. } | Request::Close { .. } => {
                let close = matches!(request, Request::Close { .. });
                reply.replaced = queue.pending.take().map(|j| j.id);
                if let Some(active) = &mut queue.active {
                    active.cancelled = true;
                }
                reply.action = "cancelled";
                if close {
                    queue.closed = true;
                    if queue.active.as_ref().is_none_or(|j| j.finished.is_some()) {
                        reply.job = queue.active.as_ref().map(|j| j.id);
                        self.queues.remove(&id);
                    }
                }
            }
        }
        Ok(reply)
    }
}
static REGISTRY: OnceLock<Mutex<Registry>> = OnceLock::new();
#[unsafe(no_mangle)]
pub extern "C" fn narwhal_load_jobs_abi() -> u32 {
    1
}

fn dispatch(input: &[u8], registry: &Mutex<Registry>) -> Vec<u8> {
    let text = match std::str::from_utf8(input) {
        Ok(t) => t,
        Err(_) => return super::error("invalid_utf8"),
    };
    if let Err(code) = crate::wire::preflight(text) {
        return super::error(code);
    }
    let request = match serde_json::from_str::<Request>(text) {
        Ok(r) => r,
        Err(_) => return super::error("invalid_load_request"),
    };
    let mut output = Vec::with_capacity(4096);
    let reply = match registry.lock() {
        Ok(mut registry) => registry.execute(request),
        Err(_) => return super::error("registry_poisoned"),
    };
    match reply {
        Ok(reply) => {
            #[derive(Serialize)]
            struct Success {
                status: &'static str,
                #[serde(flatten)]
                reply: Reply,
            }
            serde_json::to_writer(
                &mut output,
                &Success {
                    status: "ok",
                    reply,
                },
            )
            .expect("fixed load reply serializes");
            output
        }
        Err(code) => super::error(code),
    }
}
/// Submit a bounded load-state request. A small output never executes a mutation.
/// # Safety
/// Input and output buffers must be readable/writable for their declared lengths.
/// No host pointer or payload is retained; guards contain opaque integer identities.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn narwhal_load_jobs_json(
    input: *const u8,
    length: usize,
    output: *mut u8,
    capacity: usize,
) -> isize {
    if output.is_null() || capacity > 65536 {
        return -1;
    }
    let bytes = super::contain(|| {
        if capacity < 4096 {
            return super::error("output_too_small");
        }
        if input.is_null() || length > 65536 {
            return super::error("invalid_load_input");
        }
        // SAFETY: bounded caller-owned descriptors, as documented above.
        dispatch(
            unsafe { std::slice::from_raw_parts(input, length) },
            REGISTRY.get_or_init(|| Mutex::new(Registry::default())),
        )
    });
    if bytes.len() <= capacity {
        // SAFETY: bytes are owned and output capacity was checked.
        unsafe {
            std::ptr::copy_nonoverlapping(bytes.as_ptr(), output, bytes.len());
        }
    }
    bytes.len() as isize
}

#[cfg(test)]
mod tests {
    use super::*;
    fn guard() -> Guard {
        Guard {
            model: 1,
            context: 2,
            serial: 3,
            state: 4,
            generation: None,
            path: None,
        }
    }
    fn create(r: &mut Registry) -> u64 {
        r.execute(Request::Create).unwrap().queue
    }
    fn request(r: &mut Registry, q: u64, strict: bool) -> u64 {
        r.execute(Request::Enqueue {
            queue: q,
            guard: guard(),
            strict,
        })
        .unwrap()
        .job
        .unwrap()
    }
    fn tick(r: &mut Registry, q: u64, g: Guard, paused: bool) -> Reply {
        r.execute(Request::Tick {
            queue: q,
            guard: g,
            paused,
        })
        .unwrap()
    }
    fn complete(r: &mut Registry, q: u64, j: u64, success: bool) {
        r.execute(Request::Complete {
            queue: q,
            job: j,
            success,
        })
        .unwrap();
    }
    #[test]
    fn lifecycle_and_pause() {
        let mut r = Registry::default();
        let q = create(&mut r);
        let j = request(&mut r, q, true);
        assert_eq!(tick(&mut r, q, guard(), false).action, "start");
        assert_eq!(tick(&mut r, q, guard(), false).action, "wait");
        complete(&mut r, q, j, true);
        assert_eq!(tick(&mut r, q, guard(), true).action, "wait");
        assert_eq!(tick(&mut r, q, guard(), false).action, "hydrate");
        r.execute(Request::Hydrated {
            queue: q,
            job: j,
            success: true,
        })
        .unwrap();
        assert_eq!(tick(&mut r, q, guard(), false).action, "commit");
        assert_eq!(tick(&mut r, q, guard(), false).action, "idle");
    }
    #[test]
    fn changed_document_never_commits() {
        for field in 0..6 {
            let mut r = Registry::default();
            let q = create(&mut r);
            let j = request(&mut r, q, true);
            tick(&mut r, q, guard(), false);
            complete(&mut r, q, j, true);
            let mut g = guard();
            match field {
                0 => g.model += 1,
                1 => g.context += 1,
                2 => g.serial += 1,
                3 => g.state += 1,
                4 => g.generation = Some(1),
                _ => g.path = Some("new.narwhal".into()),
            }
            assert_eq!(tick(&mut r, q, g, false).action, "cancel");
        }
    }
    #[test]
    fn insert_allows_edits_not_document_replacement() {
        let mut r = Registry::default();
        let q = create(&mut r);
        let j = request(&mut r, q, false);
        tick(&mut r, q, guard(), false);
        complete(&mut r, q, j, true);
        let mut g = guard();
        g.state += 1;
        assert_eq!(tick(&mut r, q, g.clone(), false).action, "hydrate");
        r.execute(Request::Hydrated {
            queue: q,
            job: j,
            success: true,
        })
        .unwrap();
        assert_eq!(tick(&mut r, q, g, false).action, "commit");
        let j = request(&mut r, q, false);
        tick(&mut r, q, guard(), false);
        complete(&mut r, q, j, true);
        let mut g = guard();
        g.model += 1;
        assert_eq!(tick(&mut r, q, g, false).action, "cancel");
    }
    #[test]
    fn edits_during_hydration_and_failed_hydration_never_commit() {
        for success in [true, false] {
            let mut r = Registry::default();
            let q = create(&mut r);
            let j = request(&mut r, q, true);
            tick(&mut r, q, guard(), false);
            complete(&mut r, q, j, true);
            assert!(
                r.execute(Request::Hydrated {
                    queue: q,
                    job: j,
                    success: true
                })
                .is_err()
            );
            assert_eq!(tick(&mut r, q, guard(), false).action, "hydrate");
            r.execute(Request::Hydrated {
                queue: q,
                job: j,
                success,
            })
            .unwrap();
            let mut g = guard();
            if success {
                g.state += 1;
            }
            assert_eq!(
                tick(&mut r, q, g, false).action,
                if success { "cancel" } else { "error" }
            );
        }
    }
    #[test]
    fn newest_pending_only_and_cancel_running() {
        let mut r = Registry::default();
        let q = create(&mut r);
        let a = request(&mut r, q, true);
        tick(&mut r, q, guard(), false);
        let b = request(&mut r, q, true);
        let reply = r
            .execute(Request::Enqueue {
                queue: q,
                guard: guard(),
                strict: true,
            })
            .unwrap();
        assert_eq!(reply.replaced, Some(b));
        let c = reply.job.unwrap();
        complete(&mut r, q, a, true);
        assert_eq!(tick(&mut r, q, guard(), false).action, "cancel");
        assert_eq!(tick(&mut r, q, guard(), false).job, Some(c));
        assert_eq!(r.queues[&q].pending.as_ref().map(|j| j.id), None);
    }
    #[test]
    fn failures_and_stale_completions() {
        let mut r = Registry::default();
        let q = create(&mut r);
        let j = request(&mut r, q, true);
        tick(&mut r, q, guard(), false);
        assert!(
            r.execute(Request::Complete {
                queue: q,
                job: j + 1,
                success: true
            })
            .is_err()
        );
        complete(&mut r, q, j, false);
        assert!(
            r.execute(Request::Complete {
                queue: q,
                job: j,
                success: true
            })
            .is_err()
        );
        assert_eq!(tick(&mut r, q, guard(), false).action, "error");
    }
    #[test]
    fn closed_active_discards_and_releases_registry() {
        let mut r = Registry::default();
        let q = create(&mut r);
        let j = request(&mut r, q, true);
        tick(&mut r, q, guard(), false);
        r.execute(Request::Close { queue: q }).unwrap();
        assert!(
            r.execute(Request::Enqueue {
                queue: q,
                guard: guard(),
                strict: true
            })
            .is_err()
        );
        assert_eq!(
            r.execute(Request::Complete {
                queue: q,
                job: j,
                success: true
            })
            .unwrap()
            .action,
            "discard"
        );
        assert!(!r.queues.contains_key(&q));
    }
    #[test]
    fn cancel_pending_and_registry_bounds() {
        let mut r = Registry::default();
        let q = create(&mut r);
        let j = request(&mut r, q, true);
        assert_eq!(
            r.execute(Request::Cancel { queue: q }).unwrap().replaced,
            Some(j)
        );
        assert_eq!(tick(&mut r, q, guard(), false).action, "idle");
        for _ in 1..64 {
            create(&mut r);
        }
        assert!(r.execute(Request::Create).is_err());
        r.execute(Request::Close { queue: q }).unwrap();
        assert!(r.execute(Request::Create).is_ok());
    }
}
