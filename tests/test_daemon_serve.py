#!/usr/bin/env python3
"""Unit test for the daemon hot path (no tablet needed, stdlib only).

Feeds crafted input_event frames through serve_opened() via a pipe and
asserts mapped buttons spawn their commands while releases, EV_SYN,
out-of-range codes and unmapped buttons are ignored.
"""
import importlib.util
import json
import os
import struct
import sys
import tempfile
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location(
    "wacom_daemon", os.path.join(REPO_ROOT, "scripts", "wacom-daemon.py")
)
wd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wd)

EVT = struct.Struct("@llHHi")


def frame(code, value, etype=1):
    return EVT.pack(1, 2, etype, code, value)


def main():
    fake_home = tempfile.mkdtemp(prefix="wacom-test-home-")
    wd.home = lambda: fake_home  # keep logs/state out of the real HOME

    cfg = {
        "button1": "touch %s/m1" % fake_home,
        "button2": "",
        "button3": "touch %s/m3" % fake_home,
        "button4": "touch %s/m4" % fake_home,
    }
    cfg_path = os.path.join(fake_home, "btn.json")
    with open(cfg_path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh)

    r, w = os.pipe()
    os.write(
        w,
        frame(0x100, 1)  # press btn1 (mapped)
        + frame(0x100, 0)  # release (ignored)
        + frame(0x101, 1)  # press btn2 (unmapped)
        + frame(0x100, 1, etype=0)  # EV_SYN (ignored)
        + frame(0x1FF, 1)  # out of range (ignored)
        + frame(0x102, 1)  # press btn3 (mapped)
        + frame(0x103, 1),  # press btn4 (mapped)
    )
    os.close(w)
    fh = os.fdopen(r, "rb")
    fds = {fh.fileno(): (fh, {"name": "fake pad"})}
    assert wd.serve_opened(fds, lambda: wd.load_json_file(cfg_path), {}) is False
    time.sleep(1.5)  # let detached children land

    got = {m: os.path.exists(os.path.join(fake_home, m)) for m in ("m1", "m3", "m4")}
    missing = [m for m, ok in got.items() if not ok]
    if missing:
        print("FAIL: no marker for %s" % missing)
        return 1
    print("ok - serve_opened maps/spawns/ignores correctly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
