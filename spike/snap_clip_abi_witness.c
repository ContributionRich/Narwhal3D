/* Windows loader witness for the additive Rust clipping ABI. No application/UI. */
#include <windows.h>
#include <stdint.h>
#include <stddef.h>
#include <math.h>
#include <stdio.h>
typedef int (*query_fn)(const double *, const double *, size_t, const uint64_t *, size_t,
                       double, double, const double *, uint64_t *, double *, double *, uint8_t *);
int main(int argc, char **argv) {
    if (argc != 2) return 2;
    HMODULE module = LoadLibraryA(argv[1]);
    if (!module) { printf("LoadLibrary failed %lu\n", GetLastError()); return 3; }
    query_fn query = (query_fn)GetProcAddress(module, "narwhal_nearest_projected_polylines_clipped_f64");
    if (!query) return 4;
    double matrix[16] = {1,0,0,0, 0,1,0,0, 0,0,1,-2, 0,0,1,0};
    double cases[3][6] = {{0,-1,-1,0,1,3}, {-1,0,1,2,0,4}, {-100,0,2,100,0,2}};
    double expected[3] = {.5,1.0/3.0,.5}, mouse[2] = {400,300};
    uint64_t offsets[2] = {0,2};
    for (int i=0; i<3; ++i) {
        uint64_t index = UINT64_MAX; double t=NAN,d=INFINITY; uint8_t valid=0;
        int code = query(matrix,cases[i],2,offsets,1,800,600,mouse,&index,&t,&d,&valid);
        if (code || index || valid != 1 || fabs(t-expected[i]) > 1e-12 || d > 1e-10) return 10+i;
    }
    puts("SNAP_CLIP_WINDOWS_DLL 3/3 passed");
    FreeLibrary(module);
    return 0;
}
