#!/usr/bin/env python3
"""Prepare a one-subject Device Jobs config from an existing VCI Suite install."""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('raw_root', type=Path, help='Parent directory of the raw recordings')
    parser.add_argument('subject', help='Name of one recording directory')
    parser.add_argument('job_root', type=Path, help='Directory for job.json, run state and outputs')
    parser.add_argument('--suite-root', type=Path, default=Path.cwd(), help='Suite checkout (default: current directory)')
    parser.add_argument('--env-name', default=os.environ.get('ENV_NAME'), help='Override the installed converter Conda environment')
    parser.add_argument('--conda', type=Path, help='Path to Conda if it is outside the suite and missing from PATH')
    parser.add_argument('--gpu', default='0', help='GPU index or UUID (default: 0)')
    args = parser.parse_args()

    suite = args.suite_root.expanduser().resolve()
    capture = suite / 'capture-processing'
    if not (suite / 'device-jobs/device_jobs/controller.py').is_file():
        parser.error(f'Device Jobs source is missing in {suite}; initialize the suite submodules.')
    if not (capture / 'scripts/datasets/process_vci_subject.py').is_file():
        parser.error(f'Capture Processing checkout not found in {suite}.')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', args.subject):
        parser.error('Use a recording directory name containing only letters, numbers, underscores, dots or hyphens.')
    if not re.fullmatch(r'[0-9]+|GPU-[A-Za-z0-9-]+', args.gpu):
        parser.error('--gpu must be a GPU index or full GPU UUID.')
    raw = args.raw_root.expanduser().resolve()
    recording = raw / args.subject
    if not (recording / 'calibration_dome.json').is_file() or not any(p.is_dir() for p in recording.glob('frame_*')):
        parser.error(f'Expected calibration_dome.json and frame_* directories in {recording}.')

    local_conda = suite / '.miniforge/bin/conda'
    installed = capture / '.cli-conda'
    try:
        receipt = installed.read_text().splitlines() if installed.is_file() else []
        if receipt and len(receipt) != 2:
            parser.error(f'Invalid converter environment record: {installed}.')
        conda = args.conda or os.environ.get('VCI_CONDA_EXE') or (receipt[0] if receipt else
            local_conda if local_conda.is_file() else shutil.which('conda') or os.environ.get('CONDA_EXE'))
        if not conda:
            parser.error('Conda was not found. Pass --conda /path/to/your/conda/bin/conda.')
        conda = Path(conda).expanduser().resolve()
        conda_base = Path(subprocess.check_output([conda, 'info', '--base'], text=True).strip())
        env_args = ['-n', args.env_name] if args.env_name else ['-p', receipt[1]] if receipt else ['-n', 'vci-dataset']
        converter = Path(subprocess.check_output(
            [conda, 'run', *env_args, 'python', '-c', 'import sys; print(sys.executable)'], text=True,
        ).strip())
    except (OSError, subprocess.CalledProcessError) as exc:
        parser.error(f'Could not find the installed converter environment: {exc}')
    if not converter.is_absolute() or not converter.is_file():
        parser.error(f'Conda did not return an installed Python interpreter: {converter}')

    job = args.job_root.expanduser().resolve()
    worker_path = os.pathsep.join(dict.fromkeys((str(conda_base / 'bin'), str(converter.parent), *os.get_exec_path())))
    config = {
        'schema_version': 1,
        'shared_run_root': str(job / 'runs'),
        'output_root': str(job / 'outputs'),
        'subjects': [args.subject],
        'task': {
            'command': [
                '{python}', 'scripts/datasets/process_vci_subject.py', str(raw).replace('{', '{{').replace('}', '}}'),
                '{subject}', '{output_dir}', '--', '--workers', '4', '--mhr-source', 'none',
                '--skip-smplx-to-mhr', '--no-semantic-parsing', '--no-mamma-confidence-fit',
            ],
            'env': {'PATH': worker_path.replace('{', '{{').replace('}', '}}')},
            'output_layout': 'root',
        },
        'machines': [{
            'name': 'local', 'host': 'localhost', 'transport': 'local', 'cwd': str(capture),
            'bootstrap_python': sys.executable, 'python': str(converter),
            'gpus': [{'id': args.gpu, 'workers': 1}],
        }],
    }
    destination = job / 'job.json'
    try:
        job.mkdir(parents=True, exist_ok=True)
        with destination.open('x', encoding='utf-8') as stream:
            json.dump(config, stream, indent=2)
            stream.write('\n')
    except OSError as exc:
        parser.error(f'Could not create {destination}: {exc}')
    print(destination)


if __name__ == '__main__':
    main()
