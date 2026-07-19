"""Access to the canonical portable workflow in wheels and source checkouts."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

WORKFLOW_NAME = "agentic-cad-workflow.md"


def read_portable_workflow() -> str:
    """Read the agent-neutral workflow from an installed wheel or checkout.

    Hatch force-includes the canonical repository document as a package
    resource in built wheels. Editable installs use the source package directly,
    where that generated package-data path does not exist, so they fall back to
    the repository's canonical ``agent-workflows`` copy.
    """
    packaged = resources.files("agentic_cad_workflow").joinpath("bundled_workflows", WORKFLOW_NAME)
    try:
        return packaged.read_text(encoding="utf-8")
    except FileNotFoundError as packaged_error:
        checkout = Path(__file__).resolve().parents[2] / "agent-workflows" / WORKFLOW_NAME
        try:
            return checkout.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise FileNotFoundError(
                "portable workflow resource is missing from both the installed package "
                f"and source checkout ({checkout})"
            ) from packaged_error
