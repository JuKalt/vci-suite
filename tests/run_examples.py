#!/usr/bin/env python3
"""Run the VCI Suite documentation examples and report verified behavior."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import zipfile

SUITE = Path(__file__).resolve().parents[1]
COMPONENTS = ('multiview-data', 'capture-processing', 'device-jobs', 'avatar-creation')
DATASETS = ('processed', 'thuman', 'dancenet3d', 'dna', 'talkbody4d', '4ddress', 'actorshq')
METHODS = ('mmlphuman', 'animatable-gaussians', 'pig-avatar')
FULL = ('media', 'smplx', 'smplx_confidence', 'mhr', 'mhrfromsmplx', 'metrics', 'semantics', 'visualizations')
BASE = ('media', 'smplx', 'metrics')
BASE_ARGS = ('--mhr-source', 'none', '--skip-smplx-to-mhr', '--no-semantic-parsing', '--no-mamma-confidence-fit')
FENCES = re.compile(r'^```(?:bash|sh|python)\n(.*?)^```[ \t]*$', re.M | re.S)
# Find inline executable instructions too, keeping ordinary flags, filenames, and prose out of the index.
INLINE = re.compile(r'(?<!`)`((?:\./[\w./-]+|python3? |git |ssh |conda |capture-(?:process|validate|reprocess) )[ \t\w./${}=\-\'"/]+)`')


class Blocked(Exception):
    """A manually supplied prerequisite is missing or was not enabled."""


class CheckFailed(Exception):
    """A command or behavioral assertion failed."""


def require(condition, message):
    if not condition:
        raise CheckFailed(message)


def read(path):
    return json.loads(Path(path).read_text())


def write(path, data):
    Path(path).write_text(json.dumps(data, indent=2) + '\n')
    return Path(path)


def snippets(text):
    blocks = [(text.count('\n', 0, m.start()) + 1, m[1].strip()) for m in FENCES.finditer(text)]
    outside = FENCES.sub(lambda m: '\n' * m[0].count('\n'), text)
    blocks.extend((outside.count('\n', 0, m.start()) + 1, m[1]) for m in INLINE.finditer(outside))
    return sorted(blocks)


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


class Runner:
    def __init__(self, suite, config, output):
        self.suite, self.config, self.output = suite, config, output
        self.repos = {'vci-suite': suite, **{name: suite / name for name in COMPONENTS}}
        self.results = []
        self.env = {**os.environ, 'VCI_DATASET_CONFIG': str(output / 'dataset-paths.json'),
                    'VCI_DATASET_OUTPUT_CONFIG': str(output / 'output-paths.json'), 'PYTHONUNBUFFERED': '1'}
        for key in ('ENV_NAME', 'VCI_CLI_ENV', 'VCI_CONDA_EXE', 'PYTHONPATH'):
            self.env.pop(key, None)
        if self.config.get('mamma_repo'):
            self.env['MAMMA_REPO_PATH'] = self.config['mamma_repo']
        self.capture = self.repos['capture-processing']
        self.avatar = self.repos['avatar-creation']
        self.data = self.repos['multiview-data']
        self.device = self.repos['device-jobs']
        self.fixture = output / 'reader-fixture'
        self.coverage = read(Path(__file__).with_name('example_coverage.json'))
        self.locations = {}

    def case(self, case_id, title, fn):
        self.active = {'id': case_id, 'title': title, 'steps': []}
        self.results.append(self.active)
        self.work = self.output / case_id
        self.work.mkdir()
        start = time.monotonic()
        try:
            self.active['detail'] = fn() or 'All behavior assertions passed.'
            self.active['status'] = 'PASS'
        except Blocked as exc:
            self.active.update(status='BLOCKED', detail=str(exc))
        except KeyboardInterrupt:
            self.active.update(status='FAIL', detail='Interrupted by the operator; later cases were not run.')
            raise
        except Exception as exc:
            self.active.update(status='FAIL', detail=self.redact(f'{type(exc).__name__}: {exc}'))
        finally:
            self.active['seconds'] = round(time.monotonic() - start, 2)
            print(f"{self.active['status']:7} {case_id}: {self.active['detail']}", flush=True)
            self.report()

    def redact(self, text):
        for key, value in self.env.items():
            if len(value) >= 4 and re.search(r'(TOKEN|PASS(WOR)?D|SECRET|PRIVATE_KEY)', key, re.I):
                text = text.replace(value, '[redacted]')
        return text

    def run(self, cwd, *argv, env=None, stdin=None, timeout=None, expect=0, interactive=False):
        argv = list(map(str, argv))
        if argv[0] == './run.sh' and Path(cwd) == self.device and '--config' in argv:
            argv.append('--no-dashboard')
        label = f"step-{len(self.active['steps']) + 1:02}"
        log = self.work / f'{label}.log'
        step = {'command': self.redact(shlex.join(argv)), 'cwd': str(cwd), 'log': str(log.relative_to(self.output))}
        self.active['steps'].append(step)
        if interactive:
            step['interactive'] = True
        limit = timeout or self.config.get('timeout_seconds', 900)
        with tempfile.TemporaryFile(mode='w+', encoding='utf-8') as stdout, \
             tempfile.TemporaryFile(mode='w+', encoding='utf-8') as stderr:
            with subprocess.Popen(argv, cwd=cwd, env=self.env | (env or {}), text=True,
                                  stdin=None if interactive else (subprocess.PIPE if stdin is not None else subprocess.DEVNULL),
                                  stdout=None if interactive else stdout, stderr=None if interactive else stderr,
                                  start_new_session=not interactive) as proc:
                interrupted = False
                try:
                    proc.communicate(stdin, timeout=limit)
                except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
                    interrupted = isinstance(exc, KeyboardInterrupt)
                    if interrupted:
                        step['interrupted'] = True
                    else:
                        step['timeout_seconds'] = limit
                    # Device Jobs handles SIGINT and cancels the worker supervisors.
                    for sig, grace in ((signal.SIGINT, 12), (signal.SIGTERM, 5), (signal.SIGKILL, None)):
                        try:
                            if interactive:
                                proc.send_signal(sig)
                            else:
                                os.killpg(proc.pid, sig)
                        except ProcessLookupError:
                            pass
                        try:
                            proc.wait(timeout=grace)
                            break
                        except subprocess.TimeoutExpired:
                            continue
                step['exit'] = proc.returncode
            stdout.seek(0)
            stderr.seek(0)
            out, err = self.redact(stdout.read()), self.redact(stderr.read())
        note = 'Interactive transcript omitted to protect credentials.\n' if interactive else ''
        log.write_text(f"cwd: {cwd}\n$ {step['command']}\nexit: {step['exit']}\n\n{note}{out}\n--- stderr ---\n{err}")
        if interrupted:
            raise KeyboardInterrupt
        # Conda activation hooks can print the real error to stdout and only a wrapper error to stderr.
        # Prefer diagnostics from stdout when present; ordinary command output remains in the linked log.
        diagnostics = [line for line in out.splitlines() if line.startswith(('ERROR:', 'Error:'))]
        if not diagnostics and not err.strip():
            diagnostics = out.strip().splitlines()[-8:]
        tail = '\n'.join(diagnostics[-4:] + err.strip().splitlines()[-8:])
        require('timeout_seconds' not in step, f'{label} timed out after {limit}s: {tail} (see {step["log"]})')
        require(step['exit'] == expect, f'{label} expected exit {expect}, got {step["exit"]}: {tail} (see {step["log"]})')
        return out

    def launch(self, repo, *args, **kw):
        return self.run(repo, './run.sh', *args, **kw)

    def needed(self, key):
        value = self.config.get(key)
        if not value:
            raise Blocked(f'Set or enable `{key}` in the test config.')
        return value

    def report(self):
        results = {item['id']: item for item in self.results}
        counts = {status: sum(row.get('status') == status for row in self.results) for status in ('PASS', 'FAIL', 'BLOCKED')}
        write(self.output / 'report.json', {'suite': str(self.suite), 'counts': counts, 'cases': self.results,
                                           'documentation': self.locations})
        md = ['# VCI Suite example verification', '', f'Suite: `{self.suite}`', '',
              ' · '.join(f'{n} {status.lower()}' for status, n in counts.items()), '',
              '`PASS` means the listed outcomes were observed. `BLOCKED` means a prerequisite was missing; '
              'it is not a pass. GPU/model/dataset accuracy is separate from these functional checks.', '']
        for status in counts:
            md.extend((f'## {status.title()}', ''))
            for row in self.results:
                if row.get('status') != status:
                    continue
                md.append(f'- **{row["id"]} — {row["title"]}** ({row["seconds"]}s): {row["detail"].replace(chr(10), " ")}')
                if row['steps']:
                    md.append('  ' + ' · '.join(f'[{Path(x["log"]).stem}]({x["log"]})' for x in row['steps'] if 'exit' in x))
            md.append('')
        md.extend(('## Documentation coverage', '', 'A command can have both an offline and a live test. '
                   'Status is reported for each separately.', '', '| Location | Test results |', '| --- | --- |'))
        for loc, ids in sorted(self.locations.items()):
            md.append(f'| `{loc}` | ' + ', '.join(f'{cid}: {results.get(cid, {}).get("status", "NOT RUN")}' for cid in ids) + ' |')
        (self.output / 'report.md').write_text('\n'.join(md) + '\n')

    def docs(self):
        expected = self.coverage
        unmatched = []
        observed = set()
        for repo_name, repo in self.repos.items():
            # Scope to published docs, not external models, caches, or submodule contents.
            files = [repo / 'README.md', repo / 'LICENSING.md', *repo.glob('docs/**/*.md'), *repo.glob('examples/**/*.md')]
            if repo_name == 'vci-suite':
                files.append(repo / 'configs/distribution/README.md')
            for path in files:
                key = f'{repo_name}/{path.relative_to(repo)}'
                for line, text in snippets(path.read_text()):
                    token = f'{key}:{digest(text)}'
                    observed.add(token)
                    cases = expected.get(token)
                    if cases:
                        require(set(cases) <= set(self.case_ids()), f'{key}:{line} refers to an unknown test ID')
                        location = f'{key}:{line}'
                        self.locations[location] = sorted(set(self.locations.get(location, []) + cases))
                    else:
                        unmatched.append(f'{key}:{line} (new or changed snippet: {text.splitlines()[0]}; coverage key: {token})')
        stale = set(expected) - observed
        require(not unmatched and not stale, '\n'.join(unmatched + [f'{len(stale)} obsolete coverage entries'] if stale else unmatched))
        return f'{len(self.locations)} documented snippets have test mappings; changed commands require a reviewed mapping.'

    def suite_check(self):
        out = self.run(self.suite, './check.sh', env={'PATH': '/usr/bin:/bin'})
        require('process_vci_subject.py' in out and 'run_methods.py' in out, 'Both workload job previews must render')
        require(all(method in out for method in METHODS), 'Registered adapter inventory is incomplete')
        listing = self.run(self.suite, sys.executable, Path(__file__), '--list')
        require(all(case_id in listing for case_id in self.case_ids()), 'Runner inventory omitted a case')
        self.launch(self.data, 'examples/inspect_sequence.py', '--help')
        self.launch(self.device, '--help')
        self.launch(self.capture, 'capture-process', '--help')
        self.launch(self.capture, 'capture-reprocess', '--help')
        return 'Shared checkout verified without PATH lookup of Conda; capture and avatar job commands rendered.'

    def activated_conda(self):
        caller = self.work / 'caller-env'
        hook = caller / 'etc/conda/deactivate.d/broken-toolchain.sh'
        marker = self.work / 'caller-hook-ran'
        hook.parent.mkdir(parents=True)
        (caller / 'conda-meta').mkdir()
        hook.write_text(f'printf "%s\\n" "ERROR: caller deactivation hook ran"\n'
                        f'touch {shlex.quote(str(marker))}\nexit 71\n')
        env = {'CONDA_SHLVL': '1', 'CONDA_PREFIX': str(caller), 'CONDA_DEFAULT_ENV': 'caller-env'}
        # check.sh uses every component launcher. Also test Capture Processing's explicit override.
        out = self.run(self.suite, './check.sh', env=env)
        require('process_vci_subject.py' in out and 'run_methods.py' in out, 'Suite did not render both workload previews')
        conda, prefix = (self.capture / '.cli-conda').read_text().splitlines()
        out = self.launch(self.capture, 'python', '-c', 'import sys; print(sys.prefix)',
                          env={**env, 'ENV_NAME': Path(prefix).name, 'VCI_CONDA_EXE': conda,
                               'CONDA_ENVS_PATH': str(Path(prefix).parent)})
        require(Path(out.strip()).resolve() == Path(prefix).resolve(), 'Explicit converter override used the wrong environment')
        require(not marker.exists(), 'A launcher ran the caller’s deactivation hook')
        return 'All four launchers and the converter override worked with an unrelated active prefix and ignored its failing deactivation hook.'

    def reviewed_example(self, filename, program):
        token = f'multiview-data/{filename}:{digest(program.strip())}'
        require('reader-fixture' in self.coverage.get(token, ()),
                f'{filename} changed; review its executable example in example_coverage.json before running it.')

    def build_reader_fixture(self):
        if (self.fixture / 'demo/calibration.json').is_file():
            return
        blocks = list(FENCES.finditer((self.data / 'examples/README.md').read_text()))
        program = blocks[0][1]
        self.reviewed_example('examples/README.md', program)
        exact = program.replace('DEMO_ROOT="$(mktemp -d)"', 'DEMO_ROOT="$VCI_TEST_FIXTURE"', 1)
        out = self.run(self.data, 'bash', '-e', '-c', exact, env={'VCI_TEST_FIXTURE': str(self.fixture)})
        require(out.splitlines()[0] == 'demo' and '1 shared RGB frames; first IDs: [6]' in out, 'Wrong subject or frame intersection')
        for camera in ('cam00', 'cam01'):
            require(f'{camera} frame 6: RGB (6, 8, 3), mask (6, 8), fx 12 px' in out, f'{camera}: decoded media or calibration is wrong')

    def reader_fixture(self):
        self.build_reader_fixture()
        out = self.launch(self.data, 'examples/inspect_sequence.py', 'processed', self.fixture,
                          '--subject', 'demo', '--cameras', 'cam01', '--frame', 6)
        require('cam01 frame 6' in out and 'cam00 frame' not in out, 'Explicit camera/frame selection was ignored')
        self.launch(self.data, 'examples/inspect_sequence.py', 'processed', self.fixture,
                    '--subject', 'no-such-subject', expect=2)
        self.launch(self.data, '-', self.fixture / 'demo', stdin="""
from pathlib import Path
import sys
import numpy as np
from PIL import Image
subject = Path(sys.argv[1])
np.savez(subject / 'smpl_params.npz', transl=np.array([[1., 2., 3.]]), frame_ids=np.array([6]))
labels = subject / 'semantic_parsing/general/cam00/label_000006.png'
labels.parent.mkdir(parents=True)
Image.new('L', (8, 6), 3).save(labels)
""")
        for path in (self.data / 'README.md', self.data / 'examples/README.md'):
            programs = re.findall(r'^```python\n(.*?)^```', path.read_text(), re.M | re.S)
            for program in programs:
                self.reviewed_example(path.relative_to(self.data), program)
                program = program.replace('/path/to/processed', str(self.fixture)).replace('/data/processed', str(self.fixture)).replace('example-subject', 'demo')
                if path == self.data / 'README.md':
                    program += '\nassert image.shape == (6, 8, 3) and image[0, 0].tolist() == [90, 25, 5]\nassert mask.shape == (6, 8) and mask.all()\n'
                if path.parent.name == 'examples':
                    program += '\nassert fit["transl"].tolist() == [[1., 2., 3.]] and fit["frame_ids"].tolist() == [6]\nassert labels.shape == (6, 8) and (labels == 3).all()\n'
                out = self.launch(self.data, '-', stdin=program)
                if path.parent.name == 'examples':
                    require(out.count('(48, 3)') == 2, 'Foreground extraction did not yield 48 RGB pixels per camera')
        return 'Executed the README fixture/API examples; verified RGB pixels, masks, calibration, native IDs, SMPL-X arrays and semantic labels.'

    def reader(self, kind):
        data = self.config.get('datasets', {}).get(kind)
        if not data:
            raise Blocked(f'Set `datasets.{kind}` with root, subject, and optional cameras/frame.')
        root, subject = data['root'], data['subject']
        options = ['--trusted-pickles'] if kind == '4ddress' and data.get('trusted_pickles') else []
        env = {'DNA_RENDERING_SAMPLE_CODE': data['sample_code']} if data.get('sample_code') else {}
        out = self.launch(self.data, 'examples/inspect_sequence.py', kind, root, *options, env=env)
        require(subject in out.splitlines(), f'Native ID {subject} was not discovered')
        selected = ['--cameras', *data['cameras']] if data.get('cameras') else []
        if 'frame' in data:
            selected.extend(('--frame', data['frame']))
        out = self.launch(self.data, 'examples/inspect_sequence.py', kind, root, '--subject', subject,
                          *options, *selected, env=env)
        samples = [line for line in out.splitlines() if 'RGB (' in line and 'fx ' in line and 'fx missing' not in line]
        require(samples, 'No requested observation decoded with calibration')
        if data.get('cameras'):
            require([line.split(' frame ', 1)[0] for line in samples] == data['cameras'], 'Wrong cameras decoded with calibration')
        if 'frame' in data:
            require(all(f" frame {data['frame']}:" in line for line in samples), 'Reader decoded the wrong native frame')
        if data.get('require_mask', True):
            require(all('mask (' in line for line in samples), 'At least one selected camera is missing its mask')
        annotations = data.get('annotations', [])
        for annotation in annotations:
            require(annotation in next(line for line in out.splitlines() if line.startswith('Annotations:')).split(': ', 1)[1].split(', '),
                    f'Missing expected annotation: {annotation}')
        if annotations:
            camera, frame = samples[0].split(' frame ', 1)
            self.launch(self.data, '-', kind, root, subject, camera, frame.partition(':')[0],
                        str(bool(options)), *annotations, env=env, stdin="""
import sys
from multiview_data import open_dataset
kind, root, subject, camera, frame, trusted, *annotations = sys.argv[1:]
sequence = next(seq for seq in open_dataset(kind, root, trusted_pickles=trusted == 'True') if seq.id == subject)
with sequence:
    for name in annotations:
        # Archives carry the full sequence; native per-frame readers use the frame ID.
        value = sequence.read_annotation(name, camera_id=camera, frame_id=int(frame))
        assert value is not None and (value.size if hasattr(value, 'size') else len(value)) > 0, f'Empty {name} annotation'
""")
        return f'Discovered {subject} and decoded {len(samples)} calibrated observations and {len(annotations)} requested annotations.'

    def validate(self, root, products=BASE, expect=0, batch=False):
        out = self.launch(self.capture, 'capture-validate', '--subjects-root' if batch else '--subject-root', root,
                          '--require', *products, '--deep', expect=expect)
        reports = json.loads(out)
        require(reports and all(row['complete'] == (expect == 0) for row in reports), f'Unexpected validator result: {reports}')
        return reports

    def validate_fixture(self):
        self.build_reader_fixture()
        import shutil
        root = self.work / 'processed'
        shutil.copytree(self.fixture, root)
        self.validate(root / 'demo', ('media',))
        self.validate(root, ('media',), batch=True)
        file = root / 'demo/images/cam01/color_000006.png'
        file.write_bytes(b'Not an image')
        report = self.validate(root / 'demo', ('media',), expect=1)[0]
        require('media' in report['errors'], 'Corrupt RGB file was not diagnosed as a media error')
        return 'Deep validator accepted readable media, then rejected the corrupted frame; both root options tested.'

    def migration_report(self):
        self.needed('check_assets')
        root = Path(self.config.get('migration_root') or self.fixture)
        if root == self.fixture:
            self.build_reader_fixture()
        require(root.is_dir(), f'Migration root does not exist: {root}')
        def inventory():
            return {p.relative_to(root): (p.stat().st_mtime_ns, p.stat().st_size) for p in root.rglob('*') if p.is_file()}
        before = inventory()
        out = self.launch(self.capture, 'python', '-m', 'capture_processing.tools.migrate_cache_signatures', '--output-root', root)
        require(inventory() == before, 'Report-only cache migration mutated input files')
        require('report' in out.lower() or 'apply' in out.lower(), 'Migration did not identify that it was report-only')
        return 'Cache signature report completed without modifying files.'

    def prepare_capture(self):
        raw = self.work / 'raw/demo'
        (raw / 'frame_000001').mkdir(parents=True)
        (raw / 'calibration_dome.json').write_text('{}')
        job = self.work / 'job'
        args = ('python', self.suite / 'examples/prepare_capture_job.py', raw.parent, 'demo', job,
                '--suite-root', self.suite, '--gpu', '0')
        self.launch(self.capture, *args)
        config = read(job / 'job.json')
        require(config['subjects'] == ['demo'] and config['task']['output_layout'] == 'root', 'Wrong generated subject or output layout')
        require(config['machines'][0]['cwd'] == str(self.capture), 'Generated worker uses the wrong checkout')
        require(set(BASE_ARGS) <= set(config['task']['command']), 'Local example unexpectedly enabled optional stages')
        original = (job / 'job.json').read_bytes()
        self.launch(self.capture, *args, expect=2)
        require((job / 'job.json').read_bytes() == original, 'Generator overwrote an existing job config')
        self.preview(job / 'job.json', 'process_vci_subject.py')
        require(not (job / 'runs').exists(), 'Preview/preflight created scheduler state')
        return 'Generator pinned the local converter, preserved an existing config, and passed preview and preflight.'

    def preview(self, path, expected, *, preflight=True):
        out = self.launch(self.device, '--config', path, '--dry-run')
        require(expected in out, f'Dry run did not render {expected}')
        if preflight:
            out = self.launch(self.device, '--config', path, '--preflight-only')
            require('Worker preflight passed.' in out, 'Preflight did not confirm workers are accessible')

    def job(self, owner, name, *, file=None):
        spec = read(file or owner / 'examples' / f'{name}.json')
        spec.update(shared_run_root=str(self.work / 'runs'), output_root=str(self.work / 'outputs'), poll_seconds=0.2)
        machine = spec['machines'][0]
        machine.update(cwd=str(owner), bootstrap_python=sys.executable)
        machine['python'] = (self.launch(owner, 'python' if owner == self.capture else '--python', '-c',
                                         'import sys; print(sys.executable)').strip()
                             if owner in (self.capture, self.avatar) else sys.executable)
        return spec

    def check_job(self, spec, *, synthetic=False):
        path = write(self.work / 'job.json', spec)
        env = {}
        if synthetic:
            # Exercise the actual controller and worker processes on a CPU machine.
            exe = self.work / 'nvidia-smi'
            exe.write_text('#!/bin/sh\ncase "$*" in\n  *--query-gpu*) printf "0, GPU-12345678-1234-1234-1234-123456789abc, 1000, 0, 0\\n1, GPU-98765432-4321-4321-4321-cba987654321, [N/A], [N/A], [N/A]\\n";;\nesac\n')
            exe.chmod(0o700)
            env['PATH'] = f'{self.work}:{os.environ["PATH"]}'
        try:
            self.launch(self.device, '--config', path, env=env, timeout=self.config.get('job_timeout_seconds', 1800))
        except CheckFailed as exc:
            raise CheckFailed(f'{exc}\nScheduler state and worker logs: {spec["shared_run_root"]}') from exc
        results = sorted(Path(spec['shared_run_root']).glob('run-*/results.json'), key=lambda p: p.stat().st_mtime_ns)
        require(results, 'Scheduler returned no terminal results.json')
        terminal = read(results[-1])
        require(set(terminal) == set(spec['subjects']) and all(row['state'] == 'succeeded' for row in terminal.values()),
                f'Scheduler did not complete the expected subjects: {terminal}')
        return Path(spec['output_root'])

    def hello(self, mode):
        ssh = mode == 'ssh'
        spec = self.job(self.device, 'ssh-job' if ssh else 'python-job')
        spec['subjects'] = ['sample-a', 'sample-b']
        if ssh:
            worker = self.needed('ssh_worker')
            require(worker.get('transport') == 'ssh' and worker.get('dedicated_checkout') is True,
                    'Use a dedicated disposable SSH working tree and set ssh_worker.dedicated_checkout to true')
            spec['machines'] = [worker]
            scratch = Path(tempfile.mkdtemp(prefix='vci-example-', dir=self.needed('ssh_shared_scratch')))
            spec.update(shared_run_root=str(scratch / 'runs'), output_root=str(scratch / 'outputs'))
            spec['source']['root'] = str(self.device)
        elif mode == 'gpu':
            spec['machines'][0]['gpus'][0]['id'] = self.needed('local_gpu')['id']
        path = write(self.work / 'job.json', spec)
        self.preview(path, 'examples/hello.py')
        require(not (self.work / 'runs').exists(), 'Preview/preflight unexpectedly created a run')
        markers = []
        for _ in range(1 if ssh else 2):
            out = self.check_job(spec, synthetic=mode == 'synthetic')
            files = [out / subject / 'hello.txt' for subject in spec['subjects']]
            for subject, file in zip(spec['subjects'], files):
                require(file.read_text() == f'Hello, {subject}!\n', f'Wrong output for {subject}')
            if markers:
                require(all(marker.read_text() == 'checkpoint\n' for marker in markers), 'Relaunch discarded existing output state')
            else:
                markers = [file.with_name('existing-state.txt') for file in files]
                for marker in markers:
                    marker.write_text('checkpoint\n')
        return ('Executed the scheduler and both jobs with simulated telemetry containing a healthy GPU and an [N/A] row; relaunch preserved existing output.'
                if mode == 'synthetic' else 'Executed both jobs on the configured worker and verified terminal state and file contents.')

    def capture_runtime_args(self, data):
        args = ['--workers', str(data.get('workers', 4))]
        for setting in ('mamma_python', 'mhr_python', 'mamma_workers', 'mhr_workers',
                        'resize_long_edge', 'semantic_model', 'semantic_python', 'semantic_working_long_edge'):
            if setting in data:
                args.extend(('--' + setting.replace('_', '-'), str(data[setting])))
        if data.get('drop_cameras'):
            args.extend(('--drop-cameras', *data['drop_cameras']))
        return args

    def capture_settings(self):
        data = self.needed('capture')
        root = Path(data['root'])
        subjects = data['subjects']
        require(subjects and len(set(subjects)) == len(subjects), 'capture.subjects must contain unique IDs')
        for subject in subjects:
            require(Path(subject).name == subject and subject not in ('.', '..'), f'Unsafe subject ID: {subject}')
            require((root / subject / 'calibration_dome.json').is_file(), f'Missing calibration for {root / subject}')
        return root, subjects, self.capture_runtime_args(data)

    def capture_outputs(self, output, subjects, products):
        require({p.name for p in output.iterdir() if (p / 'subject_manifest.json').is_file()} == set(subjects),
                'Processed output does not contain exactly the requested subjects')
        reports = self.validate(output, products, batch=True)
        require(len(reports) == len(subjects), 'Validator checked an unexpected number of subjects')
        for name in subjects:
            manifest = read(output / name / 'subject_manifest.json')
            require(manifest.get('last_run', {}).get('result') == 'complete', f'{name}: subject manifest did not record completion')
        return f'Deep validation passed for {len(subjects)} subject(s): {", ".join(products)}.'

    def capture_run(self, mode):
        self.needed('live_capture')
        raw, subjects, args = self.capture_settings()
        output = self.work / 'processed'
        if mode == 'full':
            subjects = subjects[:1]
            self.launch(self.capture, 'python', 'scripts/datasets/process_vci_subject.py', raw, subjects[0], output, '--', *args)
            return self.capture_outputs(output, subjects, FULL)
        # Expose only requested recordings to the batch CLI. Keep source input read-only.
        selected = self.work / 'recordings'
        selected.mkdir()
        for subject in subjects:
            (selected / subject).symlink_to(raw / subject, target_is_directory=True)
        if mode == 'batch-full':
            self.launch(self.capture, 'capture-process', selected, output, *args)
            return self.capture_outputs(output, subjects, FULL)
        command = ('capture-process', selected, output, *args, *BASE_ARGS)
        self.launch(self.capture, *command)
        result = self.capture_outputs(output, subjects, BASE)
        products = [output / subject / 'calibration.json' for subject in subjects]
        before = [p.read_bytes() for p in products]
        self.launch(self.capture, *command, '--resume-validation', 'quick')
        require([p.read_bytes() for p in products] == before, 'Resume changed camera calibration')
        self.capture_outputs(output, subjects, BASE)
        return result + ' Quick resume preserved calibration and passed deep validation again.'

    def assets(self):
        self.needed('check_assets')
        repo = self.config.get('mamma_repo')
        self.launch(self.capture, 'python', 'scripts/setup/setup_mamma_assets.py', '--check',
                    *(['--repo', repo] if repo else []))
        return 'The documented MAMMA asset checker accepted the installed files.'

    def reprocess(self, confidence=False):
        self.needed('live_capture')
        data = self.needed('dna_reprocess')
        output = self.work / 'processed'
        env = {'DNA_RENDERING_SAMPLE_CODE': data['sample_code']} if data.get('sample_code') else {}
        flags = BASE_ARGS[:-1] if confidence else BASE_ARGS
        self.launch(self.capture, 'capture-reprocess', 'dna', data['root'], output, '--subject', data['subject'],
                    '--cameras', *data['cameras'], '--frames', *data['frames'], '--',
                    *self.capture_runtime_args({**self.config.get('capture', {}), 'drop_cameras': [], **data}), *flags, env=env)
        products = (*BASE, 'smplx_confidence') if confidence else BASE
        result = self.capture_outputs(output, [data['subject']], products)
        imported = read(output / data['subject'] / 'subject_manifest.json')['source']['metadata']['imported']
        require(imported['dataset'] == 'dna' and imported['subject_id'] == data['subject'] and
                imported['frame_ids'] == data['frames'] and list(imported['camera_ids'].values()) == data['cameras'],
                f'Reprocessing changed the requested native identifiers: {imported}')
        return result + ' The source dataset, subject, camera and frame IDs were preserved.'

    def captured_validation(self):
        data = self.needed('validate_processed')
        reports = self.validate(data['root'], data.get('products', BASE), batch=data.get('batch', False))
        return f'Deep validation accepted {len(reports)} existing processed subject(s).'

    def scheduled_capture(self, generated):
        self.needed('live_capture')
        gpu = self.needed('local_gpu')['id']
        raw, subjects, args = self.capture_settings()
        if generated:
            job = self.work / 'generated'
            self.launch(self.capture, 'python', self.suite / 'examples/prepare_capture_job.py', raw, subjects[0], job,
                        '--suite-root', self.suite, '--gpu', gpu)
            spec = read(job / 'job.json')
            spec['task']['command'].extend(args)
            products = BASE
        else:
            spec = self.job(self.capture, 'processing-job')
            spec['subjects'] = subjects
            command = spec['task']['command']
            command[2] = str(raw)
            spec['task']['command'] = command[:command.index('--') + 1] + args
            products = FULL
        spec['machines'][0]['gpus'][0]['id'] = gpu
        if not generated:
            conda = (self.capture / '.cli-conda').read_text().splitlines()[0]
            spec['task'].setdefault('env', {})['PATH'] = f'{Path(conda).parent}:{os.environ["PATH"]}'.replace('{', '{{').replace('}', '}}')
        path = write(self.work / 'preview.json', spec)
        self.preview(path, 'process_vci_subject.py')
        return self.capture_outputs(self.check_job(spec), spec['subjects'], products)

    def avatar_args(self, method, live):
        if live:
            options = self.needed('live_avatar')
            if method not in options:
                raise Blocked(f'Enable `live_avatar.{method}` and supply bounded adapter arguments.')
            require(isinstance(options[method], list) and options[method], 'Live training requires explicit adapter arguments')
            return options[method]
        return ['--views', '4', '--time-steps', '16', '--resolution', '512']

    def avatar_outputs(self, root, method, subjects, live):
        path = root / ('neu-pig-smplx' if method == 'pig-avatar' else method)
        manifest = read(path / 'dataset_manifest.json')
        require([str(row['subject_id']) for row in manifest['subjects']] == subjects, 'Avatar manifest selected the wrong subjects')
        require((path / 'run_summary.log').stat().st_size > 0, 'Run settings were not saved')
        if live:
            require((path / 'runner.log').stat().st_size > 0, 'No adapter runner log was saved')
            models = [p for p in path.rglob('*') if p.suffix in ('.pt', '.pth', '.ckpt', '.pkl', '.ply', '.safetensors') and p.is_file()
                      and p.stat().st_size > 0 and any(part in ('model', 'checkpoints', 'trained_model', 'point_cloud', 'models')
                                                     for part in p.relative_to(path).parts[:-1])]
            require(models, f'No nonempty checkpoint/model found below {path}')
            for model in models:
                with model.open('rb') as stream:
                    prefix = stream.read(8)
                if model.suffix == '.ply':
                    require(prefix.startswith((b'ply\n', b'ply\r\n')), f'Invalid PLY model header: {model}')
                elif prefix.startswith(b'PK'):
                    with zipfile.ZipFile(model) as archive:
                        require(archive.namelist(), f'Empty checkpoint archive: {model}')
                elif model.suffix == '.safetensors':
                    size = int.from_bytes(prefix, 'little')
                    require(2 <= size <= model.stat().st_size - 8, f'Invalid Safetensors header length: {model}')
            return f'Checked selected subjects, runner log and {len(models)} saved model artifact(s).'
        require(not (path / 'runner.log').exists(), 'Preview unexpectedly launched the adapter')
        return 'Checked selected subjects and saved run settings; no training was launched.'

    def avatar_run(self, method, live=False, thuman=False):
        if not live and method not in self.config.get('preview_avatar', []):
            raise Blocked(f'Add {method} to `preview_avatar` once its method runtime is installed.')
        args = self.avatar_args(method, live)
        if thuman:
            data = self.needed('datasets').get('thuman')
            if not data:
                raise Blocked('Set `datasets.thuman` to test the THuman run config.')
            spec = read(self.avatar / 'examples/thuman-run.json')
            subjects = [data['subject']]
            spec.update(dataset_path=data['root'], subjects=subjects)
            inputs = ['--run-config', write(self.work / 'thuman-run.json', spec)]
        else:
            data = self.needed('avatar_input')
            subjects = data['subjects'][:1]
            inputs = ['--dataset-kind', 'processed', '--dataset-path', data['root'], '--subjects', *subjects]
        require(subjects, 'Provide at least one avatar subject')
        env = {'VCI_SMPLX_MODEL': self.config['smplx_model']} if self.config.get('smplx_model') else {}
        self.launch(self.avatar, method, *inputs, '--output-dir', self.work, '--no-tui',
                    *([] if live else ['--dry-run']), *([] if thuman else ['--', *args]), env=env,
                    timeout=self.config.get('training_timeout_seconds', 1800) if live else None)
        return self.avatar_outputs(self.work, method, subjects, live)

    def scheduled_avatar(self):
        args = self.avatar_args('mmlphuman', True)
        data = self.needed('avatar_input')
        spec = self.job(self.avatar, 'avatar-job')
        spec['subjects'] = data['subjects']
        require(len(set(spec['subjects'])) >= 2, 'Provide at least two distinct subjects to test scheduled avatar creation')
        spec['machines'][0]['gpus'][0]['id'] = self.needed('local_gpu')['id']
        cmd = spec['task']['command']
        cmd[cmd.index('--dataset-path') + 1] = data['root']
        spec['task']['command'] = cmd[:cmd.index('--') + 1] + args
        spec['task']['timeout_seconds'] = self.config.get('training_timeout_seconds', 1800)
        conda = (self.avatar / '.cli-conda').read_text().splitlines()[0]
        spec['task'].setdefault('env', {})['PATH'] = f'{Path(conda).parent}:{os.environ["PATH"]}'.replace('{', '{{').replace('}', '}}')
        if self.config.get('smplx_model'):
            spec['task'].setdefault('env', {})['VCI_SMPLX_MODEL'] = self.config['smplx_model']
        self.preview(write(self.work / 'preview.json', spec), 'run_methods.py')
        root = self.check_job(spec)
        for subject in spec['subjects']:
            self.avatar_outputs(root / subject, 'mmlphuman', [subject], True)
        return 'Scheduler produced separate verified model artifacts and manifests for every subject.'

    def batch_preview(self):
        path = self.device / 'examples/vci-2026_09_17.json'
        out = self.launch(self.device, '--config', path, '--dry-run')
        for text in ('process_vci_subject.py', '--mamma-workers 2', '--workers 8', '--resume-validation full', 'example-subject-01'):
            require(text in out, f'Multi-machine preview did not apply {text}')
        return 'Rendered the portable multi-machine example, including worker and GPU overrides.'

    def site_batches(self, mode):
        batches = self.needed('site_batches')
        if mode == 'preflight':
            self.needed('site_preflight')
        if mode == 'live':
            self.needed('live_site_batches')
        failures, blocked, passed = [], [], []
        for index, item in enumerate(batches):
            self.work = self.output / self.active['id'] / str(index)
            self.work.mkdir()
            label = Path(item['config']).name
            try:
                self.site_batch(mode, item)
            except Blocked as exc:
                blocked.append(f'{label}: {exc}')
            except Exception as exc:
                failures.append(f'{label}: {exc}')
            else:
                passed.append(label)
        detail = f'{mode.capitalize()} passed: {", ".join(passed) or "none"}.'
        if blocked:
            detail += '\nBlocked: ' + '; '.join(blocked)
        if failures:
            raise CheckFailed(detail + '\nFailures: ' + '; '.join(failures))
        if blocked:
            raise Blocked(detail)
        return detail

    def site_batch(self, mode, item):
        spec = read(item['config'])
        workload = item.get('workload', 'capture')
        require(workload in ('capture', 'avatar'), 'Site workload must be capture or avatar')
        method = item.get('method', 'mmlphuman')
        # Never launch with the original run or output roots.
        scratch = Path(item.get('shared_scratch', self.work)).resolve()
        if mode == 'live':
            require(item.get('subjects'), 'Provide an explicit small subject subset before launching a site batch')
            if any(m.get('transport') == 'ssh' for m in spec['machines']):
                require('shared_scratch' in item, 'Remote batches require shared_scratch on every host')
            if workload == 'capture':
                self.needed('live_capture')
            else:
                self.avatar_args(method, True)
                spec['task']['timeout_seconds'] = self.config.get('training_timeout_seconds', 1800)
        scratch = Path(tempfile.mkdtemp(prefix='vci-example-', dir=scratch))
        spec.update(shared_run_root=str(scratch / 'runs'), output_root=str(scratch / 'processed'))
        if item.get('subjects'):
            require(set(item['subjects']) <= set(spec['subjects']), 'Batch sample contains subjects outside the site config')
            spec['subjects'] = item['subjects']
        path = write(self.work / 'preview.json', spec)
        default_command = 'process_vci_subject.py' if workload == 'capture' else 'run_methods.py'
        self.preview(path, item.get('expected_command', default_command), preflight=mode != 'preview')
        if mode == 'live':
            root = self.check_job(spec)
            if workload == 'capture':
                self.capture_outputs(root, spec['subjects'], item.get('products', FULL))
            else:
                for subject in spec['subjects']:
                    self.avatar_outputs(root / subject, method, [subject], True)

    def install(self, name, mode):
        options = self.needed('fresh_install')
        key = f'{name}:{mode}'
        if key not in options:
            raise Blocked(f'Add `{key}` to `fresh_install` to build an isolated installation.')
        source = self.repos[name]
        checkout = self.work / name
        self.run(self.work, 'git', 'clone', '--no-local', '--', source, checkout)
        env_name = f'vci-test-{self.output.name[-8:]}-{self.active["id"]}'
        env = {'ENV_NAME': env_name, 'VCI_CLI_ENV': env_name, 'GIT_TERMINAL_PROMPT': '0',
               'CONDA_PKGS_DIRS': str(self.work / 'conda-pkgs'), 'CONDA_ENVS_PATH': str(self.work / 'conda-envs'),
               'PIP_NO_CACHE_DIR': '1'}
        if options[key].get('conda_exe'):
            env['VCI_CONDA_EXE'] = options[key]['conda_exe']
        for var in ('MAMMA_REPO_PATH', 'MAMMA_SUBMODULE_PATH', 'SAM3D_BODY_RERUN_REPO', 'VCI_METHOD_REGISTRY'):
            env[var] = ''
        if self.config.get('smplx_model'):
            env['VCI_SMPLX_MODEL'] = self.config['smplx_model']
        args = [] if mode == 'default' else [mode]
        mode_options = options[key]
        interactive = mode_options.get('interactive', False)
        if interactive and not sys.stdin.isatty():
            raise Blocked('Interactive model install requires a terminal to enter upstream credentials.')
        if name == 'vci-suite' and mode_options.get('local_components', True):
            for component in COMPONENTS:
                self.run(checkout, 'git', 'config', f'submodule.{component}.url', self.repos[component])
            args += ['--local']
        if name == 'capture-processing' and mode == '--converter-only' and 'install_torch' in mode_options:
            env['INSTALL_TORCH'] = '1' if mode_options['install_torch'] else '0'
        if name in ('capture-processing', 'avatar-creation') and options[key].get('local_dependency', True):
            sibling = self.work / 'multiview-data'
            self.run(self.work, 'git', 'clone', '--no-local', '--', self.data, sibling)
            env['MULTIVIEW_DATA_CHECKOUT'] = str(sibling)
        self.run(checkout, './install.sh', *args, env=env, timeout=self.config.get('install_timeout_seconds', 3600),
                 interactive=interactive)
        if name == 'vci-suite':
            self.run(checkout, 'git', 'submodule', 'sync')
            submodules = self.run(checkout, 'git', 'submodule', 'status')
            require(len(submodules.splitlines()) == len(COMPONENTS) and all(line.startswith(' ') for line in submodules.splitlines()),
                    'Suite submodules are missing or did not check out the pinned revisions')
            self.run(checkout, './check.sh', env={'PATH': '/usr/bin:/bin'})
        owners = COMPONENTS if name == 'vci-suite' else (name,)
        for owner in owners:
            repo = checkout / owner if name == 'vci-suite' else checkout
            receipt = (repo / '.cli-conda').read_text().splitlines()
            require(len(receipt) == 2 and Path(receipt[1]).name == env_name and
                    (Path(receipt[1]) / 'conda-meta').is_dir(), f'{owner} did not install into the fresh environment')
            cmd = {'multiview-data': ('examples/inspect_sequence.py', '--help'),
                   'capture-processing': ('capture-process', '--help'), 'device-jobs': ('--help',),
                   'avatar-creation': ('--list-methods',)}[owner]
            self.launch(repo, *cmd, env={'PATH': '/usr/bin:/bin'})
        runtime_env = {**env, 'PATH': f'{Path(receipt[0]).parent}:{os.environ["PATH"]}'}
        if mode in ('default', '--download-mamma-assets') and name in ('vci-suite', 'capture-processing'):
            capture = checkout / 'capture-processing' if name == 'vci-suite' else checkout
            self.launch(capture, 'python', 'scripts/setup/setup_mamma_assets.py', '--check', env=runtime_env)
        methods = []
        if name == 'avatar-creation' and mode in METHODS:
            methods = [mode]
        elif (name == 'avatar-creation' and mode == 'default') or (name == 'vci-suite' and mode != '--cli-only'):
            methods = ['mmlphuman', 'animatable-gaussians']
            if (repo / 'external/neu-pig-smplx/.git').exists():
                methods.append('pig-avatar')
        if methods:
            # Use the same preflight the real avatar CLI performs, in the new install.
            avatar = checkout / 'avatar-creation' if name == 'vci-suite' else checkout
            if name == 'vci-suite' and 'VCI_SMPLX_MODEL' not in runtime_env:
                model = checkout / 'capture-processing/external/mamma/data/body_models/smplx_locked_head/smplx/SMPLX_NEUTRAL.npz'
                if model.is_file():
                    runtime_env['VCI_SMPLX_MODEL'] = str(model)
            self.launch(avatar, '--python', '-c',
                        'import sys; from avatar_creation.methods.registry import load_method_spec, validate_method_environments; '
                        'validate_method_environments([load_method_spec(method) for method in sys.argv[1:]])',
                        *methods, env=runtime_env)
        return f'Fresh tracked checkout and unique Conda environment {env_name} passed launcher checks.'

    def component_tests(self, repo):
        cmd = ('python',) if repo == self.capture else (() if repo == self.data else ('--python',))
        python = self.config.get('test_python', {}).get(repo.name)
        if python:
            out = self.run(repo, python, '-m', 'unittest', 'discover', '-v',
                           env={'PYTHONPATH': str(self.data)})
        elif repo == self.data:
            out = self.launch(repo, '-m', 'unittest', 'discover', '-v')
        else:
            out = self.launch(repo, *cmd, '-c', 'import importlib.util; print(importlib.util.find_spec("torch") is not None)')
            if out.strip() != 'True':
                raise Blocked(f'The CLI environment lacks PyTorch; set `test_python.{repo.name}` to a development environment with the test extra.')
            out = self.launch(repo, *cmd, '-m', 'unittest', 'discover', '-v')
        step_log = self.output / self.active['steps'][-1]['log']
        text = out + step_log.read_text()
        count = re.search(r'Ran (\d+) tests?', text)
        require(count and int(count[1]) > 0, 'Unittest discovery did not run any tests')
        return f'Discovered and ran {count[1]} component tests. See the step log for any component skips.'

    def offline_job(self, kind):
        owner = {'capture': self.capture, 'avatar': self.avatar, 'ssh': self.device}[kind]
        filename, expected = {'capture': ('processing-job', 'process_vci_subject.py'),
                              'avatar': ('avatar-job', 'run_methods.py'), 'ssh': ('ssh-job', 'examples/hello.py')}[kind]
        spec = self.job(owner, filename)
        if kind == 'ssh':
            spec['source']['root'] = str(self.device)
        out = write(self.work / 'job.json', spec)
        if kind == 'ssh':
            self.run(self.work, 'git', 'init', self.work / 'worker-checkout')
            require((self.work / 'worker-checkout/.git').is_dir(), 'Could not create a disposable worker checkout')
            rendered = self.launch(self.device, '--config', out, '--dry-run')
            require('Source snapshot: 1 files' in rendered and expected in rendered, 'Wrong tracked source bundle in SSH preview')
        else:
            self.preview(out, expected)
        require(not (self.work / 'runs').exists(), 'Offline example wrote scheduler state')
        return f'Rendered the documented {filename} task with isolated paths and no run state.'

    def operator_ssh(self):
        worker = self.needed('ssh_worker')
        require(worker.get('transport') == 'ssh', 'ssh_worker.transport must be ssh')
        host = worker['host']
        target = f'{worker["user"]}@{host}' if worker.get('user') else host
        output = self.run(self.device, 'ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                          '-o', 'ConnectTimeout=15', '--', target, 'hostname', timeout=30)
        require(output.strip(), 'SSH returned no remote hostname')
        return f'Noninteractive SSH connected and returned hostname {output.strip()}.'

    def negative_preflight(self):
        spec = self.job(self.device, 'python-job')
        spec['machines'][0]['cwd'] = str(self.work / 'missing-cwd')
        config = write(self.work / 'job.json', spec)
        self.preview(config, 'examples/hello.py', preflight=False)
        for flags in (('--preflight-only',), ()):
            self.launch(self.device, '--config', config, *flags, expect=1)
            log = self.output / self.active['steps'][-1]['log']
            require('Cannot enter the configured working directory' in log.read_text(), 'Wrong preflight diagnosis')
        require(not (self.work / 'runs').exists(), 'Failed preflight started a scheduler run')
        return 'A working offline preview did not hide a bad worker checkout: preflight and live launch both stopped early.'

    def walkthrough(self):
        self.needed('live_walkthrough')
        self.needed('live_capture')
        train_args = self.avatar_args('mmlphuman', True)
        raw, subjects, args = self.capture_settings()
        subject = subjects[0]
        output = self.work / 'processed'
        self.launch(self.capture, 'python', 'scripts/datasets/process_vci_subject.py', raw, subject, output, '--', *args)
        self.capture_outputs(output, [subject], FULL)
        readback = self.launch(self.data, self.data / 'examples/inspect_sequence.py', 'processed', output, '--subject', subject)
        require('RGB (' in readback and 'mask (' in readback and 'fx missing' not in readback, 'Generated capture could not be read with its calibration and mask')
        env = {'VCI_SMPLX_MODEL': self.config['smplx_model']} if self.config.get('smplx_model') else {}
        common = ('mmlphuman', '--dataset-kind', 'processed', '--dataset-path', output, '--subjects', subject, '--no-tui')
        preview, trained = self.work / 'preview', self.work / 'trained'
        self.launch(self.avatar, *common, '--output-dir', preview, '--dry-run', '--', *self.avatar_args('mmlphuman', False), env=env)
        self.avatar_outputs(preview, 'mmlphuman', [subject], False)
        self.launch(self.avatar, *common, '--output-dir', trained, '--', *train_args, env=env,
                    timeout=self.config.get('training_timeout_seconds', 1800))
        self.avatar_outputs(trained, 'mmlphuman', [subject], True)
        return 'One raw capture yielded validated products, decoded reader output, and a trained MMLPHuman avatar.'

    def cases(self):
        yield 'documentation', 'Published command coverage', self.docs
        yield 'suite-check', 'Installed suite and launchers', self.suite_check
        yield 'conda-launcher-isolation', 'Launch from another active Conda environment', self.activated_conda
        yield 'reader-fixture', 'Documented fixture and Python API', self.reader_fixture
        for kind in DATASETS:
            yield f'reader-{kind}', f'Native reader: {kind}', lambda kind=kind: self.reader(kind)
        yield 'capture-validator', 'Deep validator positive and corruption cases', self.validate_fixture
        yield 'capture-existing', 'Existing processed output', self.captured_validation
        yield 'capture-assets', 'MAMMA asset readiness', self.assets
        yield 'capture-migration', 'Read-only cache migration report', self.migration_report
        yield 'capture-job-generator', 'Local capture job generation and preflight', self.prepare_capture
        yield 'capture-full', 'Full direct capture processing', lambda: self.capture_run('full')
        yield 'capture-full-batch', 'Capture README default batch', lambda: self.capture_run('batch-full')
        yield 'capture-baseline', 'Baseline capture processing and resume', lambda: self.capture_run('baseline')
        yield 'capture-reprocess', 'Native DNA reprocessing', self.reprocess
        yield 'capture-reprocess-confidence', 'Capture README DNA reprocessing with confidence fits', lambda: self.reprocess(True)
        yield 'capture-job-preview', 'Capture scheduler preview and preflight', lambda: self.offline_job('capture')
        yield 'capture-job', 'Capture component scheduled example', lambda: self.scheduled_capture(False)
        yield 'suite-capture-job', 'Suite generated capture job', lambda: self.scheduled_capture(True)
        yield 'device-hello-simulated', 'Local hello with simulated GPU telemetry', lambda: self.hello('synthetic')
        yield 'device-hello-gpu', 'Local hello with live NVIDIA telemetry', lambda: self.hello('gpu')
        yield 'device-preflight-negative', 'Bad checkout blocks preflight and launch', self.negative_preflight
        yield 'device-ssh-login', 'Documented noninteractive SSH login', self.operator_ssh
        yield 'device-ssh-preview', 'SSH source snapshot preview', lambda: self.offline_job('ssh')
        yield 'device-ssh-job', 'SSH source sync and hello job', lambda: self.hello('ssh')
        yield 'device-batch-preview', 'Portable multi-machine capture example', self.batch_preview
        for mode in ('preview', 'preflight', 'live'):
            yield f'site-{mode}', f'Site batch {mode}', lambda mode=mode: self.site_batches(mode)
        for method in METHODS:
            for live in (False, True):
                yield (f'avatar-{method}-' + ('train' if live else 'preview'), f'{method} ' + ('training' if live else 'preview'),
                       lambda method=method, live=live: self.avatar_run(method, live))
        yield 'avatar-thuman-config', 'THuman run-config example', lambda: self.avatar_run('mmlphuman', thuman=True)
        yield 'avatar-job-preview', 'Avatar scheduler preview and preflight', lambda: self.offline_job('avatar')
        yield 'suite-walkthrough', 'Chained raw capture to trained avatar', self.walkthrough
        yield 'avatar-job', 'Scheduled MMLPHuman training', self.scheduled_avatar
        yield 'reader-unit', 'Multiview Data documented unittest command', lambda: self.component_tests(self.data)
        yield 'capture-unit', 'Capture Processing documented unittest command', lambda: self.component_tests(self.capture)
        yield 'avatar-unit', 'Avatar Creation documented unittest command', lambda: self.component_tests(self.avatar)
        for repo, variants in {'vci-suite': ('--cli-only', '--skip-models', 'default', '--download-mamma-assets'), 'multiview-data': ('default',),
                               'capture-processing': ('--converter-only', '--skip-models', 'default', '--download-mamma-assets'), 'device-jobs': ('default',),
                               'avatar-creation': ('--cli-only', 'default', 'mmlphuman', 'animatable-gaussians', 'pig-avatar')}.items():
            for variant in variants:
                yield (f'install-{repo}-{variant.lstrip("-")}', f'Fresh {repo} installation ({variant})',
                       lambda repo=repo, variant=variant: self.install(repo, variant))

    def case_ids(self):
        return [case_id for case_id, _, _ in self.cases()]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite-root', type=Path, default=SUITE)
    parser.add_argument('--config', type=Path, help='JSON settings; omitted means run checks needing no private data or GPU')
    parser.add_argument('--output-parent', type=Path, help='Existing parent for a new owner-only report directory (default: system temp)')
    parser.add_argument('--case', action='append', help='Run only this case ID; repeat to run several')
    parser.add_argument('--list', action='store_true', help='Print case IDs and descriptions')
    parser.add_argument('--require-all', action='store_true', help='Exit 2 if any selected case is blocked')
    args = parser.parse_args()
    try:
        config = read(args.config) if args.config else {}
        if not isinstance(config, dict):
            raise ValueError('config must be a JSON object')
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    suite = args.suite_root.resolve()
    if args.list:
        runner = Runner(suite, config, Path('.'))
        for case_id, title, _ in runner.cases():
            print(f'{case_id:44} {title}')
        return 0
    os.umask(0o077)
    output = Path(tempfile.mkdtemp(prefix='vci-example-report-', dir=args.output_parent)).resolve()
    print(f'Report directory: {output}', flush=True)
    runner = Runner(suite, config, output)
    write(output / 'dataset-paths.json', {})
    write(output / 'output-paths.json', {})
    if args.case and set(args.case) - set(runner.case_ids()):
        parser.error('Unknown case IDs: ' + ', '.join(sorted(set(args.case) - set(runner.case_ids()))))
    try:
        for case_id, title, fn in runner.cases():
            if not args.case or case_id in args.case:
                runner.case(case_id, title, fn)
    except KeyboardInterrupt:
        print(f'Interrupted. Partial report: {output / "report.md"}')
        return 130
    print(f'Results: {output / "report.md"} and {output / "report.json"}')
    states = {row['status'] for row in runner.results}
    return 1 if 'FAIL' in states else 2 if args.require_all and 'BLOCKED' in states else 0


if __name__ == '__main__':
    sys.exit(main())
