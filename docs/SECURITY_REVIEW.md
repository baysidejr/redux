# Release security review — Redux 0.10.5

Reviewed November 9, 2026. Scope: current Python source, Windows integration helpers, installer, declared Python build/runtime dependencies, Git staging contents and release payload boundaries. This is not an independent penetration test or a full audit of native dependencies and emulator cores.

## Changes

- Upgraded Pillow from 12.3.0 to 13.0.0 after pip-audit reported 39 advisories against the older pin. The fixed dependency set reports no known vulnerabilities. Upstream fixes are described in the [Pillow release notes](https://pillow.readthedocs.io/en/stable/releasenotes/13.0.0.html).
- Added a fail-closed release guard shared by ZIP and installer builders. It rejects user libraries, firmware, game images, credential stores, local databases, unexpected files and links/junctions. Regression tests cover allowed payloads and rejected local data.
- Resolved Windows PowerShell through the OS system-directory API instead of searching PATH or the working directory.
- Added explicit initial HTTPS/host checks to the fixed-source disc-artwork and optional-core download functions.
- Marked the legacy firmware-identification MD5 as non-security use; SHA-256 remains the integrity comparison.
- Serialized activation-pipe cleanup after testing exposed a possible double close between the listener and UI shutdown threads; a concurrent-cleanup regression test covers the shared handle ownership.
- Added source exclusions for BIOS, registered runtime copies, optional downloaded cores, databases and credential/key files. SDL3 is the only native DLL tracked in the source repository and has upstream provenance and license records.

## Checks

- Unit suite: 851 tests, no failures, four Windows symlink-privilege skips.
- Clean source export: 851 tests, no failures, six skips (the same four privilege skips plus the intentionally excluded optional LuaJIT interpreter and local reference catalogue).
- pip-audit 2.10.1 against requirements-build.txt: no known vulnerabilities after the dependency update.
- Gitleaks 8.30.1 against staged source: no leaks found. Test account strings are synthetic fixtures; real account files remain outside the repository.
- Bandit 1.9.4: no remaining high-severity findings. Six medium findings were manually reviewed: four dynamic SQL statements use allowlisted column/order fragments with bound values; two urllib call sites have explicit fixed-source HTTPS validation. Low findings principally concern explicit local process launches, platform URI activation, bounded best-effort error handling and assertions in diagnostic tools.
- Installer compiler obtained from the official Inno Setup release; Authenticode status Valid, signer Pyrsys B.V. Installer acceptance includes installation, executable verification, repeat installation and uninstall with user-data preservation; the release notes record the outcome.

## Security properties and limits

Scraper credentials use current-user DPAPI outside the portable library. Provider errors and provenance omit reusable secrets. Remote artwork is bounded by bytes and dimensions. Library write paths and staging cleanup enforce ownership boundaries. Native PC game launches use argument vectors without shell evaluation and track the owned process family.

Redux and its installer are unsigned. Windows Xbox home registration remains an optional development-package flow requiring Developer Mode at registration time; the installer does not change that setting or register the home identity. Native cores and external game executables are trusted code running as the user, not sandboxed content. Optional core downloads use official HTTPS sources and record hashes; upstream nightlies are not pinned by an independently published digest. Python, Tcl/Tk, SDL3 and every core require ongoing upstream security maintenance; this dependency scan does not establish that all native components are vulnerability-free.

No games, retail firmware, downloaded cores, artwork accounts, user libraries or local reverse-engineering data are published with the source or release.
