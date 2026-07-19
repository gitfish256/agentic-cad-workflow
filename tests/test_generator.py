from __future__ import annotations

import os

import pytest

from agentic_cad_workflow.generator import _publish_staged_artifacts


def test_publish_staged_artifacts_copies_into_destination_directory(tmp_path):
    run_path = tmp_path / "run"
    staging_dir = run_path / ".export-private"
    run_path.mkdir()
    staging_dir.mkdir()
    source = staging_dir / "model.stl"
    source.write_bytes(b"same STL bytes")

    result = _publish_staged_artifacts(
        {"stl": str(source), "nested": [{"path": str(source)}]},
        staging_dir,
        run_path,
    )

    destination = run_path / "model.stl"
    assert source.exists(), "the staging inode must not be renamed into the public output"
    assert destination.read_bytes() == source.read_bytes()
    assert not os.path.samefile(source, destination)
    assert result == {
        "stl": str(destination),
        "nested": [{"path": str(destination)}],
    }
    assert not list(run_path.glob(".*.part"))


def test_publish_staged_artifacts_rolls_back_partial_copies(tmp_path, monkeypatch):
    run_path = tmp_path / "run"
    staging_dir = run_path / ".export-private"
    run_path.mkdir()
    staging_dir.mkdir()
    first = staging_dir / "a.stl"
    second = staging_dir / "b.step"
    first.write_bytes(b"first")
    second.write_bytes(b"second")

    calls = 0

    def fail_on_second_copy(source_file, target_file, length):
        nonlocal calls
        calls += 1
        if calls == 2:
            target_file.write(b"partial")
            raise OSError("simulated copy failure")
        target_file.write(source_file.read())

    monkeypatch.setattr("agentic_cad_workflow.generator.shutil.copyfileobj", fail_on_second_copy)

    with pytest.raises(OSError, match="simulated copy failure"):
        _publish_staged_artifacts(
            {"stl": str(first), "step": str(second)},
            staging_dir,
            run_path,
        )

    assert not (run_path / "a.stl").exists()
    assert not (run_path / "b.step").exists()
    assert not list(run_path.glob(".*.part"))
