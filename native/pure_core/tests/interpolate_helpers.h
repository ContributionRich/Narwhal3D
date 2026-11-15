/* Candidate interpolation helper ABI v1. Main may register in shared headers.
 * No allocation crosses the ABI. No output payload is written on any failure.
 * Finite doubles only; rows/columns/degree <= 2048, payload <= 4200000 doubles.
 * Operation 9 accepts 2049 parameters (2048 points plus the closing parameter).
 * All pointers must name live allocations. Spans must not alias output/metadata;
 * alignment, address overflow, dimensions and capacities are checked before use.
 * A malformed/overlapping pointer layout leaves metadata untouched. Otherwise
 * used/detail are cleared; -5 sets detail to the first duplicate pair's index.
 * Status: 0 success; -1 pointer; -2 invalid input; -3 bounds/capacity;
 * -4 singular; -5 coincident adjacent points; -6 division by zero.
 * Operation/layout (n=rows,d=columns,p=degree):
 * 1 open parameters: n xyz points, style 0/1/2; output n parameters.
 * 2 averaged knots: n parameters; output n+p+1 knots.
 * 3 find span: n+p+1 knots, parameter=u; output one integral double.
 * 4 basis: full knots, index=span, parameter=u; output p+1 doubles.
 * 5 collocation: n parameters followed by d+p+1 knots; output n*d row-major.
 * 6 LU: n*n matrix; output n*n LU followed by n integral permutation doubles.
 * 7 LU solve: n*n LU, n permutation doubles, n*d RHS; output n*d row-major.
 * 8 closed parameters: n xyz points; output n+1 parameters.
 * 9 periodic knots: n parameters (last closes period); output n+2*p knots.
 * 10 cyclic solve: n sub, n diagonal, n super, n*d RHS; output n*d row-major.
 * LU solve returns lists of lists in Python; cyclic solve lists of tuples.
 * All other unused scalar fields are zero. Style is only read for 1/8.
 */
#include <stddef.h>
#include <stdint.h>
typedef struct {
    uint32_t operation, style;
    uint64_t rows, columns;
    int64_t degree, index;
    double parameter;
    const double *data;
    size_t data_len;
} NarwhalInterpolateHelperRequest;
int32_t narwhal_pure_core_interpolate_helper_v1(
    const NarwhalInterpolateHelperRequest *, double *output, size_t capacity,
    uint64_t *used, uint64_t *detail);
