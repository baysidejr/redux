[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidateSet('Enable', 'Restore')][string]$Action,
    [Parameter(Mandatory = $true)][string]$BackupDirectory,
    [Parameter(Mandatory = $true)][string]$ResultFile
)
. (Join-Path $PSScriptRoot 'Common.ps1')
$script:ReduxResultFile = $ResultFile
try {
    foreach ($path in @($BackupDirectory, $ResultFile, $PSScriptRoot)) {
        if ($path.IndexOfAny([char[]]@([char]34, [char]10, [char]13)) -ge 0) { throw 'Setup path contains unsupported characters.' }
    }
    $helper = if ($Action -eq 'Enable') { 'Enable-HomePicker.ps1' } else { 'Restore-DeviceForm.ps1' }
    $arguments = @('-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File',
        ('"' + (Join-Path $PSScriptRoot $helper) + '"'),
        '-BackupDirectory', ('"' + $BackupDirectory + '"'), '-ResultFile', ('"' + $ResultFile + '"'))
    if ($Action -eq 'Enable') { $arguments += '-Enable' }
    # Only the small registry helper elevates; Redux and its game/core hosts stay
    # at medium integrity so Xbox home activation remains valid.
    $process = Start-Process -FilePath "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" `
        -ArgumentList $arguments -Verb RunAs -WindowStyle Hidden -Wait -PassThru
    if (-not (Test-Path -LiteralPath $ResultFile -PathType Leaf)) { throw "Setup returned $($process.ExitCode) without a result." }
    $result = Get-Content -LiteralPath $ResultFile -Raw | ConvertFrom-Json
    Write-ReduxResult $result
    if (-not $result.ok) { exit 1 }
} catch {
    Write-ReduxResult @{ok = $false; error = $_.Exception.Message}
    exit 1
}
