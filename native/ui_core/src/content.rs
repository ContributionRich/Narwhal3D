//! Authored UI DATA and menu expansion. No geometry, font, or viewport ownership.
//! Typed trees retain Python tuple keys, frozensets and dictionary insertion order.
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};
use std::sync::OnceLock;

const DATA: &str = include_str!("content_data/content.json");
const MAX_BYTES: usize = 8 * 1024 * 1024;
const MAX_NODES: usize = 100_000;
const MAX_DEPTH: usize = 48;
type Result<T> = std::result::Result<T, u32>;

fn sequence<'a>(v: &'a Value, tag: &str) -> Result<&'a Vec<Value>> {
    if v.get("t").and_then(Value::as_str) != Some(tag) {
        return Err(2);
    }
    v.get("v").and_then(Value::as_array).ok_or(2)
}
fn tuple(v: &Value) -> Result<&Vec<Value>> {
    sequence(v, "tuple")
}
fn iterable(v: &Value) -> Result<&Vec<Value>> {
    // Host overrides historically accept both list and tuple iterables.
    match v.get("t").and_then(Value::as_str) {
        Some("list") => sequence(v, "list"),
        _ => tuple(v),
    }
}
fn pairs(v: &Value) -> Result<&Vec<Value>> {
    sequence(v, "dict")
}
fn text(v: &Value) -> Result<&str> {
    v.as_str().ok_or(2)
}
fn field<'a>(v: &'a Value, key: &str) -> Result<&'a Value> {
    pairs(v)?
        .iter()
        .find_map(|p| {
            let p = p.as_array()?;
            (p.first()?.as_str()? == key).then(|| &p[1])
        })
        .ok_or(2)
}
fn optional_text<'a>(v: &'a Value, key: &str) -> Result<Option<&'a str>> {
    match field(v, key) {
        Ok(Value::Null) | Err(_) => Ok(None),
        Ok(v) => Ok(Some(text(v)?)),
    }
}
fn tagged(tag: &str, rows: Vec<Value>) -> Value {
    json!({"t":tag,"v":rows})
}
fn tup(rows: Vec<Value>) -> Value {
    tagged("tuple", rows)
}
fn dictionary(rows: Vec<(Value, Value)>) -> Value {
    tagged(
        "dict",
        rows.into_iter().map(|(k, v)| json!([k, v])).collect(),
    )
}
fn validate_tree(v: &Value, depth: usize, nodes: &mut usize) -> Result<()> {
    *nodes += 1;
    if depth > MAX_DEPTH || *nodes > MAX_NODES {
        return Err(3);
    }
    match v {
        Value::Null | Value::Bool(_) | Value::Number(_) => Ok(()),
        Value::String(s) if s.len() <= 65536 => Ok(()),
        Value::Object(o) if o.len() == 2 => {
            let tag = v.get("t").and_then(Value::as_str).ok_or(2u32)?;
            let rows = v.get("v").and_then(Value::as_array).ok_or(2u32)?;
            if !matches!(tag, "dict" | "tuple" | "list" | "frozenset" | "set") {
                return Err(2);
            }
            let mut keys = BTreeSet::new();
            for row in rows {
                if tag == "dict" {
                    let pair = row.as_array().filter(|a| a.len() == 2).ok_or(2u32)?;
                    if !hashable(&pair[0]) || !keys.insert(pair[0].to_string()) {
                        return Err(2);
                    }
                    validate_tree(&pair[0], depth + 1, nodes)?;
                    validate_tree(&pair[1], depth + 1, nodes)?;
                } else {
                    if matches!(tag, "frozenset" | "set")
                        && (!hashable(row) || !keys.insert(row.to_string()))
                    {
                        return Err(2);
                    }
                    validate_tree(row, depth + 1, nodes)?;
                }
            }
            Ok(())
        }
        _ => Err(2),
    }
}
fn hashable(v: &Value) -> bool {
    match v {
        Value::Null | Value::Bool(_) | Value::Number(_) | Value::String(_) => true,
        _ => {
            tuple(v).is_ok_and(|a| a.iter().all(hashable))
                || sequence(v, "frozenset").is_ok_and(|a| a.iter().all(hashable))
        }
    }
}
struct Content {
    resources: BTreeMap<String, Value>,
    folds: BTreeMap<char, String>,
    whitespace: BTreeSet<char>,
}
impl Content {
    fn load(data: &str) -> Result<Self> {
        if data.len() > MAX_BYTES {
            return Err(3);
        }
        let root: Value = serde_json::from_str(data).map_err(|_| 2u32)?;
        if root["schema"] != 1 || root["unicode_version"] != "15.1.0" {
            return Err(2);
        }
        let resources = root["resources"].as_object().ok_or(2u32)?;
        if resources.len() != 4 {
            return Err(2);
        }
        for name in [
            "toolbar_data",
            "toolbar_macro_data",
            "menubar_resource_data",
            "flyout_icon_data",
        ] {
            validate_tree(resources.get(name).ok_or(2u32)?, 0, &mut 0)?;
        }
        let mut folds = BTreeMap::new();
        for row in root["casefold"].as_array().ok_or(2u32)? {
            let pair = row.as_array().filter(|p| p.len() == 2).ok_or(2u32)?;
            let chars: Vec<_> = text(&pair[0])?.chars().collect();
            if chars.len() != 1
                || text(&pair[1])?.len() > 16
                || folds.insert(chars[0], text(&pair[1])?.to_owned()).is_some()
            {
                return Err(2);
            }
        }
        if folds.len() != 1530 {
            return Err(2);
        }
        let mut whitespace = BTreeSet::new();
        for v in root["whitespace"].as_array().ok_or(2u32)? {
            let chars: Vec<_> = text(v)?.chars().collect();
            if chars.len() != 1 || !whitespace.insert(chars[0]) {
                return Err(2);
            }
        }
        if whitespace.len() != 29 {
            return Err(2);
        }
        let owner = Self {
            resources: resources
                .iter()
                .map(|(k, v)| (k.clone(), v.clone()))
                .collect(),
            folds,
            whitespace,
        };
        owner.validate_resources()?;
        Ok(owner)
    }
    fn resource(&self, name: &str) -> Result<&Value> {
        self.resources.get(name).ok_or(2)
    }
    fn menu(&self, key: &str) -> Result<&Value> {
        field(self.resource("menubar_resource_data")?, key)
    }
    fn fold(&self, s: &str) -> String {
        let mut result = String::new();
        for c in s.chars() {
            if let Some(f) = self.folds.get(&c) {
                result.push_str(f);
            } else {
                result.push(c);
            }
        }
        result
    }
    fn strip<'a>(&self, s: &'a str) -> &'a str {
        s.trim_matches(|c| self.whitespace.contains(&c))
    }
    fn validate_resources(&self) -> Result<()> {
        let toolbar = self.resource("toolbar_data")?;
        if pairs(toolbar)?.len() != 10
            || pairs(self.resource("toolbar_macro_data")?)?.len() != 1
            || pairs(self.resource("flyout_icon_data")?)?.len() != 4
            || pairs(self.resource("menubar_resource_data")?)?.len() != 6
        {
            return Err(2);
        }
        text(field(toolbar, "CONTAINER")?)?;
        let suffix = text(field(toolbar, "TODO_SUFFIX")?)?;
        if suffix != " (Not implemented)" || text(self.menu("TODO_SUFFIX")?)? != suffix {
            return Err(2);
        }
        let mut icon_names = BTreeSet::new();
        for tab in tuple(field(toolbar, "TABS")?)? {
            text(field(tab, "name")?)?;
            validate_buttons(field(tab, "buttons")?, &mut icon_names)?;
        }
        text(field(field(toolbar, "SIDEBAR")?, "name")?)?;
        validate_buttons(
            field(field(toolbar, "SIDEBAR")?, "buttons")?,
            &mut icon_names,
        )?;
        for pair in pairs(field(toolbar, "FLYOUTS")?)? {
            text(&pair[0])?;
            validate_buttons(&pair[1], &mut icon_names)?;
        }
        if string_set(field(toolbar, "TOOLBAR_ICON_NAMES")?)? != icon_names {
            return Err(2);
        }
        let panel = string_set(field(toolbar, "PANEL_ICON_NAMES")?)?;
        let flyout = self.resource("flyout_icon_data")?;
        let fly_icons = string_set(field(flyout, "ICON_NAMES")?)?;
        let mut all = icon_names;
        all.extend(panel);
        all.extend(fly_icons.clone());
        if string_set(field(toolbar, "ICON_NAMES")?)? != all {
            return Err(2);
        }
        let sources = field(toolbar, "ICON_SOURCES")?;
        for pair in pairs(sources)? {
            text(&pair[0])?;
            if !pair[1].is_null() {
                text(&pair[1])?;
            }
        }
        for pair in pairs(field(flyout, "ICON_SOURCES")?)? {
            if field(sources, text(&pair[0])?)? != &pair[1] {
                return Err(2);
            }
        }
        if pairs(field(flyout, "ICON_SOURCES")?)?
            .iter()
            .map(|p| text(&p[0]).map(str::to_owned))
            .collect::<Result<BTreeSet<_>>>()?
            != fly_icons
        {
            return Err(2);
        }
        for pair in pairs(field(flyout, "FLYOUT_SOURCES")?)? {
            let key = tuple(&pair[0])?;
            if key.len() != 2 {
                return Err(2);
            }
            text(&key[0])?;
            text(&key[1])?;
            let runtime = format!("narwhal-{}", text(&pair[1])?.replace('+', "plus"));
            if field(field(flyout, "ICON_SOURCES")?, &runtime)? != &pair[1] {
                return Err(2);
            }
        }
        for pair in pairs(field(flyout, "ICON_BADGES")?)? {
            text(&pair[0])?;
            if !pair[1].is_u64() {
                return Err(2);
            }
        }
        for pair in pairs(field(self.resource("toolbar_macro_data")?, "MACROS")?)? {
            if tuple(&pair[0])?.len() != 3 || tuple(&pair[1])?.len() != 2 {
                return Err(2);
            }
            for v in tuple(&pair[0])?.iter().chain(tuple(&pair[1])?) {
                if !v.is_null() {
                    text(v)?;
                }
            }
        }
        for v in tuple(field(toolbar, "MISSING_ARTWORK")?)? {
            text(v)?;
        }
        for v in sequence(self.menu("SPECIAL_COMMANDS")?, "frozenset")? {
            text(v)?;
        }
        for pair in tuple(self.menu("_CORE_MENUS")?)? {
            let pair = tuple(pair)?;
            if pair.len() != 2 {
                return Err(2);
            }
            text(&pair[0])?;
            walk(&pair[1], &mut BTreeSet::new())?;
        }
        for pair in pairs(self.menu("_SERVICE_GROUPS")?)? {
            text(&pair[0])?;
            walk(&pair[1], &mut BTreeSet::new())?;
        }
        for key in ["_TAB_MENU_GROUPS", "_STANDARD_COMMAND_GROUPS"] {
            for pair in pairs(self.menu(key)?)? {
                text(&pair[0])?;
                let group = tuple(&pair[1])?;
                if group.len() != 2 {
                    return Err(2);
                }
                text(&group[0])?;
                text(&group[1])?;
            }
        }
        Ok(())
    }
    fn groups(&self, registered: &BTreeSet<String>, tabs: &Value) -> Result<Value> {
        let mut used = BTreeSet::new();
        for pair in tuple(self.menu("_CORE_MENUS")?)? {
            walk(&tuple(pair)?[1], &mut used)?;
        }
        for pair in pairs(self.menu("_SERVICE_GROUPS")?)? {
            walk(&pair[1], &mut used)?;
        }
        let mut grouped: Vec<(Value, Value)> = Vec::new();
        let mut labels: BTreeMap<(String, String), BTreeSet<String>> = BTreeMap::new();
        for tab in iterable(tabs)? {
            let tab_name = text(field(tab, "name")?)?;
            for record in iterable(field(tab, "buttons")?)? {
                let Some(command) = optional_text(record, "command")?.filter(|s| !s.is_empty())
                else {
                    continue;
                };
                if used.contains(command) {
                    continue;
                }
                let group = if tab_name == "Standard" {
                    field(self.menu("_STANDARD_COMMAND_GROUPS")?, command)
                } else {
                    field(self.menu("_TAB_MENU_GROUPS")?, tab_name)
                };
                let Ok(group) = group else {
                    continue;
                };
                let group = tuple(group)?;
                let target = text(&group[0])?;
                let name = text(&group[1])?;
                let tooltip = optional_text(record, "tooltip")?.unwrap_or("");
                let button_text = optional_text(record, "text")?.unwrap_or("");
                let label = if !tooltip.is_empty() {
                    tooltip
                } else if !button_text.is_empty() {
                    button_text
                } else {
                    command
                };
                let mut label = self.strip(label).to_owned();
                if label.is_empty() {
                    continue;
                }
                let seen = labels
                    .entry((target.to_owned(), name.to_owned()))
                    .or_default();
                if seen.contains(&self.fold(&label)) {
                    let alternate = self.strip(if button_text.is_empty() {
                        command
                    } else {
                        button_text
                    });
                    label = if seen.contains(&self.fold(alternate)) {
                        format!("{label} [{command}]")
                    } else {
                        alternate.to_owned()
                    };
                }
                seen.insert(self.fold(&label));
                if !registered.contains(command) {
                    label.push_str(text(self.menu("TODO_SUFFIX")?)?);
                }
                let row = tup(vec![json!("item"), json!(label), json!(""), json!(command)]);
                let key = tup(vec![json!(target), json!(name)]);
                if let Some((_, rows)) = grouped.iter_mut().find(|(k, _)| k == &key) {
                    rows.get_mut("v")
                        .and_then(Value::as_array_mut)
                        .ok_or(2u32)?
                        .push(row);
                } else {
                    grouped.push((key, tagged("list", vec![row])));
                }
                used.insert(command.to_owned());
            }
        }
        Ok(dictionary(grouped))
    }
    fn expanded(&self, registered: &BTreeSet<String>, tabs: &Value) -> Result<Value> {
        let groups = self.groups(registered, tabs)?;
        let mut expanded = Vec::new();
        for pair in tuple(self.menu("_CORE_MENUS")?)? {
            let pair = tuple(pair)?;
            let menu = text(&pair[0])?;
            let mut rows = tuple(&pair[1])?.clone();
            let mut additions = match field(self.menu("_SERVICE_GROUPS")?, menu) {
                Ok(v) => tuple(v)?.clone(),
                Err(_) => Vec::new(),
            };
            for pair in pairs(&groups)? {
                let key = tuple(&pair[0])?;
                if text(&key[0])? == menu {
                    additions.push(tup(vec![
                        json!("popup"),
                        key[1].clone(),
                        json!(""),
                        tup(sequence(&pair[1], "list")?.clone()),
                        Value::Null,
                    ]));
                }
            }
            if !additions.is_empty() {
                rows.push(tup(vec![json!("sep"), json!(""), json!(""), Value::Null]));
                rows.extend(additions);
            }
            expanded.push(tup(vec![json!(menu), tup(rows)]));
        }
        Ok(tup(expanded))
    }
}
fn validate_buttons(v: &Value, icons: &mut BTreeSet<String>) -> Result<()> {
    for record in tuple(v)? {
        for key in [
            "text",
            "tooltip",
            "command",
            "right_command",
            "right_tooltip",
            "icon",
            "link",
        ] {
            let v = field(record, key)?;
            if !v.is_null() {
                text(v)?;
            }
        }
        if let Some(icon) = optional_text(record, "icon")?.filter(|s| !s.is_empty()) {
            icons.insert(icon.to_owned());
        }
    }
    Ok(())
}
fn string_set(v: &Value) -> Result<BTreeSet<String>> {
    sequence(v, "frozenset")?
        .iter()
        .map(|v| text(v).map(str::to_owned))
        .collect()
}
fn walk(rows: &Value, used: &mut BTreeSet<String>) -> Result<()> {
    for row in tuple(rows)? {
        let row = tuple(row)?;
        match row.first().and_then(Value::as_str) {
            Some("item") if row.len() == 4 => {
                text(&row[1])?;
                text(&row[2])?;
                if !row[3].is_null() {
                    used.insert(text(&row[3])?.to_owned());
                }
            }
            Some("sep") if row.len() == 4 && row[1] == "" && row[2] == "" && row[3].is_null() => (),
            Some("popup") if row.len() == 5 => {
                text(&row[1])?;
                text(&row[2])?;
                walk(&row[3], used)?;
                if !row[4].is_null() {
                    text(&row[4])?;
                }
            }
            _ => return Err(2),
        }
    }
    Ok(())
}
fn ordered_commands(rows: &Value, output: &mut Vec<Value>) -> Result<()> {
    for row in iterable(rows)? {
        let row = iterable(row)?;
        match row.first().and_then(Value::as_str) {
            Some("item") if row.len() == 4 => {
                if row[3].as_str().is_some_and(|s| !s.is_empty()) {
                    output.push(row[3].clone());
                }
            }
            Some("popup") if row.len() == 5 => ordered_commands(&row[3], output)?,
            Some("sep") if row.len() == 4 => (),
            _ => return Err(2),
        }
    }
    Ok(())
}
fn counts(menus: &Value) -> Result<Value> {
    fn count(rows: &Value, n: &mut [usize; 3]) -> Result<()> {
        for row in tuple(rows)? {
            let row = tuple(row)?;
            match row.first().and_then(Value::as_str) {
                Some("item") if row.len() == 4 => n[0] += 1,
                Some("sep") if row.len() == 4 => n[1] += 1,
                Some("popup") if row.len() == 5 => {
                    n[2] += 1;
                    count(&row[3], n)?;
                }
                _ => return Err(2),
            }
        }
        Ok(())
    }
    let mut n = [0; 3];
    for pair in tuple(menus)? {
        let pair = tuple(pair)?;
        if pair.len() != 2 {
            return Err(2);
        }
        count(&pair[1], &mut n)?;
    }
    Ok(dictionary(vec![
        (json!("items"), json!(n[0])),
        (json!("separators"), json!(n[1])),
        (json!("popups"), json!(n[2])),
    ]))
}
static CONTENT: OnceLock<Result<Content>> = OnceLock::new();
fn request(op: u32, input: &[u8]) -> Result<Vec<u8>> {
    let content = CONTENT
        .get_or_init(|| Content::load(DATA))
        .as_ref()
        .map_err(|_| 4u32)?;
    let args: Value = serde_json::from_slice(input).map_err(|_| 2u32)?;
    let args = args.as_object().ok_or(2u32)?;
    let result = match op {
        0 => {
            if args.len() != 1 {
                return Err(2);
            }
            content
                .resource(text(args.get("name").ok_or(2u32)?)?)?
                .clone()
        }
        1 | 2 => {
            if args.keys().any(|k| k != "registered" && k != "tabs") {
                return Err(2);
            }
            let keys = args
                .get("registered")
                .and_then(Value::as_array)
                .filter(|v| v.len() <= 8192)
                .ok_or(2u32)?;
            let mut registered = BTreeSet::new();
            for v in keys {
                let s = text(v)?;
                if s.len() > 4096 {
                    return Err(3);
                }
                registered.insert(s.to_owned());
            }
            let tabs = args
                .get("tabs")
                .unwrap_or(field(content.resource("toolbar_data")?, "TABS")?);
            validate_tree(tabs, 0, &mut 0)?;
            if op == 1 {
                content.groups(&registered, tabs)?
            } else {
                content.expanded(&registered, tabs)?
            }
        }
        3 => {
            if args.len() != 1 {
                return Err(2);
            }
            let menus = args.get("menus").ok_or(2u32)?;
            validate_tree(menus, 0, &mut 0)?;
            counts(menus)?
        }
        4 => {
            if args.len() != 1 {
                return Err(2);
            }
            let rows = args.get("rows").ok_or(2u32)?;
            validate_tree(rows, 0, &mut 0)?;
            let mut commands = Vec::new();
            ordered_commands(rows, &mut commands)?;
            tup(commands)
        }
        _ => return Err(1),
    };
    let bytes = serde_json::to_vec(&result).map_err(|_| 4u32)?;
    if bytes.len() > MAX_BYTES {
        return Err(3);
    }
    Ok(bytes)
}
/// Independent content ABI; editor/popup/transcript ABI versions are unchanged.
#[no_mangle]
pub extern "C" fn narwhal_ui_content_abi() -> u32 {
    1
}
/// Stateless, deterministic two-pass read. Status: 0 OK, 1 descriptor/op,
/// 2 schema, 3 budget/capacity, 4 embedded-content failure. No partial output.
///
/// # Safety
/// Input/output/required pointers must be valid for their advertised lengths,
/// and required and output must not alias input or each other. Buffers are caller-owned.
#[no_mangle]
pub unsafe extern "C" fn narwhal_ui_content_read(
    op: u32,
    input: *const u8,
    input_len: usize,
    output: *mut u8,
    capacity: usize,
    required: *mut usize,
) -> u32 {
    if required.is_null()
        || input.is_null()
        || input_len == 0
        || (output.is_null() && capacity != 0)
    {
        return 1;
    }
    unsafe {
        *required = 0;
    }
    if input_len > MAX_BYTES || capacity > MAX_BYTES {
        return 3;
    }
    let input = unsafe { std::slice::from_raw_parts(input, input_len) };
    let bytes = match request(op, input) {
        Ok(v) => v,
        Err(status) => return status,
    };
    unsafe {
        *required = bytes.len();
    }
    if output.is_null() {
        return 0;
    }
    if capacity < bytes.len() {
        return 3;
    }
    unsafe {
        std::ptr::copy_nonoverlapping(bytes.as_ptr(), output, bytes.len());
    }
    0
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn rejects_invalid_resource_schema_and_budgets() {
        assert_eq!(Content::load(DATA).err(), None);
        let mut data: Value = serde_json::from_str(DATA).unwrap();
        data["schema"] = json!(2);
        assert!(Content::load(&data.to_string()).is_err());
        data["schema"] = json!(1);
        data["casefold"] = json!([]);
        assert!(Content::load(&data.to_string()).is_err());
        let mut nodes = MAX_NODES;
        assert_eq!(validate_tree(&Value::Null, 0, &mut nodes), Err(3));
        assert!(validate_tree(&json!({"t":"dict","v":[["x",1],["x",2]]}), 0, &mut 0).is_err());
        assert!(validate_tree(&json!({"t":"execute","v":[]}), 0, &mut 0).is_err());
    }
}
