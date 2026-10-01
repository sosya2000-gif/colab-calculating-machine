---
name: colab-calculating-machine
description: Run scoped computations on an existing Google Colab runtime through the official Colab CLI (upload, execute, download), keeping hashes, stage logs and a PASS/FAIL receipt. Use when an agent needs Colab as a bounded calculating machine.
---

# Colab calculating machine

Use the official Colab CLI (`google-colab-cli`, command `colab`); do not drive notebook cells by screen coordinates when CLI execution is available. This skill supplies orchestration, not a scientific kernel or a universal numeric oracle.

## Setup

Point the helper at the CLI with the `COLAB_CLI` environment variable (a path, or a bare command name found on `PATH`) or the `--cli` flag. Authentication is the user's own; never copy, display, hash, or transmit credential contents. If the CLI is absent, stop and report it rather than installing or choosing another account.

Read [references/operations.md](references/operations.md) before the first execution. Use `scripts/calculate.py` for one-command plan execution.

## Workflow

1. Establish the requested computation, recipient (Google Colab, a third-party cloud), exact uploaded code/data, session and budget. A request to use this skill is not standing authority to export unrelated private material.
2. Inspect `colab sessions`, `colab usage`, and named-session `colab status -s NAME`. Reuse an existing, explicitly named session; session names and runtimes are transient. Never allocate a new runtime, claim an orphan assignment, stop/restart a session or mount Drive without specific authority from the user.
3. Prepare a strict plan, list the exact upload files, and select a bounded timeout. CUDA work requires a real NVIDIA runtime; local compilation or CPU fallback is not CUDA validation. Probe the actual GPU/toolchain first.
4. Run the plan once. Each invocation confirms through `colab sessions` that the named session is live before any remote upload/exec, creates a fresh timestamped local directory, records stage logs and hashes, and finally checks that the same machine is observed. It never calls allocation APIs. `new_vm_allocated=false` has only this observed scope; if the machine changed or vanished the value is null, not a claim of new allocation.
   **PASS_EXECUTION is not remote success by itself.** The CLI's `exec` exits 0 even when the remote code raises or calls `sys.exit(n)` (see `docs/FINDINGS.md`). The helper therefore screens `EXEC.stderr` for failure signals and, when the plan sets the optional `success_marker`, requires that exact line in `EXEC.stdout`. For a first run with no declared download digest, always set `success_marker` and have the reviewed script print it last; the receipt carries a warning when neither a marker nor a digest backs the PASS. Timeout or failure means stop and preserve evidence, not automatic retry. A local CLI timeout does not guarantee remote process termination; inspect the existing session before any restart/retry.
5. Retrieve explicitly listed artifacts. Remote digest checks prove transport consistency, not scientific correctness. Apply the task's independent oracle, tolerances, inventory and negative controls before claiming a numeric PASS. Never execute downloaded Python/native files for validation unless explicitly reviewed and authorized.

## One-call interface

```bash
COLAB_CLI=/path/to/colab python3 <skill-directory>/scripts/calculate.py --plan /absolute/path/PLAN.json
```

The plan's `execution_approved=true` is a record of user approval, not approval by itself. Make sure the current request authorizes the specified payload and execution. Report exact artifact paths and distinguish execution PASS, transport PASS, numeric PASS and any scientific conclusion.

Do not package a whole repository, virtualenv, caches, SSH configuration, API keys or existing result trees for upload.
