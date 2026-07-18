import json
from pathlib import Path

import cadquery as cq
import pytest
from cadquery import exporters

from agentic_cad_workflow.artifacts import split_artifact
from agentic_cad_workflow.server import call_tool


def _tool_payload(result):
    assert result.content
    return json.loads(result.content[0].text)


def _export_box(path: Path, x: float, y: float, z: float) -> Path:
    shape = cq.Workplane("XY").box(x, y, z).translate((0, 0, z / 2)).val()
    exporters.export(shape, str(path), exportType="STEP")
    return path


def test_split_artifact_oversize_bar(tmp_path):
    source = _export_box(tmp_path / "bar.step", 500, 40, 20)
    payload = split_artifact(source, output_dir=tmp_path)

    assert payload["success"], payload.get("error")
    assert payload["split_needed"] is True
    assert payload["bed_size"] == 300
    assert payload["grid"] == {"x": 2, "y": 1, "z": 1}
    assert payload["segments_total"] == 2
    assert payload["all_segments_valid"], payload["segments"]

    total_volume = 0.0
    for segment in payload["segments"]:
        bbox = segment["properties"]["bounding_box"]
        assert max(bbox["xlen"], bbox["ylen"], bbox["zlen"]) <= 300
        # Recentered for printing: base on Z=0.
        assert bbox["zmin"] == pytest.approx(0, abs=1e-5)
        assert segment["validation"]["valid"], segment["validation"]
        assert segment["validation"]["profile"] == "printable"
        total_volume += segment["properties"]["volume"]
        for key in ("stl", "step", "brep"):
            assert Path(segment["artifacts"][key]).stat().st_size > 0
    assert total_volume == pytest.approx(500 * 40 * 20, rel=0.01)


def test_split_artifact_fits_bed(tmp_path):
    source = _export_box(tmp_path / "small.step", 40, 40, 20)
    payload = split_artifact(source, output_dir=tmp_path)
    assert payload["success"]
    assert payload["split_needed"] is False
    assert "segments" not in payload


def test_split_artifact_custom_bed_size(tmp_path):
    source = _export_box(tmp_path / "plate.step", 250, 150, 10)
    payload = split_artifact(source, bed_size=100, output_dir=tmp_path)
    assert payload["success"], payload.get("error")
    assert payload["grid"] == {"x": 3, "y": 2, "z": 1}
    assert payload["segments_total"] == 6
    assert payload["all_segments_valid"], payload["segments"]


def test_split_artifact_segment_limit(tmp_path):
    source = _export_box(tmp_path / "huge.step", 500, 500, 20)
    payload = split_artifact(source, bed_size=50, output_dir=tmp_path)
    assert not payload["success"]
    assert "segments" in payload["error"]


def test_split_artifact_missing_file(tmp_path):
    payload = split_artifact(tmp_path / "nope.step", output_dir=tmp_path)
    assert not payload["success"]
    assert "not found" in payload["error"]


def test_split_artifact_rejects_non_positive_bed_size(tmp_path):
    source = _export_box(tmp_path / "small.step", 40, 40, 20)
    payload = split_artifact(source, bed_size=0, output_dir=tmp_path)
    assert not payload["success"]
    assert "must be > 0" in payload["error"]


@pytest.mark.asyncio
async def test_call_tool_split_artifact(tmp_path):
    source = _export_box(tmp_path / "bar.step", 500, 40, 20)
    result = await call_tool(
        "split_artifact",
        {"source_path": str(source), "output_dir": str(tmp_path), "include_images": False},
    )
    assert not result.isError
    assert len(result.content) == 1
    payload = _tool_payload(result)
    assert payload["success"], payload.get("error")
    assert payload["segments_total"] == 2
    assert payload["all_segments_valid"]
