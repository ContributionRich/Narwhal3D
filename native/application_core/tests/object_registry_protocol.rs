use narwhal_application_core::{
    narwhal_object_registry_abi, narwhal_object_registry_json, narwhal_object_registry_observe,
};
use serde_json::{Value, json};

fn call(input: &[u8], capacity: usize) -> (isize, Vec<u8>) {
    let mut out = vec![0xa5; capacity];
    let used = unsafe {
        narwhal_object_registry_json(input.as_ptr(), input.len(), out.as_mut_ptr(), capacity)
    };
    (used, out)
}
fn request(v: Value) -> Value {
    let data = serde_json::to_vec(&v).unwrap();
    let (n, out) = call(&data, 4096);
    assert!((0..=4096).contains(&n));
    serde_json::from_slice(&out[..n as usize]).unwrap()
}
fn close(owner: &Value) {
    assert_eq!(request(json!({"op":"close","owner":owner}))["status"], "ok");
}

#[test]
fn observation_buffers_refuse_before_dereference_or_mutation() {
    let owner = request(json!({"op":"create","rows":[]}))["owner"].clone();
    let header = serde_json::to_vec(
        &json!({"op":"prepare","owner":owner,"revision":0,"rows":[],"rebuild":false}),
    )
    .unwrap();
    let data = b"NWOBS01\0\0\0\0\0";
    let mut out = [0u8; 4096];
    let count = unsafe {
        narwhal_object_registry_observe(
            header.as_ptr(),
            header.len(),
            data.as_ptr(),
            data.len(),
            out.as_mut_ptr(),
            100,
        )
    };
    assert_eq!(
        serde_json::from_slice::<Value>(&out[..count as usize]).unwrap()["code"],
        "output_too_small"
    );
    assert_eq!(
        request(json!({"op":"info","owner":owner}))["pending"],
        false
    );
    for (h, d) in [
        (32 * 1024 * 1024 + 1, data.len()),
        (header.len(), 128 * 1024 * 1024 + 1),
    ] {
        let count = unsafe {
            narwhal_object_registry_observe(
                header.as_ptr(),
                h,
                data.as_ptr(),
                d,
                out.as_mut_ptr(),
                out.len(),
            )
        };
        assert_eq!(
            serde_json::from_slice::<Value>(&out[..count as usize]).unwrap()["code"],
            "request_too_large"
        );
    }
    assert_eq!(
        unsafe {
            narwhal_object_registry_observe(
                std::ptr::null(),
                0,
                std::ptr::null(),
                0,
                std::ptr::null_mut(),
                4096,
            )
        },
        -1
    );
    close(&owner);
}

#[test]
fn observation_input_can_overlap_output_and_bad_confirmation_invalidates() {
    let owner = request(json!({"op":"create","rows":[]}))["owner"].clone();
    let header = serde_json::to_vec(
        &json!({"op":"prepare","owner":owner,"revision":0,"rows":[],"rebuild":false}),
    )
    .unwrap();
    let data = b"NWOBS01\0\0\0\0\0";
    let mut out = vec![0u8; 4096];
    out[..data.len()].copy_from_slice(data);
    let count = unsafe {
        narwhal_object_registry_observe(
            header.as_ptr(),
            header.len(),
            out.as_ptr(),
            data.len(),
            out.as_mut_ptr(),
            out.len(),
        )
    };
    let reply: Value = serde_json::from_slice(&out[..count as usize]).unwrap();
    assert_eq!(reply["status"], "ok");
    let ticket = &reply["ticket"];
    assert_eq!(
        request(json!({"op":"begin_apply","owner":owner,"ticket":ticket}))["status"],
        "ok"
    );
    let header = serde_json::to_vec(&json!({"op":"commit","owner":owner,"ticket":ticket})).unwrap();
    let count = unsafe {
        narwhal_object_registry_observe(
            header.as_ptr(),
            header.len(),
            data.as_ptr(),
            4,
            out.as_mut_ptr(),
            out.len(),
        )
    };
    assert_eq!(
        serde_json::from_slice::<Value>(&out[..count as usize]).unwrap()["code"],
        "observation_truncated"
    );
    let info = request(json!({"op":"info","owner":owner}));
    assert_eq!(info["authoritative"], false);
    assert_eq!(info["pending"], false);
    close(&owner);
}

#[test]
fn additive_abi_and_small_output_never_execute_mutation() {
    assert_eq!(narwhal_object_registry_abi(), 1);
    let a = request(json!({"op":"create","rows":[]}));
    let owner = &a["owner"];
    let input = serde_json::to_vec(&json!({"op":"close","owner":owner})).unwrap();
    let (n, out) = call(&input, 128);
    assert_eq!(
        serde_json::from_slice::<Value>(&out[..n as usize]).unwrap()["code"],
        "output_too_small"
    );
    assert_eq!(request(json!({"op":"info","owner":owner}))["status"], "ok");
    close(owner);
}

#[test]
fn malformed_protocol_has_no_valid_reply() {
    for input in [
        b"{".as_slice(),
        b"{\"op\":\"info\",\"owner\":1,\"extra\":1}",
        b"{\"op\":\"info\",\"owner\":true}",
        b"{\"op\":\"info\",\"owner\":1,\"owner\":2}",
        b"{\"op\":\"create\",\"rows\":[],\"op\":\"close\"}",
        b"\xff",
    ] {
        let (n, out) = call(input, 4096);
        assert_eq!(
            serde_json::from_slice::<Value>(&out[..n as usize]).unwrap()["status"],
            "error"
        );
    }
}

#[test]
fn descriptor_bounds_refuse_before_input_access() {
    let mut out = [0_u8; 4096];
    let input = [0_u8];
    let n = unsafe {
        narwhal_object_registry_json(
            input.as_ptr(),
            32 * 1024 * 1024 + 1,
            out.as_mut_ptr(),
            out.len(),
        )
    };
    assert_eq!(
        serde_json::from_slice::<Value>(&out[..n as usize]).unwrap()["code"],
        "request_too_large"
    );
    assert_eq!(
        unsafe { narwhal_object_registry_json(std::ptr::null(), 0, std::ptr::null_mut(), 4096) },
        -1
    );
    assert_eq!(
        unsafe {
            narwhal_object_registry_json(input.as_ptr(), 1, out.as_mut_ptr(), 512 * 1024 * 1024 + 1)
        },
        -1
    );
}

#[test]
fn read_output_negotiation_is_repeatable_and_does_not_advance_revision() {
    let rows: Vec<Value> = (1..=80)
        .map(|n| {
            json!({"id":format!("00000000-0000-0000-0000-{n:012x}"),
        "name":"x".repeat(100),"display":null,"payload":n})
        })
        .collect();
    let a = request(json!({"op":"create","rows":rows}));
    let owner = &a["owner"];
    let input =
        serde_json::to_vec(&json!({"op":"rows","owner":owner,"offset":0,"limit":100})).unwrap();
    let (n, out) = call(&input, 4096);
    assert!(n > 4096);
    assert!(out.iter().all(|b| *b == 0xa5));
    let (used, out) = call(&input, n as usize);
    assert_eq!(used, n);
    let result: Value = serde_json::from_slice(&out).unwrap();
    assert_eq!(result["rows"], json!(rows));
    assert_eq!(result["revision"], 0);
    assert_eq!(request(json!({"op":"info","owner":owner}))["revision"], 0);
    close(owner);
}

#[test]
fn overlapping_input_output_is_supported() {
    let input = br#"{"op":"create","rows":[]}"#;
    let mut buffer = vec![0_u8; 4096];
    buffer[..input.len()].copy_from_slice(input);
    let n = unsafe {
        narwhal_object_registry_json(
            buffer.as_ptr(),
            input.len(),
            buffer.as_mut_ptr(),
            buffer.len(),
        )
    };
    let result: Value = serde_json::from_slice(&buffer[..n as usize]).unwrap();
    assert_eq!(result["status"], "ok");
    close(&result["owner"]);
}
