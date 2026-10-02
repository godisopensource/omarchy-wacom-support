#!/usr/bin/env python3
"""Regression tests for PID-reuse hardening (no tablet needed, stdlib only).

Covers the review finding: wacom-ctl stop/restart must not SIGTERM a
recycled PID, and the daemon must not truncate a predictable PID path.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
from contextlib import redirect_stderr
from io import StringIO

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location(
    "wacom_daemon", os.path.join(REPO_ROOT, "scripts", "wacom-daemon.py")
)
wd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wd)

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print("ok   - %s" % name)
    else:
        print("FAIL - %s %s" % (name, detail))
        FAILURES.append(name)


def spawn_fake_daemon():
    """Live process whose /proc cmdline contains our 'wacom-daemon' marker."""
    proc = subprocess.Popen(
        ["bash", "-c", "exec -a wacom-daemon-test sleep 30"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    time.sleep(0.2)
    return proc


def spawn_foreign():
    proc = subprocess.Popen(
        ["sleep", "30"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    time.sleep(0.2)
    return proc


def run_ctl(*args, runtime_dir, state_dir=None):
    env = dict(os.environ, XDG_RUNTIME_DIR=runtime_dir)
    if state_dir is not None:
        env["XDG_STATE_HOME"] = state_dir
    out = subprocess.run(
        ["bash", os.path.join(REPO_ROOT, "scripts", "wacom-ctl")] + list(args),
        capture_output=True,
        text=True,
        timeout=15,
        env=env,
    )
    return out


def run_status(runtime_dir, state_dir=None):
    env = dict(os.environ, XDG_RUNTIME_DIR=runtime_dir)
    if state_dir is not None:
        env["XDG_STATE_HOME"] = state_dir
    out = subprocess.run(
        ["bash", os.path.join(REPO_ROOT, "scripts", "wacom-status"), "--json"],
        capture_output=True,
        text=True,
        timeout=15,
        env=env,
    )
    return out


def main():
    # 1. Runtime fallback must never be /tmp directly.
    old_xdg = os.environ.get("XDG_RUNTIME_DIR")
    fake_home = tempfile.mkdtemp(prefix="wacom-pid-home-")
    try:
        if "XDG_RUNTIME_DIR" in os.environ:
            del os.environ["XDG_RUNTIME_DIR"]
        os.environ["HOME"] = fake_home
        # Hide the host /run/user/$UID path only if it does not exist;
        # otherwise asserting "not /tmp" is the meaningful property.
        rt = wd.resolve_runtime_dir()
        check(
            "runtime fallback is private (not /tmp)",
            rt != "/tmp" and not rt.startswith("/tmp/"),
            "got %r" % rt,
        )
        check(
            "pid_file lives in runtime dir",
            os.path.dirname(wd.pid_file()) == wd.resolve_runtime_dir(),
            "got %r" % wd.pid_file(),
        )
    finally:
        if old_xdg is not None:
            os.environ["XDG_RUNTIME_DIR"] = old_xdg
        os.environ["HOME"] = os.path.expanduser("~")

    # 2. Identity check: live foreign PID is NOT our daemon.
    foreign = spawn_foreign()
    try:
        check(
            "is_live_daemon rejects live foreign pid",
            wd.is_live_daemon(foreign.pid) is False,
            "pid=%d" % foreign.pid,
        )
        check("is_live_daemon rejects pid 1", wd.is_live_daemon(1) is False)
        check("is_live_daemon rejects garbage", wd.is_live_daemon("nope") is False)
        check("is_live_daemon rejects dead pid", wd.is_live_daemon(999999) is False)
        check(
            "is_live_daemon rejects test harness itself",
            wd.is_live_daemon(os.getpid()) is False,
        )
    finally:
        foreign.terminate()
        foreign.wait()

    fake = spawn_fake_daemon()
    try:
        check(
            "is_live_daemon accepts marker cmdline",
            wd.is_live_daemon(fake.pid) is True,
            "pid=%d" % fake.pid,
        )

        # 3. acquire_pidfile refuses while a live daemon owns the slot.
        runtime = tempfile.mkdtemp(prefix="wacom-runtime-")
        os.environ["XDG_RUNTIME_DIR"] = runtime
        try:
            with open(wd.pid_file(), "w", encoding="utf-8") as fh:
                fh.write(str(fake.pid))
            with redirect_stderr(StringIO()):
                refused = wd.acquire_pidfile() is None
            check(
                "acquire refuses live daemon pidfile",
                refused,
            )
            # The live owner's file must survive the refusal.
            check(
                "live pidfile untouched by refusal",
                wd.read_pidfile(wd.pid_file()) == fake.pid,
            )

            # 4. Stale pidfile is replaced, not mistaken for a daemon.
            with open(wd.pid_file(), "w", encoding="utf-8") as fh:
                fh.write("999999")
            path = wd.acquire_pidfile()
            check("acquire replaces stale pidfile", path is not None)
            if path is not None:
                check(
                    "stale pidfile now names us",
                    wd.read_pidfile(path) == os.getpid(),
                )
                # 5. release removes only our own file.
                wd.release_pidfile(path)
                check("release removes own pidfile", not os.path.exists(path))

            with open(wd.pid_file(), "w", encoding="utf-8") as fh:
                fh.write(str(fake.pid))
            wd.release_pidfile(wd.pid_file())
            check(
                "release keeps foreign pidfile",
                wd.read_pidfile(wd.pid_file()) == fake.pid,
            )
        finally:
            if old_xdg is not None:
                os.environ["XDG_RUNTIME_DIR"] = old_xdg
            elif "XDG_RUNTIME_DIR" in os.environ:
                del os.environ["XDG_RUNTIME_DIR"]
    finally:
        fake.terminate()
        fake.wait()

    # 6. End-to-end: `wacom-ctl stop` must not kill a recycled PID.
    runtime2 = tempfile.mkdtemp(prefix="wacom-runtime-e2e-")
    victim = spawn_foreign()
    try:
        pidfile = os.path.join(runtime2, "omarchy-wacom-support.pid")
        with open(pidfile, "w", encoding="utf-8") as fh:
            fh.write(str(victim.pid))
        res = run_ctl("stop", runtime_dir=runtime2)
        alive = victim.poll() is None
        check(
            "ctl stop spares recycled pid",
            alive,
            "stop rc=%d out=%r err=%r" % (res.returncode, res.stdout, res.stderr),
        )
        check(
            "ctl stop cleans stale pidfile",
            not os.path.exists(pidfile),
            "pidfile still present",
        )
        check("ctl stop exits 0 on stale", res.returncode == 0, "rc=%d" % res.returncode)
        st = run_status(runtime2)
        try:
            payload = json.loads(st.stdout)
        except ValueError:
            payload = {}
        check(
            "status reports stopped for foreign pid",
            payload.get("daemon") is False,
            "got %r" % st.stdout,
        )
    finally:
        if victim.poll() is None:
            victim.terminate()
            victim.wait()

    # 7. End-to-end: status sees a live fake daemon, stop terminates it.
    runtime3 = tempfile.mkdtemp(prefix="wacom-runtime-e2e2-")
    fake2 = spawn_fake_daemon()
    real_daemon = None
    try:
        pidfile3 = os.path.join(runtime3, "omarchy-wacom-support.pid")
        with open(pidfile3, "w", encoding="utf-8") as fh:
            fh.write(str(fake2.pid))
        st = run_status(runtime3)
        try:
            payload = json.loads(st.stdout)
        except ValueError:
            payload = {}
        check(
            "status reports running for live daemon pid",
            payload.get("daemon") is True and payload.get("pid") == fake2.pid,
            "got %r" % st.stdout,
        )
        # A real daemon started via `wacom-ctl start` is stoppable again
        # (proves the verified stop path works for the genuine case).
        res = run_ctl("start", runtime_dir=runtime3)
        # start refuses because fake pidfile looks live; that is the point:
        # no second daemon, no SIGTERM to the existing one.
        check(
            "ctl start refuses while daemon live",
            "already running" in (res.stdout or ""),
            "out=%r err=%r" % (res.stdout, res.stderr),
        )
        check("fake daemon survived refused start", fake2.poll() is None)
    finally:
        fake2.terminate()
        fake2.wait()
        if real_daemon is not None and real_daemon.poll() is None:
            real_daemon.terminate()

    # 8. Genuine lifecycle still works: start -> status -> stop in isolation.
    runtime4 = tempfile.mkdtemp(prefix="wacom-runtime-live-")
    state4 = tempfile.mkdtemp(prefix="wacom-state-live-")
    try:
        res = run_ctl("start", runtime_dir=runtime4, state_dir=state4)
        check(
            "ctl start launches daemon",
            res.returncode == 0 and "started" in (res.stdout or ""),
            "out=%r err=%r" % (res.stdout, res.stderr),
        )
        pidfile4 = os.path.join(runtime4, "omarchy-wacom-support.pid")
        try:
            with open(pidfile4, encoding="utf-8") as fh:
                live_pid = int(fh.read().strip().split()[0])
        except (OSError, ValueError, IndexError):
            live_pid = None
        check(
            "daemon pidfile names live daemon",
            live_pid is not None and wd.is_live_daemon(live_pid),
            "pid=%r" % (live_pid,),
        )
        st = run_status(runtime4, state_dir=state4)
        try:
            payload = json.loads(st.stdout)
        except ValueError:
            payload = {}
        check(
            "status sees genuine daemon",
            payload.get("daemon") is True and payload.get("pid") == live_pid,
            "got %r" % st.stdout,
        )
        res = run_ctl("stop", runtime_dir=runtime4, state_dir=state4)
        check(
            "ctl stop terminates genuine daemon",
            res.returncode == 0 and "stopped" in (res.stdout or ""),
            "out=%r err=%r" % (res.stdout, res.stderr),
        )
        check("genuine pidfile cleaned", not os.path.exists(pidfile4))
    finally:
        # Best-effort cleanup if stop failed mid-test.
        try:
            with open(os.path.join(runtime4, "omarchy-wacom-support.pid"),
                      encoding="utf-8") as fh:
                leftover = int(fh.read().strip().split()[0])
        except (OSError, ValueError, IndexError):
            leftover = None
        if leftover is not None and wd.is_live_daemon(leftover):
            try:
                os.kill(leftover, 15)
            except OSError:
                pass

    # 9. Lifetime lock: a second foreground daemon in the same runtime
    # dir refuses immediately, before opening any device.
    runtime5 = tempfile.mkdtemp(prefix="wacom-runtime-lock-")
    state5 = tempfile.mkdtemp(prefix="wacom-state-lock-")
    first = None
    try:
        env5 = dict(
            os.environ, XDG_RUNTIME_DIR=runtime5, XDG_STATE_HOME=state5
        )
        first = subprocess.Popen(
            ["python3", os.path.join(REPO_ROOT, "scripts", "wacom-daemon.py")],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env5,
        )
        pidfile5 = os.path.join(runtime5, "omarchy-wacom-support.pid")
        for _ in range(100):
            if os.path.exists(pidfile5):
                break
            time.sleep(0.1)
        try:
            with open(pidfile5, encoding="utf-8") as fh:
                lock_pid = int(fh.read().strip().split()[0])
        except (OSError, ValueError, IndexError):
            lock_pid = None
        check(
            "first daemon owns pidfile under lock",
            lock_pid == first.pid,
            "pid=%r proc=%d" % (lock_pid, first.pid),
        )
        second = subprocess.run(
            ["python3", os.path.join(REPO_ROOT, "scripts", "wacom-daemon.py")],
            capture_output=True,
            text=True,
            timeout=15,
            env=env5,
        )
        check(
            "second daemon refused while lock held",
            second.returncode == 1
            and "already running" in (second.stderr or ""),
            "rc=%d err=%r" % (second.returncode, second.stderr),
        )
        check("first daemon survives refused double-start", first.poll() is None)
    finally:
        if first is not None and first.poll() is None:
            first.terminate()
            try:
                first.wait(timeout=10)
            except subprocess.TimeoutExpired:
                first.kill()
    check(
        "pidfile cleaned after lock holder exits",
        not os.path.exists(os.path.join(runtime5, "omarchy-wacom-support.pid")),
    )

    # 10. `wacom-ctl stop` sweeps stragglers running the same script path
    # (duplicates from a concurrent-start race), not just the pidfile PID.
    runtime6 = tempfile.mkdtemp(prefix="wacom-runtime-sweep-")
    daemon_abs = os.path.join(REPO_ROOT, "scripts", "wacom-daemon.py")
    stray = subprocess.Popen(
        ["bash", "-c", 'exec -a "%s" sleep 30' % daemon_abs],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        time.sleep(0.3)
        res = run_ctl("stop", runtime_dir=runtime6)
        check(
            "ctl stop sweeps same-script straggler",
            stray.poll() is not None,
            "stray still alive; out=%r" % (res.stdout,),
        )
        check(
            "ctl stop reports swept duplicate",
            "duplicate" in (res.stdout or ""),
            "out=%r" % (res.stdout,),
        )
    finally:
        if stray.poll() is None:
            stray.terminate()
            stray.wait()

    if FAILURES:
        print("FAIL: %d pidfile check(s) failed" % len(FAILURES))
        return 1
    print("ok - pidfile hardening covers pid reuse")
    return 0


if __name__ == "__main__":
    sys.exit(main())
