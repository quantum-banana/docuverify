#requires -Version 5.1

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot '_common.ps1')

$projectRoot = Get-DocuVerifyProjectRoot
$requiredFailures = New-Object 'System.Collections.Generic.List[string]'

function Write-DiagnosticSection {
    param([string]$Title)
    Write-Host ''
    Write-Host "== $Title ==" -ForegroundColor Cyan
}

function Write-DiagnosticLine {
    param(
        [string]$Label,
        [string]$Value
    )
    Write-Host ('{0,-28} {1}' -f ($Label + ':'), $Value)
}

function Get-SafeConfigurationValue {
    param(
        [string]$Name,
        [string]$DefaultValue
    )

    $environmentValue = [Environment]::GetEnvironmentVariable($Name, 'Process')
    if (-not [string]::IsNullOrWhiteSpace($environmentValue)) {
        return $environmentValue.Trim()
    }

    $localEnvironmentFile = Join-Path $projectRoot '.env'
    if (Test-Path -LiteralPath $localEnvironmentFile -PathType Leaf) {
        $matchingLine = Get-Content -LiteralPath $localEnvironmentFile | Where-Object {
            $_ -match ('^\s*' + [regex]::Escape($Name) + '\s*=')
        } | Select-Object -First 1

        if ($null -ne $matchingLine) {
            return (($matchingLine -split '=', 2)[1]).Trim()
        }
    }

    return $DefaultValue
}

function Test-PythonImport {
    param(
        [object]$PythonCommand,
        [string]$ModuleName
    )

    try {
        $null = & $PythonCommand.FilePath @($PythonCommand.PrefixArguments) -c "import importlib; importlib.import_module('$ModuleName')" 2>$null
        return ($LASTEXITCODE -eq 0)
    }
    catch {
        return $false
    }
}

Write-Host 'DocuVerify Windows diagnostics' -ForegroundColor Green
Write-DiagnosticLine -Label 'Project' -Value 'docuverify (personal path suppressed)'
Write-DiagnosticLine -Label 'PowerShell' -Value $PSVersionTable.PSVersion.ToString()

Write-DiagnosticSection -Title 'Operating system and hardware'
$windowsVersion = [Environment]::OSVersion.Version
Write-DiagnosticLine -Label 'Windows version' -Value $windowsVersion.ToString()
try {
    $windowsRecord = Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
    $windowsEdition = $windowsRecord.ProductName
    if ($windowsEdition -like 'Windows 10*' -and $windowsVersion.Build -ge 22000) {
        # Older registry ProductName values may retain "Windows 10" on Windows 11.
        $windowsEdition = $windowsEdition -replace '^Windows 10', 'Windows 11'
    }
    Write-DiagnosticLine -Label 'Windows edition' -Value $windowsEdition
}
catch {
    Write-DiagnosticLine -Label 'Windows edition' -Value 'unavailable'
}
Write-DiagnosticLine -Label 'Architecture' -Value $env:PROCESSOR_ARCHITECTURE

$processorName = $null
try {
    $processorRecord = Get-ItemProperty -LiteralPath 'HKLM:\HARDWARE\DESCRIPTION\System\CentralProcessor\0'
    $processorName = $processorRecord.ProcessorNameString.Trim()
}
catch {
    $processorName = $env:PROCESSOR_IDENTIFIER
}
Write-DiagnosticLine -Label 'Processor' -Value $processorName
Write-DiagnosticLine -Label 'Logical processors' -Value $env:NUMBER_OF_PROCESSORS

try {
    Add-Type -AssemblyName Microsoft.VisualBasic
    $computerInfo = [Microsoft.VisualBasic.Devices.ComputerInfo]::new()
    $totalMemoryGiB = [math]::Round($computerInfo.TotalPhysicalMemory / 1GB, 2)
    $availableMemoryGiB = [math]::Round($computerInfo.AvailablePhysicalMemory / 1GB, 2)
    Write-DiagnosticLine -Label 'Physical memory' -Value "$totalMemoryGiB GiB observed"
    Write-DiagnosticLine -Label 'Memory available now' -Value "$availableMemoryGiB GiB"
}
catch {
    Write-DiagnosticLine -Label 'Physical memory' -Value "unavailable ($($_.Exception.Message))"
}

try {
    $workspaceDriveRoot = [System.IO.Path]::GetPathRoot($projectRoot)
    $workspaceDrive = [System.IO.DriveInfo]::new($workspaceDriveRoot)
    $driveTotalGiB = [math]::Round($workspaceDrive.TotalSize / 1GB, 2)
    $driveFreeGiB = [math]::Round($workspaceDrive.AvailableFreeSpace / 1GB, 2)
    Write-DiagnosticLine -Label 'Workspace volume' -Value "$driveFreeGiB GiB free of $driveTotalGiB GiB"
}
catch {
    Write-DiagnosticLine -Label 'Workspace volume' -Value "unavailable ($($_.Exception.Message))"
}

$nvidiaCommand = Get-Command nvidia-smi.exe -CommandType Application -ErrorAction SilentlyContinue
$gpuDetected = $false
if ($null -eq $nvidiaCommand) {
    Write-DiagnosticLine -Label 'NVIDIA GPU' -Value 'not detected (optional for Phase 1)'
}
else {
    try {
        $gpuRows = & $nvidiaCommand.Source '--query-gpu=name,memory.total,driver_version' '--format=csv,noheader,nounits' 2>$null
        if ($LASTEXITCODE -eq 0 -and $gpuRows) {
            $gpuDetected = $true
            foreach ($gpuRow in $gpuRows) {
                $gpuParts = $gpuRow -split ',' | ForEach-Object { $_.Trim() }
                if ($gpuParts.Count -ge 3) {
                    Write-DiagnosticLine -Label 'NVIDIA GPU' -Value $gpuParts[0]
                    Write-DiagnosticLine -Label 'GPU memory' -Value ($gpuParts[1] + ' MiB')
                    Write-DiagnosticLine -Label 'NVIDIA driver' -Value $gpuParts[2]
                }
            }
        }

        $nvidiaSummary = (& $nvidiaCommand.Source 2>&1 | Out-String)
        $cudaMatch = [regex]::Match($nvidiaSummary, 'CUDA(?: UMD)? Version:\s*([0-9.]+)')
        if ($cudaMatch.Success) {
            Write-DiagnosticLine -Label 'CUDA compatibility' -Value ($cudaMatch.Groups[1].Value + ' (driver-reported, not a toolkit check)')
        }
    }
    catch {
        Write-DiagnosticLine -Label 'NVIDIA GPU' -Value "query failed ($($_.Exception.Message)); GPU remains optional"
    }
}

Write-DiagnosticSection -Title 'Toolchain'
$gitCommand = Get-Command git.exe -CommandType Application -ErrorAction SilentlyContinue
if ($null -eq $gitCommand) {
    Write-DiagnosticLine -Label 'Git' -Value 'missing (repository tooling unavailable; runtime can still start)'
}
else {
    $gitVersion = (& $gitCommand.Source --version 2>$null) -join ' '
    Write-DiagnosticLine -Label 'Git' -Value $gitVersion.Trim()
}

$githubCommand = Get-Command gh.exe -CommandType Application -ErrorAction SilentlyContinue
if ($null -eq $githubCommand) {
    Write-DiagnosticLine -Label 'GitHub CLI' -Value 'missing (optional)'
}
else {
    $githubVersion = ((& $githubCommand.Source --version 2>$null | Select-Object -First 1) -join ' ').Trim()
    Write-DiagnosticLine -Label 'GitHub CLI' -Value $githubVersion
    $githubAuthenticated = $false
    try {
        $null = & $githubCommand.Source auth status 2>&1
        $githubAuthenticated = ($LASTEXITCODE -eq 0)
    }
    catch {
        $githubAuthenticated = $false
    }
    if ($githubAuthenticated) {
        Write-DiagnosticLine -Label 'GitHub authentication' -Value 'authenticated (account details suppressed)'
    }
    else {
        Write-DiagnosticLine -Label 'GitHub authentication' -Value 'not authenticated (optional)'
    }
}

$pythonCommand = Resolve-DocuVerifyPython -IncludeProjectVenv
if ($null -eq $pythonCommand) {
    Write-DiagnosticLine -Label 'Python' -Value 'no usable interpreter found'
    $requiredFailures.Add('A runnable Python interpreter is required.')
}
else {
    $pythonVersion = Get-DocuVerifyPythonVersion -PythonCommand $pythonCommand
    Write-DiagnosticLine -Label 'Python' -Value "$pythonVersion via $($pythonCommand.Label); path suppressed"
}

$nodeCommand = Get-Command node.exe -CommandType Application -ErrorAction SilentlyContinue
if ($null -eq $nodeCommand) {
    Write-DiagnosticLine -Label 'Node.js' -Value 'missing'
    $requiredFailures.Add('Node.js is required.')
}
else {
    try {
        $nodeVersion = ((& $nodeCommand.Source --version 2>$null) -join '').Trim()
        if ($LASTEXITCODE -ne 0) { throw 'node.exe returned a failure status' }
        Write-DiagnosticLine -Label 'Node.js' -Value $nodeVersion
    }
    catch {
        Write-DiagnosticLine -Label 'Node.js' -Value "unusable ($($_.Exception.Message))"
        $requiredFailures.Add('A runnable Node.js installation is required.')
    }
}

# npm.cmd is intentional: npm.ps1 can be blocked by Windows execution policy.
$npmCommand = Get-Command npm.cmd -CommandType Application -ErrorAction SilentlyContinue
if ($null -eq $npmCommand) {
    Write-DiagnosticLine -Label 'npm' -Value 'npm.cmd missing'
    $requiredFailures.Add('npm.cmd is required.')
}
else {
    try {
        $npmVersion = ((& $npmCommand.Source --version 2>$null) -join '').Trim()
        if ($LASTEXITCODE -ne 0) { throw 'npm.cmd returned a failure status' }
        Write-DiagnosticLine -Label 'npm' -Value "$npmVersion (npm.cmd)"
    }
    catch {
        Write-DiagnosticLine -Label 'npm' -Value "unusable ($($_.Exception.Message))"
        $requiredFailures.Add('A runnable npm.cmd is required.')
    }
}

Write-DiagnosticSection -Title 'Backend imports and OCR'
$embeddedPdfTextAvailable = $false
if ($null -eq $pythonCommand) {
    Write-DiagnosticLine -Label 'Backend imports' -Value 'not checked because Python is unavailable'
}
else {
    $mandatoryImports = @('fastapi', 'uvicorn', 'pydantic', 'cv2', 'fitz', 'numpy')
    $missingImports = New-Object 'System.Collections.Generic.List[string]'
    foreach ($moduleName in $mandatoryImports) {
        if (Test-PythonImport -PythonCommand $pythonCommand -ModuleName $moduleName) {
            Write-DiagnosticLine -Label "Import $moduleName" -Value 'available'
        }
        else {
            Write-DiagnosticLine -Label "Import $moduleName" -Value 'missing'
            $missingImports.Add($moduleName)
        }
    }

    $bootstrapMarker = Join-Path $projectRoot '.venv\.docuverify-bootstrap-complete'
    if ($missingImports.Count -eq 0) {
        Write-DiagnosticLine -Label 'Backend imports' -Value 'ready'
    }
    elseif (Test-Path -LiteralPath $bootstrapMarker -PathType Leaf) {
        $requiredFailures.Add('Backend bootstrap is marked complete but mandatory imports are missing: ' + ($missingImports -join ', '))
    }
    else {
        Write-DiagnosticLine -Label 'Backend imports' -Value 'dependencies not fully installed; run bootstrap-windows.ps1'
    }

    $ocrModules = @('pytesseract', 'easyocr', 'onnxruntime', 'paddleocr', 'rapidocr_onnxruntime')
    $availableOcrModules = @($ocrModules | Where-Object { Test-PythonImport -PythonCommand $pythonCommand -ModuleName $_ })
    $tesseractCommand = Get-Command tesseract.exe -CommandType Application -ErrorAction SilentlyContinue
    if ($availableOcrModules.Count -gt 0) {
        Write-DiagnosticLine -Label 'Optional OCR modules' -Value ($availableOcrModules -join ', ')
    }
    else {
        Write-DiagnosticLine -Label 'Optional OCR modules' -Value 'none detected'
    }
    Write-DiagnosticLine -Label 'Tesseract executable' -Value $(if ($null -eq $tesseractCommand) { 'not detected' } else { 'available' })

    $embeddedPdfTextAvailable = Test-PythonImport -PythonCommand $pythonCommand -ModuleName 'fitz'
    if ($embeddedPdfTextAvailable) {
        Write-DiagnosticLine -Label 'OCR/text fallback' -Value 'PyMuPDF embedded PDF text plus visual-only raster comparison'
    }
    else {
        Write-DiagnosticLine -Label 'OCR/text fallback' -Value 'visual comparison only until PyMuPDF is installed'
    }
}

$configuredOcrProvider = Get-SafeConfigurationValue -Name 'DOCUVERIFY_OCR_PROVIDER' -DefaultValue 'auto'
$configuredOcrDevice = Get-SafeConfigurationValue -Name 'DOCUVERIFY_OCR_DEVICE' -DefaultValue 'cpu'
Write-DiagnosticLine -Label 'OCR provider setting' -Value $configuredOcrProvider
Write-DiagnosticLine -Label 'Effective text provider' -Value $(if ($embeddedPdfTextAvailable) { 'pymupdf_embedded_text' } else { 'unavailable until backend dependencies are installed' })
Write-DiagnosticLine -Label 'Raster OCR capability' -Value 'false (visual comparison remains available)'
Write-DiagnosticLine -Label 'OCR execution device' -Value $configuredOcrDevice
Write-DiagnosticLine -Label 'Visual comparison device' -Value 'CPU (OpenCV/NumPy)'
if ($gpuDetected) {
    Write-DiagnosticLine -Label 'GPU execution note' -Value 'GPU detected; Phase 1 does not assume GPU OCR from driver compatibility alone'
}

Write-DiagnosticSection -Title 'Result'
if ($requiredFailures.Count -eq 0) {
    Write-Host 'READY: required host components are available. Optional capabilities may use fallbacks.' -ForegroundColor Green
    exit 0
}

foreach ($requiredFailure in $requiredFailures) {
    Write-Host ("REQUIRED: $requiredFailure") -ForegroundColor Red
}
Write-Host ("NOT READY: {0} required check(s) failed." -f $requiredFailures.Count) -ForegroundColor Red
exit 1
