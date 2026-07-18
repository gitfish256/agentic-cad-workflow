"""Install the bundled Hermes skill for the agentic CAD workflow.

Copies the packaged ``SKILL.md`` into a Hermes skills directory so that a Hermes
agent can discover and use the agentic-cad-workflow tools. Uses only the standard
library (``argparse``, ``importlib.resources``, ``shutil``) so it works from an
installed wheel or an editable checkout.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from importlib import resources
from pathlib import Path

# Location of the bundled skill inside the package. The skill lives in a directory
# whose name ("agentic-cad-workflow") is not a valid Python identifier, so we
# traverse from the importable package root rather than importing it as a package.
_ANCHOR_PACKAGE = "agentic_cad_workflow"
_SKILL_SUBPATH = ("bundled_skills", "agentic-cad-workflow", "SKILL.md")
_SKILL_FILENAME = "SKILL.md"

# Default install target: ~/.hermes/skills/creative/agentic-cad-workflow/SKILL.md
_DEFAULT_DEST = (
    Path.home() / ".hermes" / "skills" / "creative" / "agentic-cad-workflow" / _SKILL_FILENAME
)


def _bundled_skill_resource():
    """Return an importlib.resources traversable pointing at the bundled SKILL.md."""
    return resources.files(_ANCHOR_PACKAGE).joinpath(*_SKILL_SUBPATH)


def _read_bundled_skill() -> str:
    """Return the text of the bundled SKILL.md from package resources."""
    return _bundled_skill_resource().read_text(encoding="utf-8")


def _resolve_dest(destination: str | None) -> Path:
    """Resolve the destination path, allowing a directory or a full file path."""
    if destination is None:
        return _DEFAULT_DEST
    dest = Path(destination).expanduser()
    # If the user pointed at a directory (existing dir or a path without .md suffix),
    # write SKILL.md inside it; otherwise treat it as the target file.
    if dest.is_dir() or dest.suffix.lower() != ".md":
        return dest / _SKILL_FILENAME
    return dest


def install_skill(destination: str | None = None, force: bool = False) -> Path:
    """Copy the bundled SKILL.md to ``destination``.

    Creates parent directories. Refuses to overwrite an existing file whose content
    differs unless ``force`` is set. Returns the path written (or left in place).
    Raises ``FileExistsError`` when a differing file exists and ``force`` is False.
    """
    dest = _resolve_dest(destination)
    content = _read_bundled_skill()

    if dest.exists():
        existing = dest.read_text(encoding="utf-8")
        if existing == content:
            return dest  # Already up to date; nothing to do.
        if not force:
            raise FileExistsError(
                f"{dest} already exists with different content. Re-run with --force to overwrite."
            )

    dest.parent.mkdir(parents=True, exist_ok=True)
    with resources.as_file(_bundled_skill_resource()) as src:
        shutil.copyfile(src, dest)
    return dest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agentic-cad-install-hermes-skill",
        description=(
            "Install the bundled agentic-cad-workflow Hermes skill (SKILL.md). "
            "Powered by CadQuery; not affiliated with or endorsed by the CadQuery project."
        ),
    )
    parser.add_argument(
        "destination",
        nargs="?",
        default=None,
        help=(
            "Optional destination directory or SKILL.md file path. "
            f"Default: {_DEFAULT_DEST}"
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing SKILL.md even if its content differs.",
    )
    args = parser.parse_args(argv)

    try:
        dest = install_skill(args.destination, force=args.force)
    except FileExistsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"Installed agentic-cad-workflow Hermes skill to:\n  {dest}\n")
    print("Next steps:")
    print("  1. Ensure the MCP server is installed and on PATH:")
    if sys.platform == "win32":
        print(r'       .\.venv\Scripts\python.exe -m pip install -e ".[dev]"')
    else:
        print("       uv pip install -e '.[dev]'   # or your install method")
    print("  2. Register the MCP server in ~/.hermes/config.yaml:")
    scripts_dir = Path(sys.executable).parent
    executable_name = "agentic-cad-mcp.exe" if sys.platform == "win32" else "agentic-cad-mcp"
    server_command = scripts_dir / executable_name
    output_dir = Path.cwd() / "outputs"
    print("       mcp_servers:")
    print("         agentic_cad:")
    # Forward slashes keep the emitted YAML valid on Windows; backslashes inside
    # YAML double quotes would be interpreted as escape sequences.
    print(f'           command: "{server_command.as_posix()}"')
    print(f'           args: ["--output-dir", "{output_dir.as_posix()}"]')
    print("  3. Restart Hermes so it discovers the new skill and MCP server.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
