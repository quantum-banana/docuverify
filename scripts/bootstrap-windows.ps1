#requires -Version 5.1

[CmdletBinding()]
param(
    [string]$PythonExecutable,

    [ValidateSet('3.12', '3.13', '3.14')]
    [string]$PythonVersion = '3.12',

    [ValidateSet('auto', 'rapidocr', 'none')]
    [string]$OcrProvider = 'auto',

    [ValidateSet('cpu', 'gpu')]
    [string]$OcrDevice = 'cpu'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot '_common.ps1')

$projectRoot = Get-DocuVerifyProjectRoot
$backendDirectory = Join-Path $projectRoot 'backend'
$frontendDirectory = Join-Path $projectRoot 'frontend'
$virtualEnvironmentDirectory = Join-Path $projectRoot '.venv'
$virtualEnvironmentPython = Join-Path $virtualEnvironmentDirectory 'Scripts\python.exe'
$testedPythonBaseline = Get-DocuVerifyTestedPythonBaseline
$effectivePythonVersion = $PythonVersion
$explicitPythonCommand = $null

if (-not [string]::IsNullOrWhiteSpace($PythonExecutable)) {
    $explicitPythonCommand = Resolve-DocuVerifyPython -PythonExecutable $PythonExecutable
    if ($null -eq $explicitPythonCommand) {
        throw 'The explicitly requested Python executable is missing or unusable.'
    }

    $explicitPythonVersion = Get-DocuVerifyPythonVersion -PythonCommand $explicitPythonCommand
    if ($explicitPythonVersion -notmatch '^(\d+)\.(\d+)\.') {
        throw "The explicitly requested Python version '$explicitPythonVersion' could not be interpreted."
    }
    $explicitPythonMajorMinor = $matches[1] + '.' + $matches[2]
    if ([version]$explicitPythonMajorMinor -lt [version]'3.12') {
        throw "Python $explicitPythonMajorMinor is incompatible with the current dependency set. Use Python $testedPythonBaseline or a separately constrained and fully tested dependency set."
    }
    if ($PSBoundParameters.ContainsKey('PythonVersion') -and $explicitPythonMajorMinor -ne $PythonVersion) {
        throw "The explicit interpreter uses Python $explicitPythonMajorMinor, but -PythonVersion $PythonVersion was requested."
    }
    if (-not $PSBoundParameters.ContainsKey('PythonVersion')) {
        $effectivePythonVersion = $explicitPythonMajorMinor
    }
}

Write-Host 'Bootstrapping DocuVerify for Windows' -ForegroundColor Green
Write-Host 'No administrator access or system-wide runtime changes are required.'
Write-Host "Requested Python: $effectivePythonVersion; OCR provider: $OcrProvider; OCR device: $OcrDevice" -ForegroundColor Cyan

if ($effectivePythonVersion -ne $testedPythonBaseline) {
    Write-Warning "Python $effectivePythonVersion is an explicit non-baseline request. Python $testedPythonBaseline is the tested cross-laptop baseline; this bootstrap succeeds only if the complete selected dependency set installs."
}

if ($OcrDevice -eq 'gpu') {
    throw 'GPU OCR is not enabled by the supported Phase 2 requirements. Use -OcrDevice cpu; do not change NVIDIA drivers or CUDA to force bootstrap.'
}

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
    $basePython = if ($null -ne $explicitPythonCommand) {
        $explicitPythonCommand
    }
    else {
        Resolve-DocuVerifyPython -PreferredVersion $effectivePythonVersion
    }
    if ($null -eq $basePython) {
        throw "No usable Python $effectivePythonVersion interpreter was found. Install the official current-user Python $effectivePythonVersion release or pass -PythonExecutable with a matching interpreter."
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
if (-not $venvPythonVersion.StartsWith($effectivePythonVersion + '.', [System.StringComparison]::Ordinal)) {
    throw "The existing .venv uses Python $venvPythonVersion, but Python $effectivePythonVersion was requested. Stop DocuVerify, move the environment aside without deleting it, and rerun bootstrap."
}

$requirementFiles = New-Object 'System.Collections.Generic.List[string]'
$aggregateRequirements = Join-Path $backendDirectory 'requirements.txt'
$requirementsDirectory = Join-Path $backendDirectory 'requirements'
if (Test-Path -LiteralPath $aggregateRequirements -PathType Leaf) {
    if ($OcrProvider -eq 'none') {
        $commonRequirements = Join-Path $requirementsDirectory 'common.txt'
        if (-not (Test-Path -LiteralPath $commonRequirements -PathType Leaf)) {
            throw 'The common backend requirements file is missing.'
        }
        $requirementFiles.Add($commonRequirements)
        Write-Host 'Raster OCR dependency installation disabled by -OcrProvider none.' -ForegroundColor Yellow
    }
    else {
        $requirementFiles.Add($aggregateRequirements)
    }

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
