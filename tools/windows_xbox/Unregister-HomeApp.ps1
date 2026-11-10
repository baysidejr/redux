[CmdletBinding()]
param([switch]$KeepStartup, [string]$ResultFile)
. (Join-Path $PSScriptRoot 'Common.ps1')
$script:ReduxResultFile = $ResultFile
try {
    $packages = @(Get-AppxPackage -Name $ReduxPackageName -ErrorAction Stop)
    $homeRestored = $false
    foreach ($package in $packages) {
        $aumid = "$($package.PackageFamilyName)!App"
        $key = Get-Item -LiteralPath $ReduxGamingKey -ErrorAction SilentlyContinue
        if ($null -ne $key -and $key.GetValue('GamingHomeApp', '') -eq $aumid) {
            New-ItemProperty -LiteralPath $ReduxGamingKey -Name 'GamingHomeApp' -Value $ReduxXboxAumid -PropertyType String -Force | Out-Null
            $homeRestored = $true
        }
        Remove-AppxPackage -Package $package.PackageFullName -ErrorAction Stop
    }
    if (-not $KeepStartup) { Remove-ReduxStartupValues }
    Write-ReduxResult @{ok = $true; registered = $false; packages_removed = $packages.Count;
        home_restored_to_xbox = $homeRestored; startup_removed = (-not $KeepStartup);
        note = 'Identity removed. Portable games, BIOS, saves, original device form and Developer Mode were not changed.'}
} catch {
    Write-ReduxResult @{ok = $false; error = $_.Exception.Message}
    exit 1
}
