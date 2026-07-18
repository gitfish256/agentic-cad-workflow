---
name: agentic-cad-workflow
description: >
  Use the agentic-cad-workflow MCP tools to turn a physical-object prompt into
  measured, rendered, validated STL/STEP/BREP artifacts. Use for printable parts,
  fixtures, rings, enclosures, brackets, assemblies, inspection, and splitting.
---

# Agentic CAD Workflow

The host agent owns the design loop: interpret the user's prompt, author CadQuery
code, evaluate the measured geometry and previews, then revise the code when
needed. The `agentic_cad` MCP server is a deterministic CAD runtime; it does not
call an LLM or generate/refine code on the agent's behalf.

## Agent-authored code contract

- Work in millimeters and `import cadquery as cq`.
- Assign the final model to `result` and call
  `show_object(result, name="descriptive_name")`.
- Produce one final Shape, Workplane, Assembly, or Compound. Finish booleans before
  handoff instead of returning unrelated shapes.
- Put printable geometry on or above Z=0, normally with its base at Z=0.
- Do not access the network, start subprocesses, or write outside the workflow's
  output directory. Execution is process-isolated with a timeout, not OS-sandboxed.

## Core loop

1. Clarify dimensions, units, print-bed size, orientation, required features,
   loads, clearances, and whether the result is printable now or a reference.
2. Write robust CadQuery code satisfying those constraints.
3. Call `execute_cadquery` once with the code, original prompt, `bed_size`, the
   correct `validation_profile`, and `style: "shaded"`. This one call executes,
   exports STL/STEP/BREP/SVG/PNG, measures geometry, and validates it.
4. Compare returned dimensions, volume, topology, BREP validity, validation, and
   shaded previews with the prompt. A successful build alone is not conformance.
5. If anything is wrong, revise the code yourself and call `execute_cadquery`
   again. Never relabel a failing or visually incorrect model as done.
6. Report the final STL and STEP/BREP paths, measured dimensions, validation
   profile/result, and residual manufacturing risks.

Use `inspect_cadquery` when you need measurements without exporting. Use
`inspect_artifact` or `render_artifact` for an already saved STEP/BREP/BIN file.
STL is for slicing and is not re-imported by this workflow.

## Validation profiles

- `printable`: BREP validity, solid/positive volume, overall bounding-box bed fit,
  and base at Z=0 are fatal checks.
- `reference`: full-size master geometry; oversize and base placement are
  advisories while validity, solidity, and volume remain fatal.
- `assembly`: multi-part compound semantics, with the same relaxed bed checks as
  `reference`.

`bed_size` is one conservative maximum applied to X, Y, and Z; it does not model
separate build-plate axes and build height.

Reports use `scope: geometry_heuristic`. They do not prove prompt conformance or
measure local walls, holes/clearances, overhangs, supports, materials, strength,
or STL manifoldness. Confirm the returned measurements and previews, then review
the STL in a slicer before manufacturing.

The execution timeout reduces the impact of direct worker crashes/loops but is
not a sandbox or a complete memory, disk, network, or subprocess-tree limit.
Treat generated Python and imported STEP/BREP/BIN artifacts as trusted input.

## Oversize models

Always preserve the complete STL plus STEP/BREP and validate the master with
`reference`. Do not silently scale it down. Only call `split_artifact` when the
user asks the workflow to split it; each segment is recentered on Z=0 and checked
with `printable`. Cuts are planar and have no automatic alignment features.

Use `make_radial_assembly` to rebuild a complete reference from rotated segment
copies. Validate the printable segment with `printable` and the full result with
`assembly`.

## Efficient results

MCP build tools default to `verbosity: "normal"`, which omits the code the agent
already owns. Use `summary` for compact status or `full`/`include_code: true` only
when debugging. Request only the preview views needed to resolve uncertainty.

## Hermes MCP config

Add a local stdio server entry to `~/.hermes/config.yaml` and adjust paths:

```yaml
mcp_servers:
  agentic_cad:
    command: "/path/to/agentic-cad-workflow/.venv/bin/agentic-cad-mcp"
    args: ["--output-dir", "/path/to/agentic-cad-workflow/outputs"]
    timeout: 240
    connect_timeout: 60
```

Generated artifacts belong under `<output-dir>/<run-id>/` and should not be
committed unless a maintainer deliberately curates an example.
