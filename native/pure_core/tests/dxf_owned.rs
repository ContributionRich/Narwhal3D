//! Independent original byte captures, typed ABI bounds, and atomicity.
use narwhal_pure_core::dxf::*;

struct Fixture {
    names: Vec<u8>,
    lengths: Vec<i64>,
    layers: Vec<DxfOwnedLayer>,
    entities: Vec<DxfOwnedEntity>,
    nums: Vec<f64>,
    unit: Vec<u8>,
    mode: u8,
}
fn entity(
    kind: i64,
    name: i64,
    color: i64,
    points: i64,
    closed: i64,
    true_color: i64,
) -> DxfOwnedEntity {
    DxfOwnedEntity {
        kind,
        name,
        color,
        points,
        closed,
        true_color,
        label: name,
    }
}
impl Fixture {
    fn mixed() -> Self {
        Self {
            names: "FirstCaféCutRound0".as_bytes().to_vec(),
            lengths: vec![5, 5, 3, 5, 1],
            layers: vec![
                DxfOwnedLayer { name: 0, color: 3 },
                DxfOwnedLayer {
                    name: 1,
                    color: 0x1000000,
                },
                DxfOwnedLayer { name: 2, color: 2 },
            ],
            entities: vec![
                entity(0, 2, 7, 0, 0, 0),
                entity(1, 3, 0, 0, 0, 1),
                entity(2, 4, 4, 0, 0, 0),
                entity(3, 2, 7, 3, 1, 0),
                entity(4, 1, 0x123456, 7, 0, 1),
            ],
            nums: vec![
                0., 0., 0., 1., 2., 3., 1., 2., 3., 2.5, 0., 0., 1., 3.5, -10., 720., 0., 0., 0.,
                1., 2., 0., 2., 2., 0., 0., 0., 0., 1., 2., 0., 2., 2., 0., 3., 0., 0., 4., -2.,
                0., 5., -2., 0., 6., 0., 0.,
            ],
            unit: vec![],
            mode: 0,
        }
    }
    fn line() -> Self {
        Self {
            names: b"Cut".to_vec(),
            lengths: vec![3],
            layers: vec![],
            entities: vec![entity(0, 0, 7, 0, 0, 0)],
            nums: vec![0., 0., 0., 1., 2., 3.],
            unit: vec![],
            mode: 0,
        }
    }
    fn input(&self) -> DxfOwnedInput {
        DxfOwnedInput {
            version: 2,
            names: self.names.as_ptr(),
            names_len: self.names.len(),
            lengths: self.lengths.as_ptr(),
            names_count: self.lengths.len(),
            layers: self.layers.as_ptr(),
            layer_count: self.layers.len(),
            entities: self.entities.as_ptr(),
            entity_count: self.entities.len(),
            nums: self.nums.as_ptr(),
            nums_len: self.nums.len(),
            unit: self.unit.as_ptr(),
            unit_len: self.unit.len(),
            unit_mode: self.mode,
        }
    }
    fn encode(&self) -> (Vec<u8>, i64) {
        let mut capacity = 0;
        let mut diag = [0; 3];
        let input = self.input();
        assert_eq!(
            unsafe {
                narwhal_pure_core_dxf_owned_capacity(&input, &mut capacity, diag.as_mut_ptr())
            },
            0,
            "{diag:?}"
        );
        assert!(capacity <= 512 * 1024 * 1024);
        let mut out = vec![0xa5; capacity];
        let (mut written, mut encoding) = (-1, -1);
        assert_eq!(
            unsafe {
                narwhal_pure_core_dxf_owned_encode(
                    &input,
                    out.as_mut_ptr(),
                    out.len(),
                    &mut written,
                    &mut encoding,
                    diag.as_mut_ptr(),
                )
            },
            0,
            "{diag:?}"
        );
        assert_eq!(diag, [0; 3]);
        out.truncate(written as usize);
        (out, encoding)
    }
}
macro_rules! golden {
    ($name:literal) => {
        include_bytes!(concat!("fixtures/dxf_writer23/", $name, ".dxf")).as_slice()
    };
}

#[test]
fn dxf_owned_all_analytic_entities_and_all_unit_headers_match_original() {
    let cases: &[(&str, u8, &[u8])] = &[
        ("", 0, golden!("units-default")),
        ("Inches", 1, golden!("units-Inches")),
        ("Feet", 1, golden!("units-Feet")),
        ("Miles", 1, golden!("units-Miles")),
        ("Millimeters", 1, golden!("units-Millimeters")),
        ("Centimeters", 1, golden!("units-Centimeters")),
        ("Meters", 1, golden!("units-Meters")),
        ("Kilometers", 1, golden!("units-Kilometers")),
        ("Microinches", 1, golden!("units-Microinches")),
        ("Mils", 1, golden!("units-Mils")),
        ("Yards", 1, golden!("units-Yards")),
        ("Angstroms", 1, golden!("units-Angstroms")),
        ("Nanometers", 1, golden!("units-Nanometers")),
        ("Microns", 1, golden!("units-Microns")),
        ("Decimeters", 1, golden!("units-Decimeters")),
        ("Dekameters", 1, golden!("units-Dekameters")),
        ("Hectometers", 1, golden!("units-Hectometers")),
        ("Gigameters", 1, golden!("units-Gigameters")),
        ("UnknownUnit", 1, golden!("units-UnknownUnit")),
        ("", 1, golden!("units-empty")),
    ];
    for (unit, mode, expected) in cases {
        let mut f = Fixture::mixed();
        f.unit = unit.as_bytes().to_vec();
        f.mode = *mode;
        let (bytes, encoding) = f.encode();
        assert_eq!(encoding, 0);
        assert_eq!(bytes, *expected, "{unit:?} mode={mode}");
    }
}

#[test]
fn dxf_owned_closed_cubic_chain_keeps_exact_controls_and_flags() {
    let mut f = Fixture::mixed();
    f.entities[4].closed = 1;
    assert_eq!(f.encode().0, golden!("mixed-closed"));
}

#[test]
fn dxf_owned_two_thousand_original_float_answers_are_not_a_runtime_oracle() {
    let mut f = Fixture::line();
    let input = include_bytes!("fixtures/dxf_writer23/random-coordinates.bin");
    f.nums = input
        .chunks_exact(8)
        .map(|b| f64::from_le_bytes(b.try_into().unwrap()))
        .collect();
    f.entities = vec![entity(0, 0, 7, 0, 0, 0); 2000];
    assert_eq!(f.encode().0, golden!("random-floats"));
}

#[test]
fn dxf_owned_short_polylines_and_raw_color_sentinel_keep_legacy_bytes() {
    let mut f = Fixture::line();
    f.entities[0].color = 0x1123456;
    assert_eq!(f.encode().0, golden!("color-sentinel")); // AC1009, despite group 420
    f.names = b"0".to_vec();
    f.lengths = vec![1];
    f.entities = vec![entity(3, 0, 7, 0, 0, 0)];
    f.nums.clear();
    assert_eq!(f.encode().0, golden!("poly-zero"));
    f.entities[0].points = 1;
    f.nums = vec![1., 2., 3.];
    assert_eq!(f.encode().0, golden!("poly-one"));
}

#[test]
fn dxf_owned_short_polyline_cursor_does_not_corrupt_the_next_analytic_entity() {
    let mut f = Fixture::line();
    f.names = b"0".to_vec();
    f.lengths = vec![1];
    f.entities = vec![
        entity(3, 0, 7, 0, 0, 0),
        entity(3, 0, 7, 1, 0, 0),
        entity(0, 0, 7, 0, 0, 0),
    ];
    f.nums = vec![9., 8., 7., 0., 0., 0., 1., 2., 3.];
    assert_eq!(f.encode().0, golden!("short-poly-chain"));
}

#[test]
fn dxf_owned_unsafe_fields_and_malformed_inputs_leave_outputs_untouched() {
    let mut cases = Vec::new();
    for value in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
        let mut f = Fixture::line();
        f.nums[0] = value;
        cases.push((f, -44));
    }
    let mut f = Fixture::line();
    f.names = b"bad\nname".to_vec();
    f.lengths = vec![8];
    cases.push((f, -45));
    let mut f = Fixture::line();
    f.entities[0].color = 256;
    cases.push((f, -40));
    let mut f = Fixture::line();
    f.entities[0].true_color = 1;
    f.entities[0].color = -1;
    cases.push((f, -41));
    let mut f = Fixture::line();
    f.entities[0].kind = 99;
    cases.push((f, -42));
    let mut f = Fixture::line();
    f.entities[0].kind = 4;
    f.entities[0].points = 3;
    cases.push((f, -43));
    let mut f = Fixture::line();
    f.entities[0].closed = 2;
    cases.push((f, -46));
    let mut f = Fixture::line();
    f.entities[0].kind = 1;
    f.nums = vec![0., 0., 0., -1.];
    cases.push((f, -47));
    for (f, error) in cases {
        let mut out = [0xa5; 4096];
        let (mut written, mut encoding) = (-1, -1);
        let mut diag = [0; 3];
        let input = f.input();
        assert_eq!(
            unsafe {
                narwhal_pure_core_dxf_owned_encode(
                    &input,
                    out.as_mut_ptr(),
                    out.len(),
                    &mut written,
                    &mut encoding,
                    diag.as_mut_ptr(),
                )
            },
            error
        );
        assert_eq!(written, -1);
        assert_eq!(encoding, -1);
        assert_eq!(out, [0xa5; 4096]);
        assert_eq!(diag[0], error as i64);
    }
}

#[test]
fn dxf_owned_capacity_and_raw_spans_are_checked_before_any_publication() {
    let f = Fixture::line();
    let mut input = f.input();
    let mut cap = 0;
    let mut diag = [0; 3];
    assert_eq!(
        unsafe { narwhal_pure_core_dxf_owned_capacity(&input, &mut cap, diag.as_mut_ptr()) },
        0
    );
    let mut out = vec![0xa5; cap - 1];
    let (mut n, mut enc) = (-1, -1);
    assert_eq!(
        unsafe {
            narwhal_pure_core_dxf_owned_encode(
                &input,
                out.as_mut_ptr(),
                out.len(),
                &mut n,
                &mut enc,
                diag.as_mut_ptr(),
            )
        },
        -3
    );
    assert!(out.iter().all(|b| *b == 0xa5));
    assert_eq!((n, enc), (-1, -1));
    input.version = 1; // stale pre-label descriptor must refuse before reading spans
    cap = 77;
    assert_eq!(
        unsafe { narwhal_pure_core_dxf_owned_capacity(&input, &mut cap, diag.as_mut_ptr()) },
        -2
    );
    assert_eq!(cap, 77);
    input = f.input();
    input.nums = std::ptr::null();
    assert_eq!(
        unsafe { narwhal_pure_core_dxf_owned_capacity(&input, &mut cap, diag.as_mut_ptr()) },
        -1
    );
    assert_eq!(cap, 77);
    input = f.input();
    input.entities = std::ptr::null();
    input.entity_count = usize::MAX;
    assert_eq!(
        unsafe { narwhal_pure_core_dxf_owned_capacity(&input, &mut cap, diag.as_mut_ptr()) },
        -3
    );
    assert_eq!(
        unsafe {
            narwhal_pure_core_dxf_owned_capacity(std::ptr::null(), &mut cap, diag.as_mut_ptr())
        },
        -1
    );
    assert_eq!(
        unsafe {
            narwhal_pure_core_dxf_owned_capacity(
                &f.input(),
                std::ptr::null_mut(),
                diag.as_mut_ptr(),
            )
        },
        -1
    );
    assert_eq!(
        unsafe { narwhal_pure_core_dxf_owned_capacity(&f.input(), &mut cap, std::ptr::null_mut()) },
        -1
    );
}

#[test]
fn dxf_owned_unicode_is_full_original_text_for_host_exception_not_file_bytes() {
    let mut f = Fixture::line();
    f.names = "snow☃".as_bytes().to_vec();
    f.lengths = vec![7];
    let (text, encoding) = f.encode();
    assert_eq!(encoding, 1);
    assert_eq!(
        text,
        include_bytes!("fixtures/dxf_writer23/unicode-frame.utf8")
    );
}

#[test]
fn dxf_owned_layer_ids_keep_distinct_keys_order_and_duplicate_updates() {
    let mut f = Fixture::line();
    f.names = b"11".to_vec();
    f.lengths = vec![1, 1];
    f.layers = vec![
        DxfOwnedLayer { name: 0, color: 2 },
        DxfOwnedLayer { name: 1, color: 5 },
        DxfOwnedLayer { name: 0, color: 3 },
    ];
    f.entities[0].name = 1;
    let bytes = f.encode().0;
    let text = String::from_utf8(bytes).unwrap();
    assert!(text.contains("2\nLAYER\n70\n2\n"));
    assert!(text.find("62\n3\n").unwrap() < text.find("62\n5\n").unwrap());
}

#[test]
fn dxf_owned_equal_mapping_keys_do_not_overwrite_entity_label_text() {
    let mut f = Fixture::line();
    f.names = b"True1".to_vec();
    f.lengths = vec![4, 1];
    f.layers = vec![DxfOwnedLayer { name: 0, color: 2 }];
    f.entities[0].label = 1;
    assert_eq!(f.encode().0, golden!("key-alias"));
}

#[test]
fn dxf_owned_public_metadata_unknown_default_and_layer_color_preflight() {
    assert_eq!(narwhal_pure_core_dxf_owned_abi_version(), 2);
    for (name, mode, expected) in [
        (b"".as_slice(), 0, [4, -1]),
        (b"", 1, [0, -1]),
        (b"Inches", 1, [1, 0]),
        (b"Millimeters", 1, [4, 1]),
        (b"unknown", 1, [0, -1]),
    ] {
        let mut out = [77; 2];
        assert_eq!(
            unsafe {
                narwhal_pure_core_dxf_owned_units(name.as_ptr(), name.len(), mode, out.as_mut_ptr())
            },
            0
        );
        assert_eq!(out, expected);
    }
    let colors = [7, 256];
    let mut diag = [0; 3];
    assert_eq!(
        unsafe { narwhal_pure_core_dxf_owned_colors(colors.as_ptr(), 2, diag.as_mut_ptr()) },
        -40
    );
    assert_eq!(diag, [-40, 1, 0]);
}
