"""Tests for binary file detection in read_file_content.

The frontend (workspace.js) checks ``data.binary`` to trigger a download
instead of previewing garbled text.  Before this fix the server never
returned the ``binary`` flag, so binary files with extensions not in the
client-side DOWNLOAD_EXTS set were displayed as mangled UTF-8.
"""
from pathlib import Path

import pytest

from api.workspace import _is_binary_file, read_file_content


# ── _is_binary_file unit tests ────────────────────────────────────────────


def test_is_binary_detects_null_bytes(tmp_path):
    binary = tmp_path / "data.bin"
    binary.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
    assert _is_binary_file(binary) is True


def test_is_binary_allows_plain_text(tmp_path):
    text = tmp_path / "readme.txt"
    text.write_text("Hello, world!\nLine two.\n", encoding="utf-8")
    assert _is_binary_file(text) is False


def test_is_binary_allows_utf8_with_multibyte(tmp_path):
    text = tmp_path / "unicode.txt"
    text.write_text("日本語テキスト\n", encoding="utf-8")
    assert _is_binary_file(text) is False


def test_is_binary_returns_false_for_empty_file(tmp_path):
    empty = tmp_path / "empty"
    empty.write_bytes(b"")
    assert _is_binary_file(empty) is False


def test_is_binary_returns_false_for_missing_file(tmp_path):
    missing = tmp_path / "no-such-file"
    assert _is_binary_file(missing) is False


# ── read_file_content integration tests ───────────────────────────────────


def test_read_file_content_returns_binary_flag_for_binary_file(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    binary = ws / "image.o"
    binary.write_bytes(b"\x7fELF\x00\x01\x02" + b"\x00" * 100)

    result = read_file_content(ws, "image.o")

    assert result["binary"] is True
    assert result["path"] == "image.o"
    assert result["size"] == binary.stat().st_size
    assert "content" not in result


def test_read_file_content_returns_content_for_text_file(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    text = ws / "hello.py"
    text.write_text("print('hello')\n", encoding="utf-8")

    result = read_file_content(ws, "hello.py")

    assert "binary" not in result
    assert result["content"] == "print('hello')\n"
    assert result["lines"] == 2


def test_read_file_content_returns_binary_for_wasm(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    wasm = ws / "module.wasm"
    wasm.write_bytes(b"\x00asm\x01\x00\x00\x00")

    result = read_file_content(ws, "module.wasm")

    assert result["binary"] is True
