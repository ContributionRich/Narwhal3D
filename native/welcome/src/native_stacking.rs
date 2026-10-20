use std::process::Command;

#[cfg(target_os = "linux")]
pub type NativeWindowId = u32;
#[cfg(not(target_os = "linux"))]
pub type NativeWindowId = ();

#[cfg(target_os = "linux")]
pub fn welcome_window_id(cc: &eframe::CreationContext<'_>) -> Option<NativeWindowId> {
    use raw_window_handle::{HasWindowHandle as _, RawWindowHandle};

    match cc.window_handle().ok()?.as_raw() {
        RawWindowHandle::Xlib(handle) => u32::try_from(handle.window).ok(),
        RawWindowHandle::Xcb(handle) => Some(handle.window.get()),
        _ => None,
    }
}

#[cfg(not(target_os = "linux"))]
pub fn welcome_window_id(_: &eframe::CreationContext<'_>) -> Option<NativeWindowId> {
    None
}

fn forced_wayland_socket(pid: u32) -> String {
    format!("narwhal3d-force-x11-{pid}")
}

#[cfg(target_os = "linux")]
pub fn configure_child(command: &mut Command, welcome: Option<NativeWindowId>) {
    if welcome.is_some() && std::env::var_os("DISPLAY").is_some() {
        // Blender probes the default Wayland socket even when WAYLAND_DISPLAY is absent. An
        // intentionally nonexistent socket makes it use X11, placing both application windows
        // on the same server so ICCCM transient ownership can be established.
        command.env("WAYLAND_DISPLAY", forced_wayland_socket(std::process::id()));
        eprintln!("[narwhal-welcome] forcing runtime onto X11 for native ownership");
    } else {
        eprintln!("[narwhal-welcome] portable stacking fallback (no X11 window handle)");
    }
}

#[cfg(not(target_os = "linux"))]
pub fn configure_child(_: &mut Command, _: Option<NativeWindowId>) {}

#[cfg(target_os = "linux")]
pub fn start_worker(welcome: Option<NativeWindowId>, runtime_pid: u32) {
    let Some(welcome) = welcome else {
        return;
    };
    eprintln!(
        "[narwhal-welcome] starting native stacking (welcome={welcome:#x}, pid={runtime_pid})"
    );
    std::thread::spawn(move || {
        if let Err(error) = x11::keep_welcome_above(welcome, runtime_pid) {
            eprintln!("[narwhal-welcome] native stacking unavailable: {error}");
        }
    });
}

#[cfg(not(target_os = "linux"))]
pub fn start_worker(_: Option<NativeWindowId>, _: u32) {}

#[cfg(target_os = "linux")]
mod x11 {
    use std::error::Error;
    use std::thread;
    use std::time::{Duration, Instant};

    use x11rb::connection::Connection as _;
    use x11rb::protocol::xproto::{
        Atom, AtomEnum, ClientMessageEvent, ConfigureWindowAux, ConnectionExt as _, EventMask,
        MapState, PropMode, StackMode, Window,
    };
    use x11rb::rust_connection::RustConnection;
    use x11rb::wrapper::ConnectionExt as _;

    type Result<T> = std::result::Result<T, Box<dyn Error + Send + Sync>>;

    struct Atoms {
        client_list: Atom,
        wm_pid: Atom,
        wm_state: Atom,
        wm_state_above: Atom,
        active_window: Atom,
        restack_window: Atom,
    }

    impl Atoms {
        fn new(connection: &RustConnection) -> Result<Self> {
            Ok(Self {
                client_list: intern(connection, b"_NET_CLIENT_LIST")?,
                wm_pid: intern(connection, b"_NET_WM_PID")?,
                wm_state: intern(connection, b"_NET_WM_STATE")?,
                wm_state_above: intern(connection, b"_NET_WM_STATE_ABOVE")?,
                active_window: intern(connection, b"_NET_ACTIVE_WINDOW")?,
                restack_window: intern(connection, b"_NET_RESTACK_WINDOW")?,
            })
        }
    }

    fn intern(connection: &RustConnection, name: &[u8]) -> Result<Atom> {
        Ok(connection.intern_atom(false, name)?.reply()?.atom)
    }

    fn property_window(
        connection: &RustConnection,
        window: Window,
        property: Atom,
    ) -> Option<Window> {
        connection
            .get_property(false, window, property, AtomEnum::WINDOW, 0, 1)
            .ok()?
            .reply()
            .ok()?
            .value32()?
            .next()
    }

    fn runtime_window(
        connection: &RustConnection,
        root: Window,
        atoms: &Atoms,
        runtime_pid: u32,
    ) -> Option<Window> {
        let clients = connection
            .get_property(
                false,
                root,
                atoms.client_list,
                AtomEnum::WINDOW,
                0,
                u32::MAX,
            )
            .ok()?
            .reply()
            .ok()?;
        let candidate = clients
            .value32()?
            .filter_map(|window| {
                let pid = connection
                    .get_property(false, window, atoms.wm_pid, AtomEnum::CARDINAL, 0, 1)
                    .ok()?
                    .reply()
                    .ok()?
                    .value32()?
                    .next()?;
                if pid != runtime_pid {
                    return None;
                }
                let attributes = connection
                    .get_window_attributes(window)
                    .ok()?
                    .reply()
                    .ok()?;
                if attributes.map_state != MapState::VIEWABLE {
                    return None;
                }
                let geometry = connection.get_geometry(window).ok()?.reply().ok()?;
                let area = u64::from(geometry.width) * u64::from(geometry.height);
                Some((area, window))
            })
            .max_by_key(|(area, _)| *area)
            .map(|(_, window)| window);
        candidate
    }

    fn send_ewmh(
        connection: &RustConnection,
        root: Window,
        window: Window,
        message_type: Atom,
        data: [u32; 5],
    ) -> Result<()> {
        connection
            .send_event(
                false,
                root,
                EventMask::SUBSTRUCTURE_REDIRECT | EventMask::SUBSTRUCTURE_NOTIFY,
                ClientMessageEvent::new(32, window, message_type, data),
            )?
            .check()?;
        Ok(())
    }

    fn establish(
        connection: &RustConnection,
        root: Window,
        atoms: &Atoms,
        welcome: Window,
        runtime: Window,
        request_focus: bool,
    ) -> Result<()> {
        connection
            .change_property32(
                PropMode::REPLACE,
                welcome,
                AtomEnum::WM_TRANSIENT_FOR,
                AtomEnum::WINDOW,
                &[runtime],
            )?
            .check()?;
        send_ewmh(
            connection,
            root,
            welcome,
            atoms.wm_state,
            [1, atoms.wm_state_above, 0, 1, 0],
        )?;
        send_ewmh(
            connection,
            root,
            welcome,
            atoms.restack_window,
            [1, runtime, u32::from(StackMode::ABOVE), 0, 0],
        )?;
        if request_focus {
            let previous = property_window(connection, root, atoms.active_window).unwrap_or(0);
            send_ewmh(
                connection,
                root,
                welcome,
                atoms.active_window,
                [1, x11rb::CURRENT_TIME, previous, 0, 0],
            )?;
        }
        // A direct raise is a best-effort fallback. The EWMH request above remains authoritative
        // because reparenting window managers may reject sibling-based ConfigureWindow requests.
        if let Ok(cookie) = connection.configure_window(
            welcome,
            &ConfigureWindowAux::new().stack_mode(StackMode::ABOVE),
        ) {
            let _ = cookie.check();
        }
        connection.flush()?;
        Ok(())
    }

    pub fn keep_welcome_above(welcome: Window, runtime_pid: u32) -> Result<()> {
        let (connection, _) = x11rb::connect(None)?;
        let root = connection.query_tree(welcome)?.reply()?.root;
        let atoms = Atoms::new(&connection)?;
        let deadline = Instant::now() + Duration::from_secs(10);
        let runtime = loop {
            if let Some(window) = runtime_window(&connection, root, &atoms, runtime_pid) {
                break window;
            }
            if Instant::now() >= deadline {
                return Err("runtime window did not map before the stacking timeout".into());
            }
            thread::sleep(Duration::from_millis(25));
        };

        establish(&connection, root, &atoms, welcome, runtime, true)?;
        for delay in [50, 150, 300] {
            thread::sleep(Duration::from_millis(delay));
            establish(&connection, root, &atoms, welcome, runtime, false)?;
        }
        eprintln!(
            "[narwhal-welcome] transient stacking established (welcome={welcome:#x}, runtime={runtime:#x})"
        );
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::forced_wayland_socket;

    #[test]
    fn forced_socket_is_process_specific_and_non_default() {
        assert_eq!(forced_wayland_socket(42), "narwhal3d-force-x11-42");
        assert_ne!(forced_wayland_socket(42), "wayland-0");
    }
}
