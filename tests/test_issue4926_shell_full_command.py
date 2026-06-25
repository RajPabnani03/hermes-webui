"""Regression coverage for #4926 — the EXPANDED shell tool card must show the
full multi-line command, not just the first line.

In the transparent activity stream, a shell/terminal tool card's collapsed
header correctly shows only the first line of the command (compact metadata),
but the EXPANDED card's detail lead (`$ <command>`) was reusing that same
first-line-only value, so a multi-line script still rendered as line 1 when the
user expanded it. The full command is available frontend-side; the truncation
was purely in the label derivation. The fix keeps the collapsed header on the
first line but gives the expanded detail lead the full command.

Drives the real `static/ui.js` helpers under node (no server).
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.resolve()
UI_JS_PATH = REPO_ROOT / "static" / "ui.js"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node not on PATH")

# Extract the three helper functions by name and eval them with light stubs for
# their dependencies (redaction / entity-decode / action-kind), so the test is
# resilient to the rest of ui.js not being importable headlessly.
_DRIVER_SRC = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');
function grab(name){
  const re = new RegExp('function ' + name + '\\([^]*?\\n}', 'm');
  const m = src.match(re);
  if (!m) throw new Error('function not found: ' + name);
  return m[0];
}
// Stubs for dependencies of the grabbed functions.
global._redactToolTargetLabel = (s) => s;
global._decodeToolLabelEntities = (s) => s;
global._toolActionKind = (tc) => 'shell';
eval(grab('_toolTargetLabel'));
eval(grab('_toolFullCommandLabel'));
eval(grab('_toolDetailLeadText'));
let buf = '';
process.stdin.on('data', c => { buf += c; });
process.stdin.on('end', () => {
  const payload = JSON.parse(buf || '{}');
  const tc = payload.tc || {};
  process.stdout.write(JSON.stringify({
    header: _toolTargetLabel(tc),
    lead: _toolDetailLeadText('shell', tc),
  }));
});
"""


@pytest.fixture(scope="module")
def driver_path(tmp_path_factory):
    p = tmp_path_factory.mktemp("shell_fullcmd_driver") / "driver.js"
    p.write_text(_DRIVER_SRC, encoding="utf-8")
    return str(p)


def _run(driver_path: str, tc: dict) -> dict:
    assert NODE is not None
    result = subprocess.run(
        [NODE, driver_path, str(UI_JS_PATH)],
        input=json.dumps({"tc": tc}),
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return json.loads(result.stdout)


_MULTILINE = (
    "cd ~/hermes-webui-public\n"
    "git fetch origin --tags -q\n"
    "for t in a b c; do echo $t; done\n"
    "echo done"
)


def test_expanded_shell_lead_shows_full_multiline_command(driver_path):
    """#4926 — the expanded detail lead must contain every line of the command."""
    out = _run(driver_path, {"args": {"command": _MULTILINE}})
    lead = out["lead"]
    assert lead.startswith("$ "), lead
    # Every line of the original command must be present in the expanded lead.
    for line in _MULTILINE.split("\n"):
        assert line in lead, f"expanded lead dropped line {line!r}: {lead!r}"
    # And it must actually be multi-line (the bug rendered it as one line).
    assert lead.count("\n") == 3, f"expected 4 lines in expanded lead, got: {lead!r}"


def test_collapsed_header_stays_first_line_only(driver_path):
    """The compact header should remain the first line (unchanged behavior)."""
    out = _run(driver_path, {"args": {"command": _MULTILINE}})
    assert out["header"] == "cd ~/hermes-webui-public", out["header"]
    assert "\n" not in out["header"]


def test_single_line_command_unchanged(driver_path):
    """A single-line command is identical in header and (less the `$ `) lead."""
    out = _run(driver_path, {"args": {"command": "ls -la /tmp"}})
    assert out["header"] == "ls -la /tmp"
    assert out["lead"] == "$ ls -la /tmp"


def test_command_from_top_level_field(driver_path):
    """Full-command accessor also reads tc.command (not just tc.args.command)."""
    out = _run(driver_path, {"command": "echo one\necho two"})
    assert "echo one" in out["lead"] and "echo two" in out["lead"]
    assert out["lead"].count("\n") == 1
