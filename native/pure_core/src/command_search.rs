//! Packed command-name search for the interactive command line.

const ERR_NULL: i32 = -1;
const ERR_INPUT: i32 = -2;
const ERR_SIZE: i32 = -3;

struct Names<'a> {
    values: Vec<&'a [u8]>,
}

unsafe fn prepare<'a>(
    concat: *const u8,
    concat_len: usize,
    lens: *const i64,
    count: usize,
) -> Result<Names<'a>, i32> {
    let bytes = crate::checked_slice(concat, concat_len).ok_or(ERR_NULL)?;
    let lengths = crate::checked_slice(lens, count).ok_or(ERR_NULL)?;
    let mut values = Vec::with_capacity(count);
    let mut offset = 0usize;
    for &raw in lengths {
        let len = usize::try_from(raw).map_err(|_| ERR_INPUT)?;
        let end = offset.checked_add(len).ok_or(ERR_SIZE)?;
        if end > bytes.len() {
            return Err(ERR_INPUT);
        }
        let value = &bytes[offset..end];
        if !value.is_ascii() {
            return Err(ERR_INPUT);
        }
        values.push(value);
        offset = end;
    }
    if offset != bytes.len() {
        return Err(ERR_INPUT);
    }
    Ok(Names { values })
}

fn lower(value: &[u8]) -> Vec<u8> {
    value.iter().map(u8::to_ascii_lowercase).collect()
}

fn prefix_indices(query: &[u8], names: &Names<'_>) -> Vec<usize> {
    let query_low = lower(query);
    let mut found: Vec<usize> = names
        .values
        .iter()
        .enumerate()
        .filter_map(|(index, name)| {
            (name.len() >= query.len() && name[..query.len()].eq_ignore_ascii_case(query))
                .then_some(index)
        })
        .collect();
    found.sort_by(|&a, &b| {
        let left = names.values[a];
        let right = names.values[b];
        let rank = |name: &[u8]| {
            if lower(name) == query_low {
                0
            } else if name.starts_with(query) {
                1
            } else {
                2
            }
        };
        rank(left)
            .cmp(&rank(right))
            .then_with(|| lower(left).cmp(&lower(right)))
            .then_with(|| a.cmp(&b))
    });
    found
}

fn fuzzy_score(query_low: &[u8], name_low: &[u8]) -> Option<(usize, usize)> {
    let mut start = 0usize;
    let mut first = None;
    let mut last = 0usize;
    for wanted in query_low {
        let relative = name_low[start..].iter().position(|value| value == wanted)?;
        last = start + relative;
        first.get_or_insert(last);
        start = last + 1;
    }
    let first = first?;
    Some((first, last - first + 1))
}

fn fuzzy_indices(query: &[u8], names: &Names<'_>, usage: &[i64]) -> (Vec<usize>, Vec<usize>) {
    let exact = prefix_indices(query, names);
    let query_low = lower(query);
    let lowered: Vec<Vec<u8>> = names.values.iter().map(|name| lower(name)).collect();
    let mut inexact: Vec<(usize, usize, usize)> = lowered
        .iter()
        .enumerate()
        .filter(|(_, name)| !name.starts_with(&query_low))
        .filter_map(|(index, name)| {
            fuzzy_score(&query_low, name).map(|(first, span)| (index, first, span))
        })
        .collect();
    inexact.sort_by(
        |&(left, left_first, left_span), &(right, right_first, right_span)| {
            left_first
                .cmp(&right_first)
                .then_with(|| left_span.cmp(&right_span))
                .then_with(|| usage[right].cmp(&usage[left]))
                .then_with(|| names.values[left].len().cmp(&names.values[right].len()))
                .then_with(|| lowered[left].cmp(&lowered[right]))
                .then_with(|| left.cmp(&right))
        },
    );
    (
        exact,
        inexact.into_iter().map(|(index, _, _)| index).collect(),
    )
}

/// Return command indices in exact/case-sensitive/case-insensitive prefix order.
///
/// # Safety
///
/// Input spans are readable for their declared lengths; output is writable for
/// `output_capacity` u64 values; count output is writable; regions do not overlap.
#[allow(clippy::too_many_arguments)]
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_command_prefix(
    query: *const u8,
    query_len: usize,
    names_concat: *const u8,
    names_concat_len: usize,
    name_lens: *const i64,
    name_count: usize,
    out_indices: *mut u64,
    output_capacity: usize,
    out_count: *mut u64,
) -> i32 {
    if out_count.is_null() {
        return ERR_NULL;
    }
    *out_count = 0;
    if query_len == 0 {
        return 0;
    }
    let query = match crate::checked_slice(query, query_len) {
        Some(value) if value.is_ascii() => value,
        Some(_) => return ERR_INPUT,
        None => return ERR_NULL,
    };
    let names = match prepare(names_concat, names_concat_len, name_lens, name_count) {
        Ok(value) => value,
        Err(code) => return code,
    };
    let found = prefix_indices(query, &names);
    if output_capacity < found.len() {
        return ERR_SIZE;
    }
    let output = match crate::checked_slice_mut(out_indices, output_capacity) {
        Some(value) => value,
        None => return ERR_NULL,
    };
    for (slot, index) in found.iter().enumerate() {
        output[slot] = *index as u64;
    }
    *out_count = found.len() as u64;
    0
}

/// Return exact-prefix indices followed by ranked inexact subsequence indices.
///
/// # Safety
///
/// Input spans are readable for their declared lengths; output is writable for
/// `output_capacity` u64 values; count outputs are writable; regions do not overlap.
#[allow(clippy::too_many_arguments)]
#[no_mangle]
pub unsafe extern "C" fn narwhal_pure_core_command_fuzzy(
    query: *const u8,
    query_len: usize,
    names_concat: *const u8,
    names_concat_len: usize,
    name_lens: *const i64,
    usage: *const i64,
    name_count: usize,
    out_indices: *mut u64,
    output_capacity: usize,
    out_exact_count: *mut u64,
    out_count: *mut u64,
) -> i32 {
    if out_exact_count.is_null() || out_count.is_null() {
        return ERR_NULL;
    }
    *out_exact_count = 0;
    *out_count = 0;
    if query_len == 0 {
        return 0;
    }
    let query = match crate::checked_slice(query, query_len) {
        Some(value) if value.is_ascii() => value,
        Some(_) => return ERR_INPUT,
        None => return ERR_NULL,
    };
    let names = match prepare(names_concat, names_concat_len, name_lens, name_count) {
        Ok(value) => value,
        Err(code) => return code,
    };
    let usage = match crate::checked_slice(usage, name_count) {
        Some(value) => value,
        None => return ERR_NULL,
    };
    let (exact, inexact) = fuzzy_indices(query, &names, usage);
    let total = match exact.len().checked_add(inexact.len()) {
        Some(value) if value <= output_capacity => value,
        _ => return ERR_SIZE,
    };
    let output = match crate::checked_slice_mut(out_indices, output_capacity) {
        Some(value) => value,
        None => return ERR_NULL,
    };
    for (slot, index) in exact.iter().chain(inexact.iter()).enumerate() {
        output[slot] = *index as u64;
    }
    *out_exact_count = exact.len() as u64;
    *out_count = total as u64;
    0
}

#[cfg(test)]
mod tests {
    use super::*;

    fn names_fixture() -> (Vec<u8>, Vec<i64>, Vec<&'static str>) {
        let names = vec!["line", "Line", "LinearDimension", "Limit"];
        let concat = names
            .iter()
            .flat_map(|name| name.as_bytes())
            .copied()
            .collect();
        let lens = names.iter().map(|name| name.len() as i64).collect();
        (concat, lens, names)
    }

    #[test]
    fn prefix_order_matches_python_contract() {
        let (concat, lens, names) = names_fixture();
        let prepared = unsafe { prepare(concat.as_ptr(), concat.len(), lens.as_ptr(), lens.len()) }
            .expect("fixture");
        let got: Vec<&str> = prefix_indices(b"Li", &prepared)
            .into_iter()
            .map(|index| names[index])
            .collect();
        assert_eq!(got, vec!["Limit", "Line", "LinearDimension", "line"]);
        let exact: Vec<&str> = prefix_indices(b"line", &prepared)
            .into_iter()
            .map(|index| names[index])
            .collect();
        assert_eq!(exact[0], "line");
    }

    #[test]
    fn fuzzy_order_matches_score_usage_and_stable_contract() {
        let names = ["zAB", "zABCDE", "abacus", "cab"];
        let concat: Vec<u8> = names
            .iter()
            .flat_map(|name| name.as_bytes())
            .copied()
            .collect();
        let lens: Vec<i64> = names.iter().map(|name| name.len() as i64).collect();
        let prepared = unsafe { prepare(concat.as_ptr(), concat.len(), lens.as_ptr(), lens.len()) }
            .expect("fixture");
        let usage = [0_i64, 5, 99, 0];
        let (exact, inexact) = fuzzy_indices(b"ab", &prepared, &usage);
        assert_eq!(exact, vec![2]);
        assert_eq!(inexact, vec![1, 3, 0]);

        let names = ["zaebcd", "zaxxe"];
        let concat: Vec<u8> = names
            .iter()
            .flat_map(|name| name.as_bytes())
            .copied()
            .collect();
        let lens: Vec<i64> = names.iter().map(|name| name.len() as i64).collect();
        let prepared = unsafe { prepare(concat.as_ptr(), concat.len(), lens.as_ptr(), lens.len()) }
            .expect("fixture");
        assert_eq!(fuzzy_indices(b"ae", &prepared, &[0, 0]).1, vec![0, 1]);
    }

    #[test]
    fn fuzzy_capacity_rejection_clears_both_counts() {
        let (concat, lens, _) = names_fixture();
        let usage = [0_i64; 4];
        let mut out = [u64::MAX; 1];
        let mut exact_count = 99_u64;
        let mut count = 99_u64;
        let code = unsafe {
            narwhal_pure_core_command_fuzzy(
                b"i".as_ptr(),
                1,
                concat.as_ptr(),
                concat.len(),
                lens.as_ptr(),
                usage.as_ptr(),
                lens.len(),
                out.as_mut_ptr(),
                out.len(),
                &mut exact_count,
                &mut count,
            )
        };
        assert_eq!(code, ERR_SIZE);
        assert_eq!((exact_count, count), (0, 0));
        assert_eq!(out, [u64::MAX]);
    }

    #[test]
    fn malformed_name_spans_are_rejected_without_count() {
        let names = b"Line";
        let lens = [5_i64];
        let mut out = [u64::MAX; 1];
        let mut count = 99_u64;
        let code = unsafe {
            narwhal_pure_core_command_prefix(
                b"L".as_ptr(),
                1,
                names.as_ptr(),
                names.len(),
                lens.as_ptr(),
                1,
                out.as_mut_ptr(),
                out.len(),
                &mut count,
            )
        };
        assert_eq!(code, ERR_INPUT);
        assert_eq!(count, 0);
        assert_eq!(out, [u64::MAX]);
    }
}
