[CmdletBinding()]
param([string]$BackupDirectory, [string]$ResultFile)
. (Join-Path $PSScriptRoot 'Common.ps1')
$script:ReduxResultFile = $ResultFile
try {
    Assert-ReduxAdministrator
    if (-not $BackupDirectory) { $BackupDirectory = Join-Path (Get-ReduxIntegrationDirectory) 'device-form-backup' }
    $backup = [IO.Path]::GetFullPath($BackupDirectory)
    $metadataPath = Join-Path $backup 'device-form.json'
    $exportPath = Join-Path $backup 'OEM.reg'
    $saved = Get-Content -LiteralPath $metadataPath -Raw | ConvertFrom-Json
    if ($saved.schema -ne 1 -or $saved.computer -ne $env:COMPUTERNAME) { throw 'This backup does not belong to this computer.' }
    if ((Get-FileHash -LiteralPath $exportPath -Algorithm SHA256).Hash -ne $saved.oem_export_sha256) {
        throw 'The original OEM export has changed. Preserve it and review the backup before restoring.'
    }
    $keyPath = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\OEM'
    if ($saved.value_existed) {
        New-Item -Path $keyPath -Force | Out-Null
        $value = $saved.value
        if ($saved.value_kind -eq 'Binary') { $value = [byte[]]$value }
        if ($saved.value_kind -eq 'MultiString') { $value = [string[]]$value }
        New-ItemProperty -LiteralPath $keyPath -Name 'DeviceForm' -Value $value -PropertyType $saved.value_kind -Force | Out-Null
    } elseif (Test-Path -LiteralPath $keyPath) {
        Remove-ItemProperty -LiteralPath $keyPath -Name 'DeviceForm' -ErrorAction SilentlyContinue
    }
    # Restore only the original DeviceForm. Re-importing the complete OEM export
    # would overwrite unrelated OEM changes made after the backup.
    Write-ReduxResult @{ok = $true; restored = $true; device_form = (Get-ReduxDeviceForm);
        restart_required = $true; backup_directory = $backup}
} catch {
    Write-ReduxResult @{ok = $false; error = $_.Exception.Message}
    exit 1
}
