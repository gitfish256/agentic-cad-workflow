# Upstream reading notes

## CadQuery/cadquery

Reviewed CadQuery upstream materials while designing this workflow.

Relevant facts used in this implementation:

- CadQuery is intended for headless Python-scripted parametric CAD; the README explicitly calls out server/script integration as a good use case.
- CadQuery can export high-quality CAD/print formats: STL, STEP, DXF, AMF, 3MF, VRML, etc.
- `cadquery.cq.Workplane.toSvg()` delegates to `cadquery.occ_impl.exporters.svg.getSVG`.
- `cadquery.cq.Workplane.exportSvg(fileName)` delegates to `exportSVG`.
- `cadquery.occ_impl.exporters.export(shape, fname, exportType=..., tolerance=..., angularTolerance=..., opt=...)` supports `STL`, `STEP`, `SVG`, `BREP` and other formats.
- SVG rendering is headless and view-dependent via `projectionDir`, so this project exports `isometric`, `front`, `top`, and `right` SVG/PNG previews.

## CadQuery/cadquery-contrib MCP server

Reviewed the upstream CadQuery contrib MCP server implementation as a reference.

Useful reference patterns:

- Uses the Python MCP SDK with `mcp.server.Server`, `stdio_server`, `Tool`, `TextContent`, and `ImageContent`.
- Exposes tools for rendering, inspecting, extracting parameters, and exporting CadQuery code.
- Executes scripts through `cadquery.cqgi.parse(code).build()` and extracts `show_object` results or a `result` variable.
- Renders SVG using `cadquery.occ_impl.exporters.svg.getSVG` with named projection directions.

This project keeps the same MCP stdio style, but adds headless matplotlib PNG
previews, geometry-heuristic validation reports, print-oriented artifacts, and a
portable host-agent revision loop. Natural-language reasoning remains in the
host agent rather than inside the CAD server.

## bertvanbrakel/mcp-cadquery

Also reviewed `bertvanbrakel/mcp-cadquery` as a richer reference.

Useful reference patterns:

- Separates handlers, core export functions, shape properties, part-library indexing, and subprocess runner logic.
- Uses BREP intermediate files to persist results between tool calls.
- Provides `execute_cadquery_script`, `export_shape`, `export_shape_to_svg`, `get_shape_properties`, and related tools.

This project intentionally starts smaller: one self-contained local stdio server
optimized for a host agent to execute, preview, validate, and iteratively revise
CadQuery models.
