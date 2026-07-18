from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Iterable

import cadquery as cq
from cadquery import importers

from .runner import (
    PREVIEW_STYLES,
    VIEWS,
    ensure_output_dir,
    export_shape_files,
    new_run_id,
    properties_to_dict,
    render_png,
    render_svg,
    shape_properties,
    slugify,
)
from .validation import DEFAULT_MAX_PRINT_DIM_MM, validate_prompt_shape


# Formats cadquery.importers.importShape can load. STL is intentionally absent:
# CadQuery 2.x has no STL importer, so STL gets a structured error instead.
IMPORT_TYPES: dict[str, str] = {
    ".step": "STEP",
    ".stp": "STEP",
    ".brep": "BREP",
    ".bin": "BIN",
}

DEFAULT_ARTIFACT_VIEWS = ("isometric", "front", "top", "right")


def _file_info(path: Path) -> dict[str, Any]:
    info: dict[str, Any] = {"path": str(path)}
    if path.exists():
        info["file_size"] = path.stat().st_size
    info["format"] = path.suffix.lower().lstrip(".") or None
    return info


def import_artifact_shape(path: str | Path) -> Any:
    """Import a STEP/BREP/BIN artifact and return a single Shape (compound if multi-body).

    Raises ValueError with an agent-actionable message for missing files, STL, and
    unsupported extensions.
    """
    path = Path(path).expanduser()
    if not path.exists():
        raise ValueError(f"artifact not found: {path}")
    suffix = path.suffix.lower()
    if suffix == ".stl":
        raise ValueError(
            "STL import is not supported by cadquery.importers (mesh, not BREP). "
            "Inspect the matching .step or .brep artifact instead; agentic-cad-workflow exports all three side by side."
        )
    import_type = IMPORT_TYPES.get(suffix)
    if import_type is None:
        raise ValueError(f"unsupported artifact format {suffix!r}; supported: {sorted(IMPORT_TYPES)}")

    workplane = importers.importShape(import_type, str(path))  # type: ignore[arg-type]
    shapes = workplane.vals()
    if not shapes:
        raise ValueError(f"no shapes found in {path}")
    if len(shapes) == 1:
        return shapes[0]
    return cq.Compound.makeCompound(shapes)


def _import_or_error(path: Path, payload: dict[str, Any]) -> Any | None:
    """Import an artifact; on failure record payload["error"] and return None.

    ValueError messages from import_artifact_shape are already agent-actionable;
    other exception types keep their class name for context.
    """
    try:
        return import_artifact_shape(path)
    except Exception as exc:
        payload["error"] = str(exc) if isinstance(exc, ValueError) else f"{type(exc).__name__}: {exc}"
        return None


def _props_and_validation(
    props: Any,
    prompt: str,
    profile: str,
    max_print_dim_mm: float | None,
) -> dict[str, Any]:
    return {
        "properties": properties_to_dict(props),
        "validation": validate_prompt_shape(
            prompt, props, profile=profile, max_print_dim_mm=max_print_dim_mm
        ).to_dict(),
    }


def inspect_artifact(
    path: str | Path,
    prompt: str = "artifact inspection",
    profile: str = "printable",
    max_print_dim_mm: float | None = None,
) -> dict[str, Any]:
    """Return geometry properties + validation for an existing exported artifact."""
    path = Path(path).expanduser()
    payload: dict[str, Any] = {"success": False, **_file_info(path)}
    shape = _import_or_error(path, payload)
    if shape is None:
        return payload

    payload["success"] = True
    payload.update(_props_and_validation(shape_properties(shape), prompt, profile, max_print_dim_mm))
    return payload


def render_artifact(
    path: str | Path,
    output_dir: str | Path = "outputs",
    views: Iterable[str] | None = None,
    run_id: str | None = None,
    prompt: str = "artifact preview",
    profile: str = "printable",
    max_print_dim_mm: float | None = None,
    style: str = "outline",
) -> dict[str, Any]:
    """Render SVG/PNG previews of an existing artifact without re-exporting solids."""
    path = Path(path).expanduser()
    payload: dict[str, Any] = {"success": False, **_file_info(path)}
    views = tuple(views or DEFAULT_ARTIFACT_VIEWS)
    unknown = [v for v in views if v not in VIEWS]
    if unknown:
        payload["error"] = f"unknown views {unknown}; available: {sorted(VIEWS)}"
        return payload
    if style not in PREVIEW_STYLES:
        payload["error"] = f"unknown style {style!r}; available: {list(PREVIEW_STYLES)}"
        return payload

    shape = _import_or_error(path, payload)
    if shape is None:
        return payload

    # Measure before tessellation. CadQuery can attach a mesh whose deflection
    # slightly pads later BoundingBox() calls, which must not change validation
    # simply because the same artifact was rendered.
    props = shape_properties(shape)

    run_path = ensure_output_dir(output_dir, run_id or new_run_id("artifact"))
    # Keep preview names bounded for Windows path-length compatibility and
    # strip characters that are invalid in Windows filenames.
    base_name = slugify(path.stem, default="artifact")
    previews: list[dict[str, str]] = []
    for view in views:
        svg_path = run_path / f"{base_name}-{view}.svg"
        png_path = run_path / f"{base_name}-{view}.png"
        render_svg(shape, svg_path, view=view)
        render_png(shape, png_path, view=view, style=style)
        previews.append({"view": view, "svg": str(svg_path), "png": str(png_path)})

    payload["success"] = True
    payload["output_dir"] = str(run_path)
    payload["artifacts"] = {"previews": previews}
    payload.update(_props_and_validation(props, prompt, profile, max_print_dim_mm))
    return payload


def make_radial_assembly(
    source_path: str | Path,
    count: int = 4,
    angle_step: float = 90.0,
    output_dir: str | Path = "outputs",
    boolean_fuse: bool = False,
    run_id: str | None = None,
    max_print_dim_mm: float | None = None,
) -> dict[str, Any]:
    """Rotate `count` copies of a segment artifact about the Z axis and export the assembly.

    Built for segmented-ring workflows (e.g. four 90-degree segments): the source segment
    is imported, copies are rotated by i*angle_step, and the result is exported as a
    compound (default) or fused into one solid. Validation uses the "assembly" profile,
    so exceeding the print bed is an advisory, not a failure.
    """
    source_path = Path(source_path).expanduser()
    payload: dict[str, Any] = {"success": False, **_file_info(source_path)}
    if count < 1:
        payload["error"] = f"count must be >= 1, got {count}"
        return payload

    segment = _import_or_error(source_path, payload)
    if segment is None:
        return payload

    copies = [segment.rotate(cq.Vector(0, 0, 0), cq.Vector(0, 0, 1), i * angle_step) for i in range(count)]
    if len(copies) == 1:
        assembly = copies[0]
    elif boolean_fuse:
        assembly = copies[0].fuse(*copies[1:]).clean()
    else:
        assembly = cq.Compound.makeCompound(copies)

    # Measure before exporting: STL tessellation attaches a mesh whose deflection
    # pads every later BoundingBox() (zmin drifts below 0 by the mesh tolerance).
    props = shape_properties(assembly)

    run_path = ensure_output_dir(output_dir, run_id or new_run_id("assembly"))
    base_name = f"{source_path.stem}-assembly-{count}x"
    artifacts = export_shape_files(assembly, run_path, base_name=base_name)
    payload["success"] = True
    payload["output_dir"] = str(run_path)
    payload["assembly"] = {"count": count, "angle_step": angle_step, "boolean_fuse": bool(boolean_fuse)}
    payload["artifacts"] = artifacts
    # Fixed neutral intent: source filenames should not affect geometry validation.
    payload.update(_props_and_validation(props, "radial assembly", "assembly", max_print_dim_mm))
    return payload


def split_artifact(
    source_path: str | Path,
    bed_size: float | None = None,
    output_dir: str | Path = "outputs",
    run_id: str | None = None,
    max_segments: int = 64,
) -> dict[str, Any]:
    """Split an oversize artifact into printable segments with axis-aligned planar cuts.

    The bounding box is divided into the smallest grid whose cells fit `bed_size`;
    each cell is intersected with the model, recentered with its base on Z=0, exported
    (STL/STEP/BREP + isometric preview), and validated with the "printable" profile.
    Cuts are plain planar faces — the segments are meant to be glued/joined after
    printing; add alignment features yourself if the fit matters.
    """
    source_path = Path(source_path).expanduser()
    payload: dict[str, Any] = {"success": False, **_file_info(source_path)}
    shape = _import_or_error(source_path, payload)
    if shape is None:
        return payload

    bed = DEFAULT_MAX_PRINT_DIM_MM if bed_size is None else float(bed_size)
    if bed <= 0:
        payload["error"] = f"bed_size must be > 0, got {bed}"
        return payload
    bb = shape.BoundingBox()
    counts = tuple(max(1, math.ceil(length / bed)) for length in (bb.xlen, bb.ylen, bb.zlen))
    payload["bed_size"] = bed
    payload["grid"] = {"x": counts[0], "y": counts[1], "z": counts[2]}

    if counts == (1, 1, 1):
        payload["success"] = True
        payload["split_needed"] = False
        payload["message"] = f"model fits within a {bed:g}mm bed; no split needed"
        return payload

    total_cells = counts[0] * counts[1] * counts[2]
    if total_cells > max_segments:
        payload["error"] = (
            f"split would produce {total_cells} segments (limit {max_segments}); "
            "increase bed_size or split the model manually"
        )
        return payload

    # Cell boundaries per axis; outer walls are padded so boundary geometry is kept.
    eps = 1e-3
    axes = []
    for axis_min, axis_len, count in (
        (bb.xmin, bb.xlen, counts[0]),
        (bb.ymin, bb.ylen, counts[1]),
        (bb.zmin, bb.zlen, counts[2]),
    ):
        bounds = [axis_min + i * axis_len / count for i in range(count + 1)]
        bounds[0] -= eps
        bounds[-1] += eps
        axes.append(bounds)

    run_path = ensure_output_dir(output_dir, run_id or new_run_id("split"))
    segments: list[dict[str, Any]] = []
    previews: list[dict[str, str]] = []
    all_valid = True
    for i in range(counts[0]):
        for j in range(counts[1]):
            for k in range(counts[2]):
                box = cq.Solid.makeBox(
                    axes[0][i + 1] - axes[0][i],
                    axes[1][j + 1] - axes[1][j],
                    axes[2][k + 1] - axes[2][k],
                    cq.Vector(axes[0][i], axes[1][j], axes[2][k]),
                )
                piece = shape.intersect(box)
                try:
                    volume = float(piece.Volume())
                except Exception:
                    volume = 0.0
                if volume <= 1e-9:
                    continue  # empty grid cell (e.g. corner of an L-shaped model)

                # Recenter for printing: X/Y centered on the origin, base on Z=0.
                pb = piece.BoundingBox()
                piece = piece.translate(
                    cq.Vector(-(pb.xmin + pb.xmax) / 2, -(pb.ymin + pb.ymax) / 2, -pb.zmin)
                )
                index = len(segments)
                artifacts = export_shape_files(
                    piece, run_path, base_name=f"segment-{index:02d}", views=("isometric",)
                )
                props = shape_properties(piece)
                validation = validate_prompt_shape(
                    "printable split segment", props, profile="printable", max_print_dim_mm=bed
                )
                all_valid = all_valid and validation.valid
                previews.extend(artifacts.get("previews", []))
                segments.append(
                    {
                        "index": index,
                        "cell": {"x": i, "y": j, "z": k},
                        "artifacts": artifacts,
                        "properties": properties_to_dict(props),
                        "validation": validation.to_dict(),
                    }
                )

    payload["success"] = True
    payload["split_needed"] = True
    payload["output_dir"] = str(run_path)
    payload["segments_total"] = len(segments)
    payload["all_segments_valid"] = all_valid
    payload["segments"] = segments
    payload["artifacts"] = {"previews": previews}
    payload["note"] = (
        "cuts are plain planar faces and each segment was recentered with its base on Z=0; "
        "join segments with glue or pins after printing. "
        f"The complete model is untouched at {source_path} for users who prefer to split in other software."
    )
    return payload
