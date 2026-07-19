# Contributing

Thanks for helping improve `agentic-cad-workflow`.

## Before changing CAD workflow behavior

Read `agent-workflows/agentic-cad-workflow.md`. The host agent owns prompt
interpretation and CadQuery authoring; the runtime remains deterministic and
does not call an LLM.

## Development setup

Use Python 3.10-3.14. Linux contributors must install `libgl1` before installing
CadQuery/OCP. Create a virtual environment, then install an editable checkout:

```bash
python -m pip install -e ".[dev]"
```

On Windows, run the same command through the environment's Python executable:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

## Required checks

Run the test suite and packaging checks appropriate to your platform:

```bash
python -m ruff check .
python -m ruff format --check .
python -m pytest -q
python -m compileall -q src tests
python -m pip check
python -m build
python -m twine check dist/*
```

Changes to runtime behavior should include focused tests. Cross-platform changes
should avoid shell-specific assumptions and machine-local paths.

## Repository hygiene

Do not commit generated models, prompts, run metadata, virtual environments,
worklogs, caches, build products, credentials, or machine-local Agent/MCP
configuration. Generated runs belong under ignored `outputs/` directories unless
a maintainer deliberately curates a privacy-reviewed example.

Use STEP/BREP for exact inspection and report measured geometry before claiming
printability. STL is a slicing/export artifact and is not re-imported by this
workflow.

## Security reports

Do not open a public issue for a vulnerability or include secrets or personal
data in an issue. Follow the private reporting instructions in `SECURITY.md`.
