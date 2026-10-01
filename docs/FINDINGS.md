# Measured behavior of the official Colab CLI (google-colab-cli 0.7.4)

Observed 2026-10-01 against a live Colab CPU runtime. These are observations of one version; re-check after upgrades.

| Observation | Consequence for this helper |
|---|---|
| `colab exec` exits **0** when the remote code raises an exception | The helper must screen the captured stderr; the exit code alone would record a failed job as PASS. |
| `colab exec` exits **0** when the remote code calls `sys.exit(3)` (stderr shows `An exception has occurred, use %tb ...` and `SystemExit: 3`) | Same. |
| `colab status -s NAME` for a session that does not exist exits **0** and prints `Session 'NAME' not found.` | Session existence cannot be read from the exit code; the helper parses `colab sessions` output instead. |
| `colab sessions` prints one line per session: `[NAME] MACHINE-ID \| Hardware: CPU \| Shape: Standard \| Variant: DEFAULT` | Parsed for the live check and the same-machine check. |
| `colab new` without `--gpu`/`--tpu` creates a CPU runtime | Useful for cheap end-to-end testing; the helper itself never calls `new`. |
| `colab download` and `colab upload` returned exit 0 on success and wrote the file where requested | Digest is still verified locally against the plan. |

Representative stderr of a remote exception (colors stripped): a `Traceback (most recent call last)` block ending in `RuntimeError: ...`. Of a `sys.exit`: the IPython banner followed by `SystemExit: N`.

## What was verified live
A plan with one upload, one download and a `success_marker` passed (downloaded file hash matched the pre-computed digest); a raising script, a `sys.exit(3)` script, a missing marker, a wrong download digest and a non-existent session each stopped with `FAIL_STOP` at the expected stage, without retry.

## What was not verified
Pointing `COLAB_CLI` directly at the `colab` binary (testing used a thin wrapper script that forwards its arguments to the official CLI); GPU/TPU runtimes; what the CLI does when `exec`/`upload`/`download` name a session that does not exist (the helper's pre-check prevents that call).
