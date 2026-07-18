# Agent instructions for this repository

This repository packages an agentic CAD workflow: a stdio MCP server, CLI, and
loadable agent workflow for prompt-driven CadQuery model generation, inspection,
preview rendering, validation, and refinement.

If you are an AI coding/design agent working in this repo:

1. **Load the portable workflow first.** Before generating, validating, or changing
   CAD workflow behavior, read `agent-workflows/agentic-cad-workflow.md`. It is the
   agent-agnostic operating guide; the Hermes skill is only one adapter.
2. **Own the design loop.** Interpret the user's prompt and author/revise the
   CadQuery code yourself. Use `execute_cadquery` for the build-measure-preview-
   validate loop; the runtime does not call an LLM or refine code for you.
3. **Do not commit generated artifacts.** `outputs/`, `.hermes-runs/`, virtualenvs,
   caches, and build outputs are ignored. Use them for local smoke tests only.
4. **Measure before claiming printability.** A model that renders is not enough:
   inspect dimensions, positive volume, solidity, validation profile, and previews.
5. **Use STEP/BREP for inspection.** STL is a print/export mesh and is not
   re-imported by CadQuery in this workflow.
6. **Keep public docs path-neutral.** Avoid machine-local paths, private Hermes
   session notes, or generated model history in committed documentation.
7. **Verify changes.** For code changes, use the editable environment when it
   exists: `.\.venv\Scripts\python.exe -m pytest -q` on Windows or
   `./.venv/bin/python -m pytest -q` on macOS/Linux. Otherwise install with the
   matching README quick start first.
