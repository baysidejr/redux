[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Executable,
    [string]$Icon,
    [string]$DisplayName = 'Redux Media Library',
    [string]$BackupDirectory,
    [string]$ResultFile
)
. (Join-Path $PSScriptRoot 'Common.ps1')
$script:ReduxResultFile = $ResultFile
try {
    Assert-ReduxWindows
    Assert-ReduxAdministrator
    $exe = (Resolve-Path -LiteralPath $Executable).Path
    if ([IO.Path]::GetFileName($exe) -ne 'Redux.exe') { throw 'Choose the Redux portable executable.' }
    if (-not $Icon) { $Icon = $exe }
    $resolvedIcon = (Resolve-Path -LiteralPath $Icon).Path
    if (-not $BackupDirectory) { $BackupDirectory = Join-Path (Get-ReduxIntegrationDirectory) 'xbox-app-backup' }
    $backup = [IO.Path]::GetFullPath($BackupDirectory)
    $metadataPath = Join-Path $backup 'xbox-app.json'
    $base = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\GamingConfiguration\OEMApps'
    $slot = $null
    foreach ($number in 1..3) {
        $path = Join-Path $base "OEMAppPathsAndName$number"
        $key = Get-Item -LiteralPath $path -ErrorAction SilentlyContinue
        if ($null -ne $key -and [string]::Equals([string]$key.GetValue('OemAppExePath', ''), $exe, [StringComparison]::OrdinalIgnoreCase)) {
            Write-ReduxResult @{ok = $true; added = $true; already_registered = $true; executable = $exe; slot = $number}
            exit 0
        }
        if ($null -eq $slot -and ($null -eq $key -or ($key.GetValueNames().Count -eq 0 -and $key.GetSubKeyNames().Count -eq 0))) { $slot = $number }
    }
    if ($null -eq $slot) { throw 'All three Xbox My Apps OEM slots are in use. Redux did not replace another app.' }
    [IO.Directory]::CreateDirectory($backup) | Out-Null
    if (Test-Path -LiteralPath $metadataPath) {
        $old = Get-Content -LiteralPath $metadataPath -Raw | ConvertFrom-Json
        if ($old.active) { throw 'A Redux Xbox-app registration backup is already active. Remove that registration before moving its executable.' }
        $history = Join-Path $backup ('xbox-app-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffffffZ') + '.json')
        Move-Item -LiteralPath $metadataPath -Destination $history
    }
    $path = Join-Path $base "OEMAppPathsAndName$slot"
    $keyExisted = Test-Path -LiteralPath $path
    $exportPath = Join-Path $backup 'OEMApp.reg'
    if ($keyExisted) {
        & "$env:SystemRoot\System32\reg.exe" export "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\GamingConfiguration\OEMApps\OEMAppPathsAndName$slot" $exportPath /y /reg:64 | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'The original Xbox app slot could not be backed up; no registry value was changed.' }
    } else {
        "Windows Registry Editor Version 5.00`r`n`r`n; Original Xbox OEM app slot did not exist.`r`n" | Set-Content -LiteralPath $exportPath -Encoding Unicode
    }
    if (-not (Test-Path -LiteralPath $exportPath -PathType Leaf) -or (Get-Item -LiteralPath $exportPath).Length -lt 20) { throw 'Xbox app slot backup was not written.' }
    @{schema = 1; active = $true; computer = $env:COMPUTERNAME; created_utc = [DateTime]::UtcNow.ToString('o');
      slot = $slot; key_existed = $keyExisted; executable = $exe;
      export_sha256 = (Get-FileHash -LiteralPath $exportPath -Algorithm SHA256).Hash} |
      ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $metadataPath -Encoding UTF8
    $checkedBackup = Get-Content -LiteralPath $metadataPath -Raw | ConvertFrom-Json
    if (-not $checkedBackup.active -or $checkedBackup.executable -ne $exe -or
        $checkedBackup.export_sha256 -ne (Get-FileHash -LiteralPath $exportPath -Algorithm SHA256).Hash) {
        throw 'The Xbox app backup could not be verified; no registry value was changed.'
    }
    $stillFree = Get-Item -LiteralPath $path -ErrorAction SilentlyContinue
    if ($null -ne $stillFree -and ($stillFree.GetValueNames().Count -ne 0 -or $stillFree.GetSubKeyNames().Count -ne 0)) {
        throw 'Another application filled the Xbox app slot during setup. Redux preserved its values.'
    }
    New-Item -Path $path -Force | Out-Null
    foreach ($item in @(@('OemAppName', $DisplayName), @('OemAppExePath', $exe), @('OemAppIconPath', $resolvedIcon))) {
        New-ItemProperty -LiteralPath $path -Name $item[0] -Value $item[1] -PropertyType String -Force | Out-Null
    }
    $verified = Get-Item -LiteralPath $path
    if ($verified.GetValue('OemAppExePath', '') -ne $exe -or $verified.GetValue('OemAppName', '') -ne $DisplayName) {
        throw 'Windows did not retain the Xbox app values. The original slot backup is available.'
    }
    Write-ReduxResult @{ok = $true; added = $true; executable = $exe; slot = $slot; backup_directory = $backup;
      note = 'Redux is registered for Xbox My Apps. Close and reopen the Xbox app to refresh its app list. This does not change the selected home app.'}
} catch {
    Write-ReduxResult @{ok = $false; error = $_.Exception.Message}
    exit 1
}
