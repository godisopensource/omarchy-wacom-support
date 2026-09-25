// Shared logic for the Wacom Support plugin (pure functions, no Qt imports
// so the same file can be unit-tested with plain node).

var PLUGIN_ID = "io.github.godisopensource.omarchy-wacom-support";
var BUTTON_COUNT = 4;

function defaultSettings() {
  return {
    autoStart: true,
    label1: "Workspace 1",
    button1: "hyprctl dispatch 'hl.dsp.focus({ workspace = \"1\" })'",
    label2: "Workspace 2",
    button2: "hyprctl dispatch 'hl.dsp.focus({ workspace = \"2\" })'",
    label3: "Workspace 3",
    button3: "hyprctl dispatch 'hl.dsp.focus({ workspace = \"3\" })'",
    label4: "Workspace 4",
    button4: "hyprctl dispatch 'hl.dsp.focus({ workspace = \"4\" })'"
  };
}

// Command presets offered in the panel dropdown. `value` is the shell
// command run when the pad button is pressed; empty means unmapped.
// Hyprland actions use the Lua dispatcher syntax required since 0.55
// (the pre-0.55 `hyprctl dispatch <name> <arg>` form is translated
// automatically by the daemon, see translate_legacy).
function presets() {
  return [
    { value: "", label: "Unmapped" },
    { value: "hyprctl dispatch 'hl.dsp.focus({ workspace = \"1\" })'", label: "Workspace 1" },
    { value: "hyprctl dispatch 'hl.dsp.focus({ workspace = \"2\" })'", label: "Workspace 2" },
    { value: "hyprctl dispatch 'hl.dsp.focus({ workspace = \"3\" })'", label: "Workspace 3" },
    { value: "hyprctl dispatch 'hl.dsp.focus({ workspace = \"4\" })'", label: "Workspace 4" },
    { value: "hyprctl dispatch 'hl.dsp.focus({ workspace = \"-1\" })'", label: "Previous workspace" },
    { value: "hyprctl dispatch 'hl.dsp.focus({ workspace = \"+1\" })'", label: "Next workspace" },
    { value: "hyprctl dispatch 'hl.dsp.focus({ workspace = \"previous\" })'", label: "Last visited workspace" },
    { value: "hyprctl dispatch 'hl.dsp.workspace.toggle_special(\"scratchpad\")'", label: "Scratchpad" },
    { value: "omarchy menu", label: "Omarchy menu" },
    { value: "omarchy launch or focus zen \"omarchy launch browser\"", label: "Browser (focus or launch)" },
    { value: "omarchy launch terminal", label: "Terminal" },
    { value: "omarchy screenshot", label: "Screenshot" },
    { value: "hyprctl dispatch 'hl.dsp.window.fullscreen({ mode = \"fullscreen\" })'", label: "Toggle fullscreen" }
  ];
}

// Merge live widget settings over the defaults. Returns an array of
// { index, label, command } for buttons 1..BUTTON_COUNT.
function buttonRows(settings) {
  var defaults = defaultSettings();
  var rows = [];
  for (var i = 1; i <= BUTTON_COUNT; i++) {
    var label = valueOf(settings, "label" + i, defaults["label" + i]);
    var command = valueOf(settings, "button" + i, defaults["button" + i]);
    rows.push({ index: i, label: String(label), command: String(command) });
  }
  return rows;
}

function valueOf(settings, key, fallback) {
  if (!settings) return fallback;
  var value = settings[key];
  return value === undefined || value === null ? fallback : value;
}

function isEmptyCommand(command) {
  return String(command || "").trim() === "";
}

function autoStartOf(settings) {
  var value = valueOf(settings, "autoStart", true);
  return value === true || value === "true" || value === 1;
}

// Parse the JSON printed by scripts/wacom-status --json. Never throws;
// returns { present, name, node, daemon, pid } with safe fallbacks.
function parseStatus(raw) {
  var fallback = { present: false, name: "", node: "", daemon: false, pid: 0 };
  var text = String(raw || "").trim();
  if (text === "") return fallback;
  try {
    var parsed = JSON.parse(text);
    if (!parsed || typeof parsed !== "object") return fallback;
    return {
      present: parsed.present === true,
      name: String(parsed.name || ""),
      node: String(parsed.node || ""),
      daemon: parsed.daemon === true,
      pid: Number(parsed.pid || 0)
    };
  } catch (e) {
    return fallback;
  }
}

function statusLine(status) {
  if (!status.present) return "No Wacom tablet detected";
  var name = status.name !== "" ? status.name : "Wacom tablet";
  return status.node !== "" ? name + " (" + status.node + ")" : name;
}

function daemonLine(status) {
  if (status.daemon) return "Daemon running (pid " + status.pid + ")";
  return "Daemon stopped";
}
