import io
import json
from pathlib import Path

import pytest

from agentic_cad_workflow.cli import main
from agentic_cad_workflow.workflow import read_portable_workflow

BOX_CODE = """import cadquery as cq
result = cq.Workplane("XY").box(20, 20, 10).translate((0, 0, 5))
show_object(result, name="box")
"""


def _payload(capsys):
    captured = capsys.readouterr()
    return json.loads(captured.out), captured.err


def test_cli_prints_portable_workflow_without_source(capsys):
    exit_code = main(["--print-workflow"])

    captured = capsys.readouterr()
    assert exit_code == 0, captured.err
    assert captured.out == read_portable_workflow()
    assert "# Agentic CAD Workflow - portable agent guide" in captured.out


def test_cli_requires_source_without_print_workflow():
    with pytest.raises(SystemExit) as exc_info:
        main([])

    assert exc_info.value.code == 2


def test_cli_builds_file_from_path_with_spaces(tmp_path, capsys):
    source_dir = tmp_path / "agent code with spaces"
    source_dir.mkdir()
    source = source_dir / "model source.py"
    source.write_text(BOX_CODE, encoding="utf-8")
    output_dir = tmp_path / "build outputs with spaces"

    exit_code = main(
        [
            str(source),
            "--prompt",
            "20mm by 20mm printable box",
            "--output-dir",
            str(output_dir),
            "--style",
            "shaded",
        ]
    )

    payload, stderr = _payload(capsys)
    assert exit_code == 0, stderr
    assert payload["success"]
    assert payload["validation"]["valid"]
    for key in ("stl", "step", "brep"):
        assert Path(payload["artifacts"][key]).is_file()


def test_cli_accepts_utf8_bom_and_chinese_paths_and_prompt(tmp_path, capsys):
    source_dir = tmp_path / "中文模型目录"
    source_dir.mkdir()
    source = source_dir / "带签名的模型.py"
    source.write_text("\ufeff" + BOX_CODE, encoding="utf-8")
    output_dir = tmp_path / "中文输出目录"
    prompt = "一个二十毫米见方的可打印测试件"

    exit_code = main(
        [
            str(source),
            "--prompt",
            prompt,
            "--output-dir",
            str(output_dir),
        ]
    )

    captured = capsys.readouterr()
    # stdout is deliberately ASCII-safe so redirected output also works with
    # legacy Windows code pages; json.loads restores the original Unicode.
    captured.out.encode("ascii")
    payload = json.loads(captured.out)
    assert exit_code == 0, captured.err
    assert payload["success"]
    assert payload["prompt"] == prompt
    assert Path(payload["artifacts"]["step"]).is_file()


def test_cli_reads_code_from_stdin(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(BOX_CODE))

    exit_code = main(["-", "--output-dir", str(tmp_path)])

    payload, stderr = _payload(capsys)
    assert exit_code == 0, stderr
    assert payload["success"]
    assert payload["properties"][0]["bounding_box"]["xlen"] == pytest.approx(20)


@pytest.mark.parametrize(
    "code",
    [
        "this is not valid Python !!!",
        "raise RuntimeError('intentional test failure')",
    ],
)
def test_cli_returns_one_for_execution_failure(code, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(code))

    exit_code = main(["-", "--output-dir", str(tmp_path)])

    payload, stderr = _payload(capsys)
    assert exit_code == 1
    assert not payload["success"]
    assert "build failed" in stderr
    assert "stl" not in payload["artifacts"]


def test_cli_terminates_code_after_timeout(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("while True:\n    pass\n"))

    exit_code = main(
        [
            "-",
            "--output-dir",
            str(tmp_path),
            "--execution-timeout",
            "0.5",
        ]
    )

    payload, stderr = _payload(capsys)
    assert exit_code == 1
    assert not payload["success"]
    assert "timeout" in payload["error"]
    assert "terminated" in payload["error"]
    assert "build failed" in stderr


def test_cli_returns_two_when_geometry_validation_fails(tmp_path, monkeypatch, capsys):
    floating = (
        "import cadquery as cq\n"
        "result = cq.Workplane('XY').box(20, 20, 10).translate((0, 0, 15))\n"
        "show_object(result)\n"
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(floating))

    exit_code = main(["-", "--output-dir", str(tmp_path)])

    payload, stderr = _payload(capsys)
    assert exit_code == 2
    assert payload["success"]
    assert not payload["validation"]["valid"]
    assert Path(payload["artifacts"]["stl"]).is_file()
    assert "validation failed" in stderr


def test_cli_returns_one_for_missing_source(tmp_path, capsys):
    exit_code = main([str(tmp_path / "missing.py"), "--output-dir", str(tmp_path)])

    payload, stderr = _payload(capsys)
    assert exit_code == 1
    assert not payload["success"]
    assert "Could not read" in payload["error"]
    assert payload["error"] in stderr


def test_cli_returns_json_when_output_directory_is_a_file(tmp_path, capsys):
    source = tmp_path / "model.py"
    source.write_text(BOX_CODE, encoding="utf-8")
    invalid_output_dir = tmp_path / "already-a-file"
    invalid_output_dir.write_text("not a directory", encoding="utf-8")

    exit_code = main([str(source), "--output-dir", str(invalid_output_dir)])

    payload, stderr = _payload(capsys)
    assert exit_code == 1
    assert not payload["success"]
    assert "Could not build CadQuery model" in payload["error"]
    assert "already-a-file" in payload["error"]
    assert payload["error"] in stderr
