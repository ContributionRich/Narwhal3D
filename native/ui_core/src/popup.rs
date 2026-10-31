//! OURS popup policy, ported from shell_popup.PopupModel (including fractional-scale fix).
//! Host callbacks measure complete strings lazily; they never restart an operation.
use std::collections::BTreeSet;

pub const MAX_ROWS: usize = 65536;
pub const MAX_DEPTH: usize = 64;
pub const MAX_GUARDS: usize = 512;
pub type Rect = [f64; 4];
pub type Measure = unsafe extern "C" fn(i64, u32, f64, *mut f64) -> u32;

#[repr(C)]
#[derive(Clone, Copy, Default, Debug)]
pub struct Row {
    pub child_start: u64,
    pub child_count: u64,
    pub separator: u64,
}
#[derive(Clone, Debug)]
pub struct Panel {
    pub start: usize,
    pub count: usize,
    pub parent: Option<usize>,
    pub selected: Option<usize>,
    pub offset: f64,
    pub rect: Rect,
    pub body: Rect,
    pub rows: Vec<Rect>,
    pub max_scroll: f64,
    pub up: Option<Rect>,
    pub down: Option<Rect>,
    pub width: f64,
}
#[derive(Clone)]
pub struct Popup {
    pub size: (f64, f64),
    pub scale: f64,
    pub anchor: (f64, f64),
    pub title: bool,
    pub rows: Vec<Row>,
    pub panels: Vec<Panel>,
    pub guards: BTreeSet<String>,
    pressed: Option<(String, Hit)>,
    pub revision: u64,
    pub layout_revision: u64,
}
#[derive(Clone, Copy, PartialEq, Debug)]
pub enum Part {
    Row(usize),
    Up,
    Down,
    Border,
}
pub type Hit = (usize, Part);
#[derive(Default)]
pub struct Outcome {
    pub consumed: bool,
    pub action: Option<usize>,
}
fn clamp(x: f64, low: f64, high: f64) -> f64 {
    x.min(high).max(low)
}
fn inside(r: Rect, x: f64, y: f64) -> bool {
    r[0] <= x && x < r[2] && r[1] <= y && y < r[3]
}
pub fn dimension(x: f64) -> bool {
    x.is_finite() && x > 0.0 && x <= 1e12
}

pub fn validate_rows(rows: &[Row], roots: usize) -> bool {
    if rows.len() > MAX_ROWS || roots > rows.len() {
        return false;
    }
    // Each row occurs exactly once in an ordered forest; no cycles, aliases or orphans.
    let mut seen = vec![false; rows.len()];
    let mut stack = vec![(0, roots, 1)];
    while let Some((start, count, depth)) = stack.pop() {
        if depth > MAX_DEPTH || start > rows.len() || count > rows.len() - start {
            return false;
        }
        for i in start..start + count {
            if seen[i] || rows[i].separator > 1 {
                return false;
            }
            seen[i] = true;
            let r = rows[i];
            if r.child_count > 0 {
                if r.child_start > rows.len() as u64 || r.child_count > rows.len() as u64 {
                    return false;
                }
                stack.push((r.child_start as usize, r.child_count as usize, depth + 1));
            } else if r.child_start != 0 {
                return false;
            }
        }
    }
    seen.into_iter().all(|x| x)
}
impl Popup {
    pub fn new(w: f64, h: f64, scale: f64) -> Self {
        Self {
            size: (w, h),
            scale,
            anchor: (0.0, 0.0),
            title: false,
            rows: vec![],
            panels: vec![],
            guards: BTreeSet::new(),
            pressed: None,
            revision: 1,
            layout_revision: 1,
        }
    }
    fn panel(
        &self,
        start: usize,
        count: usize,
        parent: Option<usize>,
        root: bool,
        measure: Measure,
    ) -> Result<Panel, u32> {
        let size = 12.0 * self.scale;
        let get = |id, kind| {
            let mut width = 0.0;
            let status = unsafe { measure(id, kind, size, &mut width) };
            if status != 0 || !width.is_finite() || width.abs() > 1e12 {
                Err(5u32)
            } else {
                Ok(width)
            }
        };
        let mut width = get(if root { -1 } else { -2 }, 0)?;
        for i in start..start + count {
            let label = get(i as i64, 0)?;
            let shortcut = get(i as i64, 1)?;
            width = width.max(label + shortcut);
        }
        Ok(Panel {
            start,
            count,
            parent,
            selected: None,
            offset: 0.0,
            rect: [0.0; 4],
            body: [0.0; 4],
            rows: vec![],
            max_scroll: 0.0,
            up: None,
            down: None,
            width: (200.0 * self.scale).max(width + 64.0 * self.scale),
        })
    }
    pub fn open(
        &mut self,
        rows: Vec<Row>,
        roots: usize,
        anchor: (f64, f64),
        title: bool,
        measure: Measure,
    ) -> Result<(), u32> {
        let panel = self.panel(0, roots, None, true, measure)?;
        self.rows = rows;
        self.anchor = anchor;
        self.title = title;
        self.panels = vec![panel];
        self.pressed = None;
        self.layout();
        Ok(())
    }
    pub fn close(&mut self) {
        self.panels.clear();
        self.pressed = None;
    }
    pub fn resize(&mut self, w: f64, h: f64) {
        if self.size != (w, h) {
            self.size = (w, h);
            self.pressed = None;
            self.layout();
        }
    }
    pub fn layout(&mut self) {
        self.layout_revision += 1;
        let (w, h) = self.size;
        let s = self.scale;
        let pad = 4.0 * s;
        let margin = pad.min(w / 4.0).min(h / 4.0);
        for depth in 0..self.panels.len() {
            let p = &self.panels[depth];
            let heights: Vec<f64> = self.rows[p.start..p.start + p.count]
                .iter()
                .map(|r| if r.separator != 0 { 9.0 * s } else { 22.0 * s })
                .collect();
            let content: f64 = heights.iter().sum();
            let header = if depth == 0 && self.title {
                22.0 * s
            } else {
                0.0
            };
            let height = (content + header + 2.0 * pad)
                .min(0.8 * h)
                .min(h - 2.0 * margin);
            let header = header.min(height * 0.3);
            let inset = pad.min(height * 0.1);
            let body_height = (height - header - 2.0 * inset).max(0.0);
            let scrolling = content - body_height > 1e-6 * s.max(1.0);
            let scroll_h = if scrolling {
                (14.0 * s).min(body_height / 4.0)
            } else {
                0.0
            };
            let width = p.width.min(w - 2.0 * margin);
            let (mut x, top) = if depth == 0 {
                (self.anchor.0, self.anchor.1 - 3.0 * s)
            } else {
                let parent = &self.panels[depth - 1];
                let row = parent.rows[p.parent.unwrap()];
                let x = if parent.rect[2] + width > w - margin {
                    parent.rect[0] - width
                } else {
                    parent.rect[2]
                };
                (x, (row[3] + inset).min(parent.body[3]))
            };
            x = clamp(x, margin, w - margin - width);
            let top = clamp(top, margin + height, h - margin);
            let p = &mut self.panels[depth];
            p.rect = [x, top - height, x + width, top];
            let bottom = top - height + inset;
            let body_top = top - inset - header;
            p.up = scrolling.then_some([x, body_top - scroll_h, x + width, body_top]);
            p.down = scrolling.then_some([x, bottom, x + width, bottom + scroll_h]);
            p.body = [
                x + s.min(width / 4.0),
                bottom + scroll_h,
                x + width - s.min(width / 4.0),
                body_top - scroll_h,
            ];
            let visible = (p.body[3] - p.body[1]).max(0.0);
            p.max_scroll = (content - visible).max(0.0);
            p.offset = clamp(p.offset, 0.0, p.max_scroll);
            let mut y = p.body[3] + p.offset;
            p.rows.clear();
            for height in heights {
                p.rows.push([p.body[0], y - height, p.body[2], y]);
                y -= height;
            }
        }
    }
    pub fn hit(&self, x: f64, y: f64) -> Option<Hit> {
        for (depth, p) in self.panels.iter().enumerate().rev() {
            if !inside(p.rect, x, y) {
                continue;
            }
            if p.up.is_some_and(|r| inside(r, x, y)) {
                return Some((depth, Part::Up));
            }
            if p.down.is_some_and(|r| inside(r, x, y)) {
                return Some((depth, Part::Down));
            }
            if inside(p.body, x, y) {
                for (i, r) in p.rows.iter().enumerate() {
                    if inside(*r, x, y) {
                        return Some((depth, Part::Row(i)));
                    }
                }
            }
            return Some((depth, Part::Border));
        }
        None
    }
    fn submenu(&mut self, depth: usize, index: usize, measure: Measure) -> Result<bool, u32> {
        let row = self.rows[self.panels[depth].start + index];
        if row.child_count == 0 {
            self.panels.truncate(depth + 1);
            return Ok(false);
        }
        if self
            .panels
            .get(depth + 1)
            .is_some_and(|p| p.parent == Some(index))
        {
            return Ok(true);
        }
        self.panels.truncate(depth + 1);
        let p = self.panel(
            row.child_start as usize,
            row.child_count as usize,
            Some(index),
            false,
            measure,
        )?;
        self.panels.push(p);
        self.layout();
        Ok(true)
    }
    fn activate(
        &mut self,
        depth: usize,
        index: usize,
        measure: Measure,
    ) -> Result<Option<usize>, u32> {
        let id = self.panels[depth].start + index;
        if self.rows[id].separator != 0 || self.submenu(depth, index, measure)? {
            return Ok(None);
        }
        self.close(); // Host may reopen only after the operation has finished.
        Ok(Some(id))
    }
    fn scroll(&mut self, depth: usize, delta: f64) {
        let p = &mut self.panels[depth];
        p.offset = clamp(p.offset + delta, 0.0, p.max_scroll);
        p.selected = None;
        self.pressed = None;
        self.panels.truncate(depth + 1);
        self.layout();
    }
    fn select(&mut self, depth: usize, index: usize) {
        let p = &mut self.panels[depth];
        p.selected = Some(index);
        let r = p.rows[index];
        if r[3] > p.body[3] {
            p.offset -= r[3] - p.body[3];
        } else if r[1] < p.body[1] {
            p.offset += p.body[1] - r[1];
        }
        self.layout();
    }
    fn keyboard(&mut self, key: &str, measure: Measure) -> Result<Option<usize>, u32> {
        let depth = self.panels.len() - 1;
        let p = &self.panels[depth];
        let choices: Vec<usize> = (0..p.count)
            .filter(|i| self.rows[p.start + i].separator == 0)
            .collect();
        match key {
            "ESC" => self.close(),
            "LEFT_ARROW" if depth > 0 => {
                self.panels.pop();
            }
            "UP_ARROW" | "DOWN_ARROW" | "HOME" | "END" if !choices.is_empty() => {
                let i = if key == "HOME" {
                    choices[0]
                } else if key == "END" {
                    *choices.last().unwrap()
                } else if let Some(pos) = choices.iter().position(|i| Some(*i) == p.selected) {
                    choices[(pos
                        + if key == "DOWN_ARROW" {
                            1
                        } else {
                            choices.len() - 1
                        })
                        % choices.len()]
                } else if key == "DOWN_ARROW" {
                    choices[0]
                } else {
                    *choices.last().unwrap()
                };
                self.select(depth, i);
            }
            "RET" | "NUMPAD_ENTER" | "RIGHT_ARROW" if p.selected.is_some() => {
                let i = p.selected.unwrap();
                if key == "RIGHT_ARROW" {
                    if self.submenu(depth, i, measure)? {
                        return self.keyboard("HOME", measure);
                    }
                } else {
                    return self.activate(depth, i, measure);
                }
            }
            "PAGE_UP" | "PAGE_DOWN" => {
                let delta = (p.body[3] - p.body[1]) * if key == "PAGE_UP" { -1.0 } else { 1.0 };
                self.scroll(depth, delta);
            }
            _ => (),
        }
        Ok(None)
    }
    // value: NOTHING=0, PRESS=1, RELEASE=2, CLICK=3, DOUBLE_CLICK=4, other=5.
    pub fn event(
        &mut self,
        key: &str,
        value: u32,
        x: f64,
        y: f64,
        measure: Measure,
    ) -> Result<Outcome, u32> {
        let guarded = self.guards.contains(key);
        if value == 2 && guarded {
            self.guards.remove(key);
        }
        if self.panels.is_empty() {
            return Ok(Outcome {
                consumed: guarded,
                action: None,
            });
        }
        if matches!(
            key,
            "TIMER" | "TIMER0" | "TIMER1" | "TIMER2" | "WINDOW_DEACTIVATE"
        ) {
            if key == "WINDOW_DEACTIVATE" {
                self.close();
                self.guards.clear();
            }
            return Ok(Outcome::default());
        }
        let mut out = Outcome {
            consumed: true,
            action: None,
        };
        if matches!(key, "MOUSEMOVE" | "INBETWEEN_MOUSEMOVE") {
            if self.pressed.is_none() {
                if let Some((d, Part::Row(i))) = self.hit(x, y) {
                    self.panels[d].selected =
                        (self.rows[self.panels[d].start + i].separator == 0).then_some(i);
                    self.submenu(d, i, measure)?;
                }
            }
        } else if key.starts_with("WHEEL") {
            if value == 1 {
                let depth = self.hit(x, y).map_or(self.panels.len() - 1, |h| h.0);
                let sign = if matches!(key, "WHEELUPMOUSE" | "WHEELINMOUSE") {
                    -1.0
                } else {
                    1.0
                };
                self.scroll(depth, sign * 3.0 * 22.0 * self.scale);
            }
        } else if key.ends_with("MOUSE") || matches!(key, "PEN" | "ERASER") {
            let hit = self.hit(x, y);
            if matches!(value, 1 | 4) {
                self.guards.insert(key.to_owned());
                self.pressed = None;
                match hit {
                    None => self.close(),
                    Some((d, part)) if key == "LEFTMOUSE" => match part {
                        Part::Up | Part::Down => self.scroll(
                            d,
                            if part == Part::Up {
                                -22.0 * self.scale
                            } else {
                                22.0 * self.scale
                            },
                        ),
                        Part::Row(i) if self.rows[self.panels[d].start + i].separator == 0 => {
                            self.panels[d].selected = Some(i);
                            if !self.submenu(d, i, measure)? {
                                self.pressed = Some((key.to_owned(), (d, part)));
                            }
                        }
                        _ => (),
                    },
                    _ => (),
                }
            } else if value == 2 {
                let pressed = self.pressed.take();
                if let Some((d, Part::Row(i))) = hit {
                    if pressed
                        .as_ref()
                        .is_some_and(|p| p.0 == key && Some(p.1) == hit)
                    {
                        out.action = self.activate(d, i, measure)?;
                    }
                }
            }
        } else if !matches!(
            key,
            "TRACKPADPAN" | "TRACKPADZOOM" | "MOUSEROTATE" | "MOUSESMARTZOOM"
        ) {
            if value == 1 {
                self.guards.insert(key.to_owned());
                out.action = self.keyboard(key, measure)?;
            }
            out.consumed = matches!(value, 1..=4) || guarded;
        }
        Ok(out)
    }
}
