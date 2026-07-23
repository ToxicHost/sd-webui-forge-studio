#!/usr/bin/env bash
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
OUT="${1:-$WORKSPACE_ROOT/Evidence/environment-report.txt}"

{
  echo "date=$(date -Iseconds 2>/dev/null || date)"
  echo "git_branch=$(git branch --show-current 2>/dev/null)"
  echo "git_commit=$(git rev-parse HEAD 2>/dev/null)"
  if [[ -n "$(git status --porcelain 2>/dev/null)" ]]; then
    echo "git_status=dirty"
  else
    echo "git_status=clean"
  fi
  echo "python=$(python --version 2>&1)"
  python - <<'PY'
try:
    import torch
    print("torch=" + str(torch.__version__))
    print("cuda_runtime=" + str(torch.version.cuda))
    print("cuda_available=" + str(torch.cuda.is_available()))
    if torch.cuda.is_available():
        print("gpu=" + torch.cuda.get_device_name(0))
        print("gpu_count=" + str(torch.cuda.device_count()))
except Exception as exc:
    print("torch_probe_error=" + repr(exc))
PY
  echo "nvidia_smi_begin"
  nvidia-smi --query-gpu=name,driver_version,memory.total \
    --format=csv,noheader,nounits 2>&1 || true
  echo "nvidia_smi_end"
} > "$OUT"

echo "Wrote $OUT"
