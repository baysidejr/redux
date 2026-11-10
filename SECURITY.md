# Security

Redux runs local game executables and native emulator DLLs selected by the user. Those programs run with the Windows user's privileges; Redux is not a sandbox. Use trusted game and core sources.

For a suspected vulnerability, use the repository's private vulnerability reporting feature when available. Do not post credentials, game files, BIOS files, personal paths or raw user logs in public issues. Otherwise contact the repository owner privately to arrange a report.

Scraper account values use current-user Windows DPAPI and are kept outside the portable library. Downloads use HTTPS and bounded responses. The installer does not enable Developer Mode, change security settings, install drivers, or register an Xbox home package automatically.

Security reviews are scoped checks, not a guarantee that the application, bundled native dependencies or optional cores are free of vulnerabilities. Release checks and known limits are recorded in docs/SECURITY_REVIEW.md.
