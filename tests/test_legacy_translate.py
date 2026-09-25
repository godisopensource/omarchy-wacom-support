#!/usr/bin/env python3
"""Unit test for legacy hyprctl syntax translation (no tablet needed)."""
import importlib.util
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location(
    "wacom_daemon", os.path.join(REPO_ROOT, "scripts", "wacom-daemon.py")
)
wd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wd)

CASES = [
    ("hyprctl dispatch workspace 1",
     "hyprctl dispatch 'hl.dsp.focus({ workspace = \"1\" })'", True),
    ("hyprctl dispatch workspace m-1",
     "hyprctl dispatch 'hl.dsp.focus({ workspace = \"-1\" })'", True),
    ("hyprctl dispatch workspace m+1",
     "hyprctl dispatch 'hl.dsp.focus({ workspace = \"+1\" })'", True),
    ("hyprctl dispatch workspace e-1",
     "hyprctl dispatch 'hl.dsp.focus({ workspace = \"-1\" })'", True),
    ("hyprctl dispatch workspace previous",
     "hyprctl dispatch 'hl.dsp.focus({ workspace = \"previous\" })'", True),
    ("hyprctl dispatch togglespecialworkspace",
     "hyprctl dispatch 'hl.dsp.workspace.toggle_special(\"scratchpad\")'", True),
    ("hyprctl dispatch fullscreen 0",
     "hyprctl dispatch 'hl.dsp.window.fullscreen({ mode = \"fullscreen\" })'", True),
    # Already-new syntax and unrelated commands pass through untouched.
    ("hyprctl dispatch 'hl.dsp.focus({ workspace = \"2\" })'",
     "hyprctl dispatch 'hl.dsp.focus({ workspace = \"2\" })'", False),
    ("omarchy launch browser", "omarchy launch browser", False),
    ("", "", False),
]


def main():
    failures = 0
    for raw, expected, expected_flag in CASES:
        got, flag = wd.translate_legacy(raw)
        if got != expected or flag != expected_flag:
            print("FAIL: %r -> (%r, %r), want (%r, %r)"
                  % (raw, got, flag, expected, expected_flag))
            failures += 1
    if failures:
        return 1
    print("ok - legacy translation covers %d cases" % len(CASES))
    return 0


if __name__ == "__main__":
    sys.exit(main())
