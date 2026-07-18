# Claude Code instructions for agentic-cad-workflow

@agent-workflows/agentic-cad-workflow.md

This repository packages an agentic CAD workflow: a stdio MCP server, CLI, and
portable agent workflow for prompt-driven CadQuery model generation, inspection,
preview rendering, validation, and refinement.

When working in this repository:

1. Treat `agent-workflows/agentic-cad-workflow.md` as the canonical portable
   workflow. The Hermes skill is an adapter, not the only source of truth.
2. Interpret the user's prompt and write/revise the CadQuery code yourself. Use
   `execute_cadquery` to execute, export, measure, validate, and preview it. The
   runtime does not use MCP sampling or refine code on your behalf.
3. Do not commit generated artifacts or local scratch material: `outputs/`,
   `.hermes-runs/`, virtualenvs, caches, and build outputs are ignored.
4. Measure before claiming printability. A rendered preview is not enough; inspect
   dimensions, positive volume, solidity/topology, validation profile, and previews.
5. Prefer STEP/BREP/BIN for inspection and reloading. STL is a slicer/print output
   and is not re-imported by this workflow.
6. Keep public docs path-neutral. Do not add machine-local paths, private session
   notes, generated model history, or credentials to committed files.
7. For code changes, run `.\.venv\Scripts\python.exe -m pytest -q` on Windows or
   `./.venv/bin/python -m pytest -q` on macOS/Linux when the editable dev
   environment exists. If it does not exist, follow the matching README quick
   start first.
8. To actually build or inspect models from Claude Code, the `agentic_cad` MCP
   server must be registered. See the README section "Claude Code setup and use"
   for the exact local stdio registration commands.
