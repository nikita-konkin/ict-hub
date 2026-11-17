<#
.SYNOPSIS
Export the known AbsolTEC 2017 application procedures with REA and Ghidra.
.DESCRIPTION
Uses portable, caller-selected installations. Does not change agent settings,
run AbsolTEC, or install tools. Output must be a new directory on local NTFS.
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
$expectedHash = '31984eb0df935b52751cf92458b4471ca8260a98fed9d5fb6492fe175d3443f5'
$sourceHash = (Get-FileHash -LiteralPath $sourceBinary -Algorithm SHA256).Hash.ToLowerInvariant()
if ($sourceHash -ne $expectedHash) {
    throw 'This procedure range belongs to the recorded 2017 executable. Inspect a different build with REA directly.'
}
if (Test-Path -LiteralPath $OutputDirectory) {
    throw 'OutputDirectory must be a new directory; existing evidence is preserved.'
}
$analysisRoot = [System.IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Path $analysisRoot | Out-Null
$targetBinary = Join-Path $analysisRoot 'absolTEC.exe'
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
    if ($LASTEXITCODE -ne 0) { throw 'REA import-table inspection failed.' }
    & node $reaScript decompile-all $targetBinary --provider ghidra `
        --address-start 0x401000 --address-end 0x40abff `
        --output (Join-Path $analysisRoot 'decompiled') --format json |
        Set-Content -LiteralPath (Join-Path $analysisRoot 'decompilation.json') -Encoding UTF8
    if ($LASTEXITCODE -ne 0) { throw 'REA decompilation failed; retain its evidence and diagnostics.' }
    Write-Output "Evidence saved to $analysisRoot"
}
finally {
    foreach ($key in $savedEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($key, $savedEnvironment[$key], 'Process')
    }
}
