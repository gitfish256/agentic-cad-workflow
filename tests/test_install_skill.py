from pathlib import Path

from agentic_cad_workflow.install_skill import main


def test_installer_emits_cross_platform_yaml_paths(tmp_path, capsys):
    destination = tmp_path / "hermes skill"
    assert main([str(destination)]) == 0

    installed = destination / "SKILL.md"
    assert installed.is_file()
    content = installed.read_text(encoding="utf-8")
    assert "host agent" in content.lower()
    assert "sampling/createMessage" not in content

    output = capsys.readouterr().out
    yaml_lines = [line for line in output.splitlines() if "command:" in line or "args:" in line]
    assert yaml_lines
    assert all("\\" not in line for line in yaml_lines)
    assert Path(destination, "SKILL.md") == installed
