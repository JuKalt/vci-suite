# Site-specific capture and avatar batches

Keep real worker addresses and storage paths in local config files. For a
portable template, see the [Device Jobs VCI batch example](../../device-jobs/examples/README.md#process-a-vci-capture-batch-across-machines).

If you have a locally migrated batch config, run from the suite root:

```bash
./device-jobs/run.sh --config configs/distribution/vci-2026_09_17.json --dry-run
./device-jobs/run.sh --config configs/distribution/vci-2026_09_17.json --preflight-only
```

In the publication workspace, where the components are sibling checkouts,
use `../device-jobs/run.sh` from the suite root.

Site-specific batch files are kept outside version control. If your workers
share a source snapshot, set `machines[].cwd` to the Capture Processing
directory in that snapshot. If Multiview Data is staged alongside it, set
`task.env.PYTHONPATH` to its checkout. Each worker can keep using its existing
Conda interpreter and model assets. Keep the existing output root to resume
processing.

For the September 17, October 8, and October 20 batches, use the configs under
the suite's `configs/distribution/` directory. The files of the same name in
the original combined repository are for `distribute_subjects.py`. The suite
configs keep the original worker and GPU assignments and reuse the installed
runtimes; they do not run the legacy provisioning scripts.

If you change the worker source, stage a new snapshot and update both `cwd`
and `task.env.PYTHONPATH`. If you move Conda or the assets, update the
interpreter and runtime paths in the config. A dry run only renders commands.
After it passes, run with `--preflight-only` to check login, the Python helper,
and the shared checkout from each worker before launching GPU work.

## Train PiG-Avatar on the processed batches

Use the local `vci-2026_09_17-pig-avatar.json`,
`vci-2026_10_08-pig-avatar.json`, or `vci-2026_10_20-pig-avatar.json` config
from the suite root. Each config reads the corresponding capture job's
`output_root` and keeps its subject list, worker addresses, GPU IDs, and
concurrent job counts. PiG-Avatar uses its default SMPL-X training config,
with no overrides for views, frames, resolution, or training duration.

Each worker needs an installed Avatar Creation checkout at `machines[].cwd`,
with access to the private PiG-Avatar checkout, its Conda runtime, and the
licensed SMPL-X model referenced in `task.env`. If a worker's suite was
installed elsewhere, update its `cwd`. See the [Avatar Creation install
instructions](../../avatar-creation/README.md) for adding PiG-Avatar to an
existing suite install. The scheduler preflight checks worker access; it
does not verify the PiG-Avatar installation or available GPU memory.

```bash
JOB=configs/distribution/vci-2026_09_17-pig-avatar.json
./device-jobs/run.sh --config "$JOB" --dry-run
./device-jobs/run.sh --config "$JOB" --preflight-only
./device-jobs/run.sh --config "$JOB"
```

Set `JOB` to the date you want. Before starting the full batch, try a copy
of the config with one processed subject on one GPU to see how much memory
the default training settings need. Training writes to a separate output
root for each date; the manifest, runner log, and method outputs are under
`output_root/<subject>/neu-pig-smplx/`. This is the existing PiG-Avatar
output directory name.

## If workers report SSH permission denied

`Permission denied (publickey,password)` means authentication failed before
Device Jobs could access the worker checkout. The scheduler uses noninteractive
SSH, so load your existing passphrase-protected key in the same terminal that
will launch the scheduler. If you already have an SSH agent in this terminal,
skip the first command.

```bash
eval "$(ssh-agent -s)"
ssh-add ~/.ssh/id_ed25519
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes USER@HOST hostname
```

Replace `USER@HOST` with the user and host from `machines[]`. Run this check
from inside `screen` or `tmux` if that is where you will launch the scheduler.
Once SSH works, rerun `--preflight-only` to confirm that the workers can
enter the shared checkout.
