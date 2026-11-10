[CmdletBinding()]
param([switch]$Enable, [string]$BackupDirectory, [string]$ResultFile)
. (Join-Path $PSScriptRoot 'Common.ps1')
$script:ReduxResultFile = $ResultFile
try {
    if (-not $Enable) { throw 'Pass -Enable only after choosing to make Windows identify this PC as a gaming handheld. This advanced setting requires a reboot and can affect other Windows behavior.' }
    Assert-ReduxWindows
    Assert-ReduxAdministrator
    if (-not $BackupDirectory) { $BackupDirectory = Join-Path (Get-ReduxIntegrationDirectory) 'device-form-backup' }
    $backup = [IO.Path]::GetFullPath($BackupDirectory)
    $metadataPath = Join-Path $backup 'device-form.json'
    $exportPath = Join-Path $backup 'OEM.reg'
    $keyPath = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\OEM'
    if (Test-Path -LiteralPath $metadataPath) {
        [object]$existing = Get-Content -LiteralPath $metadataPath -Raw | ConvertFrom-Json
        if ($existing.schema -ne 1 -or $existing.computer -ne $env:COMPUTERNAME -or
            (Get-FileHash -LiteralPath $exportPath -Algorithm SHA256).Hash -ne $existing.oem_export_sha256) {
            throw 'The existing original device-form backup cannot be verified.'
        }
        if ((Get-ReduxDeviceForm) -eq 46) {
            Write-ReduxResult @{ok = $true; enabled = $true; restart_required = $true; backup_directory = $backup; reused_backup = $true}
            exit 0
        }
        $current = Get-Item -LiteralPath $keyPath -ErrorAction SilentlyContinue
        $present = $null -ne $current -and ($current.GetValueNames() -contains 'DeviceForm')
        if ($present -eq $existing.value_existed -and (-not $present -or
            ($current.GetValueKind('DeviceForm').ToString() -eq $existing.value_kind -and
             (($current.GetValue('DeviceForm') | ConvertTo-Json -Compress) -eq ($existing.value | ConvertTo-Json -Compress))))) {
            New-Item -Path $keyPath -Force | Out-Null
            New-ItemProperty -LiteralPath $keyPath -Name 'DeviceForm' -Value 46 -PropertyType DWord -Force | Out-Null
            if ((Get-ReduxDeviceForm) -ne 46) { throw 'Windows did not retain DeviceForm 46.' }
            Write-ReduxResult @{ok = $true; enabled = $true; restart_required = $true; backup_directory = $backup; reused_backup = $true}
            exit 0
        }
        throw 'An original device-form backup already exists. Restore it before creating another backup.'
    }
    [IO.Directory]::CreateDirectory($backup) | Out-Null
    $key = Get-Item -LiteralPath $keyPath -ErrorAction SilentlyContinue
    $keyExisted = $null -ne $key
    $valueExisted = $keyExisted -and ($key.GetValueNames() -contains 'DeviceForm')
    $value = $null
    $kind = $null
    if ($valueExisted) { $value = $key.GetValue('DeviceForm', $null, [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames); $kind = $key.GetValueKind('DeviceForm').ToString() }
    if ($keyExisted) {
        & "$env:SystemRoot\System32\reg.exe" export 'HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\OEM' $exportPath /y /reg:64 | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'The original OEM registry key could not be exported; no device setting was changed.' }
    } else {
        "Windows Registry Editor Version 5.00`r`n`r`n; Original OEM key did not exist.`r`n" | Set-Content -LiteralPath $exportPath -Encoding Unicode
    }
    if (-not (Test-Path -LiteralPath $exportPath -PathType Leaf) -or (Get-Item -LiteralPath $exportPath).Length -lt 20) {
        throw 'The OEM backup was not written; no device setting was changed.'
    }
    @{schema = 1; created_utc = [DateTime]::UtcNow.ToString('o'); computer = $env:COMPUTERNAME;
      key_existed = $keyExisted; value_existed = $valueExisted; value_kind = $kind; value = $value;
      oem_export_sha256 = (Get-FileHash -LiteralPath $exportPath -Algorithm SHA256).Hash} |
        ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $metadataPath -Encoding UTF8
    if (-not (Test-Path -LiteralPath $metadataPath -PathType Leaf)) { throw 'The restore metadata was not written; no device setting was changed.' }
    $checkedBackup = Get-Content -LiteralPath $metadataPath -Raw | ConvertFrom-Json
    if ($checkedBackup.schema -ne 1 -or $checkedBackup.oem_export_sha256 -ne (Get-FileHash -LiteralPath $exportPath -Algorithm SHA256).Hash) {
        throw 'The restore metadata could not be verified; no device setting was changed.'
    }
    New-Item -Path $keyPath -Force | Out-Null
    New-ItemProperty -LiteralPath $keyPath -Name 'DeviceForm' -Value 46 -PropertyType DWord -Force | Out-Null
    if ((Get-ReduxDeviceForm) -ne 46) { throw 'Windows did not retain DeviceForm 46. The original backup is available for restoration.' }
    Write-ReduxResult @{ok = $true; enabled = $true; restart_required = $true; backup_directory = $backup;
        note = 'Windows now identifies this PC as a gaming handheld. Restart manually to reveal supported Xbox home-picker settings. Restore original device form from the same Redux settings menu.'}
} catch {
    Write-ReduxResult @{ok = $false; error = $_.Exception.Message}
    exit 1
}
