from __future__ import annotations

import argparse
import asyncio
import math
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import Any, TypeVar

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, ImageContent, TextContent, Tool

from . import __version__
from .artifacts import inspect_artifact, make_radial_assembly, render_artifact, split_artifact
from .generator import build_model_from_code, render_preview_from_code, result_to_json_text
from .runner import (
    DEFAULT_EXECUTION_TIMEOUT_SECONDS,
    PREVIEW_STYLES,
    execute_cadquery,
    png_as_image_content,
    properties_to_dict,
)
from .validation import VALIDATION_PROFILES, validate_prompt_shape

SERVER_INSTRUCTIONS = """The host agent must interpret the user's prompt and write or revise the CadQuery code itself. Use millimeters, import cadquery as cq, assign the final single Shape/Workplane/Assembly to result, call show_object(result, name=...), and place printable geometry on or above Z=0. Prefer one execute_cadquery call with the original prompt, the correct validation_profile and bed_size, and style=shaded; it executes, exports STL/STEP/BREP plus previews, measures geometry, and validates it. Compare measured dimensions and previews with the prompt. If they do not conform, revise the code and call execute_cadquery again. Use inspect_artifact/render_artifact for saved STEP/BREP files and split_artifact only when the user requests splitting. Validation is geometry_heuristic only: it does not prove prompt conformance, local wall thickness, clearances, overhangs, material strength, or slicer compatibility. Generated Python runs locally with the current user's filesystem and network permissions despite process isolation and a timeout."""


server = Server(
    "agentic-cad-workflow",
    version=__version__,
    instructions=SERVER_INSTRUCTIONS,
)
DEFAULT_OUTPUT_DIR = str(Path.cwd() / "outputs")
# Server-wide printer bed size (mm), set with --bed-size; per-call bed_size overrides it.
DEFAULT_BED_SIZE: float | None = None
DEFAULT_EXECUTION_TIMEOUT = DEFAULT_EXECUTION_TIMEOUT_SECONDS

# CadQuery/OpenCASCADE and matplotlib are synchronous and are not assumed to be
# thread-safe.  Keep every CAD operation on one worker thread: the asyncio loop
# remains responsive to MCP lifecycle and discovery requests, while two model
# operations can never enter OCC or rendering code concurrently.
_CAD_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="agentic-cad")
_T = TypeVar("_T")


async def _run_cad_sync(function: Callable[..., _T], /, *args: Any, **kwargs: Any) -> _T:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _CAD_EXECUTOR,
        partial(function, *args, **kwargs),
    )


def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required or []}


VALIDATION_OPTIONS: dict[str, Any] = {
    "validation_profile": {
        "type": "string",
        "enum": list(VALIDATION_PROFILES),
        "default": "printable",
        "description": "printable: print-bed size is fatal. reference/assembly: oversize is a non-fatal advisory.",
    },
    "bed_size": {
        "type": "number",
        "exclusiveMinimum": 0,
        "description": "Uniform maximum X/Y/Z bounding-box dimension in mm (default: server --bed-size, else 300).",
    },
}

STYLE_OPTION: dict[str, Any] = {
    "style": {
        "type": "string",
        "enum": list(PREVIEW_STYLES),
        "default": "outline",
        "description": "PNG preview style: outline (white faces + feature edges) or shaded (lit faces, best for reading holes/pockets/curvature).",
    },
}

VERBOSITY_OPTIONS: dict[str, Any] = {
    "verbosity": {
        "type": "string",
        "enum": ["summary", "normal", "full"],
        "default": "normal",
        "description": "summary: compact validation without code/metadata. normal: no repeated code. full: complete payload.",
    },
    "include_code": {"type": "boolean", "description": "Force include/omit the code field regardless of verbosity."},
    "include_metadata": {"type": "boolean", "description": "Force include/omit prompt/code_source/build_time."},
}

EXECUTION_OPTIONS: dict[str, Any] = {
    "execution_timeout": {
        "type": "number",
        "exclusiveMinimum": 0,
        "default": DEFAULT_EXECUTION_TIMEOUT,
        "description": "Maximum seconds for generated/supplied CadQuery code before its worker is terminated.",
    }
}


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="execute_cadquery",
            description=(
                "Primary agent-driven build tool. Execute CadQuery Python code authored by the host agent "
                "from the user's prompt; export STL/STEP/BREP/SVG/PNG, measure geometry, and validate it."
            ),
            inputSchema=_schema(
                {
                    "code": {
                        "type": "string",
                        "description": "Agent-authored CadQuery code. Assign the final model to result and call show_object(result).",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "Original user prompt/physical intent used when assessing the returned geometry.",
                    },
                    "output_dir": {"type": "string", "description": "Directory for generated artifacts."},
                    "include_images": {"type": "boolean", "default": True},
                    **EXECUTION_OPTIONS,
                    **VALIDATION_OPTIONS,
                    **STYLE_OPTION,
                    **VERBOSITY_OPTIONS,
                },
                ["code"],
            ),
        ),
        Tool(
            name="render_preview",
            description="Execute CadQuery code and return exported SVG/PNG preview artifact paths from standard views.",
            inputSchema=_schema(
                {
                    "code": {"type": "string"},
                    "prompt": {
                        "type": "string",
                        "description": "Original user prompt/physical intent used for validation.",
                    },
                    "output_dir": {"type": "string"},
                    "include_images": {"type": "boolean", "default": True},
                    **EXECUTION_OPTIONS,
                    **VALIDATION_OPTIONS,
                    **STYLE_OPTION,
                    **VERBOSITY_OPTIONS,
                },
                ["code"],
            ),
        ),
        Tool(
            name="inspect_cadquery",
            description="Execute CadQuery code and return geometry properties and heuristic validation without exporting files.",
            inputSchema=_schema(
                {
                    "code": {"type": "string"},
                    "prompt": {"type": "string", "default": "manual CadQuery execution"},
                    **EXECUTION_OPTIONS,
                    **VALIDATION_OPTIONS,
                },
                ["code"],
            ),
        ),
        Tool(
            name="inspect_artifact",
            description=(
                "Inspect an existing exported artifact file (STEP/BREP/BIN) without re-running CadQuery code: "
                "returns file info, bounding box, volume, area, center, topology, and a validation report. "
                "STL files return a structured error (CadQuery has no STL importer); use the sibling STEP/BREP."
            ),
            inputSchema=_schema(
                {
                    "path": {"type": "string", "description": "Path to a .step/.stp/.brep/.bin artifact."},
                    "prompt": {"type": "string", "description": "Optional intent used for validation heuristics."},
                    **VALIDATION_OPTIONS,
                },
                ["path"],
            ),
        ),
        Tool(
            name="render_artifact",
            description=(
                "Render SVG/PNG previews of an existing artifact file (STEP/BREP/BIN) from standard views, "
                "plus geometry properties and validation. Does not re-export solids."
            ),
            inputSchema=_schema(
                {
                    "path": {"type": "string", "description": "Path to a .step/.stp/.brep/.bin artifact."},
                    "output_dir": {"type": "string", "description": "Directory for preview files."},
                    "views": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Views from: isometric, front, back, top, bottom, left, right. Default: isometric, front, top, right.",
                    },
                    "include_images": {"type": "boolean", "default": True},
                    **VALIDATION_OPTIONS,
                    **STYLE_OPTION,
                },
                ["path"],
            ),
        ),
        Tool(
            name="make_radial_assembly",
            description=(
                "Assemble N rotated copies of a segment artifact (STEP/BREP/BIN) about the Z axis and export "
                "STL/STEP/BREP plus previews — e.g. four 90-degree ring segments into a full reference ring. "
                "Uses the 'assembly' validation profile, so exceeding the print bed is only an advisory."
            ),
            inputSchema=_schema(
                {
                    "source_path": {"type": "string", "description": "Path to the segment artifact."},
                    "count": {"type": "integer", "default": 4, "minimum": 1},
                    "angle_step": {
                        "type": "number",
                        "default": 90.0,
                        "description": "Rotation between copies in degrees.",
                    },
                    "output_dir": {"type": "string"},
                    "boolean_fuse": {
                        "type": "boolean",
                        "default": False,
                        "description": "Fuse copies into one solid instead of a compound.",
                    },
                    "include_images": {"type": "boolean", "default": True},
                    "bed_size": {
                        "type": "number",
                        "exclusiveMinimum": 0,
                        "description": "Max print dimension in mm for the advisory (default 300).",
                    },
                },
                ["source_path"],
            ),
        ),
        Tool(
            name="split_artifact",
            description=(
                "Split an oversize artifact (STEP/BREP/BIN) into printable segments with axis-aligned planar "
                "cuts so each segment fits the print bed. Segments are recentered with their base on Z=0, "
                "exported (STL/STEP/BREP + preview), and each validated with the 'printable' profile. "
                "If the model already fits, reports split_needed=false."
            ),
            inputSchema=_schema(
                {
                    "source_path": {"type": "string", "description": "Path to the oversize artifact."},
                    "bed_size": {
                        "type": "number",
                        "exclusiveMinimum": 0,
                        "description": "Max print dimension in mm (default: server --bed-size, else 300).",
                    },
                    "output_dir": {"type": "string"},
                    "include_images": {"type": "boolean", "default": True},
                },
                ["source_path"],
            ),
        ),
    ]


def _output_dir(arguments: dict[str, Any]) -> str:
    return str(Path(arguments.get("output_dir") or DEFAULT_OUTPUT_DIR).expanduser())


def _bed_size(arguments: dict[str, Any]) -> float | None:
    """Per-call bed_size wins; otherwise the server-wide --bed-size (may be None)."""
    value = arguments["bed_size"] if "bed_size" in arguments else DEFAULT_BED_SIZE
    if value is None:
        return None
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"bed_size must be a finite number > 0, got {value}")
    return value


def _validation_kwargs(arguments: dict[str, Any]) -> dict[str, Any]:
    """Map MCP schema args (validation_profile/bed_size) to validate_prompt_shape kwargs."""
    return {
        "profile": arguments.get("validation_profile", "printable"),
        "max_print_dim_mm": _bed_size(arguments),
    }


def _execution_timeout(arguments: dict[str, Any]) -> float:
    value = float(arguments.get("execution_timeout", DEFAULT_EXECUTION_TIMEOUT))
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"execution_timeout must be a finite number > 0, got {value}")
    return value


def _shrink_result(result: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
    """Trim a tool result payload according to verbosity/include_code/include_metadata.

    Normal is the token-efficient default; explicit include_code/include_metadata
    booleans override whatever the verbosity level implies.
    """
    verbosity = arguments.get("verbosity", "normal")
    include_code = arguments.get("include_code")
    include_metadata = arguments.get("include_metadata")
    if include_code is None:
        include_code = verbosity == "full"
    if include_metadata is None:
        include_metadata = verbosity != "summary"
    if verbosity == "full" and include_code and include_metadata:
        return result

    out = dict(result)
    if not include_code:
        out.pop("code", None)
    if not include_metadata:
        for key in ("prompt", "code_source", "build_time"):
            out.pop(key, None)
    if verbosity == "summary":
        validation = out.get("validation")
        if isinstance(validation, dict):
            out["validation"] = {
                k: validation[k]
                for k in ("valid", "score", "summary", "profile", "scope", "limitations")
                if k in validation
            }
        properties = out.get("properties")
        if isinstance(properties, list) and len(properties) > 1:
            out["properties"] = properties[:1]
    return out


def _contents(result: dict[str, Any], include_images: bool = True) -> list[TextContent | ImageContent]:
    contents: list[TextContent | ImageContent] = [TextContent(type="text", text=result_to_json_text(result))]
    if include_images:
        previews = result.get("artifacts", {}).get("previews", [])
        for item in previews[:4]:
            png = item.get("png")
            if not png:
                continue
            try:
                contents.append(ImageContent(type="image", data=png_as_image_content(png), mimeType="image/png"))
            except Exception:
                # Paths are still present in the JSON if image attachment fails.
                pass
    return contents


def _call_result(result: dict[str, Any], include_images: bool = True) -> CallToolResult:
    """Preserve JSON feedback while exposing failures through MCP isError."""
    return CallToolResult(
        content=_contents(result, include_images=include_images),
        isError=not bool(result.get("success")),
    )


def _inspect_cadquery(arguments: dict[str, Any]) -> dict[str, Any]:
    """Execute, measure, and validate code entirely on the serial CAD worker."""
    execution = execute_cadquery(arguments["code"], timeout_seconds=_execution_timeout(arguments))
    execution.require_single_result()
    payload: dict[str, Any] = {
        "success": execution.success,
        "error": execution.error,
        "build_time": execution.build_time,
        "properties": [properties_to_dict(p) for p in execution.properties],
    }
    if execution.success:
        payload["validation"] = validate_prompt_shape(
            arguments.get("prompt", "manual CadQuery execution"),
            execution.properties[0],
            **_validation_kwargs(arguments),
        ).to_dict()
    return payload


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> CallToolResult:
    arguments = arguments or {}
    include_images = bool(arguments.get("include_images", True))

    try:
        if name == "execute_cadquery":
            result = await _run_cad_sync(
                build_model_from_code,
                code=arguments["code"],
                prompt=arguments.get("prompt", "manual CadQuery execution"),
                output_dir=_output_dir(arguments),
                style=arguments.get("style", "outline"),
                execution_timeout=_execution_timeout(arguments),
                **_validation_kwargs(arguments),
            )
            return _call_result(_shrink_result(result, arguments), include_images=include_images)

        if name == "render_preview":
            result = await _run_cad_sync(
                render_preview_from_code,
                code=arguments["code"],
                prompt=arguments.get("prompt", "manual CadQuery preview"),
                output_dir=_output_dir(arguments),
                style=arguments.get("style", "outline"),
                execution_timeout=_execution_timeout(arguments),
                **_validation_kwargs(arguments),
            )
            return _call_result(_shrink_result(result, arguments), include_images=include_images)

        if name == "inspect_cadquery":
            payload = await _run_cad_sync(_inspect_cadquery, arguments)
            return _call_result(payload, include_images=False)

        if name == "inspect_artifact":
            payload = await _run_cad_sync(
                inspect_artifact,
                arguments["path"],
                prompt=arguments.get("prompt", "artifact inspection"),
                **_validation_kwargs(arguments),
            )
            return _call_result(payload, include_images=False)

        if name == "render_artifact":
            result = await _run_cad_sync(
                render_artifact,
                arguments["path"],
                output_dir=_output_dir(arguments),
                views=arguments.get("views"),
                prompt=arguments.get("prompt", "artifact preview"),
                style=arguments.get("style", "outline"),
                **_validation_kwargs(arguments),
            )
            return _call_result(result, include_images=include_images)

        if name == "make_radial_assembly":
            result = await _run_cad_sync(
                make_radial_assembly,
                arguments["source_path"],
                count=int(arguments.get("count", 4)),
                angle_step=float(arguments.get("angle_step", 90.0)),
                output_dir=_output_dir(arguments),
                boolean_fuse=bool(arguments.get("boolean_fuse", False)),
                max_print_dim_mm=_bed_size(arguments),
            )
            return _call_result(result, include_images=include_images)

        if name == "split_artifact":
            result = await _run_cad_sync(
                split_artifact,
                arguments["source_path"],
                bed_size=_bed_size(arguments),
                output_dir=_output_dir(arguments),
            )
            return _call_result(result, include_images=include_images)

        return _call_result({"success": False, "error": f"unknown tool: {name}"}, include_images=False)
    except Exception as exc:
        return _call_result(
            {"success": False, "error": f"{type(exc).__name__}: {exc}"},
            include_images=False,
        )


async def _run_stdio(output_dir: str, bed_size: float | None, execution_timeout: float) -> None:
    global DEFAULT_OUTPUT_DIR, DEFAULT_BED_SIZE, DEFAULT_EXECUTION_TIMEOUT
    DEFAULT_OUTPUT_DIR = str(Path(output_dir).expanduser().resolve())
    DEFAULT_BED_SIZE = bed_size
    DEFAULT_EXECUTION_TIMEOUT = execution_timeout
    EXECUTION_OPTIONS["execution_timeout"]["default"] = execution_timeout
    Path(DEFAULT_OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Agentic CAD workflow MCP stdio server (powered by CadQuery)")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="Artifact output directory")
    parser.add_argument(
        "--bed-size",
        type=float,
        default=None,
        help="Uniform maximum X/Y/Z bounding-box dimension in mm, used unless a call overrides bed_size (default 300)",
    )
    parser.add_argument(
        "--execution-timeout",
        type=float,
        default=DEFAULT_EXECUTION_TIMEOUT,
        help="Maximum seconds for each generated/supplied CadQuery execution worker (default 60)",
    )
    args = parser.parse_args(argv)
    if not math.isfinite(args.execution_timeout) or args.execution_timeout <= 0:
        parser.error("--execution-timeout must be a finite number > 0")
    if args.bed_size is not None and (not math.isfinite(args.bed_size) or args.bed_size <= 0):
        parser.error("--bed-size must be a finite number > 0")
    asyncio.run(_run_stdio(args.output_dir, args.bed_size, args.execution_timeout))


if __name__ == "__main__":
    main()
