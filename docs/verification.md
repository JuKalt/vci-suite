# Verify the suite examples

Run these tests from the root of the installed suite. Use a small recording or
sequence first. The runner uses only Python's standard library and calls the
installed component launchers, so there is no environment to activate. You
can leave your current Conda environment active. The default run includes a
regression test with a deliberately failing deactivation hook, like the
compiler hook that can emit `This cross-compiler package contains no program`
before a VCI command starts.

```bash
python3 tests/run_examples.py
python3 tests/run_examples.py --list
cp tests/example_config.json "$HOME/vci-example-config.json"
# Edit the copy. Remove entries for datasets you do not have.
python3 tests/run_examples.py --config "$HOME/vci-example-config.json"
```

The first command exercises the installed CLI, the documented reader fixture
and Python API, media validation (including damaged input), local scheduler
execution and state preservation with simulated GPU telemetry, and job previews and
preflight. It records unavailable live workloads as `BLOCKED`. Preview tests
never count as successful execution of a GPU or SSH job.

Choose individual cases with repeatable `--case`. For example, after setting
`datasets.dna` and `validate_processed`:

```bash
python3 tests/run_examples.py --config "$HOME/vci-example-config.json" \
  --case reader-dna --case capture-existing --require-all
```

Use absolute paths in the config and set only the options for the cases you
want. The runner writes generated data, rendered job configurations, and logs
under a new private report directory. Choose its parent with `--output-parent`
if a case needs more disk space than the system temp filesystem provides.

| What to verify | Settings and behavior checked |
| --- | --- |
| All seven readers | Set each `datasets.<kind>` with `root`, native `subject`, and optional `cameras`, `frame` and `annotations`. Checks exact camera/frame selection, decoded RGB, mask presence and calibration. Set `require_mask: false` only for a stream that has no masks. For DNA, supply `sample_code` if needed. Set `trusted_pickles: true` for 4D-DRESS only after verifying the source. |
| Existing processed output | Set `validate_processed.root`, optional `products`, and `batch: true` for a root containing multiple subjects. Checks the requested products with deep validation. |
| MAMMA and cache inspection | Set `check_assets: true`, optional `mamma_repo` for the asset check, and optional `migration_root` for an existing processed root. The report-only migration also requires the MAMMA checkout. It checks that the input files were not changed. |
| Direct and scheduled capture | Set `capture.root`, `capture.subjects`, and `live_capture: true`; optionally set `workers`, `mamma_python`, `mhr_python`, `mamma_workers`, `mhr_workers`, `resize_long_edge`, `drop_cameras`, `semantic_model`, `semantic_python` and `semantic_working_long_edge` inside `capture`. Set `local_gpu` to an object such as `{"id": 0}` for scheduled cases. Tests default stages, the baseline stage selection and quick resume. Requires the installed capture runtimes and models. |
| Native DNA reprocessing | Set `dna_reprocess` with `root`, `subject`, `cameras`, and increasing integer `frames`; optionally set `sample_code` and override the capture runtime and worker settings. Camera exclusions from the raw capture do not carry over. Enable `live_capture`. Verifies the processed output and the original camera, frame and subject IDs in its manifest. |
| Avatar preview and training | Set `avatar_input.root` and `avatar_input.subjects`, and optionally `smplx_model`. Add installed methods to `preview_avatar`, e.g. `["mmlphuman", "animatable-gaussians"]`. The THuman preview uses `datasets.thuman`. Set `live_avatar` to a map of method names to adapter argument arrays. Choose short workloads with the stopping and save controls of that method; the harness also enforces `training_timeout_seconds`. Checks manifest selection and, for training, a runner log and saved model output. The scheduled MMLPHuman case needs at least two subjects and `local_gpu`. Only configure PiG-Avatar if you have its source and assets. |
| Complete capture-to-avatar walkthrough | Enable `live_capture` and `live_walkthrough`, configure capture inputs, and set `live_avatar.mmlphuman`. Reads the actual newly processed subject before previewing and training the avatar from it. |
| Local NVIDIA scheduler | Set `local_gpu.id` to a GPU index or UUID. The hello example runs twice, verifies both output files and checks a relaunch retains existing output state. Device Jobs launches the task again; the workload is responsible for resuming from its saved state. |
| SSH source sync | Set `ssh_worker` to an edited machine object from `device-jobs/examples/ssh-job.json` with `dedicated_checkout: true`, and set `ssh_shared_scratch` to a writable absolute path on shared storage. Use a disposable Git working tree as worker `cwd`; source sync writes tracked example files there. Have noninteractive SSH and trusted host keys set up before running. |
| Site batches | Set `site_batches` to a list of objects with `config` (path to an existing Device Jobs config), `workload` (`capture` by default or `avatar`), optional `method` for avatar jobs, `subjects` (a subset required for live runs), and optional `shared_scratch` (required for remote live jobs). Set `site_preflight: true` to contact the configured workers without launching tasks. Set `live_site_batches` and the corresponding live capture or avatar setting to launch these workloads. Bound any avatar settings in the site config itself. Use reviewed configs whose commands honor the configured output root and whose worker setup steps are safe to run. The harness replaces the original output and run roots. |
| Documented component development tests | Set `test_python.capture-processing` and `test_python.avatar-creation` to absolute interpreter paths for environments with the corresponding `test` extras. The CLI-only suite intentionally does not install them. |

If a live capture fails inside MAMMA with a Conda compiler hook error, set
`capture.mamma_python` to the absolute path of the installed MAMMA
environment’s Python. MAMMA starts its own Conda commands for individual
stages; the component launcher can succeed before one of those nested commands
fails. DNA reprocessing inherits this interpreter setting.

### Test an installation from scratch

Use `fresh_install` to opt into individual installer modes. For example:

```json
{
  "fresh_install": {
    "vci-suite:--cli-only": {"conda_exe": "/opt/miniforge/bin/conda"},
    "multiview-data:default": {"conda_exe": "/opt/miniforge/bin/conda"},
    "device-jobs:default": {"conda_exe": "/opt/miniforge/bin/conda"},
    "capture-processing:--converter-only": {"conda_exe": "/opt/miniforge/bin/conda", "install_torch": false},
    "avatar-creation:--cli-only": {"conda_exe": "/opt/miniforge/bin/conda"}
  }
}
```

```bash
python3 tests/run_examples.py --config "$HOME/vci-example-config.json" \
  --case install-vci-suite-cli-only --require-all
```

The test clones the tracked revision rather than copying installed files. It
uses unique Conda environment directories and an empty package cache and
checks the recorded install without relying on Conda being on `PATH`. The
suite pins its components; use `local_components: false` in the install mode
options to use the declared component URLs and configured Git access. For
standalone Capture Processing or Avatar Creation, `local_dependency: false`
uses the configured package index instead of a fresh sibling clone. In the
converter-only case, `install_torch: false` opts into a lightweight CLI install;
omit it to test the installer defaults. Uncommitted
source edits are tested by the ordinary workflow cases, not by install cases.

Use `--list` to see the optional full installer and individual avatar method
cases. Run them on a machine with the required disk, network and GPU runtime
prerequisites. For an authorized interactive download, enable the suite or
capture `--download-mamma-assets` install mode with `interactive: true` and
run the selected case in a terminal. Answer prompts directly; do not put
credentials in the config. The report records the installer exit and subsequent
asset check; it intentionally does not capture the interactive transcript.

### Read the result

Each run prints the report directory. `report.md` is the consolidated report:
observed results, specific failures, blocked prerequisites, links to command
logs, and a map from published command locations to test results. `report.json`
contains the same case outcomes and command exit codes for automation. These
files can contain dataset paths and machine names; keep them private.

- Exit **0**: no selected case failed. Read the report for `BLOCKED` cases.
- Exit **1**: at least one selected test failed.
- Exit **2** with `--require-all`: no test failed, but a prerequisite blocked a
  selected test. The CLI also uses exit 2 for argument errors.

The `documentation` case tracks shell and Python code blocks and recognized
inline commands in the public READMEs, examples, and guides. When a documented
command changes, review the behavior test and update its entry in
`tests/example_coverage.json`. The historic combined-repository command is
covered through its replacement workflow; SSH key and account setup remain
operator steps verified by a subsequent login or asset check.
