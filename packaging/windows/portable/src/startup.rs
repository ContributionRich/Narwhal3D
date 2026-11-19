//! The outer EXE owns this window: the real welcome binary is still packed.
use std::{ptr::null_mut, sync::mpsc, thread, time::Duration};
use windows_sys::Win32::UI::WindowsAndMessaging::*;

pub struct Startup {
    messages: Option<mpsc::Sender<&'static str>>,
    worker: Option<thread::JoinHandle<()>>,
}

impl Startup {
    pub fn show() -> Self {
        let (send, receive) = mpsc::channel();
        let (ready, started) = mpsc::sync_channel(0);
        let worker = thread::Builder::new()
            .name("startup feedback".into())
            .spawn(move || window(receive, ready))
            .ok();
        // Do not start expensive verification until the window has been shown.
        // A failed window or thread must not prevent the app from starting.
        let _ = started.recv_timeout(Duration::from_secs(1));
        Self {
            messages: Some(send),
            worker,
        }
    }

    pub fn stage(&self, text: &'static str) {
        if let Some(send) = &self.messages {
            let _ = send.send(text);
        }
    }
}

impl Drop for Startup {
    fn drop(&mut self) {
        self.messages.take();
        if let Some(worker) = self.worker.take() {
            let _ = worker.join();
        }
    }
}

fn wide(text: &str) -> Vec<u16> {
    text.encode_utf16().chain(Some(0)).collect()
}

fn window(receive: mpsc::Receiver<&'static str>, ready: mpsc::SyncSender<()>) {
    unsafe {
        // No system menu: dismissing feedback must not look like cancelling a
        // cache transaction. All HWND operations run on the owning thread.
        let width = 480;
        let height = 120;
        let frame = CreateWindowExW(
            0,
            wide("STATIC").as_ptr(),
            wide("Narwhal 3D").as_ptr(),
            WS_POPUP | WS_CAPTION,
            (GetSystemMetrics(SM_CXSCREEN) - width).max(0) / 2,
            (GetSystemMetrics(SM_CYSCREEN) - height).max(0) / 2,
            width,
            height,
            null_mut(),
            null_mut(),
            null_mut(),
            null_mut(),
        );
        if frame.is_null() {
            return;
        }
        let label = CreateWindowExW(
            0,
            wide("STATIC").as_ptr(),
            wide("Checking the application package…").as_ptr(),
            WS_CHILD | WS_VISIBLE,
            12,
            28,
            width - 24,
            32,
            frame,
            null_mut(),
            null_mut(),
            null_mut(),
        );
        ShowWindow(frame, SW_SHOWNOACTIVATE);
        let _ = ready.send(());
        loop {
            let mut msg = MSG::default();
            while PeekMessageW(&mut msg, null_mut(), 0, 0, PM_REMOVE) != 0 {
                TranslateMessage(&msg);
                DispatchMessageW(&msg);
            }
            match receive.recv_timeout(Duration::from_millis(30)) {
                Ok(text) => {
                    SetWindowTextW(label, wide(text).as_ptr());
                }
                Err(mpsc::RecvTimeoutError::Timeout) => {}
                Err(mpsc::RecvTimeoutError::Disconnected) => break,
            }
        }
        DestroyWindow(frame);
    }
}
