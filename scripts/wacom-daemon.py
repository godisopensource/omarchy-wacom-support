#!/usr/bin/env python3
"""Passive Wacom pad-button daemon (stdlib only, no root needed).

Watches the evdev node(s) of Wacom tablet *Pad* devices for BTN_0..BTN_3
(the four buttons of e.g. an Intuos S, CTL-4100) and runs the configured
shell command for each button press.

The device is opened read-only and WITHOUT an exclusive grab, so apps with
native tablet support (e.g. Rnote) keep receiving the buttons too.

Button mapping resolution order:
  1. --config <file> when passed explicitly
  2. ~/.config/omarchy-wacom-support/buttons.json when it exists (manual override)
  3. the widget entry (button1..button4 / label1..label4) in
     ~/.config/omarchy/shell.json (written by the bar-widget panel)
  4. built-in defaults (workspaces 1-4)

Config format (JSON object): {"button1": "cmd", ..., "label1": "...", ...}.
Missing keys fall back to the built-in defaults; empty commands are unmapped.

Hotplug is handled: when no pad is present, or a node vanishes, the daemon
re-scans every few seconds instead of exiting.
"""

import argparse
import json
import os
import re
import select
import signal
import struct
import subprocess
import sys
import time

PLUGIN_ID = "io.github.godisopensource.omarchy-wacom-support"
EV_KEY = 0x01
BTN_0 = 0x100
BUTTON_COUNT = 4
DEBOUNCE_SEC = 0.25
RESCAN_SEC = 2.0
# struct input_event: { struct timeval (2x long), __u16, __u16, __s32 }.
# Native byte order/size/alignment ("@") matches the kernel layout.
EVENT = struct.Struct("@llHHi")

DEFAULTS = {
    "label1": "Workspace 1",
    "button1": "hyprctl dispatch workspace 1",
    "label2": "Workspace 2",
    "button2": "hyprctl dispatch workspace 2",
    "label3": "Workspace 3",
    "button3": "hyprctl dispatch workspace 3",
    "label4": "Workspace 4",
    "button4": "hyprctl dispatch workspace 4",
}

stop_requested = False


def home():
    return os.path.expanduser("~")


def state_dir():
    base = os.environ.get("XDG_STATE_HOME", os.path.join(home(), ".local", "state"))
    path = os.path.join(base, "omarchy-wacom-support")
    os.makedirs(path, exist_ok=True)
    return path


def pid_file():
    runtime = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
    return os.path.join(runtime, "omarchy-wacom-support.pid")


def log_file():
    return os.path.join(state_dir(), "daemon.log")


def log(msg):
    try:
        with open(log_file(), "a", encoding="utf-8") as fh:
            fh.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except OSError:
        pass


def on_signal(signum, _frame):
    global stop_requested
    stop_requested = True
    log("signal %d received, stopping" % signum)


def parse_proc_devices():
    """Parse /proc/bus/input/devices into a list of dicts."""
    devices = []
    current = {}
    try:
        with open("/proc/bus/input/devices", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return devices
    for line in text.splitlines():
        if line.strip() == "":
            if current:
                devices.append(current)
                current = {}
            continue
        if len(line) < 3 or line[1] != ":":
            continue
        key = line[0]
        value = line[3:].strip() if len(line) > 3 else ""
        if key == "N":
            if value.startswith('Name="') and value.endswith('"'):
                value = value[len('Name="') : -1]
            current["name"] = value
        elif key == "H":
            if value.startswith("Handlers="):
                value = value[len("Handlers="):]
            current["handlers"] = value
        elif key == "S":
            current["sysfs"] = value
    if current:
        devices.append(current)
    return devices


def event_node_for(device):
    for token in device.get("handlers", "").split():
        if token.startswith("event"):
            return "/dev/input/" + token
    return None


def is_wacom_pad(device, match):
    name = device.get("name", "")
    return "Wacom" in name and match in name and event_node_for(device) is not None


def find_pads(match):
    return [d for d in parse_proc_devices() if is_wacom_pad(d, match)]


def load_json_file(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def widget_settings_from_shell():
    """Read button mapping from the bar-widget entry in shell.json."""
    shell_json = os.path.join(home(), ".config", "omarchy", "shell.json")
    data = load_json_file(shell_json)
    try:
        layout = data.get("bar", {}).get("layout", {})
    except AttributeError:
        return {}
    for section in ("left", "center", "right"):
        entries = layout.get(section, [])
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict) and entry.get("id") == PLUGIN_ID:
                return entry
    return {}


MAPPING_KEYS = tuple(
    ["autoStart"]
    + ["label%d" % i for i in range(1, BUTTON_COUNT + 1)]
    + ["button%d" % i for i in range(1, BUTTON_COUNT + 1)]
)


def with_defaults(partial):
    """Merge a partial mapping over DEFAULTS (explicit "" stays unmapped)."""
    merged = dict(DEFAULTS)
    for key in MAPPING_KEYS:
        if key in partial and isinstance(partial[key], str):
            merged[key] = partial[key]
    return merged


def effective_config(explicit_path=None):
    """Return (config_dict, source_description)."""
    if explicit_path:
        return with_defaults(load_json_file(explicit_path)), "file:" + explicit_path
    override = os.path.join(
        home(), ".config", "omarchy-wacom-support", "buttons.json"
    )
    if os.path.exists(override):
        return with_defaults(load_json_file(override)), "file:" + override
    widget = widget_settings_from_shell()
    if any(
        isinstance(widget.get("button%d" % i), str)
        for i in range(1, BUTTON_COUNT + 1)
    ):
        return with_defaults(widget), "shell.json:" + PLUGIN_ID
    return dict(DEFAULTS), "built-in defaults"


def button_command(config, index):
    cmd = config.get("button%d" % index, "")
    return cmd.strip() if isinstance(cmd, str) else ""


LEGACY_WORKSPACE_RE = re.compile(r"^hyprctl\s+dispatch\s+workspace\s+(\S+)\s*$")
LEGACY_FULLSCREEN_RE = re.compile(r"^hyprctl\s+dispatch\s+fullscreen\s+\S+\s*$")


def translate_legacy(cmd):
    """Translate pre-Hyprland-0.55 dispatch syntax to Lua dispatchers.

    Since 0.55 (Lua config), `hyprctl dispatch <name> <arg>` is rejected,
    so old mappings would fail silently. Returns (command, translated).
    """
    if not isinstance(cmd, str):
        return cmd, False
    text = cmd.strip()
    m = LEGACY_WORKSPACE_RE.match(text)
    if m:
        sel = m.group(1)
        # Pre-0.55 relative selectors no longer cycle via hyprctl: only the
        # bare +1/-1 forms were verified to work (e±1/m±1 silently no-op).
        if sel in ("m-1", "e-1"):
            sel = "-1"
        elif sel in ("m+1", "e+1"):
            sel = "+1"
        return (
            "hyprctl dispatch 'hl.dsp.focus({ workspace = \"%s\" })'" % sel,
            True,
        )
    if text == "hyprctl dispatch togglespecialworkspace":
        return (
            "hyprctl dispatch 'hl.dsp.workspace.toggle_special(\"scratchpad\")'",
            True,
        )
    if LEGACY_FULLSCREEN_RE.match(text):
        return (
            "hyprctl dispatch 'hl.dsp.window.fullscreen({ mode = \"fullscreen\" })'",
            True,
        )
    return cmd, False


def resolve_command(config, index):
    """Final shell command for a button (legacy syntax translated)."""
    cmd = button_command(config, index)
    if not cmd:
        return "", False
    return translate_legacy(cmd)


def run_command(cmd, index):
    # Fire-and-forget: the child is never waited on nor killed, so launched
    # apps (terminal, browser…) survive. Its output is appended to the daemon
    # log instead of /dev/null, so failures stay visible (the panel greps
    # this log for errors).
    log("button %d -> %s" % (index, cmd))
    try:
        logfh = open(log_file(), "ab", buffering=0)
    except OSError as exc:
        log("button %d cannot open log: %s" % (index, exc))
        logfh = None
    try:
        proc = subprocess.Popen(
            ["sh", "-c", cmd],
            stdin=subprocess.DEVNULL,
            stdout=logfh if logfh is not None else subprocess.DEVNULL,
            stderr=subprocess.STDOUT if logfh is not None else subprocess.DEVNULL,
            start_new_session=True,
            env=dict(os.environ),
            close_fds=True,
        )
    except OSError as exc:
        log("button %d failed to spawn: %s" % (index, exc))
        if logfh is not None:
            logfh.close()
        return
    if logfh is not None:
        logfh.close()  # child keeps its own copy; O_APPEND keeps lines intact
    log("button %d spawned (pid %d)" % (index, proc.pid))


def handle_frame(chunk, get_config, last_press):
    """Process one input_event frame. Returns True when a device vanished."""
    if len(chunk) < EVENT.size:
        return True
    _sec, _usec, ev_type, ev_code, ev_value = EVENT.unpack(chunk)
    if ev_type != EV_KEY or ev_value != 1:
        return False
    index = ev_code - BTN_0 + 1
    if index < 1 or index > BUTTON_COUNT:
        return False
    now = time.monotonic()
    if now - last_press.get(index, 0.0) < DEBOUNCE_SEC:
        return False
    last_press[index] = now
    cmd, translated = resolve_command(get_config(), index)
    if translated:
        log("button %d legacy syntax translated" % index)
    if cmd:
        run_command(cmd, index)
    else:
        log("button %d pressed (unmapped)" % index)
    return False


def serve_opened(fds, get_config, last_press):
    """Event loop over already-opened {fileno: (file, pad)} devices.

    Returns True when devices remain (stop requested), False when every
    device vanished and the caller should re-scan.
    """
    while not stop_requested and fds:
        try:
            ready, _, _ = select.select(list(fds), [], [], 1.0)
        except (select.error, OSError):
            break
        for fileno in ready:
            fh, pad = fds[fileno]
            try:
                chunk = os.read(fileno, EVENT.size)
            except OSError:
                chunk = b""
            if handle_frame(chunk, get_config, last_press):
                log("device vanished: %s" % event_node_for(pad))
                try:
                    fh.close()
                except OSError:
                    pass
                del fds[fileno]
                break
    return bool(fds)
    log("button %d -> %s" % (index, cmd))
    try:
        subprocess.Popen(
            ["sh", "-c", cmd],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=dict(os.environ),
        )
    except OSError as exc:
        log("button %d failed to spawn: %s" % (index, exc))


def eviocgbit_key_codes(node, lo=BTN_0, hi=BTN_0 + 15):
    """Return sorted list of pressed-capable key codes via EVIOCGBIT ioctl.

    Falls back to [] when the ioctl is unavailable; callers then assume
    BTN_0..BTN_3.
    """
    import fcntl

    length = 64  # bytes; covers codes 0..511
    req = (2 << 30) | (length << 16) | (0x45 << 8) | (0x20 + EV_KEY)
    try:
        with open(node, "rb") as fh:
            buf = fcntl.ioctl(fh.fileno(), req, b"\x00" * length)
    except OSError:
        return []
    codes = []
    for code in range(lo, hi + 1):
        if buf[code // 8] & (1 << (code % 8)):
            codes.append(code)
    return codes


def monitor(match, explicit_config):
    last_press = {}
    config_cache = {}
    config_mtime = {}

    def get_config():
        if explicit_config:
            key = explicit_config
            try:
                mtime = os.path.getmtime(explicit_config)
            except OSError:
                mtime = -1
        else:
            key = "auto"
            mtimes = []
            for path in (
                os.path.join(home(), ".config", "omarchy", "shell.json"),
                os.path.join(home(), ".config", "omarchy-wacom-support", "buttons.json"),
            ):
                try:
                    mtimes.append(os.path.getmtime(path))
                except OSError:
                    mtimes.append(-1)
            mtime = tuple(mtimes)
        if config_cache.get("key") != key or config_mtime.get(key) != mtime:
            config, source = effective_config(explicit_config)
            config_cache["key"] = key
            config_cache["config"] = config
            config_cache["source"] = source
            config_mtime[key] = mtime
            log("using mapping from %s" % source)
        return config_cache["config"]

    while not stop_requested:
        pads = find_pads(match)
        if not pads:
            time.sleep(RESCAN_SEC)
            continue
        fds = {}
        for pad in pads:
            node = event_node_for(pad)
            try:
                fh = open(node, "rb")
            except OSError as exc:
                log("cannot open %s (%s): %s" % (node, pad.get("name"), exc))
                continue
            fds[fh.fileno()] = (fh, pad)
            log("listening on %s (%s)" % (node, pad.get("name")))
        if not fds:
            time.sleep(RESCAN_SEC)
            continue
        try:
            serve_opened(fds, get_config, last_press)
        finally:
            for fh, _pad in fds.values():
                try:
                    fh.close()
                except OSError:
                    pass


def cmd_probe(match):
    pads = find_pads(match)
    if not pads:
        print("No Wacom pad device found (match=%r)." % match)
        print("Is the tablet plugged in? See: libwacom-list-local-devices")
        return 1
    for pad in pads:
        node = event_node_for(pad)
        print("%s -> %s" % (pad.get("name"), node))
        codes = eviocgbit_key_codes(node)
        if codes:
            for code in codes:
                print("  button %d: evdev code %d (0x%x)" % (code - BTN_0 + 1, code, code))
        else:
            print("  (ioctl unavailable; assuming buttons 1..%d = BTN_0..BTN_%d)"
                  % (BUTTON_COUNT, BUTTON_COUNT - 1))
    return 0


def cmd_check_config(explicit_config):
    config, source = effective_config(explicit_config)
    print("source: %s" % source)
    for i in range(1, BUTTON_COUNT + 1):
        label = config.get("label%d" % i, "")
        cmd, translated = resolve_command(config, i)
        suffix = "  [translated from legacy syntax]" if translated else ""
        print("button %d [%s]: %s%s" % (i, label, cmd if cmd else "(unmapped)", suffix))
    return 0


def cmd_dump_events(match, timeout):
    pads = find_pads(match)
    if not pads:
        print("No Wacom pad device found.")
        return 1
    print("Press pad buttons (Ctrl+C to stop)…")
    deadline = time.monotonic() + timeout if timeout > 0 else None
    fds = {}
    for pad in pads:
        try:
            fh = open(event_node_for(pad), "rb")
            fds[fh.fileno()] = fh
        except OSError as exc:
            print("cannot open %s: %s" % (event_node_for(pad), exc))
    try:
        while fds:
            if deadline is not None and time.monotonic() > deadline:
                break
            ready, _, _ = select.select(list(fds), [], [], 0.5)
            for fileno in ready:
                chunk = os.read(fileno, EVENT.size)
                if len(chunk) < EVENT.size:
                    fds.pop(fileno).close()
                    break
                sec, _usec, ev_type, ev_code, ev_value = EVENT.unpack(chunk)
                print("type=%d code=%d value=%d" % (ev_type, ev_code, ev_value))
    except KeyboardInterrupt:
        pass
    finally:
        for fh in fds.values():
            fh.close()
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Passive Wacom pad-button daemon.")
    parser.add_argument("--match", default="Pad",
                        help="substring matched against the Wacom device name (default: Pad)")
    parser.add_argument("--config", default=None,
                        help="explicit JSON mapping file (else override file, else shell.json)")
    parser.add_argument("--probe", action="store_true",
                        help="list detected pad devices and their buttons, then exit")
    parser.add_argument("--check-config", action="store_true",
                        help="print the effective button mapping, then exit")
    parser.add_argument("--dump-events", action="store_true",
                        help="print raw pad events for debugging, then exit")
    parser.add_argument("--dump-timeout", type=int, default=15,
                        help="seconds for --dump-events (0 = until Ctrl+C)")
    args = parser.parse_args(argv)

    if args.probe:
        return cmd_probe(args.match)
    if args.check_config:
        return cmd_check_config(args.config)
    if args.dump_events:
        return cmd_dump_events(args.match, args.dump_timeout)

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGHUP, lambda s, f: log("SIGHUP: config re-read on next press"))
    signal.signal(signal.SIGINT, on_signal)

    pidpath = pid_file()
    try:
        with open(pidpath, "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
    except OSError as exc:
        print("cannot write pid file %s: %s" % (pidpath, exc), file=sys.stderr)
        return 1
    log("daemon started (pid %d)" % os.getpid())
    try:
        monitor(args.match, args.config)
    finally:
        try:
            os.unlink(pidpath)
        except OSError:
            pass
        log("daemon stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
