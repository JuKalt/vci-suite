# VCI Suite

> **Work in progress.** VCI Suite is under active development and updated
> regularly. APIs, configuration formats, and workflows may change.

Use this checkout to process multiview recordings, read them through a common
API, and train avatars. Device Jobs can run either workload across Linux GPUs.
The suite pins four Git submodules so the same tested versions install together.
Each component can also be installed on its own.

![RGB capture, rough and detailed color-coded segmentation, segmentation confidence, and standard SMPL-X and MHR body fits.](https://raw.githubusercontent.com/JuKalt/capture-processing/f401f8b545896923090b5f4ef91fe8670e923ded/docs/media/vci-capture-processing.gif)

*One synchronized capture: 34 color cameras, 252 frames at 25 fps (10.1 seconds). The GIF shows a 4.2-second excerpt from one camera.*

| Component | Purpose |
| --- | --- |
| [Multiview Data](multiview-data/) | Read calibrated RGB, masks and annotations from supported datasets |
| [Capture Processing](capture-processing/) | Process raw VCI captures or reprocess external datasets |
| [Device Jobs](device-jobs/) | Run processing and training jobs locally or over SSH |
| [Avatar Creation](avatar-creation/) | Train PiG-Avatar, MMLPHuman or Animatable Gaussians |

## Before you install

- **Linux with Bash, Git, and internet access.** For private checkouts, configure
  Git credentials that can read the suite and its component repositories.
- **Conda**, or `curl` and `sha256sum` for automatic Miniforge setup on Linux
  x86-64. When Conda is absent the installer creates `.miniforge` in this
  checkout without administrator access.
- **For the full GPU install:** a compatible NVIDIA GPU and driver for the
  runtimes you intend to use. Install GNU Wget if gated MAMMA or SMPL-X files
  need downloading. Obtain
  authorized [MAMMA](https://mamma.is.tue.mpg.de/) and
  [SMPL-X](https://smpl-x.is.tue.mpg.de/) accounts, and accept upstream terms
  for [SAM3D Body](https://huggingface.co/facebook/sam-3d-body-dinov3) and
  [Sapiens2](https://huggingface.co/facebook/sapiens2-seg-1b) as applicable.
  Create a [Hugging Face read token](https://huggingface.co/settings/tokens)
  that can read the gated models.

Clone and install the suite. The installer initializes the pinned submodules:

```bash
git clone https://github.com/JuKalt/vci-suite.git
cd vci-suite
./install.sh
```

The default install downloads missing models and prompts for authorized
credentials when needed. MAMMA and SMPL-X sign-in allows up to three attempts
per provider. Passwords and the Hugging Face token are entered without echo.
Set `VCI_MAMMA_ASSET_SOURCE` to reuse an authorized local asset directory.
See [Capture Processing setup](capture-processing/docs/setup.md)
for noninteractive setup. The installed SMPL-X model is shared with Avatar
Creation unless you set `VCI_SMPLX_MODEL` to another authorized file.

**PiG-Avatar is optional.** The installer skips it if the private upstream
repository is inaccessible. MMLPHuman and Animatable Gaussians still install.
If you gain access later, reuse the suite CLI environment and its SMPL-X model:

```bash
VCI_CLI_ENV="${ENV_NAME:-vci-dataset}" \
  VCI_SMPLX_MODEL="$PWD/capture-processing/external/mamma/data/body_models/smplx_locked_head/smplx/SMPLX_NEUTRAL.npz" \
  ./avatar-creation/install.sh pig-avatar
```

Use the environment name selected at suite install time and adjust the model
path if you used a custom MAMMA checkout or model. If Conda is neither on
`PATH` nor installed under the suite's `.miniforge`, set `VCI_CONDA_EXE` to
the installed Conda executable.

Use `./install.sh --cli-only` for the readers, CLI discovery, and scheduler job
previews on a CPU machine. `./install.sh --skip-models` builds the GPU runtimes
without preparing gated models and still requires the relevant hardware and source
access. To install just one avatar method, use its component installer instead.
For the local release candidates with trusted sibling clones, run
`./install.sh --local --cli-only` or `./install.sh --local` from this checkout.
Use `./check.sh` to rerun command discovery and offline scheduler previews
without reinstalling. Run it from the suite checkout you installed. If it
reports an incomplete installation, rerun the suite's `./install.sh --cli-only`
there to repair the four CLI tools without rebuilding the GPU runtimes.
Use a separate `ENV_NAME` for each suite checkout if you use more than one.
Use Device Jobs `--preflight-only` to check access to the workers in an actual
job config. The
suite installs all four CLIs into **one Conda environment**, `vci-dataset` by
default (set `ENV_NAME` before installing to change it). GPU runtimes remain
isolated. The component launchers find the installed environment without
shell activation. You can leave another Conda environment active in your
terminal: the installer and launchers do not run its deactivation hooks.
The terminal stays in the environment you started with. From the suite root:

```bash
./multiview-data/run.sh multiview-data/examples/inspect_sequence.py --help
./device-jobs/run.sh --help
./capture-processing/run.sh capture-process --help
./avatar-creation/run.sh --list-methods
```

## Examples and documentation

Start with the [capture-to-avatar walkthrough](docs/workflow.md) to run one
recording through the suite, or [test Device Jobs on one capture](docs/workflow.md#4-test-device-jobs-on-one-capture)
with your existing install.

| Component | Usage examples | Reference documentation |
| --- | --- | --- |
| [Multiview Data](multiview-data/) | [Inspect a sequence, find native IDs, and read annotations](multiview-data/examples/README.md) | [Reader contract](multiview-data/docs/contract.md) |
| [Capture Processing](capture-processing/) | [Process a subject or batch and reprocess an external dataset](capture-processing/examples/README.md) | [Setup](capture-processing/docs/setup.md) · [Output schema](capture-processing/docs/schema.md) · [Validation and resume](capture-processing/docs/operations.md) |
| [Device Jobs](device-jobs/) | [Schedule VCI capture processing, run local jobs, and send source to SSH workers](device-jobs/examples/README.md) | [Job configuration, provisioning, and recovery](device-jobs/docs/jobs.md) |
| [Avatar Creation](avatar-creation/) | [Preview and train methods, reuse a run config, and schedule subjects](avatar-creation/examples/README.md) | [Method inputs, outputs, and dataset support](avatar-creation/docs/methods.md) |

If your Git host does not follow a link into a submodule, open the component
from the first column. Its README links to the same examples and reference.

The relative submodule URLs assume all five repos live under the same
Git owner and retain these names. To publish under a different layout, update
`.gitmodules` and run `git submodule sync` before testing the hosted checkout.

## Verify the documented examples

Run the [example verification suite](docs/verification.md) to test all five
repos with your own datasets and workers. It writes one report with outcomes,
blocked prerequisites, exact command failures and links to logs.
