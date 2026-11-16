# Push feature/authentication to GitHub (org origin or your fork).
# Run from repo root:  .\scripts\push-feature-branch.ps1

$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)

$branch = 'feature/authentication'
Write-Host "Current branch:" (git branch --show-current)
Write-Host "Latest commit:" (git log -1 --oneline)

Write-Host "`nPushing to origin ($branch)..."
git push -u origin $branch
if ($LASTEXITCODE -eq 0) {
    Write-Host "OK — see: https://github.com/PoulTech-Solutions/dev-poultech-management/tree/$branch"
    exit 0
}

Write-Host "`nPush to org failed (often 403 = no write access for your GitHub login)." -ForegroundColor Yellow
Write-Host @"

Fix (choose one):

A) Org access — PoulTech-Solutions repo admin:
   Repo → Settings → Collaborators → Add Nitin-Padekar (Write)
   Then re-run this script.

B) Fork — On GitHub: open dev-poultech-management → Fork → your account
   Then run:
     git remote add fork https://github.com/YOUR_USER/dev-poultech-management.git
     git push -u fork $branch
   Open a Pull Request from your fork → PoulTech-Solutions/develop

C) Wrong GitHub user cached (Windows):
   Control Panel → Credential Manager → Windows Credentials
   Remove entries for git:https://github.com
   Push again and sign in with the account that has org access.

D) Cursor: Settings → connect GitHub with the org account, then re-run this script.

"@
exit 1