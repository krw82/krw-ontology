"""Tests for JSONL read/write and SHA-256 utilities."""

from __future__ import annotations

from pathlib import Path

from krw_ontology.utils.io import (
    atomic_write,
    atomic_write_json,
    compute_sha256,
    find_project_root,
    read_jsonl,
    write_jsonl,
)


class TestJsonlReadWrite:
    def test_write_and_read(self, tmp_path: Path):
        path = tmp_path / "test.jsonl"
        objects = [
            {"id": "1", "type": "SourceSpan", "text": "hello"},
            {"id": "2", "type": "EvidenceQuote", "text": "world"},
        ]
        write_jsonl(path, objects)
        result = read_jsonl(path)
        assert len(result) == 2
        assert result[0]["id"] == "1"
        assert result[1]["text"] == "world"

    def test_write_creates_parent_dirs(self, tmp_path: Path):
        path = tmp_path / "deep" / "nested" / "test.jsonl"
        write_jsonl(path, [{"id": "1"}])
        assert path.exists()

    def test_read_nonexistent_returns_empty(self, tmp_path: Path):
        path = tmp_path / "nonexistent.jsonl"
        assert read_jsonl(path) == []

    def test_read_empty_file_returns_empty(self, tmp_path: Path):
        path = tmp_path / "empty.jsonl"
        path.write_text("")
        assert read_jsonl(path) == []

    def test_read_skips_blank_lines(self, tmp_path: Path):
        path = tmp_path / "blanks.jsonl"
        path.write_text('{"id":"1"}\n\n{"id":"2"}\n\n')
        result = read_jsonl(path)
        assert len(result) == 2

    def test_roundtrip_preserves_data(self, tmp_path: Path):
        path = tmp_path / "roundtrip.jsonl"
        original = [
            {"id": "span:1", "text": "Hello, world!", "count": 42},
            {"id": "span:2", "text": "Unicode: \ud55c\uae00", "count": 0},
        ]
        write_jsonl(path, original)
        result = read_jsonl(path)
        assert result == original


class TestAtomicWrite:
    def test_atomic_write_creates_file(self, tmp_path: Path):
        path = tmp_path / "atomic.txt"
        atomic_write(path, "hello world")
        assert path.read_text() == "hello world"

    def test_atomic_write_creates_parent_dirs(self, tmp_path: Path):
        path = tmp_path / "deep" / "dir" / "file.txt"
        atomic_write(path, "content")
        assert path.exists()
        assert path.read_text() == "content"

    def test_atomic_write_json(self, tmp_path: Path):
        path = tmp_path / "data.json"
        data = {"key": "value", "number": 42}
        atomic_write_json(path, data)
        import json
        loaded = json.loads(path.read_text())
        assert loaded == data


class TestSha256:
    def test_sha256_correct(self):
        content = b"hello world"
        result = compute_sha256(content)
        assert len(result) == 64  # hex digest length
        assert result == "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"

    def test_sha256_empty(self):
        result = compute_sha256(b"")
        assert result == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

    def test_sha256_deterministic(self):
        assert compute_sha256(b"test") == compute_sha256(b"test")


class TestFindProjectRoot:
    def test_finds_pyproject_from_subdir(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'test'\n")
        nested = tmp_path / "src" / "pkg"
        nested.mkdir(parents=True)
        result = find_project_root(nested)
        assert result == tmp_path

    def test_finds_cwd_project_root(self):
        result = find_project_root()
        assert (result / "pyproject.toml").exists()

    def test_no_pyproject_returns_cwd(self, tmp_path: Path):
        empty = tmp_path / "empty"
        empty.mkdir()
        result = find_project_root(empty)
        assert result == Path.cwd()
