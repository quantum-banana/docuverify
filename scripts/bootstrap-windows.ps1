#requires -Version 5.1

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot '_common.ps1')

$projectRoot = Get-DocuVerifyProjectRoot
$backendDirectory = Join-Path $projectRoot 'backend'
$frontendDirectory = Join-Path $projectRoot 'frontend'
$virtualEnvironmentDirectory = Join-Path $projectRoot '.venv'
$virtualEnvironmentPython = Join-Path $virtualEnvironmentDirectory 'Scripts\python.exe'

Write-Host 'Bootstrapping DocuVerify for Windows' -ForegroundColor Green
Write-Host 'No administrator access or system-wide runtime changes are required.'

if (-not (Test-Path -LiteralPath $backendDirectory -PathType Container)) {
    throw 'The backend directory is missing. Run this script from a complete DocuVerify checkout.'
}
if (-not (Test-Path -LiteralPath $frontendDirectory -PathType Container)) {
    throw 'The frontend directory is missing. Run this script from a complete DocuVerify checkout.'
}

$nodeCommand = Get-Command node.exe -CommandType Application -ErrorAction SilentlyContinue
$npmCommand = Get-Command npm.cmd -CommandType Application -ErrorAction SilentlyContinue
if ($null -eq $nodeCommand) {
    throw 'Node.js is required. Install a maintained Node.js release, then rerun this script.'
}
if ($null -eq $npmCommand) {
    throw 'npm.cmd is required. Reinstall Node.js with npm, then rerun this script.'
}

$null = & $nodeCommand.Source --version 2>$null
if ($LASTEXITCODE -ne 0) {
    throw 'node.exe was found but could not run.'
}
$null = & $npmCommand.Source --version 2>$null
if ($LASTEXITCODE -ne 0) {
    throw 'npm.cmd was found but could not run.'
}

if (Test-Path -LiteralPath $virtualEnvironmentDirectory -PathType Container) {
    if (-not (Test-Path -LiteralPath $virtualEnvironmentPython -PathType Leaf)) {
        throw 'The existing .venv is incomplete. Move it aside manually, then rerun bootstrap; the script will not overwrite it.'
    }
    Write-Host 'Using the existing project virtual environment.' -ForegroundColor Cyan
}
else {
    $basePython = Resolve-DocuVerifyPython
    if ($null -eq $basePython) {
        throw 'No usable Python interpreter was found. Install Python 3.11 or another backend-compatible 64-bit release, then rerun bootstrap.'
    }

    $basePythonVersion = Get-DocuVerifyPythonVersion -PythonCommand $basePython
    Write-Host "Creating .venv with Python $basePythonVersion ($($basePython.Label); personal path suppressed)." -ForegroundColor Cyan
    & $basePython.FilePath @($basePython.PrefixArguments) -m venv $virtualEnvironmentDirectory
    if ($LASTEXITCODE -ne 0) {
        throw "Python could not create the virtual environment (exit code $LASTEXITCODE)."
    }
}

$venvPythonCommand = [pscustomobject]@{
    FilePath        = $virtualEnvironmentPython
    PrefixArguments = [string[]]@()
    Label           = 'project virtual environment'
}
if (-not (Test-PythonCandidate -FilePath $venvPythonCommand.FilePath)) {
    throw 'The project virtual environment Python is not runnable.'
}
$venvPythonVersion = Get-DocuVerifyPythonVersion -PythonCommand $venvPythonCommand
Write-Host "Project Python: $venvPythonVersion" -ForegroundColor Cyan

$requirementFiles = New-Object 'System.Collections.Generic.List[string]'
$aggregateRequirements = Join-Path $backendDirectory 'requirements.txt'
$requirementsDirectory = Join-Path $backendDirectory 'requirements'
if (Test-Path -LiteralPath $aggregateRequirements -PathType Leaf) {
    $requirementFiles.Add($aggregateRequirements)

    # requirements.txt is the runtime/OCR aggregate. Development dependencies
    # are intentionally separate and are mandatory for run-tests.ps1.
    $developmentRequirements = Join-Path $requirementsDirectory 'dev.txt'
    if (Test-Path -LiteralPath $developmentRequirements -PathType Leaf) {
        $requirementFiles.Add($developmentRequirements)
    }
}
elseif (Test-Path -LiteralPath $requirementsDirectory -PathType Container) {
    foreach ($preferredRequirementName in @('runtime.txt', 'common.txt', 'base.txt', 'ocr.txt', 'dev.txt', 'test.txt')) {
        $preferredRequirementPath = Join-Path $requirementsDirectory $preferredRequirementName
        if ((Test-Path -LiteralPath $preferredRequirementPath -PathType Leaf) -and -not $requirementFiles.Contains($preferredRequirementPath)) {
            $requirementFiles.Add($preferredRequirementPath)
        }
    }

    foreach ($discoveredRequirement in @(Get-ChildItem -LiteralPath $requirementsDirectory -File -Filter '*.txt' | Sort-Object Name)) {
        if (-not $requirementFiles.Contains($discoveredRequirement.FullName)) {
            $requirementFiles.Add($discoveredRequirement.FullName)
        }
    }
}

if ($requirementFiles.Count -eq 0) {
    throw 'No backend requirements file was found.'
}

foreach ($requirementFile in $requirementFiles) {
    $requirementDisplayName = $requirementFile.Substring($projectRoot.Length).TrimStart('\')
    Write-Host "Installing $requirementDisplayName" -ForegroundColor Cyan
    Invoke-DocuVerifyNativeCommand -FilePath $virtualEnvironmentPython -ArgumentList @(
        '-m', 'pip', 'install', '--disable-pip-version-check', '-r', $requirementFile
    ) -FailureMessage "Dependency installation failed for $requirementDisplayName"
}

$bootstrapMarker = Join-Path $virtualEnvironmentDirectory '.docuverify-bootstrap-complete'
if (-not (Test-Path -LiteralPath $bootstrapMarker -PathType Leaf)) {
    New-Item -ItemType File -Path $bootstrapMarker | Out-Null
}

$frontendPackage = Join-Path $frontendDirectory 'package.json'
if (-not (Test-Path -LiteralPath $frontendPackage -PathType Leaf)) {
    throw 'frontend\package.json is missing.'
}

Push-Location $frontendDirectory
try {
    if (Test-Path -LiteralPath (Join-Path $frontendDirectory 'package-lock.json') -PathType Leaf) {
        Write-Host 'Installing locked frontend dependencies with npm.cmd ci' -ForegroundColor Cyan
        Invoke-DocuVerifyNativeCommand -FilePath $npmCommand.Source -ArgumentList @('ci') -FailureMessage 'Frontend dependency installation failed'
    }
    else {
        Write-Host 'No package-lock.json exists yet; creating it with npm.cmd install' -ForegroundColor Yellow
        Invoke-DocuVerifyNativeCommand -FilePath $npmCommand.Source -ArgumentList @('install') -FailureMessage 'Frontend dependency installation failed'
    }
}
finally {
    Pop-Location
}

foreach ($runtimeDirectory in @(
    (Join-Path $backendDirectory 'runtime'),
    (Join-Path $backendDirectory 'runtime\uploads'),
    (Join-Path $backendDirectory 'runtime\jobs'),
    (Join-Path $backendDirectory 'runtime\artifacts'),
    (Join-Path $backendDirectory 'runtime\logs')
)) {
    if (-not (Test-Path -LiteralPath $runtimeDirectory -PathType Container)) {
        New-Item -ItemType Directory -Path $runtimeDirectory | Out-Null
    }
}

$exampleEnvironmentFile = Join-Path $projectRoot '.env.example'
$localEnvironmentFile = Join-Path $projectRoot '.env'
if (-not (Test-Path -LiteralPath $exampleEnvironmentFile -PathType Leaf)) {
    throw '.env.example is missing.'
}
if (Test-Path -LiteralPath $localEnvironmentFile) {
    Write-Host 'Keeping the existing .env unchanged.' -ForegroundColor Cyan
}
else {
    Copy-Item -LiteralPath $exampleEnvironmentFile -Destination $localEnvironmentFile
    Write-Host 'Created .env from .env.example. Review it before adding any private values.' -ForegroundColor Cyan
}

Write-Host ''
Write-Host 'Bootstrap complete.' -ForegroundColor Green
Write-Host 'Next:'
Write-Host '  powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\diagnose-windows.ps1'
Write-Host '  powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-local.ps1'
Write-Host '  powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-tests.ps1'
