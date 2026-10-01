# Plan and operations

Use absolute local paths. Remote paths are exact destinations, not glob patterns. The script is executed through the CLI's `exec -f`, which submits Python source; any CUDA compilation belongs inside that reviewed script with its own checked subprocess, bounded timeout, build flags and fixture validation.

```json
{
  "schema": "colab-calculation-plan-v1",
  "purpose": "cuda-fixture",
  "session": "explicit-existing-session-name",
  "execution_approved": true,
  "script": "/absolute/path/reviewed_job.py",
  "timeout_seconds": 240,
  "output_parent": "/absolute/path/results",
  "success_marker": "JOB-OK-<something-unique>",
  "uploads": [
    {"local": "/absolute/path/kernel.cu", "remote": "/content/job/kernel.cu"}
  ],
  "downloads": [
    {"remote": "/content/job/REPORT.json", "name": "REPORT.json", "sha256": "64-lowercase-hex-digest"}
  ]
}
```

`success_marker` is optional (8..200 printable ASCII characters): a line the reviewed script prints only after its own checks succeed; the run fails with stage `EXEC` if that exact line is absent from `EXEC.stdout`. Failure signals in `EXEC.stderr` (tracebacks, the IPython exception banner, `XxxError:`/`SystemExit:` lines) also fail the run, because the CLI exits 0 on remote exceptions.

Each run creates `<output_parent>/<YYYYMMDD-HHMMSS>__<purpose>/` (mode 0700, never overwritten). The timestamp is UTC unless `COLAB_CALC_TZ` names an IANA zone, for example `Europe/Berlin`. It holds `PLAN.json`, `EXECUTED_SCRIPT.py` (the exact bytes run), `UPLOAD-NNN` snapshots, per-stage `*.START.json`/`*.stdout`/`*.stderr`, downloaded files, and `RUN_RECEIPT.json`.

Local paths are refused when any component is a symlink or the path looks like a credential (`id_*`, `.ssh`, `.aws`, `.env*`, `.config`, `*.pem`/`*.key`, names containing credential/secret/token/password). This is a name screen only; it cannot see contents. On macOS `/tmp` is a symlink: use `/private/tmp`.

Receipt v2 records `session_machine_before/after`, `session_hardware`, `same_machine_observed` and `new_vm_allocated`. The last field is false for the same observed machine, null if it changed or vanished; observations cannot establish full allocation history. A run fails at `PRECHECK` if the named session is not live and at `POSTCHECK` if it changed or vanished.

Downloads may be empty for an initial run whose remote output/digest is only known after execution. Retrieve those artifacts in a separately authorized follow-up using the CLI's `download`; never rerun merely to retrieve. For fully one-call jobs use a predeclared output path and a digest known from a deterministic fixture, or have the reviewed job handle its own structured artifact protocol. This generic helper does not invent expected hashes for new results.

Before uploads, make sure the exact remote parent exists through a separately reviewed provisioning script (the helper does not create remote directories). Never reuse result output paths; remote job scripts must implement their own exclusive reservation/atomic publish. The local run directory is not a remote job lock.

Read-only CLI commands:
```bash
"$COLAB_CLI" sessions
"$COLAB_CLI" usage
"$COLAB_CLI" status -s SESSION
```

Authentication remains user-interactive if expired; do not paste authorization codes into logs. Runtime names, GPU type, balances and availability are transient. This helper includes no shell/SSH exposure, paid upgrade or new-VM allocation.

For independent datasets use process parallelism only after deterministic sequential parity and resource limits are established. CUDA work should avoid uncontrolled same-device concurrency. Do not fall back to CPU automatically while reporting GPU success.
