"""Disposable subprocess entry point for MCP-supplied CadQuery code."""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import sys

from cadquery import exporters

from .runner import _execute_cadquery_in_process


MAX_DIAGNOSTIC_CHARS = 64 * 1024


class _BoundedDiagnosticBuffer(io.TextIOBase):
    """Accept generated-code output without allowing unbounded Python buffers."""

    def __init__(self, limit: int = MAX_DIAGNOSTIC_CHARS) -> None:
        self.limit = limit
        self.value = ""

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        original_length = len(text)
        remaining = self.limit - len(self.value)
        if remaining > 0:
            self.value += text[:remaining]
        return original_length

    def flush(self) -> None:
        return None


def _bounded_error(error: str | None) -> str | None:
    if error is None or len(error) <= MAX_DIAGNOSTIC_CHARS:
        return error
    return error[:MAX_DIAGNOSTIC_CHARS] + "\n... worker diagnostic truncated ..."


def main() -> int:
    try:
        request = json.loads(sys.stdin.read())
        code = request["code"]
        output_dir = Path(request["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)

        # Generated scripts sometimes print large amounts of debugging text.
        # Keep stdout reserved for the protocol and cap Python-level stdout/
        # stderr so an accidental print loop does not grow the parent buffer.
        diagnostics = _BoundedDiagnosticBuffer()
        with contextlib.redirect_stdout(diagnostics), contextlib.redirect_stderr(diagnostics):
            execution = _execute_cadquery_in_process(code)

        payload: dict[str, object] = {
            "success": execution.success,
            "error": _bounded_error(execution.error),
            "build_time": execution.build_time,
            "brep_files": [],
        }
        if execution.success:
            brep_files: list[str] = []
            for index, shape in enumerate(execution.shapes):
                name = f"shape-{index:03d}.brep"
                exporters.export(shape, str(output_dir / name), exportType="BREP")
                brep_files.append(name)
            payload["brep_files"] = brep_files

        sys.stdout.write(json.dumps(payload, ensure_ascii=False))
        return 0
    except Exception as exc:
        sys.stdout.write(
            json.dumps(
                {"success": False, "error": f"{type(exc).__name__}: {exc}", "brep_files": []},
                ensure_ascii=False,
            )
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
