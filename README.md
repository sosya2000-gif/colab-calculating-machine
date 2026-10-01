# colab-calculating-machine

An agent skill and small Python helper that runs **one explicitly approved computation on an existing Google Colab session** through the official Colab CLI, then records what happened: upload -> execute -> download, with frozen copies of the script and uploads, per-stage logs, SHA-256 hashes and a PASS/FAIL receipt.

It is deliberately conservative: it never creates, stops or restarts a runtime, never retries, and treats Colab as a third-party cloud.

The Colab backend is specific; the execution-contract pattern is general.

> Not affiliated with or endorsed by Google. Use of Colab is subject to Google's terms. You are responsible for what you upload.

## Why the "PASS" is not just an exit code

The official CLI's `exec` exits `0` even when the remote code raises or calls `sys.exit(n)` (measured with `google-colab-cli` 0.7.4, details in [docs/FINDINGS.md](docs/FINDINGS.md)). A wrapper that trusts the exit code records failed jobs as successes. This helper therefore

- screens the captured stderr for failure signals (traceback, IPython exception banner, `XxxError:` / `SystemExit:` lines);
- optionally requires a `success_marker` line in stdout that your script prints only after its own checks;
- verifies every downloaded file against a SHA-256 you declared in the plan;
- confirms through `colab sessions` that the named session is live *before* any remote call, and that the same machine is still observed afterwards.

Even then, `PASS_EXECUTION` means *the job ran and its declared checks held* - not that a numerical result is correct. The receipt says `numeric_validation: NOT_EVALUATED`; validate results with your own independent oracle.

## Requirements

- Python 3.9+ (standard library only)
- The official Colab CLI (`pip install google-colab-cli`, command `colab`), authenticated as you, and an **existing, named** Colab session (`colab new -s NAME` creates a CPU runtime; the helper itself never does)
- Set `COLAB_CLI` to the CLI (a path or a command name on `PATH`), or pass `--cli`

## Quick start

```bash
export COLAB_CLI="$(command -v colab)"
colab sessions                       # confirm your session is listed
python3 scripts/calculate.py --plan /absolute/path/PLAN.json
```

Minimal plan (see [references/operations.md](references/operations.md) for every field):

```json
{
  "schema": "colab-calculation-plan-v1",
  "purpose": "smoke",
  "session": "my-existing-session",
  "execution_approved": true,
  "script": "/abs/path/job.py",
  "timeout_seconds": 120,
  "output_parent": "/abs/path/results",
  "success_marker": "JOB-OK-1234",
  "uploads": [],
  "downloads": []
}
```

Results land in `<output_parent>/<YYYYMMDD-HHMMSS>__<purpose>/` (UTC; set `COLAB_CALC_TZ=Europe/Berlin` etc. to change). Open `RUN_RECEIPT.json` first.

## Safety properties (and their limits)

| Property | Enforced by | Limit |
|---|---|---|
| Strict plan: exact keys, no duplicate JSON keys, bounded sizes/timeouts, remote paths only under `/content/` | `validate()` | `execution_approved` is a flag you set; it is not authorization |
| No symlinks (including parent directories) | `regular()` | |
| Credential-like local paths refused (`id_*`, `.ssh`, `.aws`, `.env*`, `*.pem`, ...) | name screen | Names only - it cannot see file contents |
| Named session must be live; same machine afterwards | `sessions` pre/post check | Cannot prove a new VM was never allocated; `new_vm_allocated` is `false` only for the same observed machine, otherwise `null` |
| One run, no retry; evidence kept on failure | receipt `FAIL_STOP`, `retry_authorized: false` | A local timeout does not prove the remote process stopped |

## Using it as an agent skill

`SKILL.md` and `agents/openai.yaml` follow the Codex-style skill layout (`SKILL.md` with front matter, `references/`, `scripts/`). Copy or symlink this folder into your agent's skills directory.

## Tests

```bash
cd scripts && python3 -B -m unittest -v test_calculate
```

The suite is offline: it drives the real code against a fake CLI script and uses captured real stderr from live failures as fixtures. It was also checked with mutation testing (deliberately injected defects; the only survivor is an equivalent mutant).

## License

MIT - see [LICENSE](LICENSE).
