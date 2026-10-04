"""Tests for the unified API, tools schema, CLI flags and package exports."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from cihuang import (
    SvgDocument,
    ToolResult,
    cihuang_inspect,
    cihuang_move,
    cihuang_remove,
    cihuang_set_color,
    cihuang_set_text,
)
from cihuang.api import ToolResult as ApiToolResult
from cihuang.tools import TOOLS, dispatch, list_tool_names

SAMPLES = Path(__file__).resolve().parent.parent / "examples"


@pytest.fixture()
def sample(tmp_path: Path) -> Path:
    """A copy of the sample SVG in a scratch directory."""
    dest = tmp_path / "sample.svg"
    shutil.copy(SAMPLES / "sample.svg", dest)
    return dest


class TestToolResult:
    def test_success_result(self):
        result = ToolResult(success=True, data={"key": "value"})
        assert result.success is True
        assert result.error is None

    def test_failure_result(self):
        result = ToolResult(success=False, error="boom")
        assert result.success is False
        assert result.error == "boom"

    def test_to_dict_keys(self):
        result = ToolResult(success=True, data=[1, 2])
        assert set(result.to_dict()) == {"success", "data", "error", "metadata"}

    def test_default_metadata_isolation(self):
        first = ToolResult(success=True)
        second = ToolResult(success=True)
        first.metadata["a"] = 1
        assert "a" not in second.metadata


class TestCoreTypes:
    def test_document_counts_elements(self):
        doc = SvgDocument.load(SAMPLES / "sample.svg")
        # rect, circle, rect, path, text
        assert doc.count() == 5
        tags = [doc.tag(uid) for uid in range(doc.count())]
        assert tags == ["rect", "circle", "rect", "path", "text"]

    def test_viewbox_parsed(self):
        doc = SvgDocument.load(SAMPLES / "sample.svg")
        vb = doc.viewbox
        assert (vb.width, vb.height) == (200.0, 140.0)

    def test_set_color_writes_style_or_attribute(self):
        doc = SvgDocument.load(SAMPLES / "sample.svg")
        doc.set_color(1, "fill", "#ff0000")
        assert doc.get_property(1, "fill") == "#ff0000"

    def test_translate_keeps_base_transform(self):
        doc = SvgDocument.load(SAMPLES / "sample.svg")
        doc.translate(1, 10, 5)
        doc.translate(1, 3, 2, base="")
        assert doc.element(1).get("transform") == "translate(3 2)"

    def test_set_text_replaces_content(self):
        doc = SvgDocument.load(SAMPLES / "sample.svg")
        doc.set_text(4, "hello")
        assert doc.get_text(4) == "hello"

    def test_set_text_rejects_non_text(self):
        doc = SvgDocument.load(SAMPLES / "sample.svg")
        with pytest.raises(ValueError):
            doc.set_text(1, "nope")

    def test_undo_redo_roundtrip(self):
        doc = SvgDocument.load(SAMPLES / "sample.svg")
        doc.push_undo()
        doc.set_color(1, "fill", "#123456")
        assert doc.undo() is True
        assert doc.get_property(1, "fill") == "#2c6fbb"
        assert doc.redo() is True
        assert doc.get_property(1, "fill") == "#123456"

    def test_delete_reduces_count(self):
        doc = SvgDocument.load(SAMPLES / "sample.svg")
        doc.delete(0)
        assert doc.count() == 4
        assert doc.tag(0) == "circle"

    def test_group_then_ungroup(self):
        doc = SvgDocument.load(SAMPLES / "sample.svg")
        before = doc.count()
        group_uid = doc.group([1, 2])  # circle and bar, both top level
        # A new <g> appears and stays editable alongside its children.
        assert doc.tag(group_uid) == "g"
        assert doc.count() == before + 1
        doc.ungroup(group_uid)
        assert doc.count() == before

    def test_group_requires_common_parent(self):
        doc = SvgDocument.load(SAMPLES / "sample.svg")
        group_uid = doc.group([1, 2])
        # uid+1 is a child of the new group; it lives in a different parent.
        with pytest.raises(ValueError):
            doc.group([group_uid, group_uid + 1])

    def test_group_moves_together(self):
        doc = SvgDocument.load(SAMPLES / "sample.svg")
        group_uid = doc.group([1, 2])
        doc.translate(group_uid, 10, 0)
        assert "translate(10 0)" in (doc.element(group_uid).get("transform") or "")

    def test_top_level_uid_finds_group(self):
        doc = SvgDocument.load(SAMPLES / "sample.svg")
        group_uid = doc.group([1, 2])
        # The children now sit inside the group; a click should resolve to it.
        child = next(uid for uid in range(doc.count()) if doc.tag(uid) != "g" and uid != 0)
        assert doc.top_level_uid(child) == group_uid

    def test_ungroup_rejects_non_group(self):
        doc = SvgDocument.load(SAMPLES / "sample.svg")
        with pytest.raises(ValueError):
            doc.ungroup(1)


class TestApi:
    def test_inspect_success(self, sample: Path):
        result = cihuang_inspect(sample)
        assert result.success
        assert result.data["summary"]["elements"] == 5

    def test_inspect_missing_file(self, tmp_path: Path):
        result = cihuang_inspect(tmp_path / "nope.svg")
        assert result.success is False
        assert "cannot read" in result.error

    def test_set_color_creates_edited_file(self, sample: Path):
        result = cihuang_set_color(input_path=sample, element_index=1, color="#abcdef")
        assert result.success
        out = Path(result.data["output"])
        assert out.name == "sample-edited.svg"
        doc = SvgDocument.load(out)
        assert doc.get_property(1, "fill") == "#abcdef"

    def test_set_color_none_means_none(self, sample: Path):
        result = cihuang_set_color(input_path=sample, element_index=1, color=None)
        assert result.success
        doc = SvgDocument.load(result.data["output"])
        assert doc.get_property(1, "fill") == "none"

    def test_set_color_rejects_bad_prop(self, sample: Path):
        result = cihuang_set_color(input_path=sample, element_index=1, prop="opacity")
        assert result.success is False

    def test_move_changes_transform(self, sample: Path):
        result = cihuang_move(input_path=sample, element_index=1, dx=12, dy=-3)
        assert result.success
        doc = SvgDocument.load(result.data["output"])
        assert "translate(12 -3)" in (doc.element(1).get("transform") or "")

    def test_set_text_via_api(self, sample: Path):
        result = cihuang_set_text(input_path=sample, element_index=4, text="新文字")
        assert result.success
        doc = SvgDocument.load(result.data["output"])
        assert doc.get_text(4) == "新文字"

    def test_move_out_of_range(self, sample: Path):
        result = cihuang_move(input_path=sample, element_index=99, dx=1, dy=1)
        assert result.success is False

    def test_remove_via_api(self, sample: Path):
        result = cihuang_remove(input_path=sample, element_index=0)
        assert result.success
        doc = SvgDocument.load(result.data["output"])
        assert doc.count() == 4

    def test_api_reexported_class_is_same(self):
        assert ToolResult is ApiToolResult


class TestToolsSchema:
    def test_tool_structure(self):
        for tool in TOOLS:
            assert tool["type"] == "function"
            func = tool["function"]
            assert {"name", "description", "parameters"} <= set(func)

    def test_required_fields_present(self):
        for tool in TOOLS:
            func = tool["function"]
            props = func["parameters"]["properties"]
            for required in func["parameters"]["required"]:
                assert required in props

    def test_names_have_prefix(self):
        for name in list_tool_names():
            assert name.startswith("cihuang_")


class TestDispatch:
    def test_dispatch_inspect(self, sample: Path):
        payload = dispatch("cihuang_inspect", {"input_path": str(sample)})
        assert payload["success"] is True

    def test_dispatch_accepts_json_string(self, sample: Path):
        payload = dispatch("cihuang_move", json.dumps(
            {"input_path": str(sample), "element_index": 1, "dx": 1, "dy": 1}
        ))
        assert payload["success"] is True

    def test_dispatch_unknown(self):
        with pytest.raises(ValueError):
            dispatch("nope", {})


class TestCLIFlags:
    def _run(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "cihuang", *args],
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_version(self):
        result = self._run("-V")
        assert result.returncode == 0
        assert "cihuang" in result.stdout

    def test_help_lists_unified_flags(self):
        result = self._run("--help")
        assert result.returncode == 0
        assert "--json" in result.stdout
        assert "--quiet" in result.stdout

    def test_info_json(self):
        result = self._run("info", str(SAMPLES / "sample.svg"), "--json")
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert payload["success"] is True

    def test_info_missing_file_exit_1(self, tmp_path: Path):
        result = self._run("info", str(tmp_path / "missing.svg"))
        assert result.returncode == 1


class TestPackageExports:
    def test_public_names(self):
        import cihuang

        for name in ("ToolResult", "SvgDocument", "TOOLS", "dispatch", "__version__"):
            assert hasattr(cihuang, name)

    def test_version_string(self):
        import cihuang

        assert isinstance(cihuang.__version__, str)
        assert cihuang.__version__.count(".") >= 1
