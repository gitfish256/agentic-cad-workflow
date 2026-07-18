import json
import math
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


RING_BRIM_CODE = """import cadquery as cq

# Ring-shaped pet-bed brim authored by the host agent from the user's prompt.
result = cq.Workplane("XY").circle(60).circle(45).extrude(12)
show_object(result, name="ring_pet_bed_brim")
"""


@pytest.mark.asyncio
async def test_stdio_agent_driven_build_inspect_and_render_end_to_end(tmp_path):
    """Exercise the common stdio path without MCP sampling.

    Deliberately leave StdioServerParameters.env unset. The official MCP client
    then uses its reduced safe environment, which historically omitted WINDIR
    and caused matplotlib to crash before the Windows server could initialize.
    """
    output_dir = tmp_path / "mcp outputs with spaces"
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "agentic_cad_workflow", "--output-dir", str(output_dir)],
    )

    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            initialized = await session.initialize()
            assert initialized.instructions
            assert "host agent must interpret" in initialized.instructions.lower()
            assert len(initialized.instructions.encode("utf-8")) < 2048

            listed = await session.list_tools()
            assert {tool.name for tool in listed.tools} == {
                "execute_cadquery",
                "inspect_cadquery",
                "render_preview",
                "inspect_artifact",
                "render_artifact",
                "make_radial_assembly",
                "split_artifact",
            }

            built = await session.call_tool(
                "execute_cadquery",
                {
                    "code": RING_BRIM_CODE,
                    "prompt": "ring-shaped cat bed brim, 120mm outer diameter, 90mm inner diameter, 12mm tall",
                    "style": "shaded",
                    "include_images": False,
                },
            )
            assert not built.isError
            payload = json.loads(built.content[0].text)
            assert payload["success"], payload.get("error")
            assert "code" not in payload  # normal is the token-efficient default
            assert payload["code_source"] == "agent-provided code"
            assert payload["validation"]["valid"], payload["validation"]

            props = payload["properties"][0]
            bbox = props["bounding_box"]
            assert bbox["xlen"] == pytest.approx(120, abs=0.01)
            assert bbox["ylen"] == pytest.approx(120, abs=0.01)
            assert bbox["zlen"] == pytest.approx(12, abs=0.01)
            assert bbox["zmin"] == pytest.approx(0, abs=1e-5)
            assert props["valid"] is True
            assert props["volume"] == pytest.approx(
                math.pi * (60**2 - 45**2) * 12,
                rel=0.01,
            )

            artifacts = payload["artifacts"]
            for key in ("stl", "step", "brep"):
                assert Path(artifacts[key]).is_file()
            for preview in artifacts["previews"]:
                assert Path(preview["svg"]).is_file()
                assert Path(preview["png"]).is_file()

            inspected = await session.call_tool(
                "inspect_artifact",
                {"path": artifacts["step"], "prompt": "ring-shaped cat bed brim"},
            )
            inspection = json.loads(inspected.content[0].text)
            assert inspection["success"], inspection.get("error")
            assert inspection["validation"]["valid"], inspection["validation"]
            assert inspection["properties"]["volume"] == pytest.approx(
                props["volume"], rel=0.001
            )

            rendered = await session.call_tool(
                "render_artifact",
                {
                    "path": artifacts["step"],
                    "output_dir": str(output_dir),
                    "views": ["isometric", "top"],
                    "style": "shaded",
                    "include_images": False,
                },
            )
            rendering = json.loads(rendered.content[0].text)
            assert rendering["success"], rendering.get("error")
            assert rendering["validation"]["valid"], rendering["validation"]
            assert len(rendering["artifacts"]["previews"]) == 2


@pytest.mark.asyncio
async def test_stdio_business_failures_set_mcp_is_error(tmp_path):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "agentic_cad_workflow", "--output-dir", str(tmp_path)],
    )

    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            invalid_code = await session.call_tool(
                "inspect_cadquery",
                {"code": "this is not valid Python !!!"},
            )
            assert invalid_code.isError
            invalid_payload = json.loads(invalid_code.content[0].text)
            assert not invalid_payload["success"]
            assert invalid_payload["error"]

            missing_artifact = await session.call_tool(
                "inspect_artifact",
                {"path": str(tmp_path / "missing.step")},
            )
            assert missing_artifact.isError
            missing_payload = json.loads(missing_artifact.content[0].text)
            assert not missing_payload["success"]
            assert "not found" in missing_payload["error"]


@pytest.mark.asyncio
async def test_agent_refinement_is_a_second_explicit_build(tmp_path):
    first_code = (
        "import cadquery as cq\n"
        "result = cq.Workplane('XY').box(400, 20, 10).translate((0, 0, 15))\n"
        "show_object(result)\n"
    )
    revised_code = (
        "import cadquery as cq\n"
        "result = cq.Workplane('XY').box(20, 20, 10).translate((0, 0, 5))\n"
        "show_object(result)\n"
    )
    params = StdioServerParameters(command=sys.executable, args=["-m", "agentic_cad_workflow"])

    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            first = await session.call_tool(
                "execute_cadquery",
                {
                    "code": first_code,
                    "prompt": "printable 20mm cube-like test part",
                    "output_dir": str(tmp_path),
                    "include_images": False,
                },
            )
            first_payload = json.loads(first.content[0].text)
            assert first_payload["success"]
            assert not first_payload["validation"]["valid"]

            revised = await session.call_tool(
                "execute_cadquery",
                {
                    "code": revised_code,
                    "prompt": "printable 20mm cube-like test part",
                    "output_dir": str(tmp_path),
                    "include_images": False,
                },
            )
            revised_payload = json.loads(revised.content[0].text)
            assert revised_payload["success"]
            assert revised_payload["validation"]["valid"], revised_payload["validation"]
            assert revised_payload["properties"][0]["bounding_box"]["xlen"] == pytest.approx(20)
            assert revised_payload["output_dir"] != first_payload["output_dir"]
            assert revised_payload["artifacts"]["step"] != first_payload["artifacts"]["step"]
