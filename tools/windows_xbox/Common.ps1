Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ReduxPackageName = 'Redux.MediaLibrary'
$ReduxRunValueName = 'ReduxMediaLibrary'
$ReduxGamingKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\GamingConfiguration'
$ReduxXboxAumid = 'Microsoft.GamingApp_8wekyb3d8bbwe!Microsoft.Xbox.App'
$script:ReduxResultFile = $null

function Write-ReduxResult($Value) {
    $json = $Value | ConvertTo-Json -Depth 12 -Compress
    if ($script:ReduxResultFile) { $json | Set-Content -LiteralPath $script:ReduxResultFile -Encoding UTF8 }
    $json | Write-Output
}

function Assert-ReduxWindows {
    if ([Environment]::Is64BitOperatingSystem -and -not [Environment]::Is64BitProcess) {
        throw 'Run this setup helper in 64-bit Windows PowerShell so the Xbox settings use the native registry view.'
    }
    if ([Environment]::OSVersion.Version.Build -lt 26100) {
        throw 'Xbox home registration needs Windows 11 build 26100 or newer. Normal Redux startup still works.'
    }
}

function Assert-ReduxAdministrator {
    if ([Environment]::Is64BitOperatingSystem -and -not [Environment]::Is64BitProcess) {
        throw 'Run this setup helper in 64-bit Windows PowerShell so the Xbox settings use the native registry view.'
    }
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'This device-form action needs administrator approval.'
    }
}

function Get-ReduxDeviceForm {
    $key = Get-Item -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\OEM' -ErrorAction SilentlyContinue
    if ($null -eq $key) { return $null }
    return $key.GetValue('DeviceForm', $null)
}

function Get-ReduxIntegrationDirectory {
    $directory = Join-Path $env:LOCALAPPDATA 'ReduxMediaLibrary\windows-integration'
    [IO.Directory]::CreateDirectory($directory) | Out-Null
    return $directory
}

function Get-ReduxRegisteredPackage {
    return Get-AppxPackage -Name $ReduxPackageName -ErrorAction Stop | Select-Object -First 1
}

function Remove-ReduxStartupValues {
    foreach ($path in @(
        'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run',
        'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run',
        'HKCU:\Software\Microsoft\Windows\CurrentVersion\GamingConfiguration\Startup\Run'
    )) {
        if (Test-Path -LiteralPath $path) {
            Remove-ItemProperty -LiteralPath $path -Name $ReduxRunValueName -ErrorAction SilentlyContinue
        }
    }
}
