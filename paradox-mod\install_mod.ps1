[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$documentsPath = [Environment]::GetFolderPath('MyDocuments')
if ([string]::IsNullOrWhiteSpace($documentsPath)) {
    throw 'Windows did not return a Documents folder.'
}

$stellarisModRoot = Join-Path $documentsPath 'Paradox Interactive\Stellaris\mod'
$sourceMod = Join-Path $PSScriptRoot 'paradox_agent_testbed'
$targetMod = Join-Path $stellarisModRoot 'paradox_agent_testbed'
$launcherDescriptor = Join-Path $stellarisModRoot 'paradox_agent_testbed.mod'

if (-not (Test-Path -LiteralPath $sourceMod)) {
    throw "Mod source was not found at: $sourceMod"
}

New-Item -ItemType Directory -Force -Path $stellarisModRoot | Out-Null
New-Item -ItemType Directory -Force -Path $targetMod | Out-Null
Copy-Item -Path (Join-Path $sourceMod '*') -Destination $targetMod -Recurse -Force

$descriptor = @'
name="Paradox Agent Testbed"
path="mod/paradox_agent_testbed"
tags={
    "Gameplay"
}
supported_version="4.4.*"
'@

Set-Content -LiteralPath $launcherDescriptor -Value $descriptor -Encoding utf8

Write-Host "Installed Paradox Agent Testbed to: $targetMod"
Write-Host "Launcher descriptor: $launcherDescriptor"
Write-Host 'Enable the mod in the Paradox Launcher before starting a new game.'

