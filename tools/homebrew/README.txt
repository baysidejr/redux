REDUX SELF TEST — ORIGINAL NES PLAYBACK FIXTURE

Generate:
  python tools/homebrew/generate_self_test.py

Output:
  analysis/playback_fixture/Redux Self Test.nes
  analysis/playback_fixture/Redux Self Test.json
  analysis/playback_fixture/Redux Self Test.listing.txt

All 6502 program logic, lettering, and graphics were written for this project.
No game ROM, commercial asset, existing homebrew program, or external font was
copied. The generator itself is the human-readable source, including instruction
comments and checked labels/fixups. It uses only the Python standard library.

Format: iNES, mapper 0, NROM-128, 16 KiB PRG + 8 KiB CHR; total 24,592 bytes.
No trainer, battery, RAM save file, audio, controller input, or firmware is needed.

Expected display: REDUX SELF TEST and ORIGINAL NES ROM above bright color bars;
the colors cycle every 16 frames. A bottom caption reads COLOR CYCLE.
The reset routine disables interrupts/APU, clears RAM, waits for PPU startup,
loads its own nametable/palette, and enables background/NMI rendering.
The NMI handler preserves A/X/Y, periodically changes three palette colors,
restores scroll, and returns. It does not access external files or hardware.

Verification without execution:
  python -m unittest discover -s tests -p test_homebrew_fixture.py -v
The generator verifies bank sizes, header, interrupt vectors, signed-branch
ranges, and resolved branch targets. Tests additionally decode graphics planes,
check attributes/text/color bars, and verify deterministic output.
Visible animation still requires observation in the external emulator; the
generator/tests do not launch an emulator.

Primary format/register references (consulted 2026-11-08; paraphrased):
  https://www.nesdev.org/wiki/INES
  https://www.nesdev.org/wiki/NROM
  https://www.nesdev.org/wiki/PPU_programmer_reference
NESdev documents the iNES header/bank sizes, fixed NROM mapping, PPU register
addresses, startup behavior, and palette/scroll write protocol. No example
program was copied from these references.
