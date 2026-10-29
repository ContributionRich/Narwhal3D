//! Bounded stdio framing around rmcp, not a replacement MCP dispatcher.
use crate::policy::{Era, Gate, MAX_ACTIVE, MAX_REQUESTS};
use futures::StreamExt;
pub use narwhal_automation::json::strict_json;
use rmcp::{
    RoleServer,
    model::*,
    service::{RxJsonRpcMessage, TxJsonRpcMessage},
    transport::{
        Transport,
        io::{StdioReader, StdioWriter, stdio_pipes},
    },
};
use serde_json::Value;
use std::{
    io,
    sync::{Arc, Mutex as SyncMutex},
    time::Duration,
};
use tokio::{
    io::AsyncWriteExt,
    sync::{OwnedSemaphorePermit, Semaphore, mpsc, oneshot},
};
use tokio_util::{
    codec::{FramedRead, LinesCodec},
    sync::CancellationToken,
};

const MAX_INPUT: usize = 65_535;
const MAX_OUTPUT: usize = 196_608;
const MAX_ERRORS: usize = 8;

pub struct BoundedTransport {
    input: mpsc::Receiver<RxJsonRpcMessage<RoleServer>>,
    output: mpsc::Sender<Pending>,
    shutdown: CancellationToken,
    first_frame: CancellationToken,
}

struct Pending {
    value: Value,
    admitted: bool,
    done: oneshot::Sender<io::Result<()>>,
    _error_slot: Option<OwnedSemaphorePermit>,
}

struct Reader {
    input: FramedRead<StdioReader, LinesCodec>,
    output: mpsc::Sender<Pending>,
    gate: Arc<SyncMutex<Gate>>,
    frames: usize,
    first_frame: CancellationToken,
    error_slots: Arc<Semaphore>,
}

async fn enqueue(output: mpsc::Sender<Pending>, value: Value, admitted: bool) -> io::Result<()> {
    let (done, wait) = oneshot::channel();
    output
        .send(Pending {
            value,
            admitted,
            done,
            _error_slot: None,
        })
        .await
        .map_err(|_| io::Error::other("session_closed"))?;
    wait.await.map_err(|_| io::Error::other("session_closed"))?
}

async fn write(output: &mut StdioWriter, value: &Value) -> io::Result<()> {
    let mut bytes = serde_json::to_vec(value)?;
    bytes.push(b'\n');
    if bytes.len() > MAX_OUTPUT {
        return Err(io::Error::other("output_limit"));
    }
    tokio::time::timeout(Duration::from_secs(5), async {
        output.write_all(&bytes).await?;
        output.flush().await
    })
    .await
    .map_err(|_| io::Error::other("output_timeout"))?
}

impl BoundedTransport {
    pub fn new() -> Self {
        let (input, mut stdout) = stdio_pipes();
        let (output, mut writes) = mpsc::channel::<Pending>(MAX_ACTIVE + MAX_ERRORS);
        let (incoming, messages) = mpsc::channel(1);
        let gate = Arc::new(SyncMutex::new(Gate::default()));
        let shutdown = CancellationToken::new();
        let first_frame = CancellationToken::new();
        let writer_gate = gate.clone();
        let writer_shutdown = shutdown.clone();
        tokio::spawn(async move {
            loop {
                let pending = tokio::select! {
                    biased;
                    _ = writer_shutdown.cancelled() => break,
                    pending = writes.recv() => match pending { Some(p) => p, None => break },
                };
                let cancel = if pending.admitted {
                    let gate = writer_gate.lock().unwrap();
                    pending
                        .value
                        .get("id")
                        .and_then(|id| gate.active.get(&id.to_string()))
                        .map(|active| active.cancelled.clone())
                } else {
                    None
                };
                if cancel.as_ref().is_some_and(|ct| ct.is_cancelled()) {
                    // The SDK may already have queued this response before it saw
                    // cancellation. Suppress it, retaining the quarantined ID.
                    let _ = pending.done.send(Ok(()));
                    continue;
                }
                let initializes = pending.value["result"].get("protocolVersion").is_some();
                if initializes {
                    writer_gate.lock().unwrap().initialized = true;
                }
                let result = if let Some(cancel) = cancel {
                    tokio::select! {
                        biased;
                        _ = cancel.cancelled() => Err(io::Error::other("cancelled_output")),
                        result = write(&mut stdout, &pending.value) => result,
                    }
                } else {
                    write(&mut stdout, &pending.value).await
                };
                if result.is_ok() && pending.admitted {
                    if let Some(id) = pending.value.get("id") {
                        writer_gate.lock().unwrap().active.remove(&id.to_string());
                    }
                }
                let failed = result.is_err();
                let _ = pending.done.send(result);
                if failed {
                    break;
                }
            }
            // No further frames may follow a partial/failed stdout write.
            writer_shutdown.cancel();
        });
        let reader_shutdown = shutdown.clone();
        let mut reader = Reader {
            input: FramedRead::new(input, LinesCodec::new_with_max_length(MAX_INPUT)),
            output: output.clone(),
            gate,
            frames: 0,
            first_frame: first_frame.clone(),
            error_slots: Arc::new(Semaphore::new(MAX_ERRORS)),
        };
        tokio::spawn(async move {
            tokio::select! {
                _ = reader_shutdown.cancelled() => {},
                _ = async {
                    while let Some(message) = reader.next().await {
                        if incoming.send(message).await.is_err() { break; }
                    }
                } => {},
            }
            // EOF must interrupt application work, not await rmcp's drain timeout.
            reader_shutdown.cancel();
        });
        Self {
            input: messages,
            output,
            shutdown,
            first_frame,
        }
    }

    pub fn shutdown_token(&self) -> CancellationToken {
        self.shutdown.clone()
    }

    pub fn first_frame_token(&self) -> CancellationToken {
        self.first_frame.clone()
    }
}

impl Reader {
    async fn error(
        &self,
        id: Option<RequestId>,
        code: i32,
        message: &'static str,
    ) -> io::Result<()> {
        let era = if self.gate.lock().unwrap().initializing {
            Era::Legacy
        } else {
            Era::Modern
        };
        self.send_error(id, ErrorData::new(ErrorCode(code), message, None), era)
            .await
    }

    async fn send_error(
        &self,
        id: Option<RequestId>,
        error: ErrorData,
        era: Era,
    ) -> io::Result<()> {
        let mut value = serde_json::to_value(JsonRpcError::new(id, error))?;
        if era == Era::Legacy {
            value
                .as_object_mut()
                .unwrap()
                .entry("id")
                .or_insert(Value::Null);
        }
        // Recoverable input errors must not make the reader await blocked stdout:
        // cancellation and EOF are on that same input stream. Reserve error slots
        // separately from SDK response admission and retain the frame in the one
        // writer queue. Exhaustion is an explicit terminal overload, never a drop
        // followed by continued service or an unbounded spawned send task.
        let slot = self
            .error_slots
            .clone()
            .try_acquire_owned()
            .map_err(|_| io::Error::other("error_queue_limit"))?;
        let (done, wait) = oneshot::channel();
        self.output
            .try_send(Pending {
                value,
                admitted: false,
                done,
                _error_slot: Some(slot),
            })
            .map_err(|_| io::Error::other("error_queue_limit"))?;
        drop(wait);
        Ok(())
    }

    async fn fatal_error(&self, code: i32, message: &'static str) {
        let mut value = serde_json::to_value(JsonRpcError::new(
            None,
            ErrorData::new(ErrorCode(code), message, None),
        ))
        .unwrap();
        if self.gate.lock().unwrap().initializing {
            value
                .as_object_mut()
                .unwrap()
                .insert("id".into(), Value::Null);
        }
        // Already terminal: make a bounded best-effort final error before closing.
        let _ = enqueue(self.output.clone(), value, false).await;
    }
}

impl Transport<RoleServer> for BoundedTransport {
    type Error = io::Error;
    fn send(
        &mut self,
        item: TxJsonRpcMessage<RoleServer>,
    ) -> impl Future<Output = io::Result<()>> + Send + 'static {
        let output = self.output.clone();
        async move {
            let value = serde_json::to_value(item)?;
            enqueue(output, value, true).await
        }
    }
    async fn receive(&mut self) -> Option<RxJsonRpcMessage<RoleServer>> {
        self.input.recv().await
    }
    async fn close(&mut self) -> io::Result<()> {
        self.shutdown.cancel();
        Ok(())
    }
}

impl Reader {
    // Persistent reader task owns consumed-frame/error state. rmcp may cancel a
    // Transport::receive future, but cannot lose a reply midway through this loop.
    async fn next(&mut self) -> Option<RxJsonRpcMessage<RoleServer>> {
        loop {
            let line = match self.input.next().await? {
                Ok(line) => line,
                Err(_) => {
                    self.fatal_error(-32700, "Invalid UTF-8 or oversized input frame")
                        .await;
                    return None;
                }
            };
            self.first_frame.cancel();
            self.frames += 1;
            if self.frames > MAX_REQUESTS * 2 {
                self.fatal_error(-31999, "Transport frame limit reached")
                    .await;
                return None;
            }
            let value = match strict_json(&line) {
                Ok(value) => value,
                Err(_) => {
                    if self
                        .error(None, -32700, "Malformed or ambiguous JSON")
                        .await
                        .is_err()
                    {
                        return None;
                    }
                    continue;
                }
            };
            let (era, result) = {
                let mut gate = self.gate.lock().unwrap();
                (gate.era(&value), gate.check(&value))
            };
            if let Err(error) = result {
                // Notifications never get error responses; invalid non-objects do.
                if value.get("id").is_some() || !value.is_object() || !value["method"].is_string() {
                    let id = value
                        .get("id")
                        .and_then(|v| serde_json::from_value(v.clone()).ok());
                    if self.send_error(id, error, era).await.is_err() {
                        return None;
                    }
                }
                continue;
            }
            if value["method"] == "notifications/cancelled"
                && !self.gate.lock().unwrap().sdk_running()
            {
                // rmcp's discovery-only bootstrap rejects notifications instead
                // of handling cancellation. The gate already cancelled its writer
                // token; no handler is running until the first operational request.
                continue;
            }
            match serde_json::from_value(value.clone()) {
                Ok(message) => return Some(message),
                Err(_) => {
                    if let Some(id) = value
                        .get("id")
                        .and_then(|v| serde_json::from_value(v.clone()).ok())
                    {
                        if self
                            .error(Some(id), -32602, "Invalid method parameters")
                            .await
                            .is_err()
                        {
                            return None;
                        }
                    }
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn rejects_ambiguous_json() {
        assert!(strict_json(r#"{"id":1,"id":2}"#).is_err());
        assert!(strict_json(r#"{"arguments":{"object_id":"a","object_id":"b"}}"#).is_err());
        assert!(strict_json(r#"{"a":[1,2,true,null]}"#).is_ok());
        assert!(strict_json("[NaN]").is_err());
    }
}
