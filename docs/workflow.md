# From capture to avatar

For a new checkout, use the [install instructions](../README.md#before-you-install).
If you only want to check the command-line tools without a dataset or GPU,
run this from the VCI Suite root:

```bash
./install.sh --cli-only
```

If you already installed the suite, just run `./check.sh`. The checks cover
reader imports, CLI discovery and scheduler previews; they do not verify
GPU runtimes, model access or SSH. Run both commands from the same suite
checkout. Installing just `./multiview-data/install.sh` only sets up the
standalone reader and does not repair the suite. To repair an incomplete CLI
install without rebuilding the GPU runtimes, use the suite's
`./install.sh --cli-only`. Set a distinct `ENV_NAME` if you maintain multiple
suite checkouts with editable installs. For an actual
reader example without a dataset, use the
[generated fixture](../multiview-data/examples/README.md#try-the-reader-without-downloading-a-dataset)
from the Multiview Data checkout.

For the full workflow, start in the VCI Suite checkout after running
`./install.sh` with the [full install prerequisites](../README.md#before-you-install).
The suite launchers use the installed Conda environment automatically. You
can run the walkthrough from a terminal that already has another Conda
environment active; the launchers leave that terminal unchanged.
The walkthrough uses one recording first so you can review its output before
running a batch. Replace the paths and subject with your own:

```bash
SUITE_ROOT="$PWD"
RAW_ROOT=/data/raw-captures
PROCESSED_ROOT=/data/processed
AVATAR_ROOT=/data/avatars
SUBJECT=example-subject
```

`$RAW_ROOT/$SUBJECT` must contain `calibration_dome.json` and `frame_*`
directories. Use writable paths for the processed data and avatar outputs.

## 1. Process and validate a recording

```bash
(
  cd "$SUITE_ROOT/capture-processing"
  ./run.sh python scripts/datasets/process_vci_subject.py \
    "$RAW_ROOT" "$SUBJECT" "$PROCESSED_ROOT" -- --workers 4
  ./run.sh capture-validate --subject-root "$PROCESSED_ROOT/$SUBJECT" --deep \
    --require media smplx smplx_confidence mhr mhrfromsmplx metrics semantics visualizations
)
```

This runs the default capture stages, including MAMMA fits, SAM3D and official
SMPL-X-to-MHR conversion, metrics, and Sapiens2 labels. The validator checks
the requested products and returns a nonzero status for missing or invalid
ones. If you change the stages you run, adjust
`--require` accordingly. For an existing external dataset, start with the
[reprocessing example](../capture-processing/examples/README.md#reprocess-part-of-a-native-dataset)
instead. See [capture setup](../capture-processing/docs/setup.md) for models
and [operations](../capture-processing/docs/operations.md) for resume behavior.

## 2. Inspect the processed data

```bash
"$SUITE_ROOT/multiview-data/run.sh" \
  "$SUITE_ROOT/multiview-data/examples/inspect_sequence.py" \
  processed "$PROCESSED_ROOT" --subject "$SUBJECT"
```

Check that the selected subject has calibrated views, usable masks and enough
shared frames for the avatar method. Consult the
[reader examples](../multiview-data/examples/README.md) for native IDs and
annotations, and the [reader contract](../multiview-data/docs/contract.md) for
camera and mask conventions.

## 3. Preview an avatar run, then train

```bash
(
  cd "$SUITE_ROOT/avatar-creation"
  ./run.sh mmlphuman --dataset-kind processed \
    --dataset-path "$PROCESSED_ROOT" --subjects "$SUBJECT" \
    --output-dir "$AVATAR_ROOT" --no-tui --dry-run -- \
    --views 4 --time-steps 16 --resolution 512
)
```

Match the view and frame counts to the actual capture. This preview requires
an installed method and a usable input dataset. It writes an input manifest
and summary (`dataset_manifest.json` and `run_summary.log`) under
`$AVATAR_ROOT/mmlphuman/`. It does not launch the adapter or test GPU training.
Remove `--dry-run` to train and look for `runner.log` in that directory.
The full suite installer installs MMLPHuman by default;
choose `pig-avatar` only if you installed it and have access to its upstream.
Options after `--` belong to the method. To reuse the same input settings or
switch to PiG-Avatar or Animatable Gaussians, see the
[avatar examples](../avatar-creation/examples/README.md) and
[method guide](../avatar-creation/docs/methods.md).

## 4. Test Device Jobs on one capture

Run from the **VCI Suite root** after a full install. Use the component
launcher, `./device-jobs/run.sh`. The converter stays in its existing Conda
environment. No reinstallation or shell activation is needed.

```bash
JOB_ROOT="$HOME/vci-device-jobs-test"

./capture-processing/run.sh python examples/prepare_capture_job.py \
  "$RAW_ROOT" "$SUBJECT" "$JOB_ROOT" --gpu 0
./device-jobs/run.sh --config "$JOB_ROOT/job.json" --dry-run
./device-jobs/run.sh --config "$JOB_ROOT/job.json" --preflight-only
```

Use the `RAW_ROOT` and `SUBJECT` set above and change `--gpu 0` if needed. The
helper uses the recorded converter environment and writes a baseline MAMMA job
for one local GPU. It does not overwrite an existing config. Reuse that config
to resume, or choose a new `JOB_ROOT` for a separate test. The dry run shows the
rendered command; preflight checks the worker's Python helper and checkout.
When both pass and a GPU is available, launch:

```bash
./device-jobs/run.sh --config "$JOB_ROOT/job.json"
```

The live job needs the MAMMA runtime and assets. It omits the optional MHR,
confidence-fit and semantic stages, so validate its output accordingly. Find
logs under `$JOB_ROOT/runs` and data under `$JOB_ROOT/outputs/$SUBJECT`.

Device Jobs can also run from source with Python 3.10 or newer when this
checkout has no installed scheduler.

## 5. Move from one subject to scheduled jobs

For an example that migrates the old `distribute_subjects.py` command and shows
a local GPU plus an SSH worker, see the [Device Jobs examples](../device-jobs/examples/README.md).
For the local VCI batch configs, see [site-specific capture batches](../configs/distribution/README.md).

Use separate configs for processing and training. For a single-worker template,
start with the
[processing job](../capture-processing/examples/processing-job.json) or
[avatar job](../avatar-creation/examples/avatar-job.json). Replace the sample
paths and IDs; use distinct run and output roots for the two workloads. Each
machine needs access to the workload code at its configured `cwd`, the right
interpreter and assets, and the shared run and output paths. An accessible
shared checkout also works. Use a subset of subjects first:

```bash
"$SUITE_ROOT/device-jobs/run.sh" \
  --config /path/to/your-job.json --dry-run
"$SUITE_ROOT/device-jobs/run.sh" \
  --config /path/to/your-job.json --preflight-only
```

The preview is offline. Preflight checks SSH, the Python helper and `cwd` on
each worker before a batch can start. It does not verify the GPU or workload
dependencies. If SSH rejects your key, load it in the launcher terminal as
shown in the [SSH troubleshooting notes](../configs/distribution/README.md#if-workers-report-ssh-permission-denied).
When both checks pass, run without either flag to start. For a first remote test, try the
[SSH hello job example](../device-jobs/examples/README.md#send-selected-python-source-to-an-ssh-worker)
before scheduling training or processing. See the
[Device Jobs reference](../device-jobs/docs/jobs.md) for setup, shared paths
and retries.

To run these workflows as tests and collect one report, see
[Verify the suite examples](verification.md). The chained walkthrough verifies
that the avatar receives the processed output produced by the same test run.
