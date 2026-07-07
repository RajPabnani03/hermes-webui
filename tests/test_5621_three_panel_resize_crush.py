"""Regression tests for Issue #5621: three-panel layout crush on resize.

The desktop three-panel layout (sidebar, chat, right workspace panel) can be
resized so aggressively that the center chat area becomes unusably narrow. The
fix clamps panel widths during drag, on restoration, and on window resize so the
chat area always keeps at least MAIN_PANEL_MIN_WIDTH.
"""

import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
BOOT_JS = ROOT / "static" / "boot.js"
NODE = shutil.which("node")

node_test = pytest.mark.skipif(NODE is None, reason="node not on PATH")


def _function_block(source: str, name: str) -> str:
    marker = re.search(rf"(^|\n)(?:async\s+)?function\s+{re.escape(name)}\(", source)
    assert marker is not None, f"{name}() not found"
    start = marker.start()
    next_marker = re.search(
        r"\n(?:function\s+\w+\(|async\s+function\s+\w+\(|class\s+\w+)",
        source[start + 1 :],
    )
    if next_marker:
        end = start + 1 + next_marker.start()
    else:
        end = len(source)
    return source[start:end]


def _run_node(script: str) -> dict:
    result = subprocess.run(
        [NODE, "-e", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout)


def test_constants_define_main_panel_min_width():
    src = BOOT_JS.read_text(encoding="utf-8")
    assert "MAIN_PANEL_MIN_WIDTH=420" in src, (
        "boot.js must define a floor for the center chat area"
    )
    assert "SIDEBAR_MIN=180" in src, (
        "boot.js must expose the sidebar min width constant"
    )
    assert "SIDEBAR_MAX=420" in src, (
        "boot.js must expose the sidebar max width constant"
    )
    assert "PANEL_MIN=180" in src, (
        "boot.js must expose the right panel min width constant"
    )
    assert "PANEL_MAX=1200" in src, (
        "boot.js must expose the right panel max width constant"
    )


def test_clamp_helper_exists():
    src = BOOT_JS.read_text(encoding="utf-8")
    assert "function _clampPanelWidthForViewport" in src, (
        "boot.js must expose a panel-width clamp helper"
    )
    assert "function _clampBothPanelsOnResize" in src, (
        "boot.js must expose a viewport-resize clamp helper"
    )


def test_sync_workspace_panel_inline_width_uses_clamp():
    src = BOOT_JS.read_text(encoding="utf-8")
    fn = _function_block(src, "_syncWorkspacePanelInlineWidth")
    assert "_clampPanelWidthForViewport" in fn, (
        "restored workspace panel width must be clamped to the current viewport"
    )


def _resize_iife_block(source: str) -> str:
    start = source.find("// \u2500\u2500 Resizable panels")
    assert start != -1, "Resizable panels IIFE not found"
    end = source.find("\n// \u2500\u2500 Appearance helpers", start)
    assert end != -1, "End of resizable panels IIFE not found"
    return source[start:end]


def test_resize_handler_uses_clamp():
    src = BOOT_JS.read_text(encoding="utf-8")
    fn = _resize_iife_block(src)
    assert "function initResize(" in fn, "initResize must exist inside the resizable panels IIFE"
    assert "_clampPanelWidthForViewport" in fn, (
        "drag-resize must clamp the new width to the available viewport"
    )
    assert "localStorage.setItem(storageKey" in fn, (
        "drag-resize must still persist the clamped width"
    )


def test_init_resize_panels_calls_both_clamp():
    src = BOOT_JS.read_text(encoding="utf-8")
    fn = _resize_iife_block(src)
    assert "window._initResizePanels = function" in fn, (
        "_initResizePanels must be assigned in the resizable panels IIFE"
    )
    assert "_clampBothPanelsOnResize" in fn, (
        "_initResizePanels must run a one-time clamp after restoring widths"
    )


def test_window_resize_listener_calls_clamp():
    src = BOOT_JS.read_text(encoding="utf-8")
    idx = src.find("window.addEventListener('resize'")
    assert idx != -1, "resize listener not found"
    block = src[idx:idx + 300]
    assert "_clampBothPanelsOnResize" in block, (
        "window resize listener must clamp panels to prevent crush"
    )


@node_test
def test_clamp_panel_width_respects_main_min():
    """The helper returns a width that leaves MAIN_PANEL_MIN_WIDTH for chat."""
    helper = _function_block(BOOT_JS.read_text(encoding="utf-8"), "_clampPanelWidthForViewport")
    script = textwrap.dedent("""
        const MAIN_PANEL_MIN_WIDTH=420;
        const SIDEBAR_MIN=180;
        const SIDEBAR_MAX=420;
        const PANEL_MIN=180;
        const PANEL_MAX=1200;
        global.window = { innerWidth: 1200 };
        function makeEl(w){
          return { getBoundingClientRect: () => ({ width: w }) };
        }
        __HELPER__
        const result = _clampPanelWidthForViewport(600, makeEl(0), makeEl(300), PANEL_MIN, PANEL_MAX);
        process.stdout.write(JSON.stringify({result}));
        """).replace("__HELPER__", helper)
    payload = _run_node(script)
    # viewport 1200 - other 300 - main_min 420 => maxAllowed 480
    assert payload["result"] == 480


@node_test
def test_clamp_panel_width_does_not_shrink_below_min():
    helper = _function_block(BOOT_JS.read_text(encoding="utf-8"), "_clampPanelWidthForViewport")
    script = textwrap.dedent("""
        const MAIN_PANEL_MIN_WIDTH=420;
        const SIDEBAR_MIN=180;
        const SIDEBAR_MAX=420;
        const PANEL_MIN=180;
        const PANEL_MAX=1200;
        global.window = { innerWidth: 500 };
        function makeEl(w){
          return { getBoundingClientRect: () => ({ width: w }) };
        }
        __HELPER__
        const result = _clampPanelWidthForViewport(600, makeEl(0), makeEl(300), PANEL_MIN, PANEL_MAX);
        process.stdout.write(JSON.stringify({result}));
        """).replace("__HELPER__", helper)
    payload = _run_node(script)
    # viewport 500 - other 300 - main_min 420 => maxAllowed -220 => clamped to min 180
    assert payload["result"] == 180


@node_test
def test_clamp_both_panels_reduces_right_first():
    """When the combined panels are too wide, the right panel shrinks first."""
    clamp = _function_block(BOOT_JS.read_text(encoding="utf-8"), "_clampBothPanelsOnResize")
    script = textwrap.dedent("""
        const MAIN_PANEL_MIN_WIDTH=420;
        const SIDEBAR_MIN=180;
        const SIDEBAR_MAX=420;
        const PANEL_MIN=180;
        const PANEL_MAX=1200;
        global.window = { innerWidth: 1000 };
        let widths = { sidebar: 300, right: 300 };
        function makeEl(name){
          return {
            getBoundingClientRect: () => ({ width: widths[name] }),
            style: {
              set width(v){ widths[name] = parseInt(v,10); },
              get width(){ return widths[name] + 'px'; },
            },
          };
        }
        let queries = 0;
        global.document = {
          querySelector: (sel) => {
            queries++;
            if(sel === '.sidebar') return makeEl('sidebar');
            if(sel === '.rightpanel') return makeEl('right');
            return null;
          },
        };
        function _isPhoneWidthViewport(){ return false; }
        __CLAMP__
        _clampBothPanelsOnResize();
        // viewport 1000, need 300+300+420=1020 -> 20 excess -> right goes to 280, sidebar stays 300
        process.stdout.write(JSON.stringify({right: widths.right, sidebar: widths.sidebar}));
        """).replace("__CLAMP__", clamp)
    payload = _run_node(script)
    assert payload["right"] == 280
    assert payload["sidebar"] == 300


@node_test
def test_clamp_both_panels_reduces_sidebar_after_right_min():
    """When right panel is already at min, the sidebar shrinks too."""
    clamp = _function_block(BOOT_JS.read_text(encoding="utf-8"), "_clampBothPanelsOnResize")
    script = textwrap.dedent("""
        const MAIN_PANEL_MIN_WIDTH=420;
        const SIDEBAR_MIN=180;
        const SIDEBAR_MAX=420;
        const PANEL_MIN=180;
        const PANEL_MAX=1200;
        global.window = { innerWidth: 900 };
        let widths = { sidebar: 300, right: 300 };
        function makeEl(name){
          return {
            getBoundingClientRect: () => ({ width: widths[name] }),
            style: {
              set width(v){ widths[name] = parseInt(v,10); },
              get width(){ return widths[name] + 'px'; },
            },
          };
        }
        global.document = {
          querySelector: (sel) => {
            if(sel === '.sidebar') return makeEl('sidebar');
            if(sel === '.rightpanel') return makeEl('right');
            return null;
          },
        };
        function _isPhoneWidthViewport(){ return false; }
        __CLAMP__
        _clampBothPanelsOnResize();
        // viewport 900, need 300+300+420=1020 -> 120 excess
        // right drops 120 to 180 (min), sidebar stays 300. 900-180-300 = 420.
        process.stdout.write(JSON.stringify({right: widths.right, sidebar: widths.sidebar}));
        """).replace("__CLAMP__", clamp)
    payload = _run_node(script)
    assert payload["right"] == 180
    assert payload["sidebar"] == 300


@node_test
def test_clamp_both_panels_skips_mobile_viewport():
    """The resize clamp must not run on phone-width viewports."""
    clamp = _function_block(BOOT_JS.read_text(encoding="utf-8"), "_clampBothPanelsOnResize")
    script = textwrap.dedent("""
        const MAIN_PANEL_MIN_WIDTH=420;
        const SIDEBAR_MIN=180;
        const SIDEBAR_MAX=420;
        const PANEL_MIN=180;
        const PANEL_MAX=1200;
        global.window = { innerWidth: 600 };
        let widths = { sidebar: 300, right: 300 };
        function makeEl(name){
          return {
            getBoundingClientRect: () => ({ width: widths[name] }),
            style: {
              set width(v){ widths[name] = parseInt(v,10); },
              get width(){ return widths[name] + 'px'; },
            },
          };
        }
        global.document = {
          querySelector: (sel) => {
            if(sel === '.sidebar') return makeEl('sidebar');
            if(sel === '.rightpanel') return makeEl('right');
            return null;
          },
        };
        function _isPhoneWidthViewport(){ return true; }
        __CLAMP__
        _clampBothPanelsOnResize();
        process.stdout.write(JSON.stringify({right: widths.right, sidebar: widths.sidebar}));
        """).replace("__CLAMP__", clamp)
    payload = _run_node(script)
    assert payload["right"] == 300
    assert payload["sidebar"] == 300
