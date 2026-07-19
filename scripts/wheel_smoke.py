"""Cross-platform smoke test for an already-built agentic-cad-workflow wheel.

The script deliberately creates a fresh virtual environment outside the source
tree, installs exactly one wheel from ``--dist-dir``, and then exercises both
public build paths:

* ``agentic-cad-build`` performs a real CadQuery build and exports artifacts.
* An official MCP ``ClientSession`` starts the installed stdio server and calls
  ``execute_cadquery``.

It also verifies that the canonical portable workflow is present in the wheel
and is byte-for-byte identical to the repository source.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tarfile
import tempfile
import textwrap
import venv
import zipfile
from pathlib import Path

RING_CODE = """import cadquery as cq
result = cq.Workplane("XY").circle(60).circle(45).extrude(12)
show_object(result, name="wheel_smoke_ring")
"""

MCP_SMOKE = r'''
import asyncio
import json
from importlib.metadata import version
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agentic_cad_workflow import __version__

RING_CODE = """import cadquery as cq
result = cq.Workplane("XY").circle(60).circle(45).extrude(12)
show_object(result, name="mcp_wheel_smoke_ring")
"""


async def main():
    output_dir = Path(sys.argv[1]).resolve()
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "agentic_cad_workflow", "--output-dir", str(output_dir)],
    )
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            initialized = await session.initialize()
            assert initialized.instructions
            assert initialized.serverInfo.version == __version__
            assert initialized.serverInfo.version == version("agentic-cad-workflow")
            tools = await session.list_tools()
            assert {tool.name for tool in tools.tools} == {
                "execute_cadquery",
                "inspect_cadquery",
                "render_preview",
                "inspect_artifact",
                "render_artifact",
                "make_radial_assembly",
                "split_artifact",
            }
            response = await session.call_tool(
                "execute_cadquery",
                {
                    "code": RING_CODE,
                    "prompt": "120mm OD, 90mm ID, 12mm high printable ring",
                    "bed_size": 220,
                    "validation_profile": "printable",
                    "style": "shaded",
                    "include_images": False,
                },
            )
            assert not response.isError
            payload = json.loads(response.content[0].text)
            assert payload["success"], payload.get("error")
            assert payload["validation"]["valid"], payload["validation"]
            bbox = payload["properties"][0]["bounding_box"]
            assert abs(bbox["xlen"] - 120.0) < 0.01
            assert abs(bbox["ylen"] - 120.0) < 0.01
            assert abs(bbox["zlen"] - 12.0) < 0.01
            for key in ("stl", "step", "brep"):
                assert Path(payload["artifacts"][key]).is_file()
            assert any(Path(item["png"]).is_file() for item in payload["artifacts"]["previews"])
            print(json.dumps({"mcp": "ok", "output_dir": payload["output_dir"]}))


asyncio.run(main())
'''


def _run(command: list[str], *, cwd: Path, timeout: float = 600) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        rendered = subprocess.list2cmdline(command)
        raise RuntimeError(
            f"Command failed with exit code {completed.returncode}: {rendered}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return completed


def _venv_python(environment: Path) -> Path:
    if os.name == "nt":
        return environment / "Scripts" / "python.exe"
    return environment / "bin" / "python"


def _venv_script(environment: Path, name: str) -> Path:
    if os.name == "nt":
        return environment / "Scripts" / f"{name}.exe"
    return environment / "bin" / name


def _find_wheel(dist_dir: Path) -> Path:
    wheels = sorted(dist_dir.resolve().glob("agentic_cad_workflow-*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"Expected exactly one agentic-cad-workflow wheel in {dist_dir}, found: {wheels}")
    return wheels[0]


def _find_sdist(dist_dir: Path) -> Path:
    sdists = sorted(dist_dir.resolve().glob("agentic_cad_workflow-*.tar.gz"))
    if len(sdists) != 1:
        raise RuntimeError(f"Expected exactly one agentic-cad-workflow sdist in {dist_dir}, found: {sdists}")
    return sdists[0]


def _check_packaged_workflow(wheel: Path, repository_root: Path) -> None:
    member = "agentic_cad_workflow/bundled_workflows/agentic-cad-workflow.md"
    canonical = repository_root / "agent-workflows" / "agentic-cad-workflow.md"
    with zipfile.ZipFile(wheel) as archive:
        try:
            packaged = archive.read(member)
        except KeyError as exc:
            raise RuntimeError(f"Wheel does not contain canonical workflow resource: {member}") from exc
    if packaged != canonical.read_bytes():
        raise RuntimeError("Packaged portable workflow differs from the canonical repository file")


def _check_sdist_policy(sdist: Path) -> None:
    with tarfile.open(sdist) as archive:
        names = archive.getnames()

    required_suffixes = {
        "/agent-workflows/agentic-cad-workflow.md",
        "/CHANGELOG.md",
        "/CONTRIBUTING.md",
        "/scripts/wheel_smoke.py",
    }
    for suffix in required_suffixes:
        if not any(name.endswith(suffix) for name in names):
            raise RuntimeError(f"Sdist is missing required public source: {suffix.removeprefix('/')}")

    banned_directories = {"moved", "outputs", ".agents", ".claude", ".codex", ".venv"}
    leaked: list[str] = []
    for name in names:
        parts = Path(name).parts
        lowered = [part.lower() for part in parts]
        if banned_directories.intersection(lowered):
            leaked.append(name)
        elif "worklog.md" in lowered:
            leaked.append(name)
        elif any(part == ".env" or part.startswith(".env.") for part in lowered):
            leaked.append(name)
    if leaked:
        raise RuntimeError(f"Sdist contains private/local files: {leaked}")


def _check_cli_payload(payload: dict) -> None:
    if not payload.get("success"):
        raise RuntimeError(f"CLI build failed: {payload.get('error')}")
    if not payload["validation"]["valid"]:
        raise RuntimeError(f"CLI validation failed: {payload['validation']}")
    bbox = payload["properties"][0]["bounding_box"]
    expected = {"xlen": 120.0, "ylen": 120.0, "zlen": 12.0, "zmin": 0.0}
    for key, value in expected.items():
        if abs(bbox[key] - value) >= 0.01:
            raise RuntimeError(f"Unexpected CLI bounding box {key}: {bbox[key]} (expected {value})")
    for key in ("stl", "step", "brep"):
        if not Path(payload["artifacts"][key]).is_file():
            raise RuntimeError(f"CLI did not export {key}: {payload['artifacts'].get(key)}")
    if not any(Path(item["png"]).is_file() for item in payload["artifacts"]["previews"]):
        raise RuntimeError("CLI did not export a shaded PNG preview")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist-dir", type=Path, required=True)
    args = parser.parse_args(argv)

    repository_root = Path(__file__).resolve().parents[1]
    wheel = _find_wheel(args.dist_dir)
    sdist = _find_sdist(args.dist_dir)
    _check_packaged_workflow(wheel, repository_root)
    _check_sdist_policy(sdist)

    with tempfile.TemporaryDirectory(prefix="agentic-cad-wheel-smoke-") as temporary:
        root = Path(temporary).resolve()
        environment = root / "venv"
        venv.EnvBuilder(with_pip=True, clear=True).create(environment)
        python = _venv_python(environment)

        _run([str(python), "-m", "pip", "install", "--upgrade", "pip"], cwd=root)
        _run([str(python), "-m", "pip", "install", str(wheel)], cwd=root)
        _run([str(python), "-m", "pip", "check"], cwd=root)

        model = root / "ring.py"
        model.write_text(RING_CODE, encoding="utf-8")
        cli_output = root / "cli-outputs"
        cli = _venv_script(environment, "agentic-cad-build")
        built = _run(
            [
                str(cli),
                str(model),
                "--prompt",
                "120mm OD, 90mm ID, 12mm high printable ring",
                "--output-dir",
                str(cli_output),
                "--bed-size",
                "220",
                "--validation-profile",
                "printable",
                "--style",
                "shaded",
                "--execution-timeout",
                "60",
            ],
            cwd=root,
        )
        _check_cli_payload(json.loads(built.stdout))

        printed_workflow = _run([str(cli), "--print-workflow"], cwd=root)
        canonical_workflow = (repository_root / "agent-workflows" / "agentic-cad-workflow.md").read_text(
            encoding="utf-8"
        )
        if printed_workflow.stdout != canonical_workflow:
            raise RuntimeError("Installed CLI workflow output differs from the canonical repository file")

        helper = root / "mcp_smoke.py"
        helper.write_text(textwrap.dedent(MCP_SMOKE), encoding="utf-8")
        _run([str(python), str(helper), str(root / "mcp-outputs")], cwd=root)

        for entry_point in ("agentic-cad-mcp", "agentic-cad-install-hermes-skill"):
            _run([str(_venv_script(environment, entry_point)), "--help"], cwd=root)

    print(f"Clean-wheel CLI and MCP smoke tests passed: {wheel.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
