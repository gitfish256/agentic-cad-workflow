import inspect
import json
from pathlib import Path

import cadquery as cq
import pytest
from cadquery import exporters

import agentic_cad_workflow.artifacts as artifacts_module
from agentic_cad_workflow.artifacts import inspect_artifact, make_radial_assembly, render_artifact
from agentic_cad_workflow.generator import build_model_from_code
from agentic_cad_workflow.runner import CadExecution, ShapeProperties
from agentic_cad_workflow.server import call_tool
from agentic_cad_workflow.validation import validate_prompt_shape

BOX_CODE = (
    "import cadquery as cq\nresult = cq.Workplane('XY').box(20, 20, 10).translate((0, 0, 5))\nshow_object(result)\n"
)


def _tool_payload(result):
    assert result.content
    return json.loads(result.content[0].text)


@pytest.fixture(scope="module")
def wedge_files(tmp_path_factory):
    """Small door-stopper-like wedge exported as STEP/BREP/STL."""
    base = tmp_path_factory.mktemp("wedge-artifacts")
    shape = cq.Workplane("XZ").polyline([(0, 0), (60, 0), (60, 20)]).close().extrude(25).translate((0, 12.5, 0)).val()
    paths = {}
    for ext, export_type in [("step", "STEP"), ("brep", "BREP"), ("stl", "STL")]:
        path = base / f"wedge.{ext}"
        exporters.export(shape, str(path), exportType=export_type)
        paths[ext] = path
    return paths


def test_inspect_artifact_step(wedge_files):
    payload = inspect_artifact(wedge_files["step"], prompt="wedge artifact")
    assert payload["success"], payload.get("error")
    assert payload["file_size"] > 0
    bbox = payload["properties"]["bounding_box"]
    assert bbox["xlen"] == pytest.approx(60, abs=0.01)
    assert bbox["ylen"] == pytest.approx(25, abs=0.01)
    assert bbox["zlen"] == pytest.approx(20, abs=0.01)
    assert payload["properties"]["volume"] == pytest.approx(60 * 25 * 20 / 2, rel=0.01)
    assert payload["properties"]["topology"]["solids"] == 1
    assert payload["validation"]["valid"], payload["validation"]
    assert payload["validation"]["profile"] == "printable"


def test_inspect_artifact_brep(wedge_files):
    payload = inspect_artifact(wedge_files["brep"])
    assert payload["success"], payload.get("error")
    assert payload["properties"]["volume"] == pytest.approx(60 * 25 * 20 / 2, rel=0.01)


def test_inspect_artifact_stl_structured_error(wedge_files):
    payload = inspect_artifact(wedge_files["stl"])
    assert not payload["success"]
    assert "STL" in payload["error"]
    assert payload["file_size"] > 0
    assert payload["format"] == "stl"


def test_inspect_artifact_missing_file(tmp_path):
    payload = inspect_artifact(tmp_path / "nope.step")
    assert not payload["success"]
    assert "not found" in payload["error"]
    assert "file_size" not in payload


def test_render_artifact_previews(wedge_files, tmp_path):
    payload = render_artifact(wedge_files["step"], output_dir=tmp_path, views=["isometric", "front"])
    assert payload["success"], payload.get("error")
    previews = payload["artifacts"]["previews"]
    assert [p["view"] for p in previews] == ["isometric", "front"]
    for preview in previews:
        assert Path(preview["svg"]).exists()
        assert Path(preview["png"]).stat().st_size > 0


def test_render_artifact_unknown_view(wedge_files, tmp_path):
    payload = render_artifact(wedge_files["step"], output_dir=tmp_path, views=["sideways"])
    assert not payload["success"]
    assert "sideways" in payload["error"]


def test_render_artifact_shaded_style(wedge_files, tmp_path):
    payload = render_artifact(wedge_files["step"], output_dir=tmp_path, views=["isometric"], style="shaded")
    assert payload["success"], payload.get("error")
    png = Path(payload["artifacts"]["previews"][0]["png"])
    assert png.stat().st_size > 0


def test_render_artifact_reuses_preview_export_and_measures_first(wedge_files, tmp_path, monkeypatch):
    events = []
    captured = {}
    original_shape_properties = artifacts_module.shape_properties

    def tracking_shape_properties(shape):
        events.append("measure")
        return original_shape_properties(shape)

    def fake_export_preview_files(shape, output_dir, **kwargs):
        events.append("render")
        captured.update(output_dir=output_dir, **kwargs)
        return {
            "previews": [
                {
                    "view": "isometric",
                    "svg": str(Path(output_dir) / "wedge-isometric.svg"),
                    "png": str(Path(output_dir) / "wedge-isometric.png"),
                }
            ]
        }

    monkeypatch.setattr(artifacts_module, "shape_properties", tracking_shape_properties)
    monkeypatch.setattr(artifacts_module, "export_preview_files", fake_export_preview_files)

    payload = render_artifact(
        wedge_files["step"],
        output_dir=tmp_path,
        views=["isometric"],
        run_id="preview-reuse",
        style="shaded",
    )

    assert payload["success"], payload.get("error")
    assert events == ["measure", "render"]
    assert captured["output_dir"] == tmp_path / "preview-reuse"
    assert captured["base_name"] == "wedge"
    assert captured["views"] == ("isometric",)
    assert captured["style"] == "shaded"
    assert payload["artifacts"]["previews"][0]["view"] == "isometric"


def test_render_artifact_unknown_style(wedge_files, tmp_path):
    payload = render_artifact(wedge_files["step"], output_dir=tmp_path, style="wireframe")
    assert not payload["success"]
    assert "wireframe" in payload["error"]


def test_make_radial_assembly_compound(wedge_files, tmp_path):
    payload = make_radial_assembly(wedge_files["step"], count=4, angle_step=90.0, output_dir=tmp_path)
    assert payload["success"], payload.get("error")
    assert payload["assembly"] == {"count": 4, "angle_step": 90.0, "boolean_fuse": False}
    props = payload["properties"]
    assert props["topology"]["solids"] == 4
    assert props["volume"] == pytest.approx(4 * 60 * 25 * 20 / 2, rel=0.01)
    # Four 90-degree rotated copies span symmetric X/Y extents.
    bbox = props["bounding_box"]
    assert bbox["xlen"] == pytest.approx(bbox["ylen"], abs=0.01)
    assert payload["validation"]["profile"] == "assembly"
    assert payload["validation"]["valid"], payload["validation"]
    for key in ("stl", "step", "brep"):
        assert Path(payload["artifacts"][key]).stat().st_size > 0


def _oversize_ring_props():
    return ShapeProperties(
        bounding_box={
            "xmin": -199.5,
            "xmax": 199.5,
            "ymin": -199.5,
            "ymax": 199.5,
            "zmin": 0.0,
            "zmax": 38.0,
            "xlen": 399.0,
            "ylen": 399.0,
            "zlen": 38.0,
        },
        volume=581989.19,
        area=1.0,
        center={"x": 0.0, "y": 0.0, "z": 19.0},
        topology={"solids": 4, "faces": 40, "edges": 120, "vertices": 80},
        valid=True,
    )


def test_validation_profile_printable_rejects_oversize():
    report = validate_prompt_shape("assembled ring reference", _oversize_ring_props())
    assert not report.valid
    assert any(c["name"] == "stl_printable_size" and not c["passed"] for c in report.checks)


def test_validation_profile_reference_allows_oversize():
    for profile in ("reference", "assembly"):
        report = validate_prompt_shape("assembled ring reference", _oversize_ring_props(), profile=profile)
        assert report.valid, report.to_dict()
        advisory = next(c for c in report.checks if c["name"] == "print_bed_size_advisory")
        assert advisory["passed"]
        assert advisory["severity"] == "warning"
        assert advisory["value"]["fits_bed"] is False
        assert "advisories" in report.summary
        assert "print_bed_size_advisory" in report.summary


def test_validation_profile_reference_allows_below_bed():
    props = _oversize_ring_props()
    props.bounding_box = dict(props.bounding_box, zmin=-19.0, zmax=19.0)
    assert not validate_prompt_shape("centered ring", props).valid
    report = validate_prompt_shape("centered ring", props, profile="reference")
    assert report.valid, report.to_dict()
    advisory = next(c for c in report.checks if c["name"] == "print_bed_base_advisory")
    assert advisory["value"]["on_bed"] is False
    assert advisory["severity"] == "warning"
    assert "print_bed_base_advisory" in report.summary


def test_validation_rejects_unknown_brep_validity():
    props = _oversize_ring_props()
    props.valid = None
    props.bounding_box = dict(
        props.bounding_box,
        xmin=-10.0,
        xmax=10.0,
        ymin=-10.0,
        ymax=10.0,
        xlen=20.0,
        ylen=20.0,
    )

    report = validate_prompt_shape("printable box", props)

    assert not report.valid
    validity = next(c for c in report.checks if c["name"] == "geometry_valid")
    assert not validity["passed"]
    assert validity["value"] is None
    assert "not available" in validity["message"]


def test_validation_profile_custom_bed_size():
    report = validate_prompt_shape("big ring", _oversize_ring_props(), max_print_dim_mm=400)
    assert report.valid, report.to_dict()


def test_validation_printable_requires_base_at_z_zero():
    props = _oversize_ring_props()
    props.bounding_box = dict(
        props.bounding_box,
        xmin=-10.0,
        xmax=10.0,
        ymin=-10.0,
        ymax=10.0,
        zmin=100.0,
        zmax=110.0,
        xlen=20.0,
        ylen=20.0,
        zlen=10.0,
    )
    props.valid = True

    report = validate_prompt_shape("printable box", props)

    assert not report.valid
    base = next(c for c in report.checks if c["name"] == "print_bed_base")
    assert not base["passed"]
    assert report.scope == "geometry_heuristic"
    assert report.limitations


def test_validation_rejects_non_positive_bed_size():
    with pytest.raises(ValueError, match="must be > 0"):
        validate_prompt_shape("part", _oversize_ring_props(), max_print_dim_mm=0)


def test_validation_profile_unknown_raises():
    with pytest.raises(ValueError):
        validate_prompt_shape("x", _oversize_ring_props(), profile="huge")


def test_multiple_independent_show_objects_fail_explicitly(tmp_path):
    code = (
        "import cadquery as cq\n"
        "show_object(cq.Workplane('XY').box(10, 10, 10).translate((0, 0, 5)), name='a')\n"
        "show_object(cq.Workplane('XY').box(5, 5, 5).translate((30, 0, 2.5)), name='b')\n"
    )
    result = build_model_from_code(code, output_dir=tmp_path)
    assert not result["success"]
    assert "2 separate final results" in result["error"]
    assert "stl" not in result["artifacts"]
    assert "step" not in result["artifacts"]
    assert "brep" not in result["artifacts"]


def test_multi_object_workplane_preserves_every_solid(tmp_path):
    code = (
        "import cadquery as cq\n"
        "a = cq.Workplane('XY').box(10, 10, 10).translate((0, 0, 5)).val()\n"
        "b = a.translate((30, 0, 0))\n"
        "result = cq.Workplane('XY').newObject([a, b])\n"
        "show_object(result, name='two_boxes')\n"
    )

    result = build_model_from_code(code, output_dir=tmp_path)

    assert result["success"], result.get("error")
    props = result["properties"][0]
    assert props["topology"]["solids"] == 2
    assert props["volume"] == pytest.approx(2000.0, rel=1e-6)
    assert props["bounding_box"]["xlen"] == pytest.approx(40.0, abs=0.01)
    inspected = inspect_artifact(result["artifacts"]["step"])
    assert inspected["success"], inspected.get("error")
    assert inspected["properties"]["topology"]["solids"] == 2
    assert inspected["properties"]["volume"] == pytest.approx(2000.0, rel=1e-6)


def test_build_default_uses_a_nonzero_worker_timeout(tmp_path, monkeypatch):
    captured = {}

    def fake_execute(code, timeout_seconds=None):
        captured["timeout_seconds"] = timeout_seconds
        return CadExecution(success=False, code=code, error="intentional test stop")

    monkeypatch.setattr("agentic_cad_workflow.generator.execute_cadquery", fake_execute)

    result = build_model_from_code(BOX_CODE, output_dir=tmp_path)

    default = inspect.signature(build_model_from_code).parameters["execution_timeout"].default
    assert default is not None
    assert float(default) > 0
    assert captured["timeout_seconds"] == default
    assert not result["success"]


@pytest.mark.asyncio
async def test_call_tool_inspect_artifact(wedge_files):
    result = await call_tool(
        "inspect_artifact",
        {"path": str(wedge_files["step"]), "validation_profile": "reference"},
    )
    assert not result.isError
    payload = _tool_payload(result)
    assert payload["success"], payload.get("error")
    assert payload["validation"]["profile"] == "reference"


@pytest.mark.asyncio
async def test_call_tool_render_artifact_no_images(wedge_files, tmp_path):
    result = await call_tool(
        "render_artifact",
        {
            "path": str(wedge_files["step"]),
            "output_dir": str(tmp_path),
            "views": ["isometric"],
            "include_images": False,
        },
    )
    assert not result.isError
    assert len(result.content) == 1
    payload = _tool_payload(result)
    assert payload["success"], payload.get("error")
    assert Path(payload["artifacts"]["previews"][0]["png"]).exists()


@pytest.mark.asyncio
async def test_call_tool_execute_cadquery_summary_omits_code(tmp_path):
    result = await call_tool(
        "execute_cadquery",
        {"code": BOX_CODE, "output_dir": str(tmp_path), "include_images": False, "verbosity": "summary"},
    )
    assert not result.isError
    payload = _tool_payload(result)
    assert payload["success"], payload.get("error")
    assert "code" not in payload
    assert "iterations" not in payload
    assert set(payload["validation"]) <= {
        "valid",
        "score",
        "summary",
        "profile",
        "scope",
        "limitations",
    }
    assert Path(payload["artifacts"]["stl"]).exists()


@pytest.mark.asyncio
async def test_call_tool_execute_cadquery_include_code_false_only(tmp_path):
    result = await call_tool(
        "execute_cadquery",
        {"code": BOX_CODE, "output_dir": str(tmp_path), "include_images": False, "include_code": False},
    )
    assert not result.isError
    payload = _tool_payload(result)
    assert payload["success"]
    assert "code" not in payload
    # Normal verbosity still retains compact metadata.
    assert payload["code_source"] == "agent-provided code"


@pytest.mark.asyncio
async def test_call_tool_execute_cadquery_defaults_to_normal(tmp_path):
    result = await call_tool(
        "execute_cadquery",
        {"code": BOX_CODE, "output_dir": str(tmp_path), "include_images": False},
    )
    assert not result.isError
    payload = _tool_payload(result)
    assert payload["success"]
    assert "code" not in payload
    assert payload["code_source"] == "agent-provided code"


@pytest.mark.asyncio
async def test_call_tool_render_preview_exports_previews_only(tmp_path):
    result = await call_tool(
        "render_preview",
        {
            "code": BOX_CODE,
            "output_dir": str(tmp_path),
            "include_images": False,
            "style": "shaded",
        },
    )

    assert not result.isError
    payload = _tool_payload(result)
    assert payload["success"], payload.get("error")
    artifacts = payload["artifacts"]
    assert artifacts["previews"]
    assert not {"stl", "step", "brep"} & artifacts.keys()
    run_path = Path(payload["output_dir"])
    assert not list(run_path.glob("*.stl"))
    assert not list(run_path.glob("*.step"))
    assert not list(run_path.glob("*.brep"))
    for preview in artifacts["previews"]:
        assert Path(preview["svg"]).is_file()
        assert Path(preview["png"]).is_file()


@pytest.mark.asyncio
async def test_call_tool_execute_cadquery_full_can_include_code(tmp_path):
    result = await call_tool(
        "execute_cadquery",
        {
            "code": BOX_CODE,
            "output_dir": str(tmp_path),
            "include_images": False,
            "verbosity": "full",
        },
    )
    assert not result.isError
    payload = _tool_payload(result)
    assert payload["success"]
    assert payload["code"] == BOX_CODE


@pytest.mark.asyncio
async def test_call_tool_execute_cadquery_reference_profile(tmp_path):
    code = (
        "import cadquery as cq\n"
        "result = cq.Workplane('XY').cylinder(38, 199.5).translate((0, 0, 19))\n"
        "show_object(result)\n"
    )
    result = await call_tool(
        "execute_cadquery",
        {
            "code": code,
            "output_dir": str(tmp_path),
            "include_images": False,
            "validation_profile": "reference",
            "verbosity": "summary",
        },
    )
    assert not result.isError
    payload = _tool_payload(result)
    assert payload["success"], payload.get("error")
    assert payload["validation"]["valid"], payload["validation"]
    assert payload["validation"]["profile"] == "reference"


@pytest.mark.asyncio
async def test_call_tool_terminates_cadquery_that_exceeds_timeout():
    result = await call_tool(
        "inspect_cadquery",
        {
            "code": "while True:\n    pass\n",
            "execution_timeout": 0.5,
        },
    )
    assert result.isError
    payload = _tool_payload(result)
    assert not payload["success"]
    assert "timeout" in payload["error"]
    assert "terminated" in payload["error"]
