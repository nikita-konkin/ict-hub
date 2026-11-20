<#
.SYNOPSIS
Export MapTEC 2014 helper routines and main-procedure instructions with REA and Ghidra.
.DESCRIPTION
Uses portable, caller-selected installations. Does not change agent settings,
run MapTEC, or install tools. Output must be a new directory on local NTFS.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$BinaryPath,
    [Parameter(Mandatory = $true)][string]$ReaEntry,
    [Parameter(Mandatory = $true)][string]$GhidraInstallDir,
    [Parameter(Mandatory = $true)][string]$JavaHome,
    [Parameter(Mandatory = $true)][string]$OutputDirectory
)

$ErrorActionPreference = 'Stop'
$sourceBinary = (Resolve-Path -LiteralPath $BinaryPath).Path
$reaScript = (Resolve-Path -LiteralPath $ReaEntry).Path
$ghidraRoot = (Resolve-Path -LiteralPath $GhidraInstallDir).Path
$jdkRoot = (Resolve-Path -LiteralPath $JavaHome).Path
$expectedHash = '3da31ec65c09c97c25539b3722842406a9fb09afecd43fad4448f5c080e29343'
$sourceHash = (Get-FileHash -LiteralPath $sourceBinary -Algorithm SHA256).Hash.ToLowerInvariant()
if ($sourceHash -ne $expectedHash) {
    throw 'Binary SHA-256 does not match the recorded 2014 build'
}
if (Test-Path -LiteralPath $OutputDirectory) {
    throw 'OutputDirectory must be a new directory'
}
$analysisRoot = [System.IO.Path]::GetFullPath($OutputDirectory)
if ([System.IO.Path]::GetPathRoot($analysisRoot) -ine 'C:\') {
    throw 'OutputDirectory must be on the local C drive'
}
New-Item -ItemType Directory -Path $analysisRoot | Out-Null
$targetBinary = Join-Path $analysisRoot 'MapTec.exe'
Copy-Item -LiteralPath $sourceBinary -Destination $targetBinary
$savedEnvironment = @{}
foreach ($key in @('GHIDRA_INSTALL_DIR', 'JAVA_HOME', 'REA_ANALYSIS_PROVIDER', 'REA_GHIDRA_STARTUP_TIMEOUT_MS')) {
    $savedEnvironment[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
}
try {
    $env:GHIDRA_INSTALL_DIR = $ghidraRoot
    $env:JAVA_HOME = $jdkRoot
    $env:REA_ANALYSIS_PROVIDER = 'ghidra'
    $env:REA_GHIDRA_STARTUP_TIMEOUT_MS = '900000'
    & node $reaScript inspect-pe-imports $targetBinary --format json |
        Set-Content -LiteralPath (Join-Path $analysisRoot 'pe-imports.json') -Encoding UTF8
    if ($LASTEXITCODE -ne 0 -or !(Test-Path -LiteralPath (Join-Path $analysisRoot 'pe-imports.json'))) {
        throw 'REA import-table inspection failed'
    }
    & node $reaScript decompile-all $targetBinary --provider ghidra `
        --address-start 0x401000 --address-end 0x405d8c `
        --output (Join-Path $analysisRoot 'decompiled') --format json |
        Set-Content -LiteralPath (Join-Path $analysisRoot 'decompilation.json') -Encoding UTF8
    if ($LASTEXITCODE -ne 0) { throw 'REA decompilation failed' }
    $manifest = Get-Content -LiteralPath (Join-Path $analysisRoot 'decompilation.json') -Raw | ConvertFrom-Json
    if ($manifest.normalized_result.counts.written -ne 10) { throw 'REA did not export all ten MapTEC helpers' }
    & node $reaScript instructions $targetBinary 0x405e46 --provider ghidra --format json |
        Set-Content -LiteralPath (Join-Path $analysisRoot 'main-instructions.json') -Encoding UTF8
    if ($LASTEXITCODE -ne 0 -or !(Test-Path -LiteralPath (Join-Path $analysisRoot 'main-instructions.json'))) {
        throw 'REA main-procedure instruction export failed'
    }
    Write-Output "Evidence saved to $analysisRoot"
}
finally {
    foreach ($key in $savedEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($key, $savedEnvironment[$key], 'Process')
    }
}
