# Security policy

## Supported versions

Security fixes are made on the latest `0.x` release line. This project is still
beta software and has not received an independent security audit.

## Trust model

`agentic-cad-workflow` is a **local stdio MCP server**. It is not designed to be
exposed as an unauthenticated network service or shared between mutually
untrusted users.

The workflow executes CadQuery Python authored by the host agent and supplied
directly to an execution tool. MCP executions run in a
disposable subprocess with a configurable timeout and a reduced environment.
This reduces the risk that a direct worker crash or loop takes down the MCP
server and avoids forwarding most parent-process environment variables.

The subprocess is **not an operating-system security sandbox**. Generated code
still runs with the filesystem and network permissions of the current user. Run
the server only for agents, prompts, and model providers you trust. For stronger
isolation, launch the MCP server inside a dedicated container, VM, or restricted
OS account and mount only the intended output directory.

The timeout is not a memory, CPU, disk, network, or complete process-tree quota;
child processes or native-library failures may outlive or bypass the direct
worker controls. Python-level diagnostic output is capped, but this is not a
general resource limit. STEP/BREP/BIN imports and artifact operations also parse
files locally and are not isolated from the MCP process, so only open artifacts
from trusted sources.

Generated physical designs also require independent dimensional, material,
slicer, and safety review before manufacturing.

## Run-data privacy

Each build persists the original prompt, submitted CadQuery source, and full
metadata under its run directory as `prompt.txt`, `model.py`, and
`metadata.json`. Metadata includes resolved absolute output and artifact paths,
which may reveal local usernames or directory layouts. Failed builds also retain
these diagnostic files.

The default repository-local `outputs/` directory is ignored by git, but this is
not an access-control boundary and does not protect a custom output directory.
Treat run directories as potentially sensitive: do not include credentials in
prompts or code, inspect files before sharing them, and delete or relocate private
runs when they are no longer needed.

## Reporting a vulnerability

If this repository is public and GitHub private vulnerability reporting is
enabled, use [**Report a vulnerability**](https://github.com/gitfish256/agentic-cad-workflow/security/advisories/new)
on the repository's Security tab. Maintainers of a public repository may instead
open a draft repository security advisory. While the repository is private, or
if neither public-repository option is available, contact the maintainer through
an existing private channel. As a last resort, open an issue asking for a private
contact method, but do not include vulnerability details in that issue.

Please include affected versions, reproduction steps, expected impact, and any
suggested mitigation. Do not include secrets or personal data in a report.
