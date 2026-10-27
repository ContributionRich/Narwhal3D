//! Welcome-only projection of narwhal3d/design.py; no application state or resources needed.
use eframe::egui::{self, Color32, Stroke};
use std::ffi::OsString;
use std::path::{Path, PathBuf};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Theme {
    Light,
    Dark,
}

fn parse(value: &str) -> Option<Theme> {
    match value.trim().to_ascii_lowercase().as_str() {
        "light" => Some(Theme::Light),
        "dark" => Some(Theme::Dark),
        _ => None,
    }
}

fn settings_path(os: &str, env: &impl Fn(&str) -> Option<OsString>) -> Option<PathBuf> {
    let path = |key| {
        env(key)
            .filter(|value| !value.is_empty())
            .map(PathBuf::from)
    };
    if let Some(explicit) = path("NARWHAL3D_SETTINGS_FILE") {
        return Some(explicit);
    }
    match os {
        "windows" => path("APPDATA").map(|p| p.join("Narwhal3D/preferences.json")),
        "macos" => {
            path("HOME").map(|p| p.join("Library/Application Support/Narwhal3D/preferences.json"))
        }
        _ => path("XDG_CONFIG_HOME")
            .or_else(|| path("HOME").map(|p| p.join(".config")))
            .map(|p| p.join("narwhal3d/preferences.json")),
    }
}

fn load_from(
    os: &str,
    env: impl Fn(&str) -> Option<OsString>,
    read: impl FnOnce(&Path) -> std::io::Result<Vec<u8>>,
) -> Theme {
    if let Some(theme) = env("NARWHAL3D_THEME").and_then(|value| value.to_str().and_then(parse)) {
        return theme;
    }
    settings_path(os, &env)
        .and_then(|path| read(&path).ok())
        .and_then(|bytes| serde_json::from_slice::<serde_json::Value>(&bytes).ok())
        .and_then(|value| value.get("theme")?.as_str().and_then(parse))
        .unwrap_or(Theme::Light)
}

#[derive(Clone, Copy, Debug)]
pub struct Palette {
    pub panel: Color32,
    pub chrome: Color32,
    pub chrome_text: Color32,
    pub field: Color32,
    pub menu: Color32,
    pub button: Color32,
    pub hover: Color32,
    pub border: Color32,
    pub border_strong: Color32,
    pub text: Color32,
    pub strong: Color32,
    pub muted: Color32,
    pub accent: Color32,
    pub accent_soft: Color32,
    pub on_accent: Color32,
}

impl Theme {
    pub fn load() -> Self {
        // Read once before first paint. A saved change takes effect on the next launch;
        // neither settings I/O nor polling is added to the welcome's frame loop.
        load_from(
            std::env::consts::OS,
            |key| std::env::var_os(key),
            |p| std::fs::read(p),
        )
    }

    pub fn palette(self) -> Palette {
        let rgb = Color32::from_rgb;
        // MEASURED: current light/dark semantic tokens in narwhal3d/design.py.
        match self {
            Self::Light => Palette {
                panel: rgb(0xF2, 0xF5, 0xFA),
                chrome: rgb(0x33, 0x41, 0x51),
                chrome_text: rgb(0xED, 0xF3, 0xFC),
                field: rgb(0xEF, 0xF3, 0xF9),
                menu: rgb(0xFF, 0xFF, 0xFF),
                button: rgb(0xE5, 0xEB, 0xF4),
                hover: rgb(0xDD, 0xEB, 0xFF),
                border: rgb(0xD0, 0xD9, 0xE5),
                border_strong: rgb(0x99, 0xAA, 0xBD),
                text: rgb(0x25, 0x33, 0x43),
                strong: rgb(0x20, 0x2B, 0x3A),
                muted: rgb(0x52, 0x62, 0x75),
                accent: rgb(0x26, 0x6C, 0xE2),
                accent_soft: rgb(0xD9, 0xE7, 0xFF),
                on_accent: Color32::WHITE,
            },
            Self::Dark => Palette {
                panel: rgb(0x33, 0x33, 0x33),
                chrome: rgb(0x3A, 0x3A, 0x3A),
                chrome_text: rgb(0xD8, 0xD8, 0xD8),
                field: rgb(0x2B, 0x2B, 0x2B),
                menu: rgb(0x3A, 0x3A, 0x3A),
                button: rgb(0x44, 0x44, 0x44),
                hover: rgb(0x45, 0x45, 0x45),
                border: rgb(0x4B, 0x4B, 0x4B),
                border_strong: rgb(0x5E, 0x5E, 0x5E),
                text: rgb(0xD0, 0xD0, 0xD0),
                strong: rgb(0xF0, 0xF0, 0xF0),
                muted: rgb(0xA3, 0xA3, 0xA3),
                accent: rgb(0x4E, 0xA1, 0xFF),
                accent_soft: rgb(0x20, 0x32, 0x48),
                // OURS: dark ink on the brighter dark-theme accent keeps tabs legible.
                on_accent: rgb(0x2B, 0x2B, 0x2B),
            },
        }
    }

    pub fn visuals(self) -> egui::Visuals {
        let p = self.palette();
        let mut v = match self {
            Self::Light => egui::Visuals::light(),
            Self::Dark => egui::Visuals::dark(),
        };
        v.panel_fill = p.panel;
        v.window_fill = p.menu;
        v.window_stroke = Stroke::new(1.0, p.border);
        v.extreme_bg_color = p.field;
        v.text_edit_bg_color = Some(p.field);
        v.faint_bg_color = p.field;
        v.code_bg_color = p.field;
        v.weak_text_color = Some(p.muted);
        v.hyperlink_color = p.accent;
        v.selection.bg_fill = p.accent_soft;
        v.selection.stroke = Stroke::new(1.0, p.accent);
        for widget in [
            &mut v.widgets.noninteractive,
            &mut v.widgets.inactive,
            &mut v.widgets.hovered,
            &mut v.widgets.active,
            &mut v.widgets.open,
        ] {
            widget.bg_fill = p.button;
            widget.weak_bg_fill = p.button;
            widget.bg_stroke = Stroke::new(1.0, p.border);
            widget.fg_stroke = Stroke::new(1.0, p.text);
        }
        v.widgets.noninteractive.bg_fill = p.field;
        v.widgets.noninteractive.weak_bg_fill = p.field;
        v.widgets.hovered.bg_fill = p.hover;
        v.widgets.hovered.weak_bg_fill = p.hover;
        v.widgets.hovered.bg_stroke.color = p.border_strong;
        v.widgets.active.bg_fill = p.accent_soft;
        v.widgets.active.weak_bg_fill = p.accent_soft;
        v.widgets.active.bg_stroke.color = p.accent;
        v.widgets.active.fg_stroke.color = p.strong;
        v.widgets.open.bg_fill = p.menu;
        v.widgets.open.weak_bg_fill = p.menu;
        v
    }

    pub fn apply(self, ctx: &egui::Context) {
        // Override the OS preference, not just the currently active style.
        ctx.set_theme(match self {
            Self::Light => egui::Theme::Light,
            Self::Dark => egui::Theme::Dark,
        });
        ctx.set_visuals(self.visuals());
    }

    pub fn clear_color(self, alpha: f32) -> [f32; 4] {
        (egui::Rgba::from(self.palette().panel) * alpha).to_array()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn env<'a>(values: &'a [(&'a str, &'a str)]) -> impl Fn(&str) -> Option<OsString> + 'a {
        |key| {
            values
                .iter()
                .find(|(k, _)| *k == key)
                .map(|(_, v)| OsString::from(v))
        }
    }

    #[test]
    fn paths_match_platform_contract_and_explicit_override() {
        let values = [
            ("HOME", "/home/test"),
            ("APPDATA", "/roaming"),
            ("XDG_CONFIG_HOME", "/xdg"),
        ];
        for (os, expected) in [
            ("linux", "/xdg/narwhal3d/preferences.json"),
            ("windows", "/roaming/Narwhal3D/preferences.json"),
            (
                "macos",
                "/home/test/Library/Application Support/Narwhal3D/preferences.json",
            ),
        ] {
            assert_eq!(
                settings_path(os, &env(&values)),
                Some(PathBuf::from(expected))
            );
            assert_eq!(
                settings_path(
                    os,
                    &env(&[("NARWHAL3D_SETTINGS_FILE", "/explicit prefs.json")])
                ),
                Some(PathBuf::from("/explicit prefs.json"))
            );
            assert_eq!(settings_path(os, &env(&[])), None);
        }
        assert_eq!(
            settings_path(
                "linux",
                &env(&[
                    ("HOME", "/home/test"),
                    ("XDG_CONFIG_HOME", ""),
                    ("NARWHAL3D_SETTINGS_FILE", "")
                ])
            ),
            Some(PathBuf::from(
                "/home/test/.config/narwhal3d/preferences.json"
            ))
        );
    }

    #[test]
    fn valid_override_wins_without_reading_settings() {
        for (name, expected) in [("light", Theme::Light), (" DARK \n", Theme::Dark)] {
            assert_eq!(
                load_from("linux", env(&[("NARWHAL3D_THEME", name)]), |_| panic!(
                    "override must not read settings"
                )),
                expected
            );
        }
    }

    #[test]
    fn saved_theme_used_when_override_missing_or_invalid() {
        for name in ["", "unknown", "dark; command", "dark\0"] {
            for (json, expected) in [
                (br#"{"theme":"dark"}"#.as_slice(), Theme::Dark),
                (br#"{"theme":" LIGHT "}"#.as_slice(), Theme::Light),
            ] {
                let values = [
                    ("NARWHAL3D_SETTINGS_FILE", "/custom.json"),
                    ("NARWHAL3D_THEME", name),
                ];
                assert_eq!(
                    load_from("linux", env(&values), |path| {
                        assert_eq!(path, Path::new("/custom.json"));
                        Ok(json.to_vec())
                    }),
                    expected
                );
            }
        }
        assert_eq!(
            load_from("linux", env(&[("HOME", "/home/test")]), |_| Ok(
                br#"{"theme":"dark","unrelated":42}"#.to_vec()
            )),
            Theme::Dark
        );
    }

    #[test]
    fn malformed_unknown_and_unreadable_settings_fall_back_to_light() {
        for json in [
            "",
            "{",
            "null",
            "[]",
            "{}",
            "{\"theme\":null}",
            "{\"theme\":3}",
            "{\"theme\":true}",
            "{\"theme\":\"system\"}",
            "{\"theme\":\"dark; command\"}",
            "{\"theme\":\"dark\\u0000\"}",
        ] {
            assert_eq!(
                load_from("linux", env(&[("HOME", "/home/test")]), |_| Ok(json
                    .as_bytes()
                    .to_vec())),
                Theme::Light,
                "{json}"
            );
        }
        assert_eq!(
            load_from("linux", env(&[("HOME", "/home/test")]), |_| Ok(vec![0xff])),
            Theme::Light
        );
        for kind in [
            std::io::ErrorKind::NotFound,
            std::io::ErrorKind::PermissionDenied,
        ] {
            assert_eq!(
                load_from(
                    "linux",
                    env(&[("HOME", "/home/test")]),
                    |_| Err(kind.into())
                ),
                Theme::Light
            );
        }
        assert_eq!(
            load_from("linux", env(&[]), |_| panic!("no path")),
            Theme::Light
        );
    }

    #[cfg(unix)]
    #[test]
    fn non_unicode_override_does_not_mask_saved_theme() {
        use std::os::unix::ffi::OsStringExt;
        assert_eq!(
            load_from(
                "linux",
                |key| match key {
                    "NARWHAL3D_THEME" => Some(OsString::from_vec(vec![0xff])),
                    "NARWHAL3D_SETTINGS_FILE" =>
                        Some(OsString::from_vec(b"/prefs-\xff.json".to_vec())),
                    _ => None,
                },
                |path| {
                    assert_eq!(
                        path,
                        Path::new(&OsString::from_vec(b"/prefs-\xff.json".to_vec()))
                    );
                    Ok(br#"{"theme":"dark"}"#.to_vec())
                }
            ),
            Theme::Dark
        );
    }

    #[test]
    fn palette_matches_design_tokens() {
        for (theme, expected) in [
            (
                Theme::Light,
                [
                    0xF2F5FA, 0x334151, 0xEDF3FC, 0xEFF3F9, 0xFFFFFF, 0xE5EBF4, 0xDDEBFF, 0xD0D9E5,
                    0x99AABD, 0x253343, 0x202B3A, 0x526275, 0x266CE2, 0xD9E7FF, 0xFFFFFF,
                ],
            ),
            (
                Theme::Dark,
                [
                    0x333333, 0x3A3A3A, 0xD8D8D8, 0x2B2B2B, 0x3A3A3A, 0x444444, 0x454545, 0x4B4B4B,
                    0x5E5E5E, 0xD0D0D0, 0xF0F0F0, 0xA3A3A3, 0x4EA1FF, 0x203248, 0x2B2B2B,
                ],
            ),
        ] {
            let p = theme.palette();
            let actual = [
                p.panel,
                p.chrome,
                p.chrome_text,
                p.field,
                p.menu,
                p.button,
                p.hover,
                p.border,
                p.border_strong,
                p.text,
                p.strong,
                p.muted,
                p.accent,
                p.accent_soft,
                p.on_accent,
            ];
            for (color, hex) in actual.into_iter().zip(expected) {
                assert_eq!(
                    color,
                    Color32::from_rgb((hex >> 16) as u8, (hex >> 8) as u8, hex as u8)
                );
            }
        }
    }

    #[test]
    fn first_frame_widgets_inherit_the_saved_theme() {
        for theme in [Theme::Light, Theme::Dark] {
            let ctx = egui::Context::default();
            theme.apply(&ctx);
            let p = theme.palette();
            let mut output = ctx.run_ui(egui::RawInput::default(), |ui| {
                assert_eq!(ui.visuals().dark_mode, theme == Theme::Dark);
                assert_eq!(ui.visuals().panel_fill, p.panel);
                assert_eq!(ui.visuals().text_edit_bg_color(), p.field);
                assert_eq!(ui.visuals().widgets.inactive.bg_stroke.color, p.border);
                ui.heading("Welcome");
                ui.label("Status");
                let _ = ui.button("New model");
                ui.add_enabled(false, egui::Button::new("Updating…"));
                ui.text_edit_singleline(&mut "model.3dm".to_owned());
                ui.add(egui::ProgressBar::new(0.5));
            });
            assert!(!output.shapes.is_empty());
            // This headless widget probe deliberately has no GPU texture consumer.
            output.textures_delta.clear();
        }
    }

    #[test]
    fn visuals_cover_surfaces_text_fields_and_every_widget_state() {
        for theme in [Theme::Light, Theme::Dark] {
            let p = theme.palette();
            let ctx = egui::Context::default();
            theme.apply(&ctx);
            let v = ctx.global_style().visuals.clone();
            assert_eq!(v.dark_mode, theme == Theme::Dark);
            assert_eq!(v.panel_fill, p.panel);
            assert_eq!(v.window_fill, p.menu);
            assert_eq!(v.window_stroke.color, p.border);
            assert_eq!(v.text_edit_bg_color(), p.field);
            assert_eq!(v.extreme_bg_color, p.field);
            assert_eq!(v.faint_bg_color, p.field);
            assert_eq!(v.code_bg_color, p.field);
            assert_eq!(v.text_color(), p.text);
            assert_eq!(v.strong_text_color(), p.strong);
            assert_eq!(v.weak_text_color(), p.muted);
            assert_eq!(v.hyperlink_color, p.accent);
            assert_eq!(v.selection.bg_fill, p.accent_soft);
            assert_eq!(v.selection.stroke.color, p.accent);
            for (w, fill, border, text) in [
                (v.widgets.noninteractive, p.field, p.border, p.text),
                (v.widgets.inactive, p.button, p.border, p.text),
                (v.widgets.hovered, p.hover, p.border_strong, p.text),
                (v.widgets.active, p.accent_soft, p.accent, p.strong),
                (v.widgets.open, p.menu, p.border, p.text),
            ] {
                assert_eq!(w.bg_fill, fill);
                assert_eq!(w.weak_bg_fill, fill);
                assert_eq!(w.bg_stroke.color, border);
                assert_eq!(w.fg_stroke.color, text);
            }
            let opaque = egui::Rgba::from(p.panel).to_array();
            assert_eq!(theme.clear_color(1.0), opaque);
            assert_eq!(theme.clear_color(0.5), opaque.map(|v| v * 0.5));
            assert_eq!(theme.clear_color(0.0), [0.0; 4]);
        }
    }
}
