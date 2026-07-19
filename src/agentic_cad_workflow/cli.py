from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from .generator import build_model_from_code
from .runner import DEFAULT_EXECUTION_TIMEOUT_SECONDS, PREVIEW_STYLES
from .validation import VALIDATION_PROFILES
from .workflow import read_portable_workflow


def _read_code(source: str) -> str:
    if source == "-":
        return sys.stdin.read().removeprefix("\ufeff")
    # utf-8-sig accepts both normal UTF-8 and the BOM emitted by some Windows
    # editors without passing U+FEFF to Python's parser.
    return Path(source).expanduser().read_text(encoding="utf-8-sig")


def _json_stdout(payload: dict[str, object]) -> None:
    # ASCII-safe JSON remains parseable when Windows redirects stdout through a
    # legacy code page. Unicode values are preserved as standard JSON escapes.
    print(json.dumps(payload, ensure_ascii=True, indent=2))


def _stderr(message: str) -> None:
    encoding = getattr(sys.stderr, "encoding", None) or "utf-8"
    safe = message.encode(encoding, errors="backslashreplace").decode(encoding)
    print(safe, file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agentic-cad-build",
        description=(
            "Build, measure, validate, preview, and export agent-authored CadQuery code. "
            "Pass '-' to read the code from stdin."
        ),
    )
    parser.add_argument("source", nargs="?", help="CadQuery Python file, or '-' for stdin")
    parser.add_argument(
        "--print-workflow",
        action="store_true",
        help="Print the bundled agent-neutral workflow as UTF-8 and exit",
    )
    parser.add_argument(
        "--prompt",
        default="agent-authored CadQuery model",
        help="Original physical intent used when assessing the generated geometry",
    )
    parser.add_argument("--output-dir", default="outputs", help="Artifact output directory")
    parser.add_argument(
        "--bed-size",
        type=float,
        default=None,
        help="Uniform maximum X/Y/Z bounding-box dimension in mm (default 300)",
    )
    parser.add_argument(
        "--validation-profile",
        choices=VALIDATION_PROFILES,
        default="printable",
    )
    parser.add_argument("--style", choices=PREVIEW_STYLES, default="shaded")
    parser.add_argument(
        "--execution-timeout",
        type=float,
        default=DEFAULT_EXECUTION_TIMEOUT_SECONDS,
        help="Maximum CadQuery execution time in seconds (default: %(default)g)",
    )
    args = parser.parse_args(argv)

    if args.print_workflow:
        if args.source is not None:
            parser.error("source cannot be used with --print-workflow")
        try:
            workflow = read_portable_workflow()
        except Exception as exc:
            _stderr(f"Could not read portable workflow: {type(exc).__name__}: {exc}")
            return 1
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass
        sys.stdout.write(workflow)
        if not workflow.endswith("\n"):
            sys.stdout.write("\n")
        return 0

    if args.source is None:
        parser.error("source is required unless --print-workflow is used")
    if not math.isfinite(args.execution_timeout) or args.execution_timeout <= 0:
        parser.error("--execution-timeout must be a finite number > 0")
    if args.bed_size is not None and (not math.isfinite(args.bed_size) or args.bed_size <= 0):
        parser.error("--bed-size must be a finite number > 0")

    try:
        code = _read_code(args.source)
    except Exception as exc:
        payload = {
            "success": False,
            "error": f"Could not read CadQuery source: {type(exc).__name__}: {exc}",
        }
        _json_stdout(payload)
        _stderr(payload["error"])
        return 1

    try:
        result = build_model_from_code(
            code,
            prompt=args.prompt,
            output_dir=args.output_dir,
            profile=args.validation_profile,
            max_print_dim_mm=args.bed_size,
            style=args.style,
            execution_timeout=args.execution_timeout,
        )
    except Exception as exc:
        result = {
            "success": False,
            "error": f"Could not build CadQuery model: {type(exc).__name__}: {exc}",
        }
        _json_stdout(result)
        _stderr(result["error"])
        return 1

    _json_stdout(result)
    if not result.get("success"):
        _stderr(f"build failed: {result.get('error', 'unknown error')}")
        return 1
    if not result.get("validation", {}).get("valid", False):
        _stderr("build completed, but geometry validation failed")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
