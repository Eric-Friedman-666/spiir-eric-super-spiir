#!/usr/bin/env python3
"""Dependency-free gates for the formal no-injection sidecar entrypoints."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
FORMAL_LAUNCHER = ROOT / 'run_noinj_sidecar.sh'
FORMAL_CONTRACT = ROOT / 'FORMAL_NOINJECTION_SIDECAR_ENTRYPOINT_V2.txt'
FORMAL_SUBMIT = ROOT / 'sidecar_noinj_submit.sh'
FORMAL_SBATCH = ROOT / 'sidecar_noinj_sbatch.sh'
FORMAL_PIPELINE = ROOT / 'sidecar_noinj_pipeline.sh'
FORMAL_CONSUMER = ROOT / 'sidecar_noinj_consumer.py'
FORMAL_OWNED_A107 = ROOT / 'sidecar_owned_a107.py'
FORMAL_CAUSAL_ENGINE = ROOT / 'sidecar_causal_engine.py'
FORMAL_SEGMENT_BINDING = ROOT / 'sidecar_segment_provenance.py'
FORMAL_SHAPE_BINDING = ROOT / 'sidecar_shape_source_binding.py'
FORMAL_NUMERIC_ADAPTER = ROOT / 'verification_sidecar_numeric.py'
IMAGE = Path('/fred/oz016/singularity/spiir-base-py3')
RUNTIME_FILES = (
    FORMAL_CONTRACT.name,
    FORMAL_LAUNCHER.name,
    FORMAL_SUBMIT.name,
    FORMAL_SBATCH.name,
    FORMAL_PIPELINE.name,
    FORMAL_OWNED_A107.name,
    FORMAL_CONSUMER.name,
    FORMAL_CAUSAL_ENGINE.name,
    FORMAL_SEGMENT_BINDING.name,
    FORMAL_SHAPE_BINDING.name,
    FORMAL_NUMERIC_ADAPTER.name,
)


def contract_values():
    return dict(
        line.split('=', 1)
        for line in FORMAL_CONTRACT.read_text(encoding='ascii').splitlines())


class NoInjectionLauncherTests(unittest.TestCase):
    def fixture(self):
        temporary = tempfile.TemporaryDirectory()
        base = Path(temporary.name)
        source = base / 'source'
        run = base / 'sidecar'
        raw = base / 'raw'
        banks = raw / 'banks'
        source.mkdir()
        run.mkdir()
        banks.mkdir(parents=True)
        for original in (FORMAL_LAUNCHER, FORMAL_CONTRACT, FORMAL_SUBMIT,
                         FORMAL_SBATCH, FORMAL_PIPELINE, FORMAL_CONSUMER,
                         FORMAL_OWNED_A107, FORMAL_CAUSAL_ENGINE,
                         FORMAL_SEGMENT_BINDING, FORMAL_SHAPE_BINDING,
                         FORMAL_NUMERIC_ADAPTER):
            target = source / original.name
            shutil.copy2(original, target)
            if original.suffix == '.sh':
                target.chmod(0o755)

        paths = {}
        for name in ('frames.cache', 'segments.xml', 'detrsp.xml',
                     'H1.pkl', 'L1.pkl'):
            path = raw / name
            path.write_text(name + '\n', encoding='ascii')
            paths[name] = path
        stats_root = raw / 'multi'
        for worker in range(2):
            worker_tag = f'{worker:03d}'
            worker_root = stats_root / worker_tag
            worker_root.mkdir(parents=True)
            for suffix in ('2w', '1d', '2h'):
                path = worker_root / (
                    f'{worker_tag}_marginalized_stats_{suffix}.xml.gz')
                path.write_text(
                    f'{worker_tag}-{suffix}\n', encoding='ascii')
        for bank in range(16):
            for ifo in ('H1', 'L1', 'V1'):
                path = banks / (
                    f'iir_{ifo}-GSTLAL_SPLIT_BANK_{bank:04d}-a1-0-0.xml.gz')
                path.write_text(f'{ifo}-{bank}\n', encoding='ascii')

        values = {
            'SIDECAR_RUN_ROOT': run,
            'SIDECAR_PROFILE': 'NOINJECTION_PARITY',
            'SIDECAR_MODE': 'NO_INJECTION',
            'SIDECAR_FRAME_CACHE': paths['frames.cache'],
            'SIDECAR_SEGMENT_XML': paths['segments.xml'],
            'SIDECAR_DETRSP_MAP': paths['detrsp.xml'],
            'SIDECAR_BANK_DIR': banks,
            'SIDECAR_MULTI_STATS_ROOT': stats_root,
            'SIDECAR_WGUO_PICKLE_H1': paths['H1.pkl'],
            'SIDECAR_WGUO_PICKLE_L1': paths['L1.pkl'],
            'SIDECAR_START_GPS': '1252187822',
            'SIDECAR_END_GPS': '1252274222',
            'SIDECAR_BACKGROUND_WINDOW_SECONDS': '10800',
            'SIDECAR_UPDATE_PERIOD_SECONDS': '3600',
            'SIDECAR_ZEROLAG_UPDATE_SECONDS': '3600',
            'SIDECAR_WORKER_COUNT': '2',
            'SIDECAR_BANKS_PER_WORKER': '8',
            'SIDECAR_START_BANK': '0',
            'SIDECAR_H1_STRAIN_CHANNEL': 'GDS-CALIB_STRAIN_CLEAN',
            'SIDECAR_L1_STRAIN_CHANNEL': 'GDS-CALIB_STRAIN_CLEAN',
            'SIDECAR_V1_STRAIN_CHANNEL': 'Hrec_hoft_16384Hz',
            'SIDECAR_H1_STATE_CHANNEL': 'GDS-CALIB_STATE_VECTOR',
            'SIDECAR_L1_STATE_CHANNEL': 'GDS-CALIB_STATE_VECTOR',
            'SIDECAR_V1_STATE_CHANNEL': 'DQ_ANALYSIS_STATE_VECTOR',
            'SIDECAR_FINALSINK_SCHEMA_MODE': 'legacy-a107',
            'SIDECAR_SNR_SERIES_LOGFAR_THRESHOLD': '-4',
            'SIDECAR_CONTAINER_IMAGE': IMAGE,
            'SIDECAR_DRY_RUN': '1',
        }
        config = run / 'launch.env'
        config.write_text(
            ''.join(f'{key}={value}\n' for key, value in values.items()),
            encoding='ascii')
        return temporary, source, run, raw, config

    def invoke(self, script, config, extra_env=None):
        environment = dict(os.environ)
        environment['PATH'] = '/usr/bin:/bin'
        if extra_env:
            environment.update(extra_env)
        return subprocess.run(
            [str(script), str(config)], env=environment, check=False,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def fake_scheduler(self, base):
        fakebin = base / 'fakebin'
        fakebin.mkdir()
        squeue = fakebin / 'squeue'
        sbatch = fakebin / 'sbatch'
        squeue.write_text('#!/bin/sh\nexit 0\n', encoding='ascii')
        sbatch.write_text(
            '#!/bin/sh\nprintf "%s\\n" "$@" > '
            '"$SIDECAR_TEST_CAPTURE"\nprintf "424242\\n"\n',
            encoding='ascii')
        squeue.chmod(0o755)
        sbatch.chmod(0o755)
        return fakebin

    def test_contract_and_static_callgraph(self):
        values = contract_values()
        self.assertEqual(values['entrypoint'], FORMAL_LAUNCHER.name)
        self.assertEqual(values['slurm_wrapper'], FORMAL_SBATCH.name)
        self.assertEqual(
            values['acquisition_science'],
            'normal_SPIIR_H1_L1_V1_CRASHCAR_ENABLE_0')
        self.assertEqual(
            values['component_scope'],
            'finite_valid_rho_ge_4_inclusive_H1_L1_only')
        self.assertEqual(
            values['runtime_snapshot'],
            'run_root_runtime_exact_readonly_closure')
        self.assertEqual(
            values['runtime_execution'],
            'staged_submit_sbatch_pipeline_consumer_and_imports_only')
        joined = '\n'.join(
            path.read_text(encoding='ascii')
            for path in (FORMAL_LAUNCHER, FORMAL_SUBMIT, FORMAL_SBATCH, FORMAL_PIPELINE))
        for required in ('H1,L1,V1', 'H1,L1',
                         'GPS interval must be positive',
                         'background window must be positive',
                         'update period must be positive',
                         'zerolag update period must be positive',
                         'worker count must be positive',
                         'banks per worker must be positive',
                         'unsupported BBH bank >=384',
                         'NOINJECTION_PARITY',
                         'run_spiir_py3', 'wguo-single-det-py3',
                         'sidecar_noinj_pipeline.sh',
                         'sidecar_noinj_consumer.py',
                         'sidecar_owned_a107.py'):
            self.assertIn(required, joined)
        for forbidden in ('run_postrun_a107_sidecar.sh',
                          'sidecar_a107_postrun.py',
                          'sidecar_stream_consumer.py',
                          'final_report.json', 'crashcar.env',
                          'merge_worker_far_ledgers.py',
                          'patch_zerolag_single_far_from_ledger.py',
                          'assign_frozen_far_ledger.py',
                          'frontier', 'checkpoint', 'frozen_background'):
            self.assertNotIn(forbidden, joined)
        sbatch_source = FORMAL_SBATCH.read_text(encoding='ascii')
        acquisition = sbatch_source.index(
            'wguo-single-det-py3 bash "$PIPELINE" "$CONFIG"')
        roster = sbatch_source.index(
            'completed acquisition did not publish own A107 roster')
        consumer = sbatch_source.index(
            'wguo-single-det-py3 python3 "$CONSUMER"')
        complete = sbatch_source.rindex("state=COMPLETE")
        self.assertLess(acquisition, roster)
        verify = 'verify_runtime_snapshot "$PINNED_SOURCE_SHA256"'
        first_verify = sbatch_source.index(verify)
        second_verify = sbatch_source.index(verify, first_verify + 1)
        self.assertLess(first_verify, acquisition)
        self.assertLess(roster, second_verify)
        self.assertLess(second_verify, consumer)
        optional_defaults = (
            'GST_DEBUG=${GST_DEBUG-}',
            'X509_USER_PROXY=${X509_USER_PROXY-}',
            'X509_USER_KEY=${X509_USER_KEY-}',
            'X509_USER_CERT=${X509_USER_CERT-}',
            'KRB5_KTNAME=${KRB5_KTNAME-}',
        )
        helper_source = sbatch_source.index('source "$HELPER"')
        for default in optional_defaults:
            self.assertLess(sbatch_source.index(default), helper_source)
        self.assertLess(helper_source, first_verify)
        self.assertNotIn(
            'SLURM_CPUS_PER_TASK=${SLURM_CPUS_PER_TASK-}',
            sbatch_source)
        launcher_source = FORMAL_LAUNCHER.read_text(encoding='ascii')
        submit_source = FORMAL_SUBMIT.read_text(encoding='ascii')
        consumer_source = FORMAL_CONSUMER.read_text(encoding='ascii')
        self.assertIn(
            'exec bash "$RUNTIME/sidecar_noinj_submit.sh"', launcher_source)
        self.assertIn(
            '"$SCRIPT_DIR/sidecar_noinj_sbatch.sh"', submit_source)
        self.assertIn('_verify_runtime_context(args)', consumer_source)
        self.assertLess(
            consumer_source.index('_verify_runtime_context(args)'),
            consumer_source.index('status = consume(args)'))
        self.assertLess(roster, consumer)
        self.assertLess(consumer, complete)
        for binding in ('--worker-id', '--worker-count', '--worker-group',
                        '--segment-xml', '--wguo-pickle-h1',
                        '--wguo-pickle-l1', '--runtime-manifest-sha256'):
            self.assertIn(binding, sbatch_source)

    def test_launcher_dry_run_and_missing_submit(self):
        temporary, source, run, raw, config = self.fixture()
        self.addCleanup(temporary.cleanup)
        before = sorted(path.name for path in run.iterdir())
        completed = self.invoke(source / FORMAL_LAUNCHER.name, config)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        output = dict(
            line.split('=', 1)
            for line in completed.stdout.splitlines() if '=' in line)
        self.assertIn('SIDECAR_NOINJ_DRY_RUN', completed.stdout)
        self.assertEqual(output['acquisition_ifos'], 'H1,L1,V1')
        self.assertEqual(output['single_ifos'], 'H1,L1')
        self.assertEqual(output['duration_seconds'], '86400')
        self.assertEqual(output['background_window_seconds'], '10800')
        self.assertEqual(output['update_period_seconds'], '3600')
        self.assertEqual(output['zerolag_update_seconds'], '3600')
        self.assertEqual(output['worker_count'], '2')
        self.assertEqual(output['banks_per_worker'], '8')
        self.assertEqual(output['start_bank'], '0')
        self.assertEqual(
            output['config_sha256'],
            hashlib.sha256(config.read_bytes()).hexdigest())
        self.assertEqual(before, sorted(path.name for path in run.iterdir()))

        config.write_text(
            config.read_text(encoding='ascii').replace(
                'SIDECAR_DRY_RUN=1', 'SIDECAR_DRY_RUN=0'),
            encoding='ascii')
        (source / FORMAL_SUBMIT.name).unlink()
        completed = self.invoke(source / FORMAL_LAUNCHER.name, config)
        self.assertEqual(completed.returncode, 2)
        self.assertIn('runtime source sidecar_noinj_submit.sh must be a regular non-symlink file',
                      completed.stderr)


    def submitted_fixture(self):
        temporary, source, run, raw, config = self.fixture()
        self.addCleanup(temporary.cleanup)
        config.write_text(
            config.read_text(encoding='ascii').replace(
                'SIDECAR_DRY_RUN=1', 'SIDECAR_DRY_RUN=0'),
            encoding='ascii')
        before = {
            name: hashlib.sha256((source / name).read_bytes()).hexdigest()
            for name in RUNTIME_FILES
        }
        fakebin = self.fake_scheduler(Path(temporary.name))
        capture = Path(temporary.name) / 'sbatch.argv'
        environment = {
            'PATH': f'{fakebin}:/usr/bin:/bin',
            'SIDECAR_TEST_CAPTURE': str(capture),
        }
        completed = self.invoke(
            source / FORMAL_LAUNCHER.name, config, environment)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn(
            'SIDECAR_NOINJ_SUBMITTED job_id=424242', completed.stdout)
        after = {
            name: hashlib.sha256((source / name).read_bytes()).hexdigest()
            for name in RUNTIME_FILES
        }
        self.assertEqual(after, before)
        captured = capture.read_text(encoding='ascii').splitlines()
        self.assertEqual(captured[captured.index('--array') + 1], '0-1')
        self.assertEqual(captured[captured.index('--chdir') + 1], str(run))
        exported = captured[captured.index('--export') + 1]
        self.assertEqual(
            Path(captured[-1]), run / 'runtime' / FORMAL_SBATCH.name)
        return temporary, source, run, config, exported

    @staticmethod
    def slurm_environment(exported):
        environment = dict(os.environ)
        environment['PATH'] = '/usr/bin:/bin'
        for item in exported.split(',')[1:]:
            key, value = item.split('=', 1)
            environment[key] = value
        environment['SLURM_ARRAY_TASK_ID'] = '0'
        environment['SLURM_JOB_ID'] = '424242'
        return environment

    def test_staged_runtime_manifest_and_import_origin(self):
        _temporary, source, run, _config, exported = (
            self.submitted_fixture())
        runtime = run / 'runtime'
        expected = sorted((*RUNTIME_FILES, 'expected_manifest.sha256'))
        self.assertEqual(
            sorted(path.name for path in runtime.iterdir()), expected)
        self.assertEqual(runtime.stat().st_mode & 0o222, 0)
        manifest = runtime / 'expected_manifest.sha256'
        records = manifest.read_text(encoding='ascii').splitlines()
        self.assertEqual(len(records), len(RUNTIME_FILES))
        self.assertEqual(
            [record.split('  ', 1)[1] for record in records],
            list(RUNTIME_FILES))
        for record, name in zip(records, RUNTIME_FILES):
            digest, separator, recorded_name = record.partition('  ')
            self.assertEqual(separator, '  ')
            self.assertEqual(recorded_name, name)
            staged = runtime / name
            self.assertTrue(staged.is_file())
            self.assertFalse(staged.is_symlink())
            self.assertEqual(staged.stat().st_mode & 0o222, 0)
            self.assertEqual(
                hashlib.sha256(staged.read_bytes()).hexdigest(), digest)
            self.assertEqual(staged.read_bytes(), (source / name).read_bytes())
        manifest_sha = hashlib.sha256(manifest.read_bytes()).hexdigest()
        export_map = dict(
            item.split('=', 1) for item in exported.split(',')[1:])
        self.assertEqual(
            export_map['SIDECAR_SOURCE_MANIFEST_SHA256'], manifest_sha)
        self.assertEqual(
            manifest.read_bytes(),
            (run / 'provenance' / 'source_manifest.sha256').read_bytes())

        command = (
            'import argparse, sidecar_noinj_consumer as s;'
            f'a=argparse.Namespace(run_root={str(run)!r},'
            f'source_manifest_sha256={manifest_sha!r},'
            f'runtime_manifest_sha256={manifest_sha!r});'
            'print(s._verify_runtime_context(a))')
        environment = dict(os.environ)
        environment['PYTHONDONTWRITEBYTECODE'] = '1'
        verified = subprocess.run(
            ['python3', '-c', command], cwd=runtime, env=environment,
            check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True)
        self.assertEqual(verified.returncode, 0, verified.stderr)
        self.assertEqual(verified.stdout.strip(), manifest_sha)
        self.assertEqual(
            sorted(path.name for path in runtime.iterdir()), expected)

    def test_staged_tamper_fails_before_acquisition_without_complete(self):
        _temporary, _source, run, _config, exported = (
            self.submitted_fixture())
        runtime = run / 'runtime'
        victim = runtime / FORMAL_CONSUMER.name
        victim.chmod(0o755)
        with victim.open('a', encoding='ascii') as handle:
            handle.write('\n# synthetic post-submit tamper\n')
        completed = subprocess.run(
            [str(runtime / FORMAL_SBATCH.name)],
            env=self.slurm_environment(exported), check=False,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertEqual(completed.returncode, 2)
        self.assertIn('staged runtime file is writable', completed.stderr)
        status_payload = '\n'.join(
            path.read_text(encoding='ascii')
            for path in (run / 'status').iterdir() if path.is_file())
        self.assertNotIn('state=COMPLETE', status_payload)
        self.assertFalse(
            (run / 'reference' / 'worker_000' / 'status.json').exists())
        self.assertFalse(
            (run / 'acquisition' / 'worker_000' / 'a107_roster.tsv').exists())

    def test_fresh_root_and_source_drift_fail_closed(self):
        temporary, source, run, _raw, config = self.fixture()
        self.addCleanup(temporary.cleanup)
        (run / 'runtime').mkdir()
        stale = self.invoke(source / FORMAL_LAUNCHER.name, config)
        self.assertEqual(stale.returncode, 2)
        self.assertIn('sidecar root is not fresh', stale.stderr)

        temporary2, source2, run2, _raw2, config2 = self.fixture()
        self.addCleanup(temporary2.cleanup)
        config2.write_text(
            config2.read_text(encoding='ascii').replace(
                'SIDECAR_DRY_RUN=1', 'SIDECAR_DRY_RUN=0'),
            encoding='ascii')
        fakebin = self.fake_scheduler(Path(temporary2.name))
        capture = Path(temporary2.name) / 'sbatch.argv'
        mutator = fakebin / 'cp'
        mutator.write_text(
            '#!/bin/sh\n'
            '/usr/bin/cp "$@" || exit $?\n'
            'case "$*" in *sidecar_noinj_consumer.py*) '
            'printf "\\n# synthetic source drift\\n" '
            '>> "$SIDECAR_TEST_MUTATE" ;; esac\n',
            encoding='ascii')
        mutator.chmod(0o755)
        drift = self.invoke(
            source2 / FORMAL_LAUNCHER.name, config2, {
                'PATH': f'{fakebin}:/usr/bin:/bin',
                'SIDECAR_TEST_CAPTURE': str(capture),
                'SIDECAR_TEST_MUTATE':
                    str(source2 / FORMAL_CONSUMER.name),
            })
        self.assertEqual(drift.returncode, 2)
        self.assertIn(
            'production runtime source mutated during staging: '
            'sidecar_noinj_consumer.py',
            drift.stderr)
        self.assertFalse(capture.exists())
        self.assertFalse((run2 / 'status').exists())
        self.assertFalse((run2 / 'reference').exists())

    def test_missing_image_and_input_fail_closed(self):
        temporary, source, run, raw, config = self.fixture()
        self.addCleanup(temporary.cleanup)
        config.write_text(
            config.read_text(encoding='ascii').replace(
                str(IMAGE), '/missing/sidecar-image').replace(
                'SIDECAR_DRY_RUN=1', 'SIDECAR_DRY_RUN=0'),
            encoding='ascii')
        completed = self.invoke(source / FORMAL_LAUNCHER.name, config)
        self.assertEqual(completed.returncode, 2)
        self.assertIn('container_image must be a non-symlink directory',
                      completed.stderr)

        temporary2, source2, run2, raw2, config2 = self.fixture()
        self.addCleanup(temporary2.cleanup)
        config2.write_text(
            config2.read_text(encoding='ascii').replace(
                str(raw2 / 'frames.cache'), '/missing/frames.cache').replace(
                'SIDECAR_DRY_RUN=1', 'SIDECAR_DRY_RUN=0'),
            encoding='ascii')
        completed = self.invoke(source2 / FORMAL_LAUNCHER.name, config2)
        self.assertEqual(completed.returncode, 2)
        self.assertIn('frame_cache must be a regular non-symlink file',
                      completed.stderr)

    def pipeline_fixture(self, mode, dry='0', external_find=False):
        temporary, source, run, raw, config = self.fixture()
        self.addCleanup(temporary.cleanup)
        config.write_text(
            config.read_text(encoding='ascii').replace(
                'SIDECAR_DRY_RUN=1', f'SIDECAR_DRY_RUN={dry}'),
            encoding='ascii')
        for directory in ('status', 'log', 'acquisition', 'reference',
                          'provenance'):
            (run / directory).mkdir()
        fakebin = Path(temporary.name) / 'pipeline-bin'
        fakebin.mkdir()
        capture = Path(temporary.name) / 'acquisition.argv'
        external = Path(temporary.name) / 'external.xml.gz'
        external.write_text('external\n', encoding='ascii')
        binary = fakebin / 'gstlal_inspiral_postcohspiir_online'
        binary.write_text(
            '#!/bin/sh\nprintf "%s\\n" "$@" > '
            '"$SIDECAR_TEST_CMD_CAPTURE"\n'
            'case "$SIDECAR_FAKE_MODE" in\n'
            'success) printf "beta\\n" > '
            '000/000_zerolag_000002.xml.gz; '
            'printf "alpha\\n" > 000/000_zerolag_000001.xml.gz ;;\n'
            'symlink) ln -s "$SIDECAR_TEST_EXTERNAL" '
            '000/000_zerolag_000001.xml.gz ;;\n'
            'none) : ;;\n'
            'error) exit 7 ;;\n'
            '*) exit 9 ;;\n'
            'esac\nexit 0\n', encoding='ascii')
        binary.chmod(0o755)
        finder = fakebin / 'find'
        finder.write_text(
            '#!/bin/sh\n'
            'if [ "${SIDECAR_FAKE_FIND_EXTERNAL:-0}" = 1 ]; then\n'
            '  case " $* " in *" -type f "*) '
            'printf "%s\\0" "$SIDECAR_TEST_EXTERNAL"; exit 0 ;; esac\n'
            'fi\nexec /usr/bin/find "$@"\n', encoding='ascii')
        finder.chmod(0o755)
        environment = {
            'PATH': f'{fakebin}:/usr/bin:/bin',
            'SIDECAR_TEST_CMD_CAPTURE': str(capture),
            'SIDECAR_TEST_EXTERNAL': str(external),
            'SIDECAR_FAKE_MODE': mode,
            'SIDECAR_FAKE_FIND_EXTERNAL': '1' if external_find else '0',
            'SIDECAR_WORKER_ID': '0',
            'SIDECAR_ACQUISITION_IFOS': 'H1,L1,V1',
            'SIDECAR_SINGLE_IFOS': 'H1,L1',
            'WGUO_O3A_INJECTION_MODE': 'none',
            'WGUO_O3A_INJECTION_FILE': '',
            'CRASHCAR_ENABLE': '0',
        }
        return source, run, raw, config, capture, environment

    def test_pipeline_dry_run_exact_hlv_command(self):
        source, run, raw, config, capture, environment = (
            self.pipeline_fixture('none', dry='1'))
        pipeline = source / FORMAL_PIPELINE.name
        completed = self.invoke(pipeline, config, environment)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn('SIDECAR_NOINJ_ACQUISITION_ARGV', completed.stdout)
        output = completed.stdout
        argv_values = [
            line.split('=', 1)[1]
            for line in output.splitlines()
            if line.startswith('argv[')]
        for required in (
                'H1=GDS-CALIB_STATE_VECTOR',
                'L1=GDS-CALIB_STATE_VECTOR',
                'V1=DQ_ANALYSIS_STATE_VECTOR',
                'H1=GDS-CALIB_STRAIN_CLEAN',
                'L1=GDS-CALIB_STRAIN_CLEAN',
                'V1=Hrec_hoft_16384Hz',
                'iir_H1-GSTLAL_SPLIT_BANK_0000',
                'iir_L1-GSTLAL_SPLIT_BANK_0007',
                'iir_V1-GSTLAL_SPLIT_BANK_0007',
                str(raw / 'multi' / '000' /
                    '000_marginalized_stats_2w.xml.gz'),
                str(raw / 'multi' / '000' /
                    '000_marginalized_stats_1d.xml.gz'),
                str(raw / 'multi' / '000' /
                    '000_marginalized_stats_2h.xml.gz'),
                '--gps-start-time', '1252187822',
                '--gps-end-time', '1252274222',
                'crashcar_enable=0',
                f'segment_xml={raw / "segments.xml"}'):
            self.assertIn(required, output)
        self.assertNotIn('--finalsink-cluster-window', argv_values)
        self.assertEqual(argv_values.count('--iir-bank'), 8)
        self.assertEqual(
            argv_values.count('--cohfar-accumbackground-output-prefix'), 8)
        bank_payloads = [
            argv_values[index + 1]
            for index, value in enumerate(argv_values)
            if value == '--iir-bank']
        self.assertEqual(len(bank_payloads), 8)
        for bank, payload in enumerate(bank_payloads):
            for ifo in ('H1', 'L1', 'V1'):
                self.assertIn(
                    f'iir_{ifo}-GSTLAL_SPLIT_BANK_{bank:04d}', payload)
        self.assertEqual(
            argv_values[
                argv_values.index('--finalsink-snapshot-interval') + 1],
            '3600')
        self.assertEqual(
            argv_values[
                argv_values.index(
                    '--cohfar-accumbackground-snapshot-interval') + 1],
            '3600')
        self.assertEqual(
            argv_values[
                argv_values.index('--finalsink-fapupdater-interval') + 1],
            '3600')
        self.assertEqual(
            argv_values[
                argv_values.index(
                    '--finalsink-fapupdater-collect-walltime') + 1],
            '10800,10800,10800')
        self.assertNotIn("--finalsink-postcoh-schema-mode", argv_values)
        self.assertNotIn("--snr-series-logfar-threshold", argv_values)
        self.assertFalse(capture.exists())
        self.assertFalse((run / 'acquisition' / 'worker_000').exists())

    def test_pipeline_success_atomic_sorted_own_root_roster(self):
        source, run, raw, config, capture, environment = (
            self.pipeline_fixture('success'))
        completed = self.invoke(
            source / FORMAL_PIPELINE.name, config, environment)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        roster = run / 'acquisition' / 'worker_000' / 'a107_roster.tsv'
        self.assertTrue(roster.is_file())
        rows = roster.read_text(encoding='ascii').splitlines()
        self.assertEqual(rows[0], 'relative_path\tbytes\tsha256')
        self.assertEqual(
            [row.split('\t', 1)[0] for row in rows[1:]],
            ['000/000_zerolag_000001.xml.gz',
             '000/000_zerolag_000002.xml.gz'])
        for row in rows[1:]:
            relative, size, digest = row.split('\t')
            path = run / 'acquisition' / 'worker_000' / relative
            self.assertTrue(path.is_file())
            self.assertGreater(int(size), 0)
            self.assertEqual(digest, hashlib.sha256(path.read_bytes()).hexdigest())
        command = capture.read_text(encoding='ascii')
        self.assertIn('--cohfar-assignfar-input-fname', command)
        self.assertNotIn('--injection-file', command)
        self.assertNotIn('--blind-injections', command)

    def test_pipeline_eos_error_and_roster_rejections(self):
        source, run, raw, config, capture, environment = (
            self.pipeline_fixture('error'))
        completed = self.invoke(
            source / FORMAL_PIPELINE.name, config, environment)
        self.assertEqual(completed.returncode, 7)
        self.assertFalse(
            (run / 'acquisition' / 'worker_000' / 'a107_roster.tsv').exists())

        source, run, raw, config, capture, environment = (
            self.pipeline_fixture('none'))
        completed = self.invoke(
            source / FORMAL_PIPELINE.name, config, environment)
        self.assertEqual(completed.returncode, 2)
        self.assertIn('EOS completed without A107 zerolag candidates',
                      completed.stderr)

        source, run, raw, config, capture, environment = (
            self.pipeline_fixture('symlink'))
        completed = self.invoke(
            source / FORMAL_PIPELINE.name, config, environment)
        self.assertEqual(completed.returncode, 2)
        self.assertIn('A107 candidate is a symlink', completed.stderr)

        source, run, raw, config, capture, environment = (
            self.pipeline_fixture('success', external_find=True))
        completed = self.invoke(
            source / FORMAL_PIPELINE.name, config, environment)
        self.assertEqual(completed.returncode, 2)
        self.assertIn('A107 candidate escapes worker root', completed.stderr)

        source, run, raw, config, capture, environment = (
            self.pipeline_fixture('success'))
        worker = run / 'acquisition' / 'worker_000'
        worker.mkdir()
        (worker / 'a107_roster.tsv').write_text(
            'conflict\n', encoding='ascii')
        completed = self.invoke(
            source / FORMAL_PIPELINE.name, config, environment)
        self.assertEqual(completed.returncode, 2)
        self.assertIn('a107 roster already exists', completed.stderr)
        self.assertEqual(
            (worker / 'a107_roster.tsv').read_text(encoding='ascii'),
            'conflict\n')

    def test_helper_optional_argv_defaults_and_explicit_passthrough(self):
        optional = (
            'GST_DEBUG', 'X509_USER_PROXY', 'X509_USER_KEY',
            'X509_USER_CERT', 'KRB5_KTNAME')
        helper = Path(
            '/fred/oz016/gwdc_spiir_pipeline_codebase/'
            'scripts_n_things/build/bash_helper_functions.sh')
        shell = r'''
set -euo pipefail
mode=$1
selected=$2
value=$3
helper=$4
module() { return 0; }
singularity() { printf 'ARG=%s\n' "$@"; }
SLURM_CPUS_PER_TASK=1
unset GST_DEBUG X509_USER_PROXY X509_USER_KEY X509_USER_CERT KRB5_KTNAME
if [ "$mode" = explicit ]; then
    printf -v "$selected" '%s' "$value"
fi
GST_DEBUG=${GST_DEBUG-}
X509_USER_PROXY=${X509_USER_PROXY-}
X509_USER_KEY=${X509_USER_KEY-}
X509_USER_CERT=${X509_USER_CERT-}
KRB5_KTNAME=${KRB5_KTNAME-}
source "$helper"
declare -F run_spiir_py3 >/dev/null
run_spiir_py3 -e SIDECAR_TEST=1 wguo-single-det-py3 true
'''
        cases = [('all-unset', '', '')]
        cases.extend(
            ('explicit', name, f'sentinel-{index}')
            for index, name in enumerate(optional))
        for mode, selected, value in cases:
            completed = subprocess.run(
                ['bash', '-c', shell, 'sidecar-helper-test',
                 mode, selected, value, str(helper)],
                env={'PATH': '/usr/bin:/bin'}, check=False,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            expected = {name: '' for name in optional}
            if mode == 'explicit':
                expected[selected] = value
            for name, expected_value in expected.items():
                self.assertIn(
                    f'ARG={name}={expected_value}', completed.stdout)

        required_shell = shell.replace(
            'SLURM_CPUS_PER_TASK=1',
            'unset SLURM_CPUS_PER_TASK')
        missing = subprocess.run(
            ['bash', '-c', required_shell, 'sidecar-helper-test',
             'all-unset', '', '', str(helper)],
            env={'PATH': '/usr/bin:/bin'}, check=False,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn('SLURM_CPUS_PER_TASK', missing.stderr)

    def test_shell_syntax(self):
        for script in (FORMAL_LAUNCHER, FORMAL_SUBMIT, FORMAL_SBATCH, FORMAL_PIPELINE):
            completed = subprocess.run(
                ['bash', '-n', str(script)], check=False,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == '__main__':
    unittest.main()
