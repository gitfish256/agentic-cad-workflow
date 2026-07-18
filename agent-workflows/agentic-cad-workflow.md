---
name: agentic-cad-workflow
description: Agent-neutral workflow for turning physical-object prompts into inspected, rendered, and heuristically validated STL/STEP/BREP artifacts with CadQuery.
interfaces:
  mcp_server: agentic-cad-mcp
  cli: agentic-cad-build
  python_package: agentic_cad_workflow
recommended_output_dir: outputs
---

# Agentic CAD Workflow - portable agent guide

## Purpose and ownership

Use this workflow to design, generate, inspect, render, validate, refine, split,
or assemble a physical 3D-printable model.

The host agent owns all language-model work: interpret the user's prompt, write
CadQuery code, compare the result with the prompt, and revise the code. The MCP
server and CLI are deterministic CAD runtimes and never call an LLM. This makes
the same workflow usable by Codex, Claude Code, Hermes, and other agents.

This project is powered by CadQuery and is not affiliated with, sponsored by, or
endorsed by the CadQuery project.

## CadQuery code contract

Agent-authored code must:

- use millimeters and `import cadquery as cq`;
- assign the final model to `result` and call
  `show_object(result, name="descriptive_name")`;
- produce one final Shape, Workplane, Assembly, or Compound, with intended
  booleans completed before handoff;
- place a printable model on or above Z=0, normally with its base at Z=0;
- avoid network access, subprocess creation, and writes outside the workflow
  output directory.

Prefer simple, robust geometry over fragile decorative details. Generated Python
runs with the current user's filesystem and network permissions despite worker
process isolation and a timeout.

## Interfaces

### MCP server

```bash
agentic-cad-mcp --output-dir outputs --bed-size 300
```

Ask for the user's actual printer-bed size when it is unknown. A per-call
`bed_size` overrides the server default. It is currently one conservative limit
applied to X, Y, and Z, not separate build-plate and build-height dimensions.

| Tool | Use |
| --- | --- |
| `execute_cadquery` | Primary build tool: execute agent-authored code, export artifacts, measure, validate, and preview. |
| `inspect_cadquery` | Execute code and return geometry properties/validation without exporting artifacts. |
| `render_preview` | Execute code and export preview artifacts. |
| `inspect_artifact` | Inspect a saved STEP/STP/BREP/BIN file. |
| `render_artifact` | Render a saved STEP/STP/BREP/BIN file. |
| `make_radial_assembly` | Rotate segment copies into a full compound or fused assembly. |
| `split_artifact` | Split an oversize exact artifact with axis-aligned planar cuts. |

### CLI fallback for agents without MCP

Write the CadQuery code to a file, then run:

```bash
agentic-cad-build model.py --prompt "original physical-object prompt" --output-dir outputs --bed-size 220 --validation-profile printable --style shaded
```

Pass `-` instead of `model.py` to read code from stdin. Exit status is 0 for a
valid build, 1 for execution/export failure, and 2 when artifacts were produced
but geometry validation failed.

## Standard prompt-to-model loop

1. **Clarify physical intent.** Record units, target dimensions, print-bed size,
   orientation, required features, tolerances, load expectations, small-part
   hazards, and whether the deliverable is printable now or a full reference.
2. **Author the code.** Write CadQuery satisfying the prompt and the code contract.
3. **Build once.** Call `execute_cadquery` with the code, original prompt,
   `bed_size`, correct `validation_profile`, and `style: "shaded"`. This one call
   executes, exports STL/STEP/BREP/SVG/PNG, measures, and validates.
4. **Judge evidence.** Check actual bounding box, volume, center, topology, BREP
   validity, validation checks, and shaded previews against the prompt. Do not
   repeat `inspect_artifact` immediately: the build response already contains
   measurements and validation.
5. **Refine in the host agent.** When dimensions, checks, or visuals are wrong,
   modify the code and call `execute_cadquery` again. Each call creates a new run
   directory, so confirm the final paths belong to the revised build.
6. **Handoff.** Report STL and STEP/BREP paths, measured dimensions, validation
   profile/result, and residual manufacturing or safety risks.

Use artifact inspection/rendering for saved models, cross-session review, or
post-processing. Use `inspect_cadquery` when exports are not wanted.

## Validation profiles and limits

- `printable`: BREP validity, at least one solid, positive volume, overall
  bounding-box fit, and base at Z=0 are fatal checks.
- `reference`: full-size master geometry; oversize and base placement become
  advisories while validity, solidity, and positive volume remain fatal.
- `assembly`: for multi-part compounds, with reference-like size/base treatment.

Validate printable segments with `printable`; validate a full master or compound
with `reference` or `assembly`.

Every report states `scope: geometry_heuristic`. It does not prove prompt
conformance or automatically measure local wall thickness, holes and clearances,
overhangs, supports, material behavior, strength, or STL mesh manifoldness.
Compare measurements and shaded previews with the prompt and inspect the final
STL in a slicer before manufacturing.

The worker timeout reduces the impact of direct crashes and loops but is not an
OS sandbox or a memory, disk, network, or complete subprocess-tree limit. Treat
generated code and imported STEP/BREP/BIN artifacts as trusted local input.

## Oversize models

Do not silently scale a model down. Preserve and report the complete STL plus
STEP/BREP, validate the master with `reference`, and explain that it exceeds the
bed. Splitting in a slicer or CAD tool is often easiest.

Only if the user asks the workflow to split it, call `split_artifact` on STEP or
BREP with the real `bed_size`. It preserves the complete model, recenters each
segment on Z=0, exports segment artifacts, and validates each with `printable`.
Cuts are planar glue/pin faces; no alignment features are added automatically.

## Artifact and result rules

- STL is for slicing/printing.
- STEP/BREP/BIN is the source for measurement, re-rendering, assembly, and split.
- CadQuery does not re-import STL in this workflow; inspect its STEP/BREP sibling.
- Keep generated files under ignored `outputs/` or another task-specific ignored
  directory.
- MCP build results default to `verbosity: "normal"`, avoiding repeated code.
  Use `summary` for compact status or `full`/`include_code: true` for debugging.

## Minimal MCP build recipe

```json
{
  "tool": "execute_cadquery",
  "arguments": {
    "code": "import cadquery as cq\nresult = cq.Workplane('XY').circle(60).circle(45).extrude(12)\nshow_object(result, name='ring_brim')",
    "prompt": "ring brim, 120mm OD, 90mm ID, 12mm tall",
    "bed_size": 220,
    "validation_profile": "printable",
    "style": "shaded",
    "verbosity": "normal"
  }
}
```

## Completion checklist

- [ ] Intent includes dimensions, units, printer bed, and validation profile.
- [ ] Agent-authored code follows the contract and execution succeeded.
- [ ] Actual dimensions and positive volume match the intent.
- [ ] BREP is valid and the validation result/profile is stated.
- [ ] Shaded previews were checked against required features.
- [ ] Final STL and STEP/BREP paths belong to the latest revision.
- [ ] Oversize, material, slicer, load, and safety caveats are explicit.
