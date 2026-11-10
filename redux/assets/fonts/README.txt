Roboto Condensed — bundled frontend typography

The two unmodified variable TrueType fonts are obtained from Google's official
Google Fonts repository, ofl/robotocondensed. Their source URLs, Git blob hashes,
local SHA-256 digests and native validation are recorded in provenance.json.

Copyright and license are preserved in OFL.txt. These fonts are independently
sourced dependencies, not an identified/recovered reference application UI typeface.

ui_fonts.register_fonts() uses Windows AddFontResourceExW with FR_PRIVATE.
Registration is scoped to the current application process. It does not install
fonts into Windows Fonts, write registry keys, broadcast setting changes or
alter fonts available to other applications. Windows releases the resources
when the application exits. Call registration before creating named Tk fonts.

PyInstaller data destination supported by the helper:
  source: redux/assets/fonts
  destination: assets/fonts
An alternate destination redux/assets/fonts is also recognized.
Keep OFL.txt with any distributed font files.
