[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidateSet('Add', 'Remove')][string]$Action,
    [Parameter(Mandatory = $true)][string]$Executable,
    [string]$Icon,
    [string]$DisplayName = 'Redux Media Library',
    [string]$BackupDirectory,
    [Parameter(Mandatory = $true)][string]$ResultFile
)
. (Join-Path $PSScriptRoot 'Common.ps1')
$script:ReduxResultFile = $ResultFile
try {
    foreach ($value in @($Executable, $Icon, $DisplayName, $BackupDirectory, $ResultFile, $PSScriptRoot)) {
        if ($value -and $value.IndexOfAny([char[]]@([char]34, [char]10, [char]13)) -ge 0) { throw 'Setup argument contains unsupported characters.' }
    }
    $helper = if ($Action -eq 'Add') { 'Add-XboxApp.ps1' } else { 'Remove-XboxApp.ps1' }
    $arguments = @('-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File',
        ('"' + (Join-Path $PSScriptRoot $helper) + '"'), '-Executable', ('"' + $Executable + '"'),
        '-ResultFile', ('"' + $ResultFile + '"'))
    if ($BackupDirectory) { $arguments += @('-BackupDirectory', ('"' + $BackupDirectory + '"')) }
    if ($Action -eq 'Add') {
        $arguments += @('-DisplayName', ('"' + $DisplayName + '"'))
        if ($Icon) { $arguments += @('-Icon', ('"' + $Icon + '"')) }
    }
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
