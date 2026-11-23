#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$root"

components=(multiview-data capture-processing device-jobs avatar-creation)
problems=()
for component in "${components[@]}"; do
  receipt="$component/.cli-conda"
  if [[ ! -x "$component/run.sh" ]]; then
    problems+=("$component: pinned component or launcher is missing")
  elif [[ ! -r "$receipt" ]]; then
    problems+=("$component: no Conda installation recorded in this checkout")
  else
    mapfile -t cli < "$receipt"
    if [[ "${#cli[@]}" -ne 2 || ! -x "${cli[0]:-}" || ! -x "${cli[1]:-}/bin/python" || ! -d "${cli[1]:-}/conda-meta" ]]; then
      problems+=("$component: recorded Conda environment is unavailable")
    elif [[ -r capture-processing/.cli-conda ]] && ! cmp -s capture-processing/.cli-conda "$receipt"; then
      problems+=("$component: recorded environment differs from the suite converter's")
    fi
  fi
done
if ((${#problems[@]})); then
  printf 'VCI Suite CLI install is incomplete in %s:\n' "$root" >&2
  printf '  %s\n' "${problems[@]}" >&2
  printf 'Run ./install.sh --cli-only from this suite root to set up all four tools. Add --local if the submodules use trusted local repos. This does not install GPU runtimes or models.\n' >&2
  exit 1
fi

./multiview-data/run.sh - <<'PY'
import importlib.util
from pathlib import Path

for module in ("multiview_data", "capture_processing", "device_jobs", "avatar_creation"):
    checkout = Path(module.replace("_", "-")).resolve()
    spec = importlib.util.find_spec(module)
    if not spec or not spec.origin or not Path(spec.origin).resolve().is_relative_to(checkout):
        origin = spec.origin if spec else "not installed"
        raise SystemExit(
            f"{module} resolves to {origin}, not to this suite's checkout at {checkout}.\n"
            "Run ./install.sh --cli-only from this suite root. If another checkout uses the same "
            "environment, give this one a distinct ENV_NAME."
        )

import cv2, h5py
PY

# Check the recorded install even if the current shell supplies a converter override.
capture=(env -u ENV_NAME -u VCI_CONDA_EXE ./capture-processing/run.sh)
"${capture[@]}" capture-process --help >/dev/null
"${capture[@]}" capture-reprocess --help >/dev/null
"${capture[@]}" capture-validate --help >/dev/null
./avatar-creation/run.sh --list-methods
# Check both job specs with the deployed scheduler and no data or GPUs.
converter_python="$("${capture[@]}" python -c 'import sys; print(sys.executable)')"
avatar_python="$(./avatar-creation/run.sh --python -c 'import sys; print(sys.executable)')"
./multiview-data/run.sh - "$converter_python" "$avatar_python" <<'PY'
import json
import subprocess
import sys
import tempfile
from pathlib import Path

with tempfile.TemporaryDirectory() as work:
    work = Path(work)
    for component, name in (("capture-processing", "processing-job"), ("avatar-creation", "avatar-job")):
        example = Path(component, "examples", name + ".json")
        spec = json.loads(example.read_text())
        spec.update(shared_run_root=str(work / "runs"), output_root=str(work / "outputs"))
        machine = spec["machines"][0]
        machine["cwd"] = str(Path(component).resolve())
        machine["python"] = sys.argv[1] if component == "capture-processing" else sys.argv[2]
        # Use the converter's installed defaults instead of the example worker's absolute path.
        if component == "capture-processing":
            command = spec["task"]["command"]
            index = command.index("--mhr-python")
            del command[index:index + 2]
        config = work / example.name
        config.write_text(json.dumps(spec))
        subprocess.run(["./device-jobs/run.sh", "--config", str(config), "--dry-run"], check=True)
PY
