#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$root"

# Do not run deactivation hooks from the caller's Conda environment.
unset CONDA_PREFIX CONDA_SHLVL CONDA_DEFAULT_ENV

usage() {
  cat <<'HELP'
Usage: ./install.sh [--cli-only | --skip-models | --download-mamma-assets] [--local]

Install the four pinned components and their supported GPU runtimes.
--cli-only     Install the readers and CLIs without building GPU runtimes.
--skip-models  Build the GPU runtimes without downloading or preparing models.
--download-mamma-assets
               Download MAMMA and SMPL-X from upstream using authorized accounts.
--local        Allow Git to read trusted sibling repositories on this machine.
HELP
}
mode=
git_args=()
while (($#)); do
  case "$1" in
    --cli-only|--skip-models|--download-mamma-assets)
      [[ -z "$mode" ]] || { usage >&2; exit 2; }
      mode="$1" ;;
    --local) git_args=(-c protocol.file.allow=always) ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
  shift
done
if ! command -v git >/dev/null 2>&1; then
  echo 'Git is required to fetch the suite components.' >&2
  exit 1
fi
if [[ -n "${VCI_CONDA_EXE:-}" ]]; then
  conda_base="$("$VCI_CONDA_EXE" info --base)"
  export PATH="$conda_base/bin:$PATH"
elif ! command -v conda >/dev/null 2>&1 && [[ ! -x "$root/.miniforge/bin/conda" ]]; then
  if [[ "$(uname -s)/$(uname -m)" != Linux/x86_64 ]] ||
     ! command -v curl >/dev/null 2>&1 || ! command -v sha256sum >/dev/null 2>&1; then
    echo 'Install Miniforge or Miniconda first (automatic bootstrap needs Linux x86-64, curl and sha256sum).' >&2
    exit 1
  fi
  (
    scratch="$(mktemp -d)"
    trap 'rm -f -- "$scratch/Miniforge.sh"; rmdir -- "$scratch"' EXIT
    echo 'Installing a local Miniforge (no administrator access required).'
    curl --fail --silent --show-error --location \
      https://github.com/conda-forge/miniforge/releases/download/26.9.4-2/Miniforge3-Linux-x86_64.sh \
      --output "$scratch/Miniforge.sh"
    printf '%s  %s\n' bbaf16b3897da24c4be50315934ba74848f617b4495982841d6a0a60e0edbbdf \
      "$scratch/Miniforge.sh" | sha256sum --check --status
    bash "$scratch/Miniforge.sh" -b -p "$root/.miniforge"
  )
fi
if [[ -z "${VCI_CONDA_EXE:-}" && -x "$root/.miniforge/bin/conda" ]]; then
  export PATH="$root/.miniforge/bin:$PATH"
fi
# Local file permission applies only to the four declared component checkouts.
git "${git_args[@]}" submodule update --init --checkout
export ENV_NAME="${ENV_NAME:-vci-dataset}"
export VCI_CLI_ENV="$ENV_NAME"
export MULTIVIEW_DATA_CHECKOUT="$root/multiview-data"
./multiview-data/install.sh
./device-jobs/install.sh
case "$mode" in
  --cli-only)
    INSTALL_TORCH=0 ./capture-processing/install.sh --converter-only
    ./avatar-creation/install.sh --cli-only ;;
  --skip-models)
    ./capture-processing/install.sh --skip-models
    ./avatar-creation/install.sh ;;
  ''|--download-mamma-assets)
    capture_args=()
    if [[ "$mode" == --download-mamma-assets ]]; then
      capture_args+=("$mode")
    fi
    ./capture-processing/install.sh "${capture_args[@]}"
    mamma_repo="${MAMMA_REPO_PATH:-${MAMMA_SUBMODULE_PATH:-$root/capture-processing/external/mamma}}"
    [[ "$mamma_repo" = /* ]] || mamma_repo="$root/capture-processing/$mamma_repo"
    model="$mamma_repo/data/body_models/smplx_locked_head/smplx/SMPLX_NEUTRAL.npz"
    export VCI_SMPLX_MODEL="${VCI_SMPLX_MODEL:-$model}"
    [[ "$VCI_SMPLX_MODEL" = /* ]] || VCI_SMPLX_MODEL="$root/$VCI_SMPLX_MODEL"
    [[ -s "$VCI_SMPLX_MODEL" ]] || { echo "SMPL-X model missing: $VCI_SMPLX_MODEL" >&2; exit 1; }
    ./avatar-creation/install.sh ;;
esac
./check.sh
printf '\nSuite CLI environment: %s. Use the component run.sh commands from the suite root.\n' "$ENV_NAME"
