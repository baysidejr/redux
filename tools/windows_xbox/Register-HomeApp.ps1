[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$Layout, [string]$ResultFile)
. (Join-Path $PSScriptRoot 'Common.ps1')
$script:ReduxResultFile = $ResultFile
try {
    Assert-ReduxWindows
    $devKey = Get-Item -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock' -ErrorAction SilentlyContinue
    if ($null -eq $devKey -or $devKey.GetValue('AllowDevelopmentWithoutDevLicense', 0) -ne 1) {
        throw 'Turn on Developer Mode in Windows Settings > System > For developers, then register Redux again. This helper does not enable Developer Mode.'
    }
    $resolvedLayout = (Resolve-Path -LiteralPath $Layout).Path
    $manifest = Join-Path $resolvedLayout 'AppxManifest.xml'
    [xml]$document = Get-Content -LiteralPath $manifest -Raw
    if ($document.Package.Identity.Name -ne $ReduxPackageName) { throw 'The layout is not a Redux Media Library package.' }
    foreach ($relative in @('Redux.exe', 'CustomCapability.SCCD', 'Public\README.txt', 'redux-launch.json')) {
        if (-not (Test-Path -LiteralPath (Join-Path $resolvedLayout $relative) -PathType Leaf)) {
            throw "The staged layout is missing $relative."
        }
    }
    # Do not force-kill an active game or modify an installed layout in place.
    Add-AppxPackage -Register $manifest -ForceUpdateFromAnyVersion -ErrorAction Stop
    $package = Get-ReduxRegisteredPackage
    if ($null -eq $package) { throw 'Windows did not report the registered Redux package.' }
    Write-ReduxResult @{ok = $true; registered = $true; package_name = $package.Name;
        package_family_name = $package.PackageFamilyName; aumid = "$($package.PackageFamilyName)!App";
        install_location = $package.InstallLocation; device_form = (Get-ReduxDeviceForm);
        note = 'Redux is registered. Choose its home-app entry in Windows Xbox mode settings. Developer Mode may be turned off after successful registration.'}
} catch {
    Write-ReduxResult @{ok = $false; error = $_.Exception.Message}
    exit 1
}
