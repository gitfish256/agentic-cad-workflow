from __future__ import annotations

import base64
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import traceback
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# CAD/preview dependencies normally cache under the user's home directory. Some
# agent sandboxes intentionally make that location read-only, which produces
# noisy stderr during otherwise successful stdio MCP startup. Use a securely
# created, process-private temp directory unless the caller configured a cache.
# A predictable shared /tmp path would be unsafe on a multi-user machine.
_cache_temp_dir: tempfile.TemporaryDirectory[str] | None = None
try:
    _cache_temp_dir = tempfile.TemporaryDirectory(prefix="agentic-cad-cache-")
    _cache_root = Path(_cache_temp_dir.name)
    os.environ.setdefault("MPLCONFIGDIR", str(_cache_root / "matplotlib"))
    os.environ.setdefault("XDG_CACHE_HOME", str(_cache_root))
except OSError:
    # Cache failures are non-fatal; the dependencies can fall back themselves.
    pass

# The official MCP Python client deliberately starts stdio servers with a
# reduced environment.  On Windows that environment contains SYSTEMROOT but
# not WINDIR, while matplotlib's font discovery requires WINDIR.  Normalise it
# before importing CadQuery/matplotlib so the server can start from any MCP
# client, not only from an interactive shell.
if os.name == "nt" and not os.environ.get("WINDIR"):
    system_root = os.environ.get("SYSTEMROOT")
    if system_root:
        os.environ["WINDIR"] = system_root

import cadquery as cq
from cadquery import cqgi, exporters, importers

VIEWS: dict[str, tuple[float, float, float]] = {
    # Camera/projection directions shared by SVG (projectionDir) and PNG rendering.
    # front/back look along Y and reveal an X/Z side profile.
    "isometric": (-1.75, 1.10, 5.0),
    "front": (0.0, -1.0, 0.0),
    "back": (0.0, 1.0, 0.0),
    "top": (0.0, 0.0, 1.0),
    "bottom": (0.0, 0.0, -1.0),
    "left": (-1.0, 0.0, 0.0),
    "right": (1.0, 0.0, 0.0),
}

PREVIEW_STYLES = ("outline", "shaded")
DEFAULT_EXECUTION_TIMEOUT_SECONDS = 60.0


@dataclass
class ShapeProperties:
    bounding_box: dict[str, float]
    volume: float | None
    area: float | None
    center: dict[str, float] | None
    topology: dict[str, int] = field(default_factory=dict)
    valid: bool | None = None


@dataclass
class CadExecution:
    success: bool
    code: str
    shapes: list[Any] = field(default_factory=list)
    properties: list[ShapeProperties] = field(default_factory=list)
    error: str | None = None
    build_time: float | None = None


def slugify(text: str, default: str = "model") -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", text).strip("-._").lower()
    return slug[:80] or default


def new_run_id(prefix: str = "run") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def ensure_output_dir(output_dir: str | Path, run_id: str | None = None) -> Path:
    base = Path(output_dir).expanduser().resolve()
    target = base / (run_id or new_run_id())
    target.mkdir(parents=True, exist_ok=True)
    return target


def _format_exception(exc: BaseException) -> str:
    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))


def _normalize_shape(obj: Any) -> Any:
    """Return a CadQuery Shape-like object suitable for exporters."""
    if isinstance(obj, cq.Workplane):
        values = obj.vals()
        if len(values) == 1:
            return values[0]
        if len(values) > 1 and all(isinstance(value, cq.Shape) for value in values):
            # A Workplane can legitimately hold several solids (for example,
            # pushPoints(...).box(..., combine=False)).  Workplane.val() silently
            # returns only the first stack item, which would produce a plausible
            # but incomplete STL.  Preserve every modeled object as one Compound.
            return cq.Compound.makeCompound(values)
        if not values:
            raise ValueError("CadQuery Workplane contains no exportable objects")
        raise TypeError(
            "CadQuery Workplane contains multiple non-Shape objects; combine the "
            "final geometry into a Shape or Compound before exporting"
        )
    if isinstance(obj, cq.Assembly):
        return obj.toCompound()
    if isinstance(obj, cq.Shape):
        return obj
    if hasattr(obj, "val"):
        return obj.val()
    return obj


def _extract_shapes(build_result: Any) -> list[Any]:
    shapes: list[Any] = []

    for item in getattr(build_result, "results", []) or []:
        shape = getattr(item, "shape", None)
        if shape is not None:
            shapes.append(_normalize_shape(shape))

    if not shapes and getattr(build_result, "first_result", None) is not None:
        first_shape = getattr(build_result.first_result, "shape", None)
        if first_shape is not None:
            shapes.append(_normalize_shape(first_shape))

    # cqgi exposes env on recent CadQuery builds. This catches scripts that only assign result = ...
    env = getattr(build_result, "env", {}) or {}
    if not shapes and "result" in env:
        shapes.append(_normalize_shape(env["result"]))

    return shapes


def _execute_cadquery_in_process(code: str) -> CadExecution:
    """Execute CadQuery code in this process.

    MCP requests use :func:`execute_cadquery` with a timeout, which delegates to
    a disposable worker process.  This in-process primitive remains useful for
    the worker itself and for trusted library callers.
    """
    try:
        model = cqgi.parse(code)
        build_result = model.build()
    except Exception as exc:  # syntax or CQGI setup error
        return CadExecution(success=False, code=code, error=_format_exception(exc))

    if getattr(build_result, "exception", None):
        return CadExecution(
            success=False,
            code=code,
            error=_format_exception(build_result.exception),
            build_time=getattr(build_result, "buildTime", None),
        )

    shapes = _extract_shapes(build_result)
    if not shapes:
        return CadExecution(
            success=False,
            code=code,
            error="No CadQuery shape was produced. Use show_object(result) or assign a CadQuery object to result.",
            build_time=getattr(build_result, "buildTime", None),
        )

    props: list[ShapeProperties] = []
    for shape in shapes:
        props.append(shape_properties(shape))

    return CadExecution(
        success=True,
        code=code,
        shapes=shapes,
        properties=props,
        build_time=getattr(build_result, "buildTime", None),
    )


def _worker_environment() -> dict[str, str]:
    """Minimal environment for the disposable CadQuery execution worker."""
    allowed = {
        "APPDATA",
        "HOME",
        "HOMEDRIVE",
        "HOMEPATH",
        "LOCALAPPDATA",
        "PATH",
        "PATHEXT",
        "PROCESSOR_ARCHITECTURE",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "USERNAME",
        "USERPROFILE",
        "WINDIR",
    }
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def execute_cadquery(code: str, timeout_seconds: float | None = None) -> CadExecution:
    """Execute CadQuery code and return shapes plus properties.

    When ``timeout_seconds`` is provided, execution happens in a disposable
    subprocess.  This protects the MCP server from crashes and infinite loops
    and avoids passing the server's complete environment to generated code.  It
    is process isolation, not an OS security sandbox: only run the local stdio
    server for users and agents you trust.
    """
    if timeout_seconds is None:
        return _execute_cadquery_in_process(code)
    timeout = float(timeout_seconds)
    if timeout <= 0:
        return CadExecution(
            success=False,
            code=code,
            error=f"execution timeout must be > 0 seconds, got {timeout}",
        )

    with tempfile.TemporaryDirectory(prefix="agentic-cad-worker-") as temp_dir:
        request = json.dumps({"code": code, "output_dir": temp_dir})
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            completed = subprocess.run(
                [sys.executable, "-m", "agentic_cad_workflow._worker"],
                input=request,
                text=True,
                encoding="utf-8",
                capture_output=True,
                cwd=temp_dir,
                env=_worker_environment(),
                timeout=timeout,
                creationflags=creationflags,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return CadExecution(
                success=False,
                code=code,
                error=f"CadQuery execution exceeded the {timeout:g}s timeout and was terminated.",
            )
        except Exception as exc:
            return CadExecution(success=False, code=code, error=_format_exception(exc))

        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or "worker exited without details"
            return CadExecution(
                success=False,
                code=code,
                error=f"CadQuery worker exited with code {completed.returncode}: {detail}",
            )
        try:
            payload = json.loads(completed.stdout)
        except Exception as exc:
            return CadExecution(
                success=False,
                code=code,
                error=f"Invalid CadQuery worker response: {exc}; stderr={completed.stderr.strip()}",
            )
        if not payload.get("success"):
            return CadExecution(
                success=False,
                code=code,
                error=payload.get("error") or "CadQuery worker execution failed",
                build_time=payload.get("build_time"),
            )

        shapes: list[Any] = []
        try:
            for brep_name in payload.get("brep_files", []):
                workplane = importers.importShape("BREP", str(Path(temp_dir) / brep_name))
                values = workplane.vals()
                if len(values) == 1:
                    shapes.append(values[0])
                elif values:
                    shapes.append(cq.Compound.makeCompound(values))
        except Exception as exc:
            return CadExecution(
                success=False,
                code=code,
                error=f"Could not import CadQuery worker result: {_format_exception(exc)}",
                build_time=payload.get("build_time"),
            )
        if not shapes:
            return CadExecution(
                success=False,
                code=code,
                error="CadQuery worker returned no importable shapes.",
                build_time=payload.get("build_time"),
            )
        return CadExecution(
            success=True,
            code=code,
            shapes=shapes,
            properties=[shape_properties(shape) for shape in shapes],
            build_time=payload.get("build_time"),
        )


def shape_properties(shape: Any) -> ShapeProperties:
    shape = _normalize_shape(shape)
    bb = shape.BoundingBox()
    bbox = {
        "xmin": float(bb.xmin),
        "xmax": float(bb.xmax),
        "ymin": float(bb.ymin),
        "ymax": float(bb.ymax),
        "zmin": float(bb.zmin),
        "zmax": float(bb.zmax),
        "xlen": float(bb.xlen),
        "ylen": float(bb.ylen),
        "zlen": float(bb.zlen),
    }

    def try_float(method_name: str) -> float | None:
        try:
            return float(getattr(shape, method_name)())
        except Exception:
            return None

    center = None
    try:
        c = shape.Center()
        center = {"x": float(c.x), "y": float(c.y), "z": float(c.z)}
    except Exception:
        pass

    topology: dict[str, int] = {}
    for name in ("Solids", "Faces", "Edges", "Vertices"):
        try:
            topology[name.lower()] = len(getattr(shape, name)())
        except Exception:
            pass

    valid = None
    try:
        valid = bool(shape.isValid())
    except Exception:
        pass

    return ShapeProperties(
        bounding_box=bbox,
        volume=try_float("Volume"),
        area=try_float("Area"),
        center=center,
        topology=topology,
        valid=valid,
    )


def properties_to_dict(props: ShapeProperties) -> dict[str, Any]:
    return {
        "bounding_box": props.bounding_box,
        "volume": props.volume,
        "area": props.area,
        "center": props.center,
        "topology": props.topology,
        "valid": props.valid,
    }


def render_svg(shape: Any, svg_path: str | Path, view: str = "isometric", width: int = 900, height: int = 650) -> Path:
    svg_path = Path(svg_path)
    svg_path.parent.mkdir(parents=True, exist_ok=True)
    opts = {
        "width": width,
        "height": height,
        "marginLeft": 25,
        "marginTop": 25,
        "projectionDir": VIEWS.get(view, VIEWS["isometric"]),
        "showAxes": view == "isometric",
        "showHidden": False,
        "strokeWidth": 1.0,
        "strokeColor": (0, 0, 0),
        "hiddenColor": (160, 160, 160),
    }
    exporters.export(_normalize_shape(shape), str(svg_path), exportType="SVG", opt=opts)
    return svg_path


def _camera_angles(direction: Any) -> tuple[float, float]:
    x, y, z = direction
    azim = math.degrees(math.atan2(y, x))
    elev = math.degrees(math.atan2(z, math.hypot(x, y)))
    return elev, azim


def _edge_segments(shape: Any) -> list[list[tuple[float, float, float]]]:
    """Feature edges as uniform 2-point segments (Line3DCollection needs a regular array)."""
    segments: list[list[tuple[float, float, float]]] = []
    for edge in shape.Edges():
        try:
            n = 2 if edge.geomType() == "LINE" else 24
            pts = [edge.positionAt(i / (n - 1)) for i in range(n)]
        except Exception:
            continue
        coords = [(p.x, p.y, p.z) for p in pts]
        segments.extend([coords[i], coords[i + 1]] for i in range(len(coords) - 1))
    return segments


def render_png(
    shape: Any,
    png_path: str | Path,
    view: str = "isometric",
    style: str = "outline",
    width: int = 900,
    height: int = 650,
) -> Path:
    """Render a PNG preview from tessellated geometry with matplotlib (headless Agg).

    Styles: "outline" (white faces, black feature edges) or "shaded" (Lambert-lit
    faces plus feature edges, which makes holes/pockets/curvature unambiguous).
    """
    if style not in PREVIEW_STYLES:
        raise ValueError(f"unknown preview style {style!r}; expected one of {PREVIEW_STYLES}")

    # Keep heavy rendering imports out of MCP startup.  Besides reducing launch
    # time, this prevents platform-specific font discovery from breaking tools
    # that only inspect geometry.
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from mpl_toolkits.mplot3d.art3d import Line3DCollection, Poly3DCollection

    shape = _normalize_shape(shape)
    png_path = Path(png_path)
    png_path.parent.mkdir(parents=True, exist_ok=True)

    vertices, triangles = shape.tessellate(0.1)
    pts = np.array([(v.x, v.y, v.z) for v in vertices])
    tris = pts[np.array(triangles)]

    direction = np.array(VIEWS.get(view, VIEWS["isometric"]), dtype=float)
    direction /= np.linalg.norm(direction)

    fig = plt.figure(figsize=(width / 100, height / 100), dpi=100)
    try:
        ax = fig.add_subplot(projection="3d")
        ax.set_proj_type("ortho")

        if style == "shaded":
            normals = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
            lengths = np.linalg.norm(normals, axis=1, keepdims=True)
            normals = normals / np.where(lengths == 0, 1.0, lengths)
            light = direction + np.array([0.35, 0.25, 0.6])
            light /= np.linalg.norm(light)
            intensity = np.abs(normals @ light)
            shade = 0.45 + 0.5 * intensity
            colors = np.column_stack([shade * 0.72, shade * 0.78, shade * 0.92])
            ax.add_collection3d(Poly3DCollection(tris, facecolors=colors, edgecolors="none"))
        else:
            ax.add_collection3d(Poly3DCollection(tris, facecolors="white", edgecolors="none"))

        edges = _edge_segments(shape)
        if edges:
            ax.add_collection3d(Line3DCollection(edges, colors="black", linewidths=1.0))

        bb = shape.BoundingBox()
        ax.set_xlim(bb.xmin, bb.xmax)
        ax.set_ylim(bb.ymin, bb.ymax)
        ax.set_zlim(bb.zmin, bb.zmax)
        ax.set_box_aspect((bb.xlen or 1.0, bb.ylen or 1.0, bb.zlen or 1.0))
        elev, azim = _camera_angles(direction)
        ax.view_init(elev=elev, azim=azim)
        ax.set_axis_off()
        fig.tight_layout(pad=0)
        fig.savefig(png_path, facecolor="white")
    finally:
        plt.close(fig)
    return png_path


def export_shape_files(
    shape: Any,
    output_dir: str | Path,
    base_name: str = "model",
    views: Iterable[str] = ("isometric", "front", "top", "right"),
    width: int = 900,
    height: int = 650,
    style: str = "outline",
) -> dict[str, Any]:
    """Export one normalized shape to 3D print formats and 2D previews."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    base_name = slugify(base_name)
    shape = _normalize_shape(shape)

    artifacts: dict[str, Any] = {}
    stl_path = output_dir / f"{base_name}.stl"
    step_path = output_dir / f"{base_name}.step"
    brep_path = output_dir / f"{base_name}.brep"

    exporters.export(shape, str(stl_path), exportType="STL", tolerance=0.05, angularTolerance=0.1)
    exporters.export(shape, str(step_path), exportType="STEP")
    exporters.export(shape, str(brep_path), exportType="BREP")

    artifacts["stl"] = str(stl_path)
    artifacts["step"] = str(step_path)
    artifacts["brep"] = str(brep_path)

    artifacts.update(
        export_preview_files(
            shape,
            output_dir,
            base_name=base_name,
            views=views,
            width=width,
            height=height,
            style=style,
        )
    )
    return artifacts


def export_preview_files(
    shape: Any,
    output_dir: str | Path,
    base_name: str = "model",
    views: Iterable[str] = ("isometric", "front", "top", "right"),
    width: int = 900,
    height: int = 650,
    style: str = "outline",
) -> dict[str, Any]:
    """Export only SVG/PNG previews for one normalized shape."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    base_name = slugify(base_name)
    shape = _normalize_shape(shape)

    preview_items: list[dict[str, str]] = []
    for view in views:
        svg_path = output_dir / f"{base_name}-{view}.svg"
        png_path = output_dir / f"{base_name}-{view}.png"
        render_svg(shape, svg_path, view=view, width=width, height=height)
        render_png(shape, png_path, view=view, style=style, width=width, height=height)
        preview_items.append({"view": view, "svg": str(svg_path), "png": str(png_path)})
    return {"previews": preview_items}


def write_run_files(output_dir: str | Path, prompt: str, code: str, metadata: dict[str, Any]) -> dict[str, str]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = output_dir / "prompt.txt"
    code_path = output_dir / "model.py"
    metadata_path = output_dir / "metadata.json"
    prompt_path.write_text(prompt, encoding="utf-8")
    code_path.write_text(code, encoding="utf-8")
    metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"prompt": str(prompt_path), "code": str(code_path), "metadata": str(metadata_path)}


def png_as_image_content(path: str | Path) -> str:
    """Return base64 PNG payload for MCP ImageContent."""
    return base64.b64encode(Path(path).read_bytes()).decode("ascii")
