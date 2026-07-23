param(
    [string]$Output
)

$ErrorActionPreference = "Continue"

if (-not $Output) {
    $workspaceRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
    $Output = Join-Path $workspaceRoot "Evidence\environment-report.txt"
}

$lines = New-Object System.Collections.Generic.List[string]
$lines.Add("date=$((Get-Date).ToString('o'))")
$lines.Add("git_branch=$(git branch --show-current)")
$lines.Add("git_commit=$(git rev-parse HEAD)")
$gitStatus = git status --porcelain
$lines.Add("git_status=$(if ($gitStatus) { 'dirty' } else { 'clean' })")
$lines.Add("python=$(python --version 2>&1)")

$probe = @'
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
'@

$lines.Add(($probe | python - | Out-String))
$lines.Add("nvidia_smi_begin")
$lines.Add((nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader,nounits 2>&1 | Out-String))
$lines.Add("nvidia_smi_end")
$lines | Set-Content -Encoding UTF8 $Output
Write-Host "Wrote $Output"
