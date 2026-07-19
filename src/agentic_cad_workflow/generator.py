from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

from .runner import (
    DEFAULT_EXECUTION_TIMEOUT_SECONDS,
    CadExecution,
    ensure_output_dir,
    execute_cadquery,
    export_preview_files,
    export_shape_files,
    new_run_id,
    properties_to_dict,
    write_run_files,
)
from .validation import validate_prompt_shape


def _result_payload(
    prompt: str,
    code: str,
    execution: CadExecution,
    output_dir: Path,
    artifacts: dict[str, Any] | None = None,
    validation: Any | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "success": execution.success,
        "prompt": prompt,
        "code": code,
        "code_source": "agent-provided code",
        "output_dir": str(output_dir),
        "build_time": execution.build_time,
        "error": execution.error,
        "artifacts": artifacts or {},
        "properties": [properties_to_dict(item) for item in execution.properties],
    }
    if validation is not None:
        data["validation"] = validation.to_dict()
    return data


def _publish_staged_artifacts(artifacts: dict[str, Any], staging_dir: Path, run_path: Path) -> dict[str, Any]:
    """Publish a complete export set and rewrite paths to the run directory.

    Exporters write into a private staging directory first.  If rendering or a
    solid export fails, TemporaryDirectory removes the incomplete set and the
    run directory contains only diagnostics, not misleading partial artifacts.

    Files are copied through run-local ``.part`` files instead of being moved
    directly out of the private staging directory.  On Windows, a rename keeps
    the source file's restrictive temporary-directory ACL; creating the copy in
    ``run_path`` makes it inherit the caller-selected output directory ACL so
    desktop slicers and other user applications can read it.
    """
    staged_files = sorted(path for path in staging_dir.iterdir() if path.is_file())
    destinations = {path: run_path / path.name for path in staged_files}
    conflicts = [destination for destination in destinations.values() if destination.exists()]
    if conflicts:
        raise FileExistsError(f"artifact destination already exists: {conflicts[0]}")

    publish_temps: dict[Path, Path] = {}
    published: list[Path] = []
    try:
        for source, destination in destinations.items():
            publish_temp = run_path / f".{destination.name}.{uuid4().hex}.part"
            publish_temps[destination] = publish_temp
            with source.open("rb") as source_file, publish_temp.open("xb") as target_file:
                shutil.copyfileobj(source_file, target_file, length=1024 * 1024)

        for destination, publish_temp in publish_temps.items():
            publish_temp.replace(destination)
            published.append(destination)
    except Exception:
        for publish_temp in publish_temps.values():
            publish_temp.unlink(missing_ok=True)
        for destination in published:
            destination.unlink(missing_ok=True)
        raise

    def rewrite(value: Any) -> Any:
        if isinstance(value, str):
            path = Path(value)
            try:
                relative = path.relative_to(staging_dir)
            except ValueError:
                return value
            return str(run_path / relative)
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        if isinstance(value, dict):
            return {key: rewrite(item) for key, item in value.items()}
        return value

    return rewrite(artifacts)


def _execute_validate_and_export(
    code: str,
    prompt: str,
    output_dir: str | Path,
    run_id: str | None,
    profile: str,
    max_print_dim_mm: float | None,
    style: str,
    execution_timeout: float,
    *,
    previews_only: bool,
) -> dict[str, Any]:
    run_path = ensure_output_dir(output_dir, run_id or new_run_id("preview" if previews_only else "build"))
    execution = execute_cadquery(code, timeout_seconds=execution_timeout)

    # Multiple show_object(...) results are ambiguous.  Exporting the first one
    # would silently discard geometry, while automatically combining unrelated
    # debug objects could also create the wrong printable model.  A Workplane
    # containing multiple final solids is normalized to one Compound by runner.
    if execution.success and len(execution.shapes) != 1:
        execution.success = False
        execution.error = (
            f"CadQuery code produced {len(execution.shapes)} separate final results. "
            "Assign and show one final Shape, Workplane, Assembly, or Compound."
        )

    validation = (
        validate_prompt_shape(
            prompt,
            execution.properties[0],
            profile=profile,
            max_print_dim_mm=max_print_dim_mm,
        )
        if execution.success
        else None
    )
    artifacts: dict[str, Any] = {}
    if execution.success:
        try:
            exporter = export_preview_files if previews_only else export_shape_files
            with tempfile.TemporaryDirectory(prefix=".export-", dir=run_path) as staging:
                staging_dir = Path(staging)
                staged_artifacts = exporter(
                    execution.shapes[0],
                    staging_dir,
                    base_name="model",
                    style=style,
                )
                artifacts = _publish_staged_artifacts(staged_artifacts, staging_dir, run_path)
        except Exception as exc:
            execution.success = False
            execution.error = f"Artifact export failed: {type(exc).__name__}: {exc}"

    payload = _result_payload(
        prompt,
        code,
        execution,
        run_path,
        artifacts,
        validation,
    )
    payload.setdefault("artifacts", {}).update(write_run_files(run_path, prompt, code, payload))
    return payload


def build_model_from_code(
    code: str,
    prompt: str = "agent-authored CadQuery model",
    output_dir: str | Path = "outputs",
    run_id: str | None = None,
    profile: str = "printable",
    max_print_dim_mm: float | None = None,
    style: str = "outline",
    execution_timeout: float = DEFAULT_EXECUTION_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Execute agent-authored CadQuery code, validate it, and export artifacts.

    Natural-language interpretation and code refinement belong to the host agent.
    This function is intentionally deterministic and never calls an LLM.
    """
    return _execute_validate_and_export(
        code,
        prompt,
        output_dir,
        run_id,
        profile,
        max_print_dim_mm,
        style,
        execution_timeout,
        previews_only=False,
    )


def render_preview_from_code(
    code: str,
    prompt: str = "agent-authored CadQuery model preview",
    output_dir: str | Path = "outputs",
    run_id: str | None = None,
    profile: str = "printable",
    max_print_dim_mm: float | None = None,
    style: str = "outline",
    execution_timeout: float = DEFAULT_EXECUTION_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Execute and validate agent-authored code, exporting previews only."""
    return _execute_validate_and_export(
        code,
        prompt,
        output_dir,
        run_id,
        profile,
        max_print_dim_mm,
        style,
        execution_timeout,
        previews_only=True,
    )


def result_to_json_text(result: dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False, indent=2)
