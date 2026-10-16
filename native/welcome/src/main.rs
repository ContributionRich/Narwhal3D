mod native_stacking;

use eframe::egui::{self, Color32, RichText, Stroke, Vec2};
use serde_json::Value;
use std::fs;
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicI32, Ordering};
use std::sync::mpsc::{self, Receiver, Sender};
use std::sync::Arc;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

const READY_MARKER: &str = "[narwhal3d] first presentation released (ui-ready)";
const WINDOW_SIZE: [f32; 2] = [580.0, 438.0];
const MIN_VISIBLE_SECONDS: f32 = 1.8;
const FADE_SECONDS: f32 = 0.38;
const ATTENTION_GRACE_SECONDS: f32 = 0.75;
const EXIT_PENDING: i32 = i32::MIN;
const NEW_UNITS: [(&str, &str); 5] = [
    ("Millimeters", "Millimeters"),
    ("Centimeters", "Centimeters"),
    ("Meters", "Meters"),
    ("Inches", "Inches"),
    ("Feet", "Feet"),
];

#[derive(Clone, Copy, PartialEq)]
enum Tab {
    New,
    Recent,
    Open,
    About,
}

enum RuntimeEvent {
    Ready,
    Exited(i32),
    Failed(String),
}

struct LaunchConfig {
    runtime: PathBuf,
    bootstrap: PathBuf,
    forwarded: Vec<String>,
    request: PathBuf,
}

struct WelcomeApp {
    tab: Tab,
    recent: Vec<PathBuf>,
    request: PathBuf,
    events: Receiver<RuntimeEvent>,
    sender: Sender<RuntimeEvent>,
    launch: Option<LaunchConfig>,
    native_window: Option<native_stacking::NativeWindowId>,
    cancel_child: Arc<AtomicBool>,
    exit_code: Arc<AtomicI32>,
    started: Instant,
    first_paint: Option<Instant>,
    lifetime: WelcomeLifetime,
    hidden: bool,
    ready: bool,
    child_exited: bool,
    status: String,
    logo: egui::TextureHandle,
}

fn data_dir() -> PathBuf {
    if let Some(path) = std::env::var_os("NARWHAL3D_DATA_DIR") {
        return PathBuf::from(path);
    }
    let home = std::env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."));
    home.join(".local").join("share").join("narwhal3d")
}

fn request_path() -> PathBuf {
    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    std::env::temp_dir().join(format!(
        "narwhal3d-welcome-{}-{stamp}.request",
        std::process::id()
    ))
}

fn parse_args_from(args: impl IntoIterator<Item = String>) -> Result<LaunchConfig, String> {
    let mut args = args.into_iter();
    let mut runtime = None;
    let mut bootstrap = None;
    let mut forwarded = Vec::new();
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--runtime" => runtime = args.next().map(PathBuf::from),
            "--bootstrap" => bootstrap = args.next().map(PathBuf::from),
            "--" => {
                forwarded.extend(args);
                break;
            }
            _ => return Err(format!("unknown welcome argument: {arg}")),
        }
    }
    Ok(LaunchConfig {
        runtime: runtime.ok_or("--runtime is required")?,
        bootstrap: bootstrap.ok_or("--bootstrap is required")?,
        forwarded,
        request: request_path(),
    })
}

fn parse_args() -> Result<LaunchConfig, String> {
    parse_args_from(std::env::args().skip(1))
}

fn recent_files_from(path: &Path) -> Vec<PathBuf> {
    let Ok(text) = fs::read_to_string(path) else {
        return Vec::new();
    };
    let Ok(value) = serde_json::from_str::<Value>(&text) else {
        return Vec::new();
    };
    let items = value
        .get("items")
        .and_then(Value::as_array)
        .or_else(|| value.as_array());
    items
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(PathBuf::from)
        .filter(|path| path.is_file())
        .take(8)
        .collect()
}

fn recent_files() -> Vec<PathBuf> {
    recent_files_from(&data_dir().join("recent.json"))
}

fn replace_request(temporary: &Path, request: &Path) -> std::io::Result<()> {
    // Unix rename replaces atomically. Windows rename refuses an existing destination, so remove
    // it first; the Python consumer treats that brief missing-path claim race as retryable.
    #[cfg(target_os = "windows")]
    match fs::remove_file(request) {
        Ok(()) => {}
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(error) => return Err(error),
    }
    fs::rename(temporary, request)
}

fn request_payload(action: &str, argument: Option<&str>) -> String {
    let mut payload = format!("{action}\n");
    if let Some(argument) = argument {
        payload.push_str(argument);
        payload.push('\n');
    }
    payload
}

fn is_ready_line(line: &str) -> bool {
    line == READY_MARKER
}

fn progress_state(ready: bool, elapsed: Duration) -> (f32, bool) {
    if ready {
        (1.0, false)
    } else {
        (((elapsed.as_secs_f32() * 0.55) % 1.0).max(0.04), true)
    }
}

#[derive(Default)]
struct WelcomeLifetime {
    last_attention: Option<Instant>,
    fade_started: Option<Instant>,
    engaged: bool,
    dismissed: bool,
}

impl WelcomeLifetime {
    fn attention_active(&self, now: Instant) -> bool {
        self.last_attention.is_some_and(|attention| {
            now.saturating_duration_since(attention)
                < Duration::from_secs_f32(ATTENTION_GRACE_SECONDS)
        })
    }

    fn note_attention(&mut self, now: Instant, interaction_started: bool) {
        if self.dismissed {
            return;
        }
        self.last_attention = Some(now);
        self.engaged |= interaction_started;
        self.fade_started = None;
    }

    fn maybe_start_auto_fade(&mut self, ready: bool, visible_for: Duration, now: Instant) {
        if ready
            && self.fade_started.is_none()
            && !self.engaged
            && !self.dismissed
            && !self.attention_active(now)
            && visible_for >= Duration::from_secs_f32(MIN_VISIBLE_SECONDS)
        {
            self.fade_started = Some(now);
        }
    }

    fn dismiss(&mut self) {
        self.dismissed = true;
    }

    fn fade_alpha(&self, now: Instant) -> f32 {
        if self.dismissed {
            return 0.0;
        }
        self.fade_started
            .map(|started| {
                (1.0 - now.saturating_duration_since(started).as_secs_f32() / FADE_SECONDS)
                    .clamp(0.0, 1.0)
            })
            .unwrap_or(1.0)
    }
}

fn load_logo(cc: &eframe::CreationContext<'_>) -> egui::TextureHandle {
    let bytes = include_bytes!("../../../launch/narwhal3d.png");
    let decoded = image::load_from_memory(bytes)
        .expect("embedded Narwhal icon must decode")
        .to_rgba8();
    let size = [decoded.width() as usize, decoded.height() as usize];
    let image = egui::ColorImage::from_rgba_unmultiplied(size, decoded.as_raw());
    cc.egui_ctx
        .load_texture("narwhal-logo", image, egui::TextureOptions::LINEAR)
}

fn supervise_child(
    mut child: Child,
    sender: Sender<RuntimeEvent>,
    cancel_child: Arc<AtomicBool>,
    exit_code: Arc<AtomicI32>,
) {
    loop {
        if cancel_child.load(Ordering::Acquire) {
            let _ = child.kill();
            let code = child
                .wait()
                .ok()
                .and_then(|status| status.code())
                .unwrap_or(1);
            exit_code.store(code, Ordering::Release);
            let _ = sender.send(RuntimeEvent::Exited(code));
            break;
        }
        match child.try_wait() {
            Ok(Some(status)) => {
                let code = status.code().unwrap_or(1);
                exit_code.store(code, Ordering::Release);
                let _ = sender.send(RuntimeEvent::Exited(code));
                break;
            }
            Ok(None) => std::thread::sleep(Duration::from_millis(50)),
            Err(error) => {
                let _ = child.kill();
                let _ = child.wait();
                exit_code.store(1, Ordering::Release);
                let _ = sender.send(RuntimeEvent::Failed(format!(
                    "Narwhal runtime failed: {error}"
                )));
                break;
            }
        }
    }
}

fn spawn_runtime(
    config: &LaunchConfig,
    sender: Sender<RuntimeEvent>,
    native_window: Option<native_stacking::NativeWindowId>,
    cancel_child: Arc<AtomicBool>,
    exit_code: Arc<AtomicI32>,
) -> Result<(), String> {
    let mut command = Command::new(&config.runtime);
    command
        .arg("--factory-startup")
        .arg("--python")
        .arg(&config.bootstrap)
        .arg("--")
        .args(&config.forwarded)
        .env("NARWHAL3D_DEFER_FIRST_PRESENT", "1")
        .env_remove("NARWHAL3D_UI_READY")
        .env_remove("NARWHAL3D_PRESENT_WATCHDOG")
        .env("NARWHAL3D_WELCOME_REQUEST", &config.request)
        .stdout(Stdio::inherit())
        .stderr(Stdio::piped());
    native_stacking::configure_child(&mut command, native_window);
    let mut child = command
        .spawn()
        .map_err(|error| format!("could not start Narwhal: {error}"))?;
    native_stacking::start_worker(native_window, child.id());
    let stderr = child
        .stderr
        .take()
        .ok_or("could not monitor Narwhal startup")?;
    let ready_sender = sender.clone();
    std::thread::spawn(move || {
        let reader = BufReader::new(stderr);
        for line in reader.lines().map_while(Result::ok) {
            eprintln!("{line}");
            if is_ready_line(&line) {
                let _ = ready_sender.send(RuntimeEvent::Ready);
            }
        }
    });
    std::thread::spawn(move || supervise_child(child, sender, cancel_child, exit_code));
    Ok(())
}

impl WelcomeApp {
    fn new(
        cc: &eframe::CreationContext<'_>,
        config: LaunchConfig,
        cancel_child: Arc<AtomicBool>,
        exit_code: Arc<AtomicI32>,
    ) -> Self {
        let (sender, events) = mpsc::channel();
        Self {
            tab: Tab::Recent,
            recent: recent_files(),
            request: config.request.clone(),
            events,
            sender,
            launch: Some(config),
            native_window: native_stacking::welcome_window_id(cc),
            cancel_child,
            exit_code,
            started: Instant::now(),
            first_paint: None,
            lifetime: WelcomeLifetime::default(),
            hidden: false,
            ready: false,
            child_exited: false,
            status: "Preparing Narwhal 3D…".to_owned(),
            logo: load_logo(cc),
        }
    }

    fn send_request(&mut self, action: &str, argument: Option<&str>) {
        let temporary = self.request.with_extension("tmp");
        let result = (|| -> std::io::Result<()> {
            let mut file = fs::File::create(&temporary)?;
            file.write_all(request_payload(action, argument).as_bytes())?;
            file.sync_all()?;
            replace_request(&temporary, &self.request)?;
            Ok(())
        })();
        self.status = match result {
            Ok(()) if action == "open" => "Opening selected model…".to_owned(),
            Ok(()) if action == "open-dialog" => "Opening file browser…".to_owned(),
            Ok(()) => "Creating a new model…".to_owned(),
            Err(error) => format!("Could not send startup request: {error}"),
        };
    }

    fn mark_closed(&self) {
        if let Err(error) = fs::write(self.request.with_extension("closed"), b"") {
            eprintln!("[narwhal-welcome] could not close request channel: {error}");
        }
    }

    fn hide_welcome(&mut self, ctx: &egui::Context) {
        if self.hidden {
            return;
        }
        self.mark_closed();
        self.hidden = true;
        ctx.send_viewport_cmd(egui::ViewportCommand::Visible(false));
    }

    fn poll_runtime(&mut self, ctx: &egui::Context) {
        while let Ok(event) = self.events.try_recv() {
            match event {
                RuntimeEvent::Ready => {
                    self.status = "Ready".to_owned();
                    self.ready = true;
                }
                RuntimeEvent::Exited(code) => {
                    self.child_exited = true;
                    if self.lifetime.fade_started.is_none() {
                        self.status = format!("Narwhal closed during startup (status {code})");
                    }
                }
                RuntimeEvent::Failed(error) => {
                    self.child_exited = true;
                    self.status = error;
                }
            }
        }
        if self.child_exited {
            ctx.send_viewport_cmd(egui::ViewportCommand::Close);
        }
    }

    fn alpha(&mut self, ctx: &egui::Context, now: Instant) -> f32 {
        let alpha = self.lifetime.fade_alpha(now);
        if alpha <= 0.0 {
            self.hide_welcome(ctx);
        }
        ctx.request_repaint_after(Duration::from_millis(16));
        alpha
    }

    fn top_tabs(&mut self, ui: &mut egui::Ui, alpha: u8) {
        let tabs = [
            (Tab::New, "New"),
            (Tab::Recent, "Recent"),
            (Tab::Open, "Open…"),
            (Tab::About, "About"),
        ];
        ui.horizontal(|ui| {
            for (tab, label) in tabs {
                let selected = self.tab == tab;
                let text = RichText::new(label).size(10.5).color(if selected {
                    Color32::from_white_alpha(alpha)
                } else {
                    Color32::from_black_alpha(alpha)
                });
                let button = egui::Button::new(text)
                    .fill(if selected {
                        Color32::from_rgba_unmultiplied(24, 93, 133, alpha)
                    } else {
                        Color32::TRANSPARENT
                    })
                    .stroke(Stroke::NONE)
                    .min_size(Vec2::new(72.0, 24.0));
                if ui.add(button).clicked() {
                    self.tab = tab;
                }
            }
            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                let close = egui::Button::new(RichText::new("×").size(17.0))
                    .min_size(Vec2::new(28.0, 24.0))
                    .frame(false);
                if ui
                    .add(close)
                    .on_hover_text("Close welcome window")
                    .clicked()
                {
                    self.lifetime.dismiss();
                    self.hide_welcome(ui.ctx());
                }
                ui.label(
                    RichText::new("EARLY PREVIEW")
                        .size(8.0)
                        .color(Color32::from_black_alpha(alpha)),
                );
            });
        });
    }

    fn recent_grid(&mut self, ui: &mut egui::Ui, alpha: u8) {
        if self.recent.is_empty() {
            ui.vertical_centered(|ui| {
                ui.add_space(90.0);
                ui.label(
                    RichText::new("No recent models yet")
                        .size(12.0)
                        .color(Color32::from_black_alpha(alpha)),
                );
                ui.label("Use Open… to choose a .3dm model.");
            });
            return;
        }
        let recent = self.recent.clone();
        egui::Grid::new("recent-grid")
            .num_columns(4)
            .spacing([8.0, 9.0])
            .show(ui, |ui| {
                for (index, path) in recent.iter().enumerate() {
                    let name = path
                        .file_name()
                        .and_then(|value| value.to_str())
                        .unwrap_or("Model.3dm");
                    ui.vertical(|ui| {
                        let preview = egui::Button::new(
                            RichText::new("NARWHAL\n3DM")
                                .size(8.5)
                                .strong()
                                .color(Color32::from_rgba_unmultiplied(225, 241, 247, alpha)),
                        )
                        .fill(Color32::from_rgba_unmultiplied(99, 118, 132, alpha))
                        .stroke(Stroke::new(1.0, Color32::from_black_alpha(alpha)))
                        .min_size(Vec2::new(76.0, 70.0));
                        if ui
                            .add(preview)
                            .on_hover_text(path.display().to_string())
                            .clicked()
                        {
                            self.send_request("open", Some(path.to_string_lossy().as_ref()));
                        }
                        ui.add(
                            egui::Label::new(
                                RichText::new(name)
                                    .size(7.5)
                                    .color(Color32::from_black_alpha(alpha)),
                            )
                            .truncate(),
                        );
                    });
                    if index % 4 == 3 {
                        ui.end_row();
                    }
                }
            });
    }

    fn new_panel(&mut self, ui: &mut egui::Ui, alpha: u8) {
        ui.heading(RichText::new("Start a new model").color(Color32::from_black_alpha(alpha)));
        ui.label("Choose the document unit system:");
        ui.add_space(12.0);
        for (label, unit) in NEW_UNITS {
            if ui
                .add_sized([190.0, 31.0], egui::Button::new(label))
                .clicked()
            {
                self.send_request("new", Some(unit));
            }
            ui.add_space(7.0);
        }
    }

    fn open_panel(&mut self, ui: &mut egui::Ui, alpha: u8) {
        ui.heading(RichText::new("Open a model").color(Color32::from_black_alpha(alpha)));
        ui.label("Open a .3dm or another supported model file.");
        ui.add_space(18.0);
        if ui
            .add_sized([190.0, 31.0], egui::Button::new("Choose a model…"))
            .clicked()
        {
            self.send_request("open-dialog", None);
        }
    }
}

impl Drop for WelcomeApp {
    fn drop(&mut self) {
        self.cancel_child.store(true, Ordering::Release);
    }
}

impl eframe::App for WelcomeApp {
    fn clear_color(&self, _visuals: &egui::Visuals) -> [f32; 4] {
        let alpha = self.lifetime.fade_alpha(Instant::now());
        [0.97 * alpha, 0.97 * alpha, 0.97 * alpha, alpha]
    }

    fn logic(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        self.poll_runtime(ctx);
        if self
            .first_paint
            .is_some_and(|painted| painted.elapsed() >= Duration::from_millis(350))
        {
            if let Some(config) = self.launch.take() {
                self.status = match spawn_runtime(
                    &config,
                    self.sender.clone(),
                    self.native_window,
                    Arc::clone(&self.cancel_child),
                    Arc::clone(&self.exit_code),
                ) {
                    Ok(()) => "Loading application components…".to_owned(),
                    Err(error) => {
                        self.exit_code.store(1, Ordering::Release);
                        self.child_exited = true;
                        error
                    }
                };
            }
        }

        let now = Instant::now();
        let visible_for = self
            .first_paint
            .map(|painted| now.saturating_duration_since(painted))
            .unwrap_or_default();
        self.lifetime
            .maybe_start_auto_fade(self.ready, visible_for, now);
        if self.hidden {
            ctx.request_repaint_after(Duration::from_millis(100));
        }
    }

    fn ui(&mut self, ui: &mut egui::Ui, _frame: &mut eframe::Frame) {
        self.first_paint.get_or_insert_with(Instant::now);
        let ctx = ui.ctx().clone();
        let now = Instant::now();
        let (pointer_over, interaction_started) = ctx.input(|input| {
            let interacted = input.events.iter().any(|event| {
                matches!(
                    event,
                    egui::Event::PointerMoved(_)
                        | egui::Event::MouseMoved(_)
                        | egui::Event::PointerButton { .. }
                        | egui::Event::MouseWheel { .. }
                        | egui::Event::Touch { .. }
                        | egui::Event::Key { .. }
                        | egui::Event::Text(_)
                        | egui::Event::Paste(_)
                )
            });
            (input.pointer.hover_pos().is_some(), interacted)
        });
        if pointer_over || interaction_started {
            self.lifetime.note_attention(now, interaction_started);
        }
        let alpha_f = self.alpha(&ctx, now);
        if self.hidden {
            return;
        }
        let alpha = (alpha_f * 255.0) as u8;
        ui.set_opacity(alpha_f);
        ctx.set_visuals(egui::Visuals::light());
        egui::Frame::new()
            .fill(Color32::from_rgba_unmultiplied(247, 248, 249, alpha))
            .stroke(Stroke::new(1.0, Color32::from_rgba_unmultiplied(85, 92, 100, alpha)))
            .show(ui, |ui| {
                self.top_tabs(ui, alpha);
                ui.separator();
                ui.horizontal(|ui| {
                    ui.vertical(|ui| {
                        ui.set_width(400.0);
                        ui.add_space(12.0);
                        match self.tab {
                            Tab::New => self.new_panel(ui, alpha),
                            Tab::Recent => self.recent_grid(ui, alpha),
                            Tab::Open => self.open_panel(ui, alpha),
                            Tab::About => {
                                ui.heading("Narwhal 3D");
                                ui.label("Early precision NURBS CAD preview");
                                ui.label("Open-source, independently developed, and still under active construction.");
                            }
                        }
                    });
                    ui.separator();
                    ui.vertical(|ui| {
                        ui.set_width(155.0);
                        ui.heading("News");
                        ui.label("Rust acceleration and startup work are in progress.");
                        ui.add_space(35.0);
                        ui.heading("Tips");
                        ui.label("Use the viewport-title arrow to change display mode.");
                        ui.add_space(50.0);
                        ui.image((self.logo.id(), Vec2::splat(85.0)));
                        ui.label(RichText::new("Narwhal 3D").size(16.0).strong().color(Color32::from_rgba_unmultiplied(24, 93, 133, alpha)));
                    });
                });
                ui.with_layout(egui::Layout::bottom_up(egui::Align::LEFT), |ui| {
                    let (progress, animate) = progress_state(self.ready, self.started.elapsed());
                    ui.add(
                        egui::ProgressBar::new(progress)
                            .animate(animate)
                            .desired_width(ui.available_width()),
                    );
                    ui.label(RichText::new(&self.status).color(Color32::from_black_alpha(alpha)));
                });
            });
        ctx.request_repaint_after(Duration::from_millis(33));
    }
}

fn main() -> eframe::Result {
    #[cfg(target_os = "linux")]
    if std::env::var_os("DISPLAY").is_some() {
        // winit 0.30 chooses Wayland whenever its display variables are present; its old
        // WINIT_UNIX_BACKEND override no longer exists. Use X11 for this process so the
        // welcome and runtime windows can have a real ICCCM transient relationship.
        std::env::remove_var("WAYLAND_DISPLAY");
        std::env::remove_var("WAYLAND_SOCKET");
    }
    let config = match parse_args() {
        Ok(config) => config,
        Err(error) => {
            eprintln!("narwhal-welcome: {error}");
            std::process::exit(2);
        }
    };
    let options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default()
            .with_title("Narwhal 3D")
            .with_inner_size(WINDOW_SIZE)
            .with_min_inner_size(WINDOW_SIZE)
            .with_max_inner_size(WINDOW_SIZE)
            .with_resizable(false)
            .with_decorations(false)
            .with_transparent(true)
            .with_always_on_top(),
        centered: true,
        ..Default::default()
    };
    let cancel_child = Arc::new(AtomicBool::new(false));
    let exit_code = Arc::new(AtomicI32::new(EXIT_PENDING));
    let app_cancel = Arc::clone(&cancel_child);
    let app_exit = Arc::clone(&exit_code);
    eframe::run_native(
        "Narwhal 3D",
        options,
        Box::new(move |cc| Ok(Box::new(WelcomeApp::new(cc, config, app_cancel, app_exit)))),
    )?;
    cancel_child.store(true, Ordering::Release);
    for _ in 0..40 {
        if exit_code.load(Ordering::Acquire) != EXIT_PENDING {
            break;
        }
        std::thread::sleep(Duration::from_millis(25));
    }
    let code = exit_code.load(Ordering::Acquire);
    if code != EXIT_PENDING && code != 0 {
        std::process::exit(code);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{
        is_ready_line, parse_args_from, progress_state, recent_files_from, replace_request,
        request_payload, supervise_child, RuntimeEvent, WelcomeLifetime, ATTENTION_GRACE_SECONDS,
        EXIT_PENDING, NEW_UNITS, READY_MARKER,
    };
    use std::fs;
    use std::path::Path;
    use std::sync::atomic::{AtomicBool, AtomicI32, Ordering};
    use std::sync::{mpsc, Arc};
    use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

    fn temporary_dir(label: &str) -> std::path::PathBuf {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let path = std::env::temp_dir().join(format!(
            "narwhal-welcome-test-{label}-{}-{stamp}",
            std::process::id()
        ));
        fs::create_dir_all(&path).unwrap();
        path
    }

    #[test]
    fn parser_forwards_only_arguments_after_separator() {
        let config = parse_args_from(
            [
                "--runtime",
                "/runtime",
                "--bootstrap",
                "/bootstrap.py",
                "--",
                "a.3dm",
                "--flag",
            ]
            .into_iter()
            .map(str::to_owned),
        )
        .unwrap();
        assert_eq!(config.runtime, Path::new("/runtime"));
        assert_eq!(config.bootstrap, Path::new("/bootstrap.py"));
        assert_eq!(config.forwarded, ["a.3dm", "--flag"]);
        assert!(parse_args_from(["unexpected".to_owned()]).is_err());
    }

    #[test]
    fn readiness_marker_must_match_the_complete_line() {
        assert!(is_ready_line(READY_MARKER));
        assert!(!is_ready_line(&format!("prefix {READY_MARKER}")));
        assert!(!is_ready_line(
            "[narwhal3d] first presentation released (watchdog)"
        ));
    }

    #[test]
    fn lifetime_holds_for_attention_and_manual_close_is_authoritative() {
        let start = Instant::now();
        let mut lifetime = WelcomeLifetime::default();

        lifetime.maybe_start_auto_fade(false, Duration::from_secs(10), start);
        assert!(lifetime.fade_started.is_none());
        lifetime.maybe_start_auto_fade(true, Duration::from_millis(1799), start);
        assert!(lifetime.fade_started.is_none());
        lifetime.maybe_start_auto_fade(true, Duration::from_millis(1800), start);
        assert_eq!(lifetime.fade_started, Some(start));

        let reentered = start + Duration::from_millis(100);
        lifetime.note_attention(reentered, false);
        assert_eq!(lifetime.last_attention, Some(reentered));
        assert!(!lifetime.engaged);
        assert!(lifetime.fade_started.is_none());
        lifetime.maybe_start_auto_fade(true, Duration::from_secs(10), reentered);
        assert!(lifetime.fade_started.is_none());

        let after_grace =
            reentered + Duration::from_secs_f32(ATTENTION_GRACE_SECONDS) + Duration::from_millis(1);
        lifetime.maybe_start_auto_fade(true, Duration::from_secs(10), after_grace);
        assert_eq!(lifetime.fade_started, Some(after_grace));

        let interacted = after_grace + Duration::from_millis(50);
        lifetime.note_attention(interacted, true);
        assert!(lifetime.engaged);
        assert!(lifetime.fade_started.is_none());
        lifetime.maybe_start_auto_fade(
            true,
            Duration::from_secs(30),
            interacted + Duration::from_secs(30),
        );
        assert!(lifetime.fade_started.is_none());

        lifetime.dismiss();
        assert!(lifetime.dismissed);
        lifetime.note_attention(interacted + Duration::from_secs(31), true);
        assert!(lifetime.dismissed && lifetime.engaged);
        assert_eq!(lifetime.fade_alpha(interacted), 0.0);
    }

    #[test]
    fn request_publisher_replaces_an_existing_choice_portably() {
        let root = temporary_dir("publisher");
        let request = root.join("welcome.request");
        let temporary = root.join("welcome.tmp");
        fs::write(&request, "old\n").unwrap();
        fs::write(&temporary, "new\n").unwrap();
        replace_request(&temporary, &request).unwrap();
        assert_eq!(fs::read_to_string(&request).unwrap(), "new\n");
        assert!(!temporary.exists());
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn request_payload_and_new_units_are_line_oriented_and_exact() {
        assert_eq!(request_payload("new", None), "new\n");
        assert_eq!(request_payload("new", Some("Inches")), "new\nInches\n");
        assert_eq!(
            request_payload("open", Some("/models/Clock face.3dm")),
            "open\n/models/Clock face.3dm\n"
        );
        assert_eq!(
            NEW_UNITS,
            [
                ("Millimeters", "Millimeters"),
                ("Centimeters", "Centimeters"),
                ("Meters", "Meters"),
                ("Inches", "Inches"),
                ("Feet", "Feet"),
            ]
        );
    }

    #[test]
    fn ready_progress_is_complete_and_not_animated() {
        assert_eq!(progress_state(true, Duration::from_secs(10)), (1.0, false));
        let (progress, animate) = progress_state(false, Duration::from_millis(500));
        assert!((0.04..1.0).contains(&progress));
        assert!(animate);
    }

    #[cfg(unix)]
    #[test]
    fn supervisor_reports_a_natural_nonzero_child_exit() {
        let child = std::process::Command::new("sh")
            .args(["-c", "exit 7"])
            .spawn()
            .unwrap();
        let (sender, events) = mpsc::channel();
        let cancel = Arc::new(AtomicBool::new(false));
        let code = Arc::new(AtomicI32::new(EXIT_PENDING));
        supervise_child(child, sender, cancel, Arc::clone(&code));
        assert!(matches!(events.recv().unwrap(), RuntimeEvent::Exited(7)));
        assert_eq!(code.load(Ordering::Acquire), 7);
    }

    #[cfg(unix)]
    #[test]
    fn supervisor_kills_and_reaps_an_abandoned_child() {
        let child = std::process::Command::new("sh")
            .args(["-c", "exec sleep 30"])
            .spawn()
            .unwrap();
        let (sender, events) = mpsc::channel();
        let cancel = Arc::new(AtomicBool::new(true));
        let code = Arc::new(AtomicI32::new(EXIT_PENDING));
        supervise_child(child, sender, cancel, Arc::clone(&code));
        assert!(matches!(events.recv().unwrap(), RuntimeEvent::Exited(_)));
        assert_ne!(code.load(Ordering::Acquire), EXIT_PENDING);
    }

    #[test]
    fn recent_reader_tolerates_missing_and_corrupt_json_and_filters_files() {
        let root = temporary_dir("recent");
        let recent = root.join("recent.json");
        assert!(recent_files_from(&recent).is_empty());
        fs::write(&recent, "not json").unwrap();
        assert!(recent_files_from(&recent).is_empty());
        let model = root.join("kept.3dm");
        fs::write(&model, b"model").unwrap();
        let missing = root.join("missing.3dm");
        fs::write(
            &recent,
            serde_json::json!({"items": [model, missing]}).to_string(),
        )
        .unwrap();
        assert_eq!(recent_files_from(&recent), [root.join("kept.3dm")]);
        fs::remove_dir_all(root).unwrap();
    }
}
