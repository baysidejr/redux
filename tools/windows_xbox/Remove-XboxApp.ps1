[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$Executable, [string]$BackupDirectory, [string]$ResultFile)
. (Join-Path $PSScriptRoot 'Common.ps1')
$script:ReduxResultFile = $ResultFile
try {
    Assert-ReduxAdministrator
    $exe = [IO.Path]::GetFullPath($Executable)
    if ([IO.Path]::GetFileName($exe) -ne 'Redux.exe') { throw 'Choose the Redux portable executable.' }
    if (-not $BackupDirectory) { $BackupDirectory = Join-Path (Get-ReduxIntegrationDirectory) 'xbox-app-backup' }
    $backup = [IO.Path]::GetFullPath($BackupDirectory)
    $metadataPath = Join-Path $backup 'xbox-app.json'
    $saved = $null
    if (Test-Path -LiteralPath $metadataPath -PathType Leaf) {
        $saved = Get-Content -LiteralPath $metadataPath -Raw | ConvertFrom-Json
        if ($saved.schema -ne 1 -or $saved.computer -ne $env:COMPUTERNAME) { throw 'Xbox app backup does not belong to this computer.' }
        if ($saved.active -and (Get-FileHash -LiteralPath (Join-Path $backup 'OEMApp.reg') -Algorithm SHA256).Hash -ne $saved.export_sha256) {
            throw 'Xbox app slot backup has changed. No app registration was removed.'
        }
    }
    $removed = @()
    foreach ($number in 1..3) {
        $path = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\GamingConfiguration\OEMApps\OEMAppPathsAndName$number"
        $key = Get-Item -LiteralPath $path -ErrorAction SilentlyContinue
        if ($null -eq $key -or -not [string]::Equals([string]$key.GetValue('OemAppExePath', ''), $exe, [StringComparison]::OrdinalIgnoreCase)) { continue }
        # Never remove a different app, including a slot reassigned after install.
        foreach ($name in @('OemAppName', 'OemAppExePath', 'OemAppIconPath')) {
            Remove-ItemProperty -LiteralPath $path -Name $name -ErrorAction SilentlyContinue
        }
        if ($null -ne $saved -and $saved.active -and $saved.slot -eq $number) {
            $saved.active = $false
            $saved | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $metadataPath -Encoding UTF8
            if (-not $saved.key_existed) {
                $remaining = Get-Item -LiteralPath $path
                if ($remaining.GetValueNames().Count -eq 0 -and $remaining.GetSubKeyNames().Count -eq 0) { Remove-Item -LiteralPath $path }
            }
        }
        $removed += $number
    }
    if ($null -ne $saved -and $saved.active -and [string]::Equals([string]$saved.executable, $exe, [StringComparison]::OrdinalIgnoreCase)) {
        $saved.active = $false
        $saved | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $metadataPath -Encoding UTF8
    }
    Write-ReduxResult @{ok = $true; added = $false; removed_slots = $removed; executable = $exe;
        note = 'Only Xbox My Apps entries pointing to this Redux executable were removed. Other app slots and the home-app choice were preserved.'}
} catch {
    Write-ReduxResult @{ok = $false; error = $_.Exception.Message}
    exit 1
}
