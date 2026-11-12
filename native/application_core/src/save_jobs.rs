//! Save/AutoSave scheduling only. Exact serialization/recovery remains host/kernel work.
//! An active save is never cancelled or replaced. Busy AutoSave declines; newest
//! queued user Save wins. A repair/retry retains the same active native job.
use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeMap,
    sync::{Mutex, OnceLock},
};

#[derive(Deserialize)]
#[serde(tag = "op", rename_all = "snake_case", deny_unknown_fields)]
enum Request {
    Create,
    Enqueue { queue: u64, autosave: bool },
    Next { queue: u64 },
    Finish { queue: u64, job: u64 },
    Info { queue: u64 },
    Intended { queue: u64 },
    Close { queue: u64 },
}
#[derive(Default, Serialize)]
struct Reply {
    action: &'static str,
    queue: u64,
    job: Option<u64>,
    replaced: Option<u64>,
}
#[derive(Clone, Copy)]
struct Job {
    id: u64,
    autosave: bool,
}
#[derive(Default)]
struct Queue {
    active: Option<Job>,
    pending: Option<Job>,
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
                return Err("save_queue_limit");
            }
            self.next_queue = self.next_queue.checked_add(1).ok_or("save_id_exhausted")?;
            self.queues.insert(self.next_queue, Queue::default());
            return Ok(Reply {
                queue: self.next_queue,
                action: "created",
                ..Reply::default()
            });
        }
        let id = match &request {
            Request::Create => unreachable!(),
            Request::Enqueue { queue, .. }
            | Request::Next { queue }
            | Request::Finish { queue, .. }
            | Request::Info { queue }
            | Request::Intended { queue }
            | Request::Close { queue } => *queue,
        };
        let queue = self.queues.get_mut(&id).ok_or("invalid_save_queue")?;
        let mut reply = Reply {
            queue: id,
            ..Reply::default()
        };
        match request {
            Request::Create => unreachable!(),
            Request::Enqueue { autosave, .. } => {
                let busy = queue.active.is_some() || queue.pending.is_some();
                if autosave && busy {
                    reply.action = "declined";
                    return Ok(reply);
                }
                self.next_job = self.next_job.checked_add(1).ok_or("save_id_exhausted")?;
                let job = Job {
                    id: self.next_job,
                    autosave,
                };
                reply.job = Some(job.id);
                if busy {
                    reply.replaced = queue.pending.replace(job).map(|j| j.id);
                    reply.action = "queued";
                } else {
                    queue.active = Some(job);
                    reply.action = "start";
                }
            }
            Request::Next { .. } => {
                if queue.active.is_some() {
                    reply.action = "wait";
                } else if let Some(job) = queue.pending.take() {
                    reply.action = "start";
                    reply.job = Some(job.id);
                    queue.active = Some(job);
                } else {
                    reply.action = "idle";
                }
            }
            Request::Finish { job, .. } => {
                if queue.active.map(|j| j.id) != Some(job) {
                    return Err("invalid_save_completion");
                }
                queue.active = None;
                reply.action = "finished";
                reply.job = Some(job);
            }
            Request::Info { .. } => {
                reply.action = if queue.active.is_some() || queue.pending.is_some() {
                    "busy"
                } else {
                    "idle"
                };
                reply.job = queue.active.map(|j| j.id);
                reply.replaced = queue.pending.map(|j| j.id);
            }
            Request::Intended { .. } => {
                reply.action = "intended";
                reply.job = queue
                    .pending
                    .or(queue.active)
                    .filter(|j| !j.autosave)
                    .map(|j| j.id);
            }
            Request::Close { .. } => {
                if queue.active.is_some() || queue.pending.is_some() {
                    return Err("save_queue_busy");
                }
                self.queues.remove(&id);
                reply.action = "closed";
            }
        }
        Ok(reply)
    }
}
static REGISTRY: OnceLock<Mutex<Registry>> = OnceLock::new();
#[unsafe(no_mangle)]
pub extern "C" fn narwhal_save_jobs_abi() -> u32 {
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
        Err(_) => return super::error("invalid_save_request"),
    };
    let mut output = Vec::with_capacity(4096);
    let reply = match registry.lock() {
        Ok(mut r) => r.execute(request),
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
            .expect("fixed save reply serializes");
            output
        }
        Err(code) => super::error(code),
    }
}
/// Same bounded descriptors/no-mutation-retry contract as load jobs ABI1.
/// # Safety
/// Input/output must be readable/writable for their declared lengths. No pointers retained.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn narwhal_save_jobs_json(
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
            return super::error("invalid_save_input");
        }
        // SAFETY: caller owns bounded buffers for this call.
        dispatch(
            unsafe { std::slice::from_raw_parts(input, length) },
            REGISTRY.get_or_init(|| Mutex::new(Registry::default())),
        )
    });
    if bytes.len() <= capacity {
        // SAFETY: output capacity checked, owned source bytes do not overlap retained data.
        unsafe {
            std::ptr::copy_nonoverlapping(bytes.as_ptr(), output, bytes.len());
        }
    }
    bytes.len() as isize
}

#[cfg(test)]
mod tests {
    use super::*;
    fn create(r: &mut Registry) -> u64 {
        r.execute(Request::Create).unwrap().queue
    }
    fn enqueue(r: &mut Registry, q: u64, auto: bool) -> Reply {
        r.execute(Request::Enqueue {
            queue: q,
            autosave: auto,
        })
        .unwrap()
    }
    #[test]
    fn user_save_never_replaces_active_and_latest_pending_wins() {
        let mut r = Registry::default();
        let q = create(&mut r);
        let a = enqueue(&mut r, q, false);
        assert_eq!(a.action, "start");
        let b = enqueue(&mut r, q, false);
        assert_eq!(b.action, "queued");
        let c = enqueue(&mut r, q, false);
        assert_eq!(c.replaced, b.job);
        assert_eq!(r.queues[&q].active.map(|j| j.id), a.job);
        r.execute(Request::Finish {
            queue: q,
            job: a.job.unwrap(),
        })
        .unwrap();
        assert_eq!(r.execute(Request::Next { queue: q }).unwrap().job, c.job);
    }
    #[test]
    fn autosave_never_queues_or_displaces_user_work() {
        let mut r = Registry::default();
        let q = create(&mut r);
        let a = enqueue(&mut r, q, true);
        assert_eq!(a.action, "start");
        let b = enqueue(&mut r, q, false);
        assert_eq!(b.action, "queued");
        assert_eq!(enqueue(&mut r, q, true).action, "declined");
        assert_eq!(r.queues[&q].pending.map(|j| j.id), b.job);
        assert_eq!(
            r.execute(Request::Intended { queue: q }).unwrap().job,
            b.job
        );
    }
    #[test]
    fn close_and_completion_cannot_discard_an_active_save() {
        let mut r = Registry::default();
        let q = create(&mut r);
        let a = enqueue(&mut r, q, false).job.unwrap();
        assert!(r.execute(Request::Close { queue: q }).is_err());
        assert!(
            r.execute(Request::Finish {
                queue: q,
                job: a + 1
            })
            .is_err()
        );
        assert_eq!(
            r.execute(Request::Next { queue: q }).unwrap().action,
            "wait"
        );
        r.execute(Request::Finish { queue: q, job: a }).unwrap();
        assert!(r.execute(Request::Finish { queue: q, job: a }).is_err());
        r.execute(Request::Close { queue: q }).unwrap();
        assert!(!r.queues.contains_key(&q));
    }
    #[test]
    fn autosave_has_no_user_target_and_read_queries_are_pure() {
        let mut r = Registry::default();
        let q = create(&mut r);
        enqueue(&mut r, q, true);
        for _ in 0..3 {
            assert!(
                r.execute(Request::Intended { queue: q })
                    .unwrap()
                    .job
                    .is_none()
            );
            assert_eq!(
                r.execute(Request::Info { queue: q }).unwrap().action,
                "busy"
            );
        }
        assert!(r.queues[&q].active.is_some());
        assert!(r.queues[&q].pending.is_none());
    }
}
