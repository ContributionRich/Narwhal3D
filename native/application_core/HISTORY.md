# Persistent document history — implementation contract

This adds a history service to application-core without changing metadata ABI1.
Python retains opaque geometry payloads. Rust owns timeline/order/grouping, record
metadata, payload reference accounting and navigation transactions. No PyObject
pointers or serialized geometry cross this ABI. It is not a full document revision.

## ABI

New exports `narwhal_history_abi() -> uint32_t` (=1),
`narwhal_history_json(input,length,output,capacity) -> ptrdiff_t`.
Same descriptor/4096-byte mutation preflight as metadata ABI. Requests max1MiB,
output max512MiB. Mutations always return compact results; only read replies may
report required capacity for retry. Panic containment/poison fails closed.
Existing metadata exports/behavior unchanged.

JSON errors: `{status:"error",code:"fixed_code"}`. Successes:
`{status:"ok",result:<below>,info:<header>}`. Close has null info/result.
Every request includes `op`; all except create include `history` (handle).
Strict schemas, unknown/duplicate fields refused. No implicit library fallback.

Header (no strings, fits4096): generation,state_id,current_serial,top_serial,
next_serial,depth,enabled,uncertain,undo_count,redo_count,open_record,undo_record,
redo_record,captured,gc_pending,record_count,payload_count. Absent records are null.
`gc_pending` is a retired-payload count, not a boolean.
State identity is separate from record serial/generation. Initial state is unique;
no-op/abandon/ordinary clear preserve it; end-of-real-record gets a new state ID.
Undo/redo restores the appropriate before/after state ID even after pruning.
Generations advance on logical mutations, not refcount-only reads/retains.

## Operations

|op|Additional fields|result|
|---|---|---|
|create|limit:u64 (1..100000)|{history:u64}|
|close|none|null|
|info|none|null|
|register_payload|none|{payload:u64}|
|drop_payload|payload:u64|null; only an unused registered payload|
|begin|name:string <=16384 UTF8 bytes|{serial:u64}; disabled→0, nested returns current serial|
|capture|payload:u64 or null|{captured:bool}; first capture wins, null remains uncaptured|
|end|none|{committed:bool}; nested only decreases depth; real outer commit prunes and clears redo|
|abandon|none|null; drops open record without navigation|
|enable|value:bool|null; disabled begin returns0 even while another record is open|
|records|side:"undo" or "redo"|{records:[record IDs in oldest→newest order]} (read)|
|names|side:"undo" or "redo"|{names:[strings most-recent-first]} (read; standard info; stack names only, bounded by existing16MiB total name storage; pure sizing/retry)|
|record|record:u64|{serial,name,before:payload|null,after:payload|null} (read)|
|retain_record|record:u64|null|
|release_record|record:u64|null|
|set_before|record:u64,payload:u64 or null|null|
|set_after|record:u64,payload:u64 or null|null|
|set_name|record:u64,name:string|null|
|prepare_nav|direction:"undo" or "redo",current:payload|null|null if open/empty, else {ticket,record,target:payload|null}|
|commit_nav|ticket:u64|{record:u64}|
|abort_nav|ticket:u64,unknown:bool|null|
|clear|none|null|
|garbage|limit:u64 (1..128)|{payloads:[unused retired payload IDs]} (read)|
|ack_garbage|payloads:[<=128 unique IDs]|null; validate all before removing any|

All history/record/payload/ticket/state IDs come from one monotonic non-reused history
namespace. Invalid/cross-history IDs are refused without consuming another history's
resource. Internal serials remain per-history starting1; empty records consume serials.
Bound histories/records/payloads/tickets/name storage explicitly; no silent eviction
except the configured oldest undo-record count limit. Resource checks precede mutation.

## Record/payload lifetime

The native stack owns each referenced record. Python Record views may additionally
retain/release it; a retained pruned record remains readable. Dropping the last
stack/lease reference deletes its payload references. Python caches Record wrappers
strongly while Rust says the timeline owns them, then weakly for escaped references.
The wrapper cache does not store ordering or duplicate writable record fields.
Record wrappers may keep their bounded history/payload arena alive until released.
Explicit history close invalidates all handles. Serial mutation is not supported.

Registered payloads begin unattached, not garbage. Capture/set/nav takes a ref.
Refcount reaching zero queues the payload for garbage acknowledgment. Reattachment
removes it from that queue. Python uses a single arena (ID→actual opaque object) and
identity index; payload values are never copied/serialized. On failed attach it
drops the unused registration. Cleanup batches are separate from mutating replies,
so clearing a large redo branch cannot require replaying the clear operation.

## Navigation

Prepare freezes navigation before the host applies a payload. While pending, block
begin/capture/end/abandon/enable/record setters/another navigation/clear. Reads and
safe lifetime release/garbage operations may proceed. Wrong tickets do not unfreeze.
Commit updates the record's opposite payload and stack position only once after
restoration. Abort leaves record fields, stacks and state ID unchanged. If host
reports unknown/partial restoration, set uncertain=true and refuse further edits/
navigation until explicit clear/new history. Clear from uncertain creates a fresh
state ID and clears uncertainty, keeping the document dirty relative to its prior
saved identity. Do not interpret an exception as verified geometry rollback.

The host adapter must report its failed model restoration as unchanged only when
it can establish that fact. Context classifies pure metadata restore-admission
failure as unchanged; an exception after model/display restoration starts is
conservatively unknown. Arbitrary standalone callbacks have no geometry certification.
Compatibility calls without a restore callback perform prepare+commit immediately,
as the existing caller is responsible for applying the returned Record payload.
