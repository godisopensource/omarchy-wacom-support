#!/usr/bin/env bash
# Smoke tests for the Wacom Support plugin. No tablet required, except the
# live checks which are skipped gracefully when absent.
set -u
cd "$(dirname "$0")/.."
fail=0

ok()   { echo "ok   - $1"; }
bad()  { echo "FAIL - $1"; fail=1; }

# 1. manifest: valid JSON + required marketplace fields.
if python3 -c "
import json
m = json.load(open('manifest.json'))
for k in ['schemaVersion','id','name','version','author','license','description','kinds','entryPoints']:
    assert k in m, 'missing ' + k
assert m['schemaVersion'] == 1
assert m['kinds'] == ['bar-widget']
assert m['entryPoints']['barWidget'] == 'BarWidget.qml'
assert m['id'] == 'io.github.godisopensource.omarchy-wacom-support'
assert not m['id'].startswith('omarchy.')
"; then ok "manifest.json"; else bad "manifest.json"; fi

# 2. entry points referenced by the manifest exist.
for f in BarWidget.qml Panel.qml Model.js; do
  if [[ -f "$f" ]]; then ok "$f exists"; else bad "$f missing"; fi
done

# 3. python syntax.
if python3 -m py_compile scripts/wacom-daemon.py; then ok "daemon compiles"; else bad "daemon compile"; fi

# 4. bash syntax.
for s in scripts/wacom-status scripts/wacom-ctl; do
  if bash -n "$s"; then ok "$s syntax"; else bad "$s syntax"; fi
done

# 5. Model.js parses under node when available.
if command -v node >/dev/null 2>&1; then
  if node -e "
const fs = require('fs');
const src = fs.readFileSync('Model.js','utf8');
const Model = new Function(src + '; return {buttonRows, presets, parseStatus, defaultSettings, autoStartOf};')();
const rows = Model.buttonRows({});
if (rows.length !== 4 || rows[0].command.indexOf('hl.dsp.focus') === -1 || rows[0].command.indexOf('workspace = ') === -1) throw new Error('defaults');
if (Model.parseStatus('nope').present !== false) throw new Error('parseStatus');
if (!Model.presets().length) throw new Error('presets');
"; then ok "Model.js logic"; else bad "Model.js logic"; fi
else
  echo "skip - node not installed (Model.js check)"
fi

# 6. status probe always emits valid JSON and never crashes.
if out="$(bash scripts/wacom-status --json)" && python3 -c "import json,sys; json.loads('''$out''')" 2>/dev/null; then
  ok "wacom-status --json ($out)"
else
  bad "wacom-status --json"
fi

# 7. mapping resolution works (defaults or shell.json).
if python3 scripts/wacom-daemon.py --check-config >/dev/null; then
  ok "daemon --check-config"
else
  bad "daemon --check-config"
fi

# 7b. serve-loop unit test (pipe-fed frames, no hardware).
if python3 tests/test_daemon_serve.py; then
  : # test prints its own ok line
else
  bad "daemon serve loop"
fi

# 7c. legacy hyprctl translation unit test.
if python3 tests/test_legacy_translate.py; then
  : # test prints its own ok line
else
  bad "legacy translation"
fi

# 8. pad detection (live; informational when no tablet).
if python3 scripts/wacom-daemon.py --probe >/dev/null 2>&1; then
  ok "daemon --probe (pad found)"
else
  echo "skip - no Wacom pad detected right now"
fi

exit "$fail"
