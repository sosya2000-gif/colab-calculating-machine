import copy
import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
import calculate

# Real stderr captured from the official CLI `exec` on a live Colab CPU runtime (google-colab-cli 0.7.4): exit code was 0 in both cases.
LIVE_STDERR_RAISE = (b'\x1b[0;31m---------------------------------------------------------------------------\x1b[0m\x1b[0;31mRuntimeError\x1b[0m'
                     b'                              Traceback (most recent call last)\x1b[0;32m/tmp/ipykernel_2329/2958612679.py\x1b[0m in \x1b[0;36m<cell line: 0>\x1b[0;34m()\x1b[0m\n'
                     b'\x1b[0;32m----> 3\x1b[0;31m \x1b[0;32mraise\x1b[0m \x1b[0mRuntimeError\x1b[0m\n\x1b[0m\x1b[0;31mRuntimeError\x1b[0m: deliberate synthetic failure\n')
LIVE_STDERR_EXIT3 = (b'An exception has occurred, use %tb to see the full traceback.\n\x1b[0;31mSystemExit\x1b[0m\x1b[0;31m:\x1b[0m 3\n\n'
                     b'/usr/local/lib/python3.13/dist-packages/IPython/core/interactiveshell.py:3561: UserWarning: To exit: use \'exit\', \'quit\', or Ctrl-D.\n')
PAYLOAD = b'synthetic-result-123'
PAYLOAD_SHA = hashlib.sha256(PAYLOAD).hexdigest()

FAKE_CLI = r'''#!/bin/bash
echo "$@" >> "$FAKE_LOG"
case "$1" in
  sessions)
    echo "[colab] Pruned 0 stale local session(s)."
    echo "[decoy-session] m-s-decoy | Hardware: T4 | Shape: Standard | Variant: DEFAULT"
    n=$(grep -c '^sessions' "$FAKE_LOG")
    case "$FAKE_SESSIONS" in
      absent) echo "[colab] No active sessions found on server." ;;
      swap) if [ "$n" -ge 2 ]; then m=m-s-other; else m=m-s-one; fi; echo "[existing] $m | Hardware: CPU | Shape: Standard | Variant: DEFAULT" ;;
      vanish) if [ "$n" -lt 2 ]; then echo "[existing] m-s-one | Hardware: CPU | Shape: Standard | Variant: DEFAULT"; fi ;;
      *) echo "[existing] m-s-one | Hardware: CPU | Shape: Standard | Variant: DEFAULT" ;;
    esac ;;
  upload) exit 0 ;;
  exec)
    case "$FAKE_EXEC" in
      fail) echo boom >&2; exit 2 ;;
      raise) cat "$FAKE_STDERR_FILE" >&2; echo "before crash"; exit 0 ;;
      marker) echo "RESULT-OK-MARKER-1"; exit 0 ;;
      nomarker) echo "something else"; exit 0 ;;
      *) echo "ran"; exit 0 ;;
    esac ;;
  download)
    last="${@: -1}"
    if [ "$FAKE_DOWNLOAD" = bad ]; then printf 'wrong' > "$last"; else printf '%s' "$FAKE_PAYLOAD" > "$last"; fi ;;
  *) exit 9 ;;
esac
'''


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.script = self.base / 'job.py'; self.script.write_text('print(1)')
        self.upload = self.base / 'input.txt'; self.upload.write_text('public synthetic input\n')
        self.cli = self.base / 'fake_colab.sh'; self.cli.write_text(FAKE_CLI); self.cli.chmod(0o755)
        self.log = self.base / 'cli.log'
        for key, value in (('FAKE_LOG', str(self.log)), ('FAKE_SESSIONS', 'present'), ('FAKE_EXEC', 'ok'),
                           ('FAKE_DOWNLOAD', 'ok'), ('FAKE_PAYLOAD', PAYLOAD.decode())):
            self.set_env(key, value)
        self.plan = {'schema': calculate.PLAN_SCHEMA, 'purpose': 'test', 'session': 'existing',
                     'execution_approved': True, 'script': str(self.script), 'timeout_seconds': 30,
                     'output_parent': str(self.base / 'results'), 'uploads': [], 'downloads': []}

    def set_env(self, key, value):
        patcher = patch.dict(os.environ, {key: value}); patcher.start(); self.addCleanup(patcher.stop)

    def verbs(self):
        return [line.split()[0] for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def receipt(self, root):
        return json.loads((root / 'RUN_RECEIPT.json').read_text())

    def failed_run(self, plan=None, exc=Exception):
        with self.assertRaises(exc):
            calculate.run(copy.deepcopy(plan or self.plan), cli=self.cli)
        runs = sorted((self.base / 'results').glob('*__*'))
        return runs[-1], self.receipt(runs[-1])

    def with_artifacts(self):
        self.plan['uploads'] = [{'local': str(self.upload), 'remote': '/content/job/input.txt'}]
        self.plan['downloads'] = [{'remote': '/content/job/REPORT.json', 'name': 'REPORT.json', 'sha256': PAYLOAD_SHA}]


class PlanRules(Base):
    def test_valid(self): calculate.validate(self.plan)
    def test_no_approval(self):
        self.plan['execution_approved'] = False
        with self.assertRaises(ValueError): calculate.validate(self.plan)
    def test_injection(self):
        self.plan['session'] = 'x; echo bad'
        with self.assertRaises(ValueError): calculate.validate(self.plan)
    def test_unknown_field(self):
        self.plan['allocate'] = True
        with self.assertRaises(ValueError): calculate.validate(self.plan)
    def test_timeout_type(self):
        self.plan['timeout_seconds'] = True
        with self.assertRaises(ValueError): calculate.validate(self.plan)
    def test_timeout_cap(self):
        for bad in (0, 3601):
            self.plan['timeout_seconds'] = bad
            with self.assertRaises(ValueError): calculate.validate(self.plan)
        self.plan['timeout_seconds'] = 3600; calculate.validate(self.plan)
    def test_symlink(self):
        link = self.base / 'link'; link.symlink_to(self.script); self.plan['script'] = str(link)
        with self.assertRaises(ValueError): calculate.validate(self.plan)
    def test_symlinked_parent_directory(self):
        real = self.base / 'real'; real.mkdir(); (real / 'file.txt').write_text('x')
        (self.base / 'linkdir').symlink_to(real)
        self.plan['uploads'] = [{'local': str(self.base / 'linkdir' / 'file.txt'), 'remote': '/content/f'}]
        with self.assertRaises(ValueError): calculate.validate(self.plan)
    def test_secret_like_names_refused(self):
        for name in ('id_ed25519', 'server.pem', '.env', 'api_token.txt', 'my_credentials.json'):
            path = self.base / name; path.write_text('synthetic placeholder')
            self.plan['uploads'] = [{'local': str(path), 'remote': '/content/f'}]
            with self.assertRaises(ValueError, msg=name): calculate.validate(self.plan)
        hidden = self.base / '.ssh'; hidden.mkdir(); (hidden / 'known').write_text('x')
        self.plan['uploads'] = [{'local': str(hidden / 'known'), 'remote': '/content/f'}]
        with self.assertRaises(ValueError): calculate.validate(self.plan)
    def test_secret_like_script_refused(self):
        bad = self.base / 'secret_job.py'; bad.write_text('print(1)'); self.plan['script'] = str(bad)
        with self.assertRaises(ValueError): calculate.validate(self.plan)
    def test_duplicate_json(self):
        p = self.base / 'plan'; p.write_text('{"a":1,"a":2}')
        with self.assertRaises(ValueError): calculate.strict_load(p)
    def test_traversal(self):
        self.plan['uploads'] = [{'local': str(self.upload), 'remote': '/content/../bad'}]
        with self.assertRaises(ValueError): calculate.validate(self.plan)
    def test_remote_must_be_under_content(self):
        self.plan['uploads'] = [{'local': str(self.upload), 'remote': '/tmp/x'}]
        with self.assertRaises(ValueError): calculate.validate(self.plan)
        self.plan['uploads'] = []
        self.plan['downloads'] = [{'remote': '/etc/passwd', 'name': 'p', 'sha256': PAYLOAD_SHA}]
        with self.assertRaises(ValueError): calculate.validate(self.plan)
    def test_upload_size_cap(self):
        big = self.base / 'big.bin'
        with big.open('wb') as f: f.truncate(65 * 1024 * 1024)
        self.plan['uploads'] = [{'local': str(big), 'remote': '/content/big'}]
        with self.assertRaises(ValueError): calculate.validate(self.plan)
    def test_reserved_download_names(self):
        for name in ('PLAN.json', 'RUN_RECEIPT.json', 'EXECUTED_SCRIPT.py', 'EXEC.stdout', 'UPLOAD-000', 'DOWNLOAD-000.stderr', 'PRECHECK.stdout', 'POSTCHECK.stdout'):
            self.plan['downloads'] = [{'remote': '/content/a', 'name': name, 'sha256': PAYLOAD_SHA}]
            with self.assertRaises(ValueError, msg=name): calculate.validate(self.plan)
    def test_duplicate_download_names(self):
        self.plan['downloads'] = [{'remote': '/content/a', 'name': 'x.json', 'sha256': PAYLOAD_SHA},
                                  {'remote': '/content/b', 'name': 'x.json', 'sha256': PAYLOAD_SHA}]
        with self.assertRaises(ValueError): calculate.validate(self.plan)
    def test_success_marker_shape(self):
        self.plan['success_marker'] = 'short'
        with self.assertRaises(ValueError): calculate.validate(self.plan)
        self.plan['success_marker'] = 'RESULT-OK-MARKER-1'; calculate.validate(self.plan)


class LiveBehaviour(Base):
    def test_happy_path_order_args_and_receipt(self):
        self.with_artifacts()
        root = calculate.run(copy.deepcopy(self.plan), cli=self.cli)
        self.assertEqual(self.verbs(), ['sessions', 'upload', 'exec', 'download', 'sessions'])
        lines = self.log.read_text().splitlines()
        self.assertTrue(lines[1].startswith('upload -s existing ') and lines[1].endswith(' /content/job/input.txt'))
        self.assertTrue(lines[2].startswith('exec -s existing -f ') and '--timeout 30' in lines[2])
        self.assertTrue(lines[3].startswith('download -s existing /content/job/REPORT.json '))
        r = self.receipt(root)
        self.assertEqual(r['status'], 'PASS_EXECUTION')
        self.assertIs(r['new_vm_allocated'], False)
        self.assertEqual((r['session_machine_before'], r['session_machine_after'], r['session_hardware']), ('m-s-one', 'm-s-one', 'CPU'))
        self.assertEqual((r['numeric_validation'], r['scientific_claim_effect']), ('NOT_EVALUATED', 'NONE'))
        self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
        self.assertEqual((root / 'EXECUTED_SCRIPT.py').read_bytes(), self.script.read_bytes())
        self.assertEqual((root / 'UPLOAD-000').read_bytes(), self.upload.read_bytes())
        self.assertEqual(r['warnings'], [])

    def test_no_download_no_marker_carries_warning(self):
        r = self.receipt(calculate.run(copy.deepcopy(self.plan), cli=self.cli))
        self.assertEqual(len(r['warnings']), 1)

    def test_absent_session_stops_before_any_mutation(self):
        self.set_env('FAKE_SESSIONS', 'absent'); self.with_artifacts()
        root, r = self.failed_run(exc=ValueError)
        self.assertEqual(self.verbs(), ['sessions'])
        self.assertEqual(r['failed_stage'], 'PRECHECK')

    def test_machine_change_is_a_failure(self):
        self.set_env('FAKE_SESSIONS', 'swap')
        root, r = self.failed_run(exc=ValueError)
        self.assertEqual(r['failed_stage'], 'POSTCHECK'); self.assertIsNone(r['new_vm_allocated'])
        self.assertIs(r['same_machine_observed'], False)

    def test_vanished_session_does_not_claim_new_allocation(self):
        self.set_env('FAKE_SESSIONS', 'vanish')
        root, r = self.failed_run(exc=ValueError)
        self.assertIsNone(r['new_vm_allocated'])
        self.assertIsNone(r['session_machine_after'])
        self.assertEqual(r['status'], 'FAIL_STOP')

    def test_exec_cli_failure_skips_download_and_keeps_stderr(self):
        self.set_env('FAKE_EXEC', 'fail'); self.with_artifacts()
        root, r = self.failed_run(exc=RuntimeError)
        self.assertEqual(self.verbs(), ['sessions', 'upload', 'exec'])
        self.assertEqual((r['failed_stage'], r['retry_authorized']), ('EXEC', False))
        self.assertEqual((root / 'EXEC.stderr').read_bytes().strip(), b'boom')

    def test_remote_exception_with_cli_exit_zero_is_a_failure(self):
        for raw in (LIVE_STDERR_RAISE, LIVE_STDERR_EXIT3):
            stderr_file = self.base / 'stderr.bin'; stderr_file.write_bytes(raw)
            self.set_env('FAKE_EXEC', 'raise'); self.set_env('FAKE_STDERR_FILE', str(stderr_file))
            self.log.write_text('')
            root, r = self.failed_run(exc=RuntimeError)
            self.assertEqual(r['status'], 'FAIL_STOP'); self.assertEqual(r['failed_stage'], 'EXEC')
            self.assertTrue(r['remote_failure_signals'])
            for leftover in (self.base / 'results').glob('*__test'): __import__('shutil').rmtree(leftover)

    def test_colored_exception_line_alone_is_detected(self):
        stderr_file = self.base / 'stderr.bin'; stderr_file.write_bytes(b'\x1b[0;31mValueError\x1b[0m: bad input\n')
        self.set_env('FAKE_EXEC', 'raise'); self.set_env('FAKE_STDERR_FILE', str(stderr_file))
        root, r = self.failed_run(exc=RuntimeError)
        self.assertEqual(r['remote_failure_signals'], ['exception-line'])

    def test_clean_stderr_with_python_warning_is_not_a_false_alarm(self):
        stderr_file = self.base / 'stderr.bin'
        stderr_file.write_bytes(b'/usr/lib/python3/x.py:1: UserWarning: something harmless\n  warnings.warn("x")\n')
        self.set_env('FAKE_EXEC', 'raise'); self.set_env('FAKE_STDERR_FILE', str(stderr_file))
        self.assertEqual(self.receipt(calculate.run(copy.deepcopy(self.plan), cli=self.cli))['status'], 'PASS_EXECUTION')

    def test_success_marker_required_when_declared(self):
        self.plan['success_marker'] = 'RESULT-OK-MARKER-1'
        self.set_env('FAKE_EXEC', 'nomarker')
        root, r = self.failed_run(exc=RuntimeError)
        self.assertIs(r['success_marker_found'], False)
        self.set_env('FAKE_EXEC', 'marker'); self.plan['purpose'] = 'test-marker-ok'
        root = calculate.run(copy.deepcopy(self.plan), cli=self.cli)
        self.assertIs(self.receipt(root)['success_marker_found'], True)
        self.assertEqual(self.receipt(root)['warnings'], [])

    def test_download_digest_mismatch_stops_and_keeps_evidence(self):
        self.set_env('FAKE_DOWNLOAD', 'bad'); self.with_artifacts()
        root, r = self.failed_run(exc=ValueError)
        self.assertEqual((r['failed_stage'], r['retry_authorized']), ('DOWNLOAD-000', False))
        self.assertTrue((root / 'REPORT.json').exists())
        self.assertNotIn('POSTCHECK', ''.join(p.name for p in root.iterdir()))

    def test_second_run_in_same_second_never_overwrites(self):
        class Frozen:
            @staticmethod
            def now(tz=None):
                from datetime import datetime
                return datetime(2026, 10, 1, 12, 0, 0, tzinfo=tz)
        with patch('calculate.datetime', Frozen):
            first = calculate.run(copy.deepcopy(self.plan), cli=self.cli)
            before = (first / 'RUN_RECEIPT.json').read_bytes()
            with self.assertRaises(FileExistsError): calculate.run(copy.deepcopy(self.plan), cli=self.cli)
        self.assertEqual((first / 'RUN_RECEIPT.json').read_bytes(), before)

    def test_cli_calls_are_bounded_by_plan_timeout_plus_margin(self):
        seen = []
        real = calculate.subprocess.run
        def spy(*a, **k):
            seen.append(k.get('timeout')); return real(*a, **k)
        with patch('calculate.subprocess.run', spy):
            calculate.run(copy.deepcopy(self.plan), cli=self.cli)
        self.assertEqual(set(seen), {60})

    def test_only_expected_verbs_are_ever_called(self):
        self.with_artifacts(); calculate.run(copy.deepcopy(self.plan), cli=self.cli)
        self.assertEqual(set(self.verbs()), {'sessions', 'upload', 'exec', 'download'})

    def test_failure_receipt_written_even_when_cli_dies(self):
        self.set_env('FAKE_EXEC', 'fail')
        root, r = self.failed_run(exc=RuntimeError)
        self.assertFalse(r['remote_termination_confirmed'])


class CliAndTimezone(Base):
    def test_explicit_relative_cli_runs_without_path_lookup(self):
        previous = os.getcwd()
        try:
            os.chdir(self.base)
            self.assertEqual(calculate.resolve_cli('./fake_colab.sh'), self.cli)
            root = calculate.run(copy.deepcopy(self.plan), cli='./fake_colab.sh')
            self.assertEqual(self.receipt(root)['status'], 'PASS_EXECUTION')
            self.assertEqual(self.verbs(), ['sessions', 'exec', 'sessions'])
        finally:
            os.chdir(previous)

    def test_explicit_relative_cli_ignores_same_name_on_path(self):
        other = self.base / 'other'; other.mkdir()
        decoy = other / self.cli.name
        decoy.write_text('#!/bin/sh\nexit 99\n'); decoy.chmod(0o755)
        previous = os.getcwd()
        try:
            os.chdir(self.base)
            with patch.dict(os.environ, {'PATH': str(other) + os.pathsep + os.environ['PATH']}):
                root = calculate.run(copy.deepcopy(self.plan), cli='./fake_colab.sh')
            self.assertEqual(self.receipt(root)['status'], 'PASS_EXECUTION')
            self.assertEqual(self.verbs(), ['sessions', 'exec', 'sessions'])
        finally:
            os.chdir(previous)

    def test_bare_cli_missing_from_path_does_not_use_cwd_file(self):
        previous = os.getcwd()
        try:
            os.chdir(self.base)
            with patch.dict(os.environ, {'PATH': '/usr/bin:/bin'}):
                with self.assertRaises(ValueError):
                    calculate.run(copy.deepcopy(self.plan), cli='fake_colab.sh')
            self.assertFalse((self.base / 'results').exists())
        finally:
            os.chdir(previous)

    def test_cli_resolution_from_argument_env_and_path(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('COLAB_CLI', None)
            with self.assertRaises(ValueError): calculate.resolve_cli(None)
            self.assertEqual(calculate.resolve_cli(str(self.cli)), self.cli)
            os.environ['COLAB_CLI'] = str(self.cli)
            self.assertEqual(calculate.resolve_cli(None), self.cli)
            os.environ['PATH'] = str(self.base) + os.pathsep + os.environ['PATH']
            self.assertEqual(calculate.resolve_cli('fake_colab.sh'), self.cli)

    def test_cli_must_exist_and_be_executable(self):
        with self.assertRaises(ValueError): calculate.resolve_cli(str(self.base / 'missing.sh'))
        plain = self.base / 'plain.txt'; plain.write_text('x'); plain.chmod(0o644)
        with self.assertRaises(ValueError): calculate.resolve_cli(str(plain))

    def test_run_without_configured_cli_fails_before_creating_anything(self):
        os.environ.pop('COLAB_CLI', None)
        with self.assertRaises(ValueError): calculate.run(copy.deepcopy(self.plan))
        self.assertFalse((self.base / 'results').exists())

    def test_run_uses_env_cli(self):
        self.set_env('COLAB_CLI', str(self.cli))
        root = calculate.run(copy.deepcopy(self.plan))
        self.assertEqual(self.receipt(root)['status'], 'PASS_EXECUTION')

    def test_folder_stamp_defaults_to_utc_and_follows_env(self):
        from datetime import datetime, timezone
        class Clock:
            @staticmethod
            def now(tz=None): return datetime(2026, 10, 1, 3, 0, 0, tzinfo=timezone.utc).astimezone(tz)
        os.environ.pop('COLAB_CALC_TZ', None)
        with patch('calculate.datetime', Clock):
            utc_root = calculate.run(copy.deepcopy(self.plan), cli=self.cli)
        self.assertTrue(utc_root.name.startswith('20261001-030000__'), utc_root.name)
        self.set_env('COLAB_CALC_TZ', 'Europe/Berlin'); self.plan['purpose'] = 'berlin'
        with patch('calculate.datetime', Clock):
            berlin_root = calculate.run(copy.deepcopy(self.plan), cli=self.cli)
        self.assertTrue(berlin_root.name.startswith('20261001-050000__'), berlin_root.name)  # CEST = UTC+2

    def test_receipt_and_plan_schemas_are_generic(self):
        root = calculate.run(copy.deepcopy(self.plan), cli=self.cli)
        self.assertEqual(self.receipt(root)['schema'], 'colab-calculation-receipt-v2')
        self.assertEqual(json.loads((root / 'PLAN.json').read_text())['schema'], 'colab-calculation-plan-v1')


if __name__ == '__main__': unittest.main()
