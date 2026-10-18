"""Execute the real message-footer builder to protect last-exchange actions."""

import json
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not on PATH")


class _FooterButtons(HTMLParser):
    def __init__(self):
        super().__init__()
        self.buttons = []

    def handle_starttag(self, tag, attrs):
        if tag == "button":
            self.buttons.append(dict(attrs))


@pytest.fixture(scope="module")
def rendered_footers():
    source = (ROOT / "static" / "ui.js").read_text(encoding="utf-8")
    # Keep the eligibility predicates and the whole footer-building block from
    # production; testing only undoBtn's definition misses an omitted template
    # interpolation, which leaves the defined control absent from the UI.
    predicates = []
    for marker in ("const isUser=", "const isLastAssistant="):
        start = source.index(marker)
        predicates.append(source[start : source.index(";", start) + 1])
    start = source.index("const isEditableUser=")
    footer_start = source.index("const footHtml =", start)
    end = source.index("\n", footer_start)
    builder = "\n".join(predicates) + "\n" + source[start:end]
    script = r"""
const vm = require('node:vm');
const builder = BUILDER;
const scenarios = {
  latest_assistant: {roles: ['user', 'assistant', 'user', 'assistant'], vi: 3},
  older_assistant: {roles: ['user', 'assistant', 'user', 'assistant'], vi: 1},
  user_before_assistant: {roles: ['user', 'assistant'], vi: 0},
  latest_user: {roles: ['user', 'assistant', 'user'], vi: 2},
};
const results = {};
for (const [name, {roles, vi}] of Object.entries(scenarios)) {
  const renderVisWithIdx = roles.map((role, rawIdx) => ({m: {role}, rawIdx}));
  const context = vm.createContext({
    m: renderVisWithIdx[vi].m,
    vi,
    rawIdx: vi,
    renderVisWithIdx,
    lastUserRawIdx: roles.lastIndexOf('user'),
    S: {session: {session_id: 'footer-test'}},
    t: key => key,
    li: (name, size) => `<svg data-icon="${name}" width="${size}"></svg>`,
    esc: value => String(value),
    _formatMessageFooterTimestamp: () => '',
    questionRawIdxByAssistantRawIdx: new Map(),
    assistantRawIdxByQuestionRawIdx: new Map(),
  });
  results[name] = vm.runInContext(builder + '\nfootHtml;', context, {timeout: 1000});
}
process.stdout.write(JSON.stringify(results));
""".replace("BUILDER", json.dumps(builder))
    result = subprocess.run(
        [NODE, "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize(
    ("scenario", "expected_count"),
    [
        ("latest_assistant", 1),
        ("older_assistant", 0),
        ("user_before_assistant", 0),
        ("latest_user", 0),
    ],
)
def test_only_latest_assistant_has_undo_and_regenerate(rendered_footers, scenario, expected_count):
    footer = _FooterButtons()
    footer.feed(rendered_footers[scenario])
    for handler, title in (
        ("undoLastExchange()", "undo_exchange"),
        ("regenerateResponse(this)", "regenerate"),
    ):
        controls = [button for button in footer.buttons if button.get("onclick") == handler]
        assert len(controls) == expected_count, (
            f"{scenario}: expected {expected_count} {handler} control(s), got {len(controls)}"
        )
        for control in controls:
            assert control["title"] == title
            assert "msg-action-btn" in control["class"].split()
