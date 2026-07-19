# Changelog

All notable user-visible changes to this project are documented here. The
format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

No user-visible changes have been assigned to the next release yet.

## 0.5.0 - 2026-07-18

### Added

- Agent-neutral stdio MCP server, deterministic CLI, portable workflow, and
  thin adapters for Codex, Claude Code, Hermes, and other agents.
- CadQuery execution in a disposable worker with timeout, bounded diagnostics,
  measurement, heuristic validation, STL/STEP/BREP export, and shaded previews.
- Existing-artifact inspection/rendering, radial assembly, and planar splitting.
- Cross-platform test and clean-wheel matrices for Windows, macOS, and Linux.
- An installed CLI entry point for printing the canonical portable workflow.

### Changed

- CAD work is serialized on a background executor so MCP lifecycle and
  discovery requests remain responsive during builds.
- Supported Python range expanded through Python 3.14 where upstream CadQuery
  and OpenCASCADE wheels are available.

### Fixed

- Windows artifacts now inherit the selected output directory's permissions,
  allowing desktop slicers such as Bambu Studio to read exported files.
- Multiple final model results fail explicitly instead of silently discarding
  geometry; multi-object Workplanes preserve all intended solids.
- CLI output remains parseable with Unicode prompts, BOM-marked source, paths
  containing spaces, worker failures, timeouts, and validation failures.
