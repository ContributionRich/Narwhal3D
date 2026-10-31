use super::transcript::*;
use super::transcript_ffi::*;
use std::sync::Mutex;
static LOCK: Mutex<()> = Mutex::new(());
fn cp(text: &str) -> Vec<u32> {
    text.chars().map(u32::from).collect()
}
fn wire(lines: &[&[u32]]) -> Vec<u8> {
    encode_lines(lines).unwrap()
}
fn call(handle: u64, r: Request, input: &[u8]) -> (u32, Reply) {
    let mut out = Reply::default();
    let rc = unsafe {
        narwhal_transcript_apply(handle, &r, 40, input.as_ptr(), input.len(), &mut out, 56)
    };
    (rc, out)
}
fn create() -> Reply {
    let (rc, out) = call(0, Request::default(), &[]);
    assert_eq!(rc, 0);
    out
}
#[test]
fn transcript_policies_and_unicode() {
    let mut raw = Transcript::new(0, false, false);
    raw.append(vec![cp("a\r\n\r"), vec![], vec![0xd800, 0, 0x1f600]])
        .unwrap();
    assert_eq!(&*raw.lines[0], &cp("a\r\n\r"));
    let mut capped = Transcript::new(2, true, false);
    capped
        .append(vec![cp("old"), cp("a\r\n\r"), cp("b\nc\n\n")])
        .unwrap();
    assert_eq!(
        capped.lines.iter().map(|l| l.to_vec()).collect::<Vec<_>>(),
        vec![cp("a"), cp("b\nc")]
    );
    capped.append(vec![cp("ΑΣ"), cp("İẞ😀")]).unwrap();
    assert_eq!(capped.search(&cp("ας")).unwrap(), vec![0]);
    assert_eq!(capped.search(&cp("i\u{307}ß")).unwrap(), vec![1]);
    assert!(capped.search(&cp("ss")).unwrap().is_empty());
    assert_eq!(lower(&[0xd800, 65], false), vec![0xd800, 97]);
    assert_eq!(points(&[65, 0, 0, 0]), Ok(vec![65]));
    assert_eq!(points(&[65, 0, 0]), Err(1));
    capped.prune(1).unwrap();
    assert_eq!(capped.search(&cp("ß")).unwrap(), vec![0]);
}
#[test]
fn transcript_slices_and_limits() {
    let mut t = Transcript::new(0, false, true);
    t.append(vec![cp("0"), cp("1"), cp("2")]).unwrap();
    assert_eq!(
        t.range(2, -1, -1).unwrap(),
        vec![&cp("2")[..], &cp("1")[..], &cp("0")[..]]
    );
    assert!(t.range(0, 3, 0).is_err());
    t.limit = 1;
    for _ in 0..500 {
        t.append(vec![cp("new")]).unwrap();
    }
    assert_eq!(t.lines.len(), 1);
    let rev = t.revision;
    t.revision = u64::MAX;
    assert_eq!(t.prune(1), Err(4));
    assert_eq!(t.lines.len(), 1);
    t.revision = rev;
    t.prune(900).unwrap();
    assert_eq!((t.points, t.lines.capacity()), (0, 0));
}
#[test]
fn transcript_immutable_snapshots_and_owner_isolation() {
    let _guard = LOCK.lock().unwrap();
    let mut s = create();
    let r = Request {
        op: 1,
        revision: s.revision,
        ..Request::default()
    };
    let (rc, added) = call(s.handle, r, &wire(&[&cp("first"), &cp("second")]));
    assert_eq!(rc, 0);
    s = added;
    let (rc, snap) = call(
        s.handle,
        Request {
            op: 4,
            revision: s.revision,
            a: 0,
            b: 2,
            c: 1,
            flags: 0,
        },
        &[],
    );
    assert_eq!(rc, 0);
    let other = create();
    let mut needed = 0;
    let mut data = vec![0; snap.bytes as usize];
    unsafe {
        assert_eq!(
            narwhal_transcript_read(
                other.handle,
                snap.token,
                snap.revision,
                data.as_mut_ptr(),
                data.len(),
                &mut needed
            ),
            2
        );
        assert_eq!(
            narwhal_transcript_read(
                s.handle,
                snap.token,
                snap.revision,
                std::ptr::null_mut(),
                0,
                &mut needed
            ),
            3
        );
    }
    assert_eq!(needed, snap.bytes as usize);
    let (rc, cleared) = call(
        s.handle,
        Request {
            op: 3,
            revision: s.revision,
            ..Request::default()
        },
        &[],
    );
    assert_eq!((rc, cleared.len), (0, 0));
    unsafe {
        assert_eq!(
            narwhal_transcript_read(
                s.handle,
                snap.token,
                snap.revision,
                data.as_mut_ptr(),
                data.len(),
                &mut needed
            ),
            0
        );
    }
    assert_eq!(data, wire(&[&cp("first"), &cp("second")]));
    assert_eq!(narwhal_transcript_release(other.handle, snap.token), 2);
    assert_eq!(narwhal_transcript_release(s.handle, snap.token), 0);
    assert_eq!(narwhal_transcript_release(s.handle, snap.token), 2);
    assert_eq!(narwhal_transcript_close(s.handle), 0);
    assert_eq!(narwhal_transcript_close(s.handle), 2);
    assert_eq!(narwhal_transcript_close(other.handle), 0);
}
#[test]
fn transcript_rejects_before_mutation() {
    let _guard = LOCK.lock().unwrap();
    let s = create();
    let request = Request {
        op: 1,
        revision: s.revision,
        ..Request::default()
    };
    let data = wire(&[&cp("once")]);
    let mut out = Reply::default();
    unsafe {
        assert_eq!(
            narwhal_transcript_apply(
                s.handle,
                &request,
                40,
                data.as_ptr(),
                data.len(),
                &mut out,
                55
            ),
            1
        );
    }
    assert_eq!(
        call(
            s.handle,
            Request {
                revision: s.revision + 1,
                ..request
            },
            &data
        )
        .0,
        2
    );
    assert_eq!(
        call(s.handle, request, &[1, 0, 0, 0, 255, 255, 255, 255]).0,
        4
    );
    assert_eq!(call(s.handle, request, &wire(&[&[0x110000]])).0, 1);
    let (rc, good) = call(s.handle, request, &data);
    assert_eq!((rc, good.len, good.revision), (0, 1, 2));
    assert_eq!(call(s.handle, request, &data).0, 2); // stale mutation cannot append twice
    narwhal_transcript_close(s.handle);
}
#[test]
fn transcript_clone_and_last_command() {
    let _guard = LOCK.lock().unwrap();
    let s = create();
    let (_, s) = call(
        s.handle,
        Request {
            op: 1,
            revision: s.revision,
            ..Request::default()
        },
        &wire(&[&cp("> Line"), &cp("output"), &cp("> Box"), &cp("done")]),
    );
    let (rc, copy) = call(
        s.handle,
        Request {
            op: 6,
            revision: s.revision,
            ..Request::default()
        },
        &[],
    );
    assert_eq!(rc, 0);
    assert_ne!(copy.handle, s.handle);
    let (rc, last) = call(
        copy.handle,
        Request {
            op: 8,
            revision: copy.revision,
            ..Request::default()
        },
        &[],
    );
    assert_eq!((rc, last.count), (0, 1));
    let mut data = vec![0; last.bytes as usize];
    let mut needed = 0;
    unsafe {
        assert_eq!(
            narwhal_transcript_read(
                copy.handle,
                last.token,
                last.revision,
                data.as_mut_ptr(),
                data.len(),
                &mut needed
            ),
            0
        );
    }
    assert_eq!(data, wire(&[&cp("Box")]));
    narwhal_transcript_close(s.handle);
    narwhal_transcript_close(copy.handle); // also disposes retained snapshot
    unsafe {
        assert_eq!(
            narwhal_transcript_read(
                copy.handle,
                last.token,
                last.revision,
                data.as_mut_ptr(),
                data.len(),
                &mut needed
            ),
            2
        );
    }
}
