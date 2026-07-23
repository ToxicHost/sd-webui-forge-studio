param(
    [string]$UpstreamBranch = "neo"
)

$ErrorActionPreference = "Stop"
$status = git status --porcelain
if ($status) {
    throw "Working tree is not clean. Commit or stash changes first."
}

$dateTag = Get-Date -Format "yyyyMMdd"
$branch = "integration/upstream-$dateTag"

git fetch upstream
git switch -c $branch
git merge --no-ff "upstream/$UpstreamBranch"

Write-Host ""
Write-Host "Merge created on $branch."
Write-Host "Now follow docs/05_UPSTREAM_SYNC_PLAYBOOK.md."
