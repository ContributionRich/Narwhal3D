//! Thread-local allocator receipts; test build only, never part of the ABI.
use std::alloc::{GlobalAlloc, Layout, System};
use std::cell::Cell;
thread_local! { static COUNTS: Cell<Option<(usize, usize)>> = const { Cell::new(None) }; }
struct Counter;
#[global_allocator]
static ALLOCATOR: Counter = Counter;
fn record(bytes: usize) {
    let _ = COUNTS.try_with(|v| {
        if let Some((count, total)) = v.get() {
            v.set(Some((count + 1, total + bytes)));
        }
    });
}
unsafe impl GlobalAlloc for Counter {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        record(layout.size());
        System.alloc(layout)
    }
    unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
        System.dealloc(ptr, layout)
    }
    unsafe fn realloc(&self, ptr: *mut u8, layout: Layout, size: usize) -> *mut u8 {
        record(size);
        System.realloc(ptr, layout, size)
    }
}
#[test]
fn allocation_profile() {
    use super::*;
    let mut state = State::default();
    let input = vec![65; 100000];
    unsafe {
        assert_eq!(
            narwhal_editor_edit(0, 0, 0, 0, input.as_ptr(), input.len(), &mut state, 88),
            0
        );
        let handle = state.handle;
        COUNTS.with(|c| c.set(Some((0, 0))));
        for _ in 0..1000 {
            assert_eq!(
                narwhal_editor_edit(handle, 6, -1, 0, std::ptr::null(), 0, &mut state, 88),
                0
            );
        }
        let movement = COUNTS.with(|c| c.replace(None).unwrap());
        assert_eq!(movement, (0, 0));
        COUNTS.with(|c| c.set(Some((0, 0))));
        for _ in 0..100 {
            assert_eq!(
                narwhal_editor_edit(handle, 2, 0, 0, [90].as_ptr(), 1, &mut state, 88),
                0
            );
            assert_eq!(
                narwhal_editor_edit(handle, 3, 0, 0, std::ptr::null(), 0, &mut state, 88),
                0
            );
        }
        let typing = COUNTS.with(|c| c.replace(None).unwrap());
        println!("native allocation calls/requested bytes: 1000 moves {movement:?}; 100 insert/delete pairs {typing:?}");
        narwhal_editor_close(handle);
    }
}
