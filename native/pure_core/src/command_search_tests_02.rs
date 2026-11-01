//! Hand-derived prefix contract (see spike/fixtures/native_search02.json).
//! Expected values are derived by hand from the ordering rules, not from this module's output.

use super::*;

fn packed(names: &[&str]) -> (Vec<u8>, Vec<i64>) {
    let concat = names.iter().flat_map(|name| name.bytes()).collect();
    let lens = names.iter().map(|name| name.len() as i64).collect();
    (concat, lens)
}

fn prefix_names(query: &str, names: &[&str]) -> Vec<String> {
    let (concat, lens) = packed(names);
    let prepared = unsafe { prepare(concat.as_ptr(), concat.len(), lens.as_ptr(), lens.len()) }
        .expect("fixture");
    prefix_indices(query.as_bytes(), &prepared)
        .into_iter()
        .map(|index| names[index].to_string())
        .collect()
}

fn prefix_index_order(query: &str, names: &[&str]) -> Vec<usize> {
    let (concat, lens) = packed(names);
    let prepared = unsafe { prepare(concat.as_ptr(), concat.len(), lens.as_ptr(), lens.len()) }
        .expect("fixture");
    prefix_indices(query.as_bytes(), &prepared)
}

#[test]
fn rank_groups_put_case_sensitive_prefixes_before_lower_only() {
    assert_eq!(
        prefix_names("Li", &["line", "Line", "LinearDimension", "Limit"]),
        ["Limit", "Line", "LinearDimension", "line"]
    );
}

#[test]
fn exact_case_insensitive_match_is_rank_zero_and_ties_by_index() {
    assert_eq!(
        prefix_names("line", &["line", "Line", "LinearDimension", "Limit"]),
        ["line", "Line", "LinearDimension"]
    );
}

#[test]
fn exact_case_is_not_a_rank_inside_the_exact_group() {
    assert_eq!(
        prefix_names("LINE", &["Line", "line", "LINE"]),
        ["Line", "line", "LINE"]
    );
    assert_eq!(
        prefix_index_order("LINE", &["Line", "line", "LINE"]),
        [0, 1, 2]
    );
}

#[test]
fn case_sensitive_group_precedes_lower_only_and_sorts_lowercase() {
    assert_eq!(
        prefix_names("Po", &["Pointer", "point", "Point"]),
        ["Point", "Pointer", "point"]
    );
}

#[test]
fn duplicate_names_are_all_returned_in_index_order() {
    assert_eq!(
        prefix_names("Li", &["Line", "Line", "Limit"]),
        ["Limit", "Line", "Line"]
    );
    assert_eq!(
        prefix_index_order("Li", &["Line", "Line", "Limit"]),
        [2, 0, 1]
    );
}

#[test]
fn no_hit_and_empty_table_return_nothing() {
    assert!(prefix_names("zz", &["Line"]).is_empty());
    assert!(prefix_names("a", &[]).is_empty());
}

#[test]
fn lowercase_only_name_still_matches_uppercase_query() {
    assert_eq!(prefix_names("LIN", &["line"]), ["line"]);
}

fn fuzzy_names(query: &str, names: &[&str], usage: &[i64]) -> (Vec<String>, Vec<String>) {
    let (concat, lens) = packed(names);
    let prepared = unsafe { prepare(concat.as_ptr(), concat.len(), lens.as_ptr(), lens.len()) }
        .expect("fixture");
    let (exact, inexact) = fuzzy_indices(query.as_bytes(), &prepared, usage);
    let to_names = |indices: Vec<usize>| -> Vec<String> {
        indices
            .into_iter()
            .map(|index| names[index].to_string())
            .collect()
    };
    (to_names(exact), to_names(inexact))
}

#[test]
fn fuzzy_exact_group_is_excluded_and_usage_breaks_the_tie() {
    assert_eq!(
        fuzzy_names("ab", &["zAB", "zABCDE", "abacus", "cab"], &[0, 5, 0, 0]),
        (
            vec!["abacus".into()],
            vec!["zABCDE".into(), "cab".into(), "zAB".into()]
        )
    );
}

#[test]
fn fuzzy_usage_extremes_order_without_overflow() {
    assert_eq!(
        fuzzy_names("ab", &["zab", "yab"], &[i64::MIN, i64::MAX]),
        (vec![], vec!["yab".into(), "zab".into()])
    );
}

#[test]
fn fuzzy_start_position_beats_span() {
    assert_eq!(
        fuzzy_names("ab", &["xxab", "xaxb", "axxxxb"], &[0, 0, 0]).1,
        vec!["axxxxb", "xaxb", "xxab"]
    );
}

#[test]
fn fuzzy_span_beats_usage_and_usage_beats_length() {
    assert_eq!(
        fuzzy_names("ab", &["xaxb", "xab"], &[100, 0]).1,
        vec!["xab", "xaxb"]
    );
    assert_eq!(
        fuzzy_names("ab", &["xab", "xabc"], &[0, 5]).1,
        vec!["xabc", "xab"]
    );
}

#[test]
fn fuzzy_length_then_lowercase_then_index() {
    assert_eq!(
        fuzzy_names("ab", &["xabz", "xabaa"], &[0, 0]).1,
        vec!["xabz", "xabaa"]
    );
    assert_eq!(
        fuzzy_names("ab", &["xabb", "xaba"], &[0, 0]).1,
        vec!["xaba", "xabb"]
    );
    assert_eq!(
        fuzzy_names("bc", &["xBC", "xbc"], &[0, 0]).1,
        vec!["xBC", "xbc"]
    );
}

#[test]
fn fuzzy_duplicates_and_greedy_leftmost_match() {
    let (exact, inexact) = fuzzy_names("ab", &["xab", "xab"], &[0, 0]);
    assert!(exact.is_empty());
    assert_eq!(inexact, vec!["xab", "xab"]);
    assert_eq!(
        fuzzy_names("aa", &["xaxxa", "xxaa"], &[0, 0]).1,
        vec!["xaxxa", "xxaa"]
    );
}

fn fuzzy_index_order(query: &str, names: &[&str], usage: &[i64]) -> Vec<usize> {
    let (concat, lens) = packed(names);
    let prepared = unsafe { prepare(concat.as_ptr(), concat.len(), lens.as_ptr(), lens.len()) }
        .expect("fixture");
    fuzzy_indices(query.as_bytes(), &prepared, usage).1
}

#[test]
fn signed_usage_extremes_order_descending_with_index_ties() {
    let names = ["xb", "xb", "xb", "xb", "xb"];
    let usage = [i64::MIN, 0, i64::MAX, i64::MIN, 0];
    assert_eq!(fuzzy_index_order("b", &names, &usage), vec![2, 1, 4, 0, 3]);
}

#[test]
fn negative_usage_is_not_reflected_into_positive_order() {
    let names = ["xb", "xb", "xb"];
    let usage = [-1, 1, 0];
    assert_eq!(fuzzy_index_order("b", &names, &usage), vec![1, 2, 0]);
}

#[test]
fn fuzzy_exact_group_is_case_insensitive_and_separate_from_usage() {
    assert_eq!(
        fuzzy_names("ab", &["ABc", "xabc"], &[0, 0]),
        (vec!["ABc".into()], vec!["xabc".into()])
    );
    assert_eq!(
        fuzzy_names("ab", &["abz", "xab"], &[0, 999]),
        (vec!["abz".into()], vec!["xab".into()])
    );
}

fn call_prefix(query: &[u8], concat: &[u8], lens: &[i64], out: &mut [u64], count: &mut u64) -> i32 {
    unsafe {
        narwhal_pure_core_command_prefix(
            query.as_ptr(),
            query.len(),
            concat.as_ptr(),
            concat.len(),
            lens.as_ptr(),
            lens.len(),
            out.as_mut_ptr(),
            out.len(),
            count,
        )
    }
}

fn call_fuzzy(
    query: &[u8],
    concat: &[u8],
    lens: &[i64],
    usage: &[i64],
    out: &mut [u64],
    exact_count: &mut u64,
    count: &mut u64,
) -> i32 {
    unsafe {
        narwhal_pure_core_command_fuzzy(
            query.as_ptr(),
            query.len(),
            concat.as_ptr(),
            concat.len(),
            lens.as_ptr(),
            usage.as_ptr(),
            lens.len(),
            out.as_mut_ptr(),
            out.len(),
            exact_count,
            count,
        )
    }
}

#[test]
fn prefix_query_limit_is_admitted_and_one_more_byte_is_refused() {
    let concat = b"b";
    let lens = [1_i64];
    let mut out = [u64::MAX; 1];
    let mut count = 99_u64;
    let at_limit = vec![b'a'; MAX_QUERY_BYTES];
    assert_eq!(
        call_prefix(&at_limit, concat, &lens, &mut out, &mut count),
        0
    );
    assert_eq!(count, 0);

    let mut count = 99_u64;
    let over = vec![b'a'; MAX_QUERY_BYTES + 1];
    assert_eq!(
        call_prefix(&over, concat, &lens, &mut out, &mut count),
        ERR_SIZE
    );
    assert_eq!(count, 0);
    assert_eq!(out, [u64::MAX]);
}

#[test]
fn fuzzy_query_limit_is_admitted_and_one_more_byte_is_refused_with_both_counts_cleared() {
    let concat = b"b";
    let lens = [1_i64];
    let usage = [0_i64];
    let mut out = [u64::MAX; 1];
    let (mut exact, mut total) = (7_u64, 7_u64);
    let at_limit = vec![b'a'; MAX_QUERY_BYTES];
    assert_eq!(
        call_fuzzy(&at_limit, concat, &lens, &usage, &mut out, &mut exact, &mut total),
        0
    );
    assert_eq!((exact, total), (0, 0));

    let (mut exact, mut total) = (7_u64, 7_u64);
    let over = vec![b'a'; MAX_QUERY_BYTES + 1];
    assert_eq!(
        call_fuzzy(&over, concat, &lens, &usage, &mut out, &mut exact, &mut total),
        ERR_SIZE
    );
    assert_eq!((exact, total), (0, 0));
    assert_eq!(out, [u64::MAX]);
}

#[test]
fn name_count_limit_is_admitted_and_one_more_entry_is_refused() {
    let query = b"a";
    let concat: Vec<u8> = Vec::new();
    let mut out = vec![u64::MAX; MAX_NAMES];
    let lens = vec![0_i64; MAX_NAMES];
    let mut count = 99_u64;
    assert_eq!(call_prefix(query, &concat, &lens, &mut out, &mut count), 0);
    assert_eq!(count, 0);

    let lens = vec![0_i64; MAX_NAMES + 1];
    let mut out = vec![u64::MAX; MAX_NAMES];
    let mut count = 99_u64;
    assert_eq!(
        call_prefix(query, &concat, &lens, &mut out, &mut count),
        ERR_SIZE
    );
    assert_eq!(count, 0);
    assert!(out.iter().all(|&slot| slot == u64::MAX));
}

#[test]
fn concatenation_limit_is_checked_before_span_validation() {
    let query = b"b";
    let concat = vec![b'a'; MAX_CONCAT_BYTES];
    let lens = [MAX_CONCAT_BYTES as i64];
    let mut out = [u64::MAX; 1];
    let mut count = 99_u64;
    assert_eq!(call_prefix(query, &concat, &lens, &mut out, &mut count), 0);
    assert_eq!(count, 0);

    // Deliberately inconsistent descriptor: only admission can refuse it with ERR_SIZE.
    let concat = vec![b'a'; MAX_CONCAT_BYTES + 1];
    let lens = [0_i64];
    let mut count = 99_u64;
    assert_eq!(
        call_prefix(query, &concat, &lens, &mut out, &mut count),
        ERR_SIZE
    );
    assert_eq!(count, 0);
    assert_eq!(out, [u64::MAX]);
}

#[test]
fn zero_query_stays_a_no_op_even_for_an_oversize_table() {
    let lens = vec![0_i64; MAX_NAMES + 1];
    let usage = vec![0_i64; MAX_NAMES + 1];
    let mut out = [u64::MAX; 1];
    let mut count = 99_u64;
    assert_eq!(call_prefix(b"", &[], &lens, &mut out, &mut count), 0);
    assert_eq!(count, 0);
    let (mut exact, mut total) = (99_u64, 99_u64);
    assert_eq!(
        call_fuzzy(b"", &[], &lens, &usage, &mut out, &mut exact, &mut total),
        0
    );
    assert_eq!((exact, total), (0, 0));
}

#[test]
fn malformed_spans_are_refused_by_both_exports_without_output() {
    let cases: [(&str, &[u8], Vec<i64>); 6] = [
        ("negative length", b"Line", vec![4, -1]),
        (
            "negative length whose magnitude fits exactly",
            b"Li",
            vec![-2],
        ),
        ("minimum i64 length", b"Li", vec![i64::MIN]),
        ("length past the concatenation (truncation)", b"Li", vec![4]),
        ("lengths short of the concatenation", b"Line", vec![2]),
        (
            "maximum i64 length past the concatenation",
            b"Li",
            vec![i64::MAX],
        ),
    ];
    for (label, concat, lens) in cases {
        let mut out = [u64::MAX; 4];
        let mut count = 99_u64;
        assert_eq!(
            call_prefix(b"L", concat, &lens, &mut out, &mut count),
            ERR_INPUT,
            "prefix {label}"
        );
        assert_eq!(count, 0, "prefix {label}");
        assert_eq!(out, [u64::MAX; 4], "prefix {label}");

        let usage = vec![0_i64; lens.len()];
        let (mut exact, mut total) = (99_u64, 99_u64);
        assert_eq!(
            call_fuzzy(b"L", concat, &lens, &usage, &mut out, &mut exact, &mut total),
            ERR_INPUT,
            "fuzzy {label}"
        );
        assert_eq!((exact, total), (0, 0), "fuzzy {label}");
        assert_eq!(out, [u64::MAX; 4], "fuzzy {label}");
    }
}

#[test]
fn non_ascii_name_bytes_are_refused_by_both_exports() {
    let concat = [b'L', 0xC3, 0xAF];
    let lens = [3_i64];
    let usage = [0_i64];
    let mut out = [u64::MAX; 1];
    let mut count = 99_u64;
    assert_eq!(
        call_prefix(b"L", &concat, &lens, &mut out, &mut count),
        ERR_INPUT
    );
    assert_eq!(count, 0);
    let (mut exact, mut total) = (99_u64, 99_u64);
    assert_eq!(
        call_fuzzy(b"L", &concat, &lens, &usage, &mut out, &mut exact, &mut total),
        ERR_INPUT
    );
    assert_eq!((exact, total), (0, 0));
    assert_eq!(out, [u64::MAX]);
}

#[test]
fn valid_empty_names_and_empty_table_are_accepted() {
    let mut out = [u64::MAX; 4];
    let mut count = 99_u64;
    assert_eq!(call_prefix(b"a", &[], &[], &mut out, &mut count), 0);
    assert_eq!(count, 0);
    assert_eq!(call_prefix(b"a", &[], &[0, 0], &mut out, &mut count), 0);
    assert_eq!(count, 0);
    let (mut exact, mut total) = (99_u64, 99_u64);
    assert_eq!(
        call_fuzzy(
            b"a",
            &[],
            &[0, 0],
            &[0, 0],
            &mut out,
            &mut exact,
            &mut total
        ),
        0
    );
    assert_eq!((exact, total), (0, 0));
}

#[test]
fn empty_name_occupies_its_slot_and_real_names_keep_their_indices() {
    let concat = b"ab";
    let mut out = [u64::MAX; 2];
    let mut count = 0_u64;
    assert_eq!(call_prefix(b"a", concat, &[0, 2], &mut out, &mut count), 0);
    assert_eq!(count, 1);
    assert_eq!(out[0], 1);
}

#[test]
fn prefix_output_contract_exact_short_zero_and_guard_slots() {
    let concat = b"LineLineLimit";
    let lens = [4_i64, 4, 5];

    let mut out = [u64::MAX; 3];
    let mut count = 0_u64;
    assert_eq!(call_prefix(b"Li", concat, &lens, &mut out, &mut count), 0);
    assert_eq!((count, out), (3, [2, 0, 1]));
}

#[test]
fn prefix_short_and_zero_capacity_refuse_without_partial_writes() {
    let concat = b"LineLineLimit";
    let lens = [4_i64, 4, 5];
    let mut out = [u64::MAX; 3];
    let mut count = 99_u64;
    let code = unsafe {
        narwhal_pure_core_command_prefix(
            b"Li".as_ptr(),
            2,
            concat.as_ptr(),
            concat.len(),
            lens.as_ptr(),
            3,
            out.as_mut_ptr(),
            2,
            &mut count,
        )
    };
    assert_eq!(code, ERR_SIZE);
    assert_eq!(count, 0);
    assert_eq!(out, [u64::MAX; 3]);

    let mut count = 99_u64;
    assert_eq!(
        call_prefix(b"Li", concat, &lens, &mut out[..0], &mut count),
        ERR_SIZE
    );
    assert_eq!(count, 0);
    assert_eq!(out, [u64::MAX; 3]);
}

#[test]
fn nullable_zero_capacity_output_is_accepted_when_nothing_matches() {
    let concat = b"Line";
    let lens = [4_i64];
    let mut count = 99_u64;
    let code = unsafe {
        narwhal_pure_core_command_prefix(
            b"zz".as_ptr(),
            2,
            concat.as_ptr(),
            concat.len(),
            lens.as_ptr(),
            1,
            std::ptr::null_mut(),
            0,
            &mut count,
        )
    };
    assert_eq!(code, 0);
    assert_eq!(count, 0);
}

#[test]
fn prefix_guard_slots_beyond_the_count_are_untouched() {
    let concat = b"LineLineLimit";
    let lens = [4_i64, 4, 5];
    let mut out = [u64::MAX; 5];
    let mut count = 0_u64;
    assert_eq!(call_prefix(b"Li", concat, &lens, &mut out, &mut count), 0);
    assert_eq!(count, 3);
    assert_eq!(out, [2, 0, 1, u64::MAX, u64::MAX]);
}

#[test]
fn prefix_null_count_is_refused_before_any_write() {
    let concat = b"LineLineLimit";
    let lens = [4_i64, 4, 5];
    let mut out = [u64::MAX; 3];
    let code = unsafe {
        narwhal_pure_core_command_prefix(
            b"Li".as_ptr(),
            2,
            concat.as_ptr(),
            concat.len(),
            lens.as_ptr(),
            3,
            out.as_mut_ptr(),
            out.len(),
            std::ptr::null_mut(),
        )
    };
    assert_eq!(code, ERR_NULL);
    assert_eq!(out, [u64::MAX; 3]);
}

#[test]
fn fuzzy_output_contract_splits_counts_and_fills_exact_capacity() {
    let mut out = [u64::MAX; 2];
    let (mut exact, mut total) = (99_u64, 99_u64);
    assert_eq!(
        call_fuzzy(
            b"ab",
            b"xababc",
            &[3, 3],
            &[0, 0],
            &mut out,
            &mut exact,
            &mut total
        ),
        0
    );
    assert_eq!((exact, total), (1, 2));
    assert_eq!(out, [1, 0]);
}

#[test]
fn fuzzy_short_capacity_clears_both_counts_and_writes_nothing() {
    let mut out = [u64::MAX; 2];
    let (mut exact, mut total) = (99_u64, 99_u64);
    assert_eq!(
        call_fuzzy(
            b"ab",
            b"xababc",
            &[3, 3],
            &[0, 0],
            &mut out[..1],
            &mut exact,
            &mut total
        ),
        ERR_SIZE
    );
    assert_eq!((exact, total), (0, 0));
    assert_eq!(out, [u64::MAX; 2]);
}

#[test]
fn fuzzy_null_counts_are_refused_before_any_write() {
    let mut out = [u64::MAX; 2];
    let mut total = 0_u64;
    let code = unsafe {
        narwhal_pure_core_command_fuzzy(
            b"ab".as_ptr(),
            2,
            b"xababc".as_ptr(),
            6,
            [3_i64, 3].as_ptr(),
            [0_i64, 0].as_ptr(),
            2,
            out.as_mut_ptr(),
            out.len(),
            std::ptr::null_mut(),
            &mut total,
        )
    };
    assert_eq!(code, ERR_NULL);
    assert_eq!(out, [u64::MAX; 2]);
    let mut exact = 0_u64;
    let code = unsafe {
        narwhal_pure_core_command_fuzzy(
            b"ab".as_ptr(),
            2,
            b"xababc".as_ptr(),
            6,
            [3_i64, 3].as_ptr(),
            [0_i64, 0].as_ptr(),
            2,
            out.as_mut_ptr(),
            out.len(),
            &mut exact,
            std::ptr::null_mut(),
        )
    };
    assert_eq!(code, ERR_NULL);
    assert_eq!(out, [u64::MAX; 2]);
}

#[test]
fn export_zero_query_is_explicit_and_clears_the_count() {
    let names = b"Line";
    let lens = [4_i64];
    let mut out = [u64::MAX; 1];
    let mut count = 99_u64;
    let code = unsafe {
        narwhal_pure_core_command_prefix(
            b"".as_ptr(),
            0,
            names.as_ptr(),
            names.len(),
            lens.as_ptr(),
            1,
            out.as_mut_ptr(),
            out.len(),
            &mut count,
        )
    };
    assert_eq!(code, 0);
    assert_eq!(count, 0);
    assert_eq!(out, [u64::MAX]);
}

#[test]
fn export_duplicate_table_writes_expected_indices() {
    let (concat, lens) = packed(&["Line", "Line", "Limit"]);
    let mut out = [u64::MAX; 3];
    let mut count = 0_u64;
    let code = unsafe {
        narwhal_pure_core_command_prefix(
            b"Li".as_ptr(),
            2,
            concat.as_ptr(),
            concat.len(),
            lens.as_ptr(),
            3,
            out.as_mut_ptr(),
            out.len(),
            &mut count,
        )
    };
    assert_eq!(code, 0);
    assert_eq!(count, 3);
    assert_eq!(out, [2, 0, 1]);
}
