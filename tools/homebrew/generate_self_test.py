"""Generate an original mapper-0 NES playback fixture using only Python.

All 6502 program logic and 5x7 glyph patterns below were written for this project.
No commercial game, existing homebrew ROM, external assembler, or asset is used.
The result is an iNES/NROM-128 image: 16 KiB PRG plus 8 KiB CHR.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct

ORIGIN = 0x8000
PRG_SIZE = 16 * 1024
CHR_SIZE = 8 * 1024
TITLE = "Redux Self Test"

# Original simple block-letter drawings, five pixels across and seven down.
FONT = {
    "A": (14, 17, 17, 31, 17, 17, 17),
    "C": (15, 16, 16, 16, 16, 16, 15),
    "D": (30, 17, 17, 17, 17, 17, 30),
    "E": (31, 16, 16, 30, 16, 16, 31),
    "F": (31, 16, 16, 30, 16, 16, 16),
    "G": (15, 16, 16, 23, 17, 17, 15),
    "I": (31, 4, 4, 4, 4, 4, 31),
    "L": (16, 16, 16, 16, 16, 16, 31),
    "M": (17, 27, 21, 21, 17, 17, 17),
    "N": (17, 25, 25, 21, 19, 19, 17),
    "O": (14, 17, 17, 17, 17, 17, 14),
    "R": (30, 17, 17, 30, 20, 18, 17),
    "S": (15, 16, 16, 14, 1, 1, 30),
    "T": (31, 4, 4, 4, 4, 4, 4),
    "U": (17, 17, 17, 17, 17, 17, 14),
    "X": (17, 17, 10, 4, 10, 17, 17),
    "Y": (17, 17, 10, 4, 4, 4, 4),
}


class Assembler:
    def __init__(self):
        self.code = bytearray()
        self.labels = {}
        self.fixups = []
        self.instructions = []

    @property
    def pc(self):
        return ORIGIN + len(self.code)

    def label(self, name):
        if name in self.labels:
            raise ValueError(f"Duplicate label: {name}")
        self.labels[name] = self.pc

    def emit(self, opcode, *operands, note):
        offset = len(self.code)
        self.instructions.append({"address": self.pc, "offset": offset,
                                  "size": 1 + len(operands), "instruction": note})
        self.code.extend((opcode, *operands))

    def absolute(self, opcode, target, note):
        offset = len(self.code) + 1
        self.emit(opcode, 0, 0, note=note)
        self.fixups.append(("absolute", offset, target))

    def immediate_address(self, opcode, target, *, high, note):
        offset = len(self.code) + 1
        self.emit(opcode, 0, note=note)
        self.fixups.append(("high" if high else "low", offset, target))

    def relative(self, opcode, target, note):
        offset = len(self.code) + 1
        self.emit(opcode, 0, note=note)
        self.fixups.append(("relative", offset, target))

    def data(self, label, content):
        self.label(label)
        self.code.extend(content)

    def resolve(self):
        branches = []
        for kind, offset, target in self.fixups:
            address = self.labels[target] if isinstance(target, str) else target
            if kind == "absolute":
                self.code[offset:offset + 2] = struct.pack("<H", address)
            elif kind in {"high", "low"}:
                self.code[offset] = (address >> (8 if kind == "high" else 0)) & 0xFF
            else:
                displacement = address - (ORIGIN + offset + 1)
                if not -128 <= displacement <= 127:
                    raise ValueError(f"6502 branch outside signed-byte range: {target}")
                self.code[offset] = displacement & 0xFF
                branches.append({"instruction_address": ORIGIN + offset - 1,
                                 "operand_offset": offset, "target": address,
                                 "displacement": displacement})
        return branches


def build_graphics():
    chr_rom = bytearray(CHR_SIZE)
    # Tile 0 stays blank; tiles 1/2/3 are solid pixel values 1/2/3.
    chr_rom[16:32] = bytes([0xFF] * 8 + [0] * 8)
    chr_rom[32:48] = bytes([0] * 8 + [0xFF] * 8)
    chr_rom[48:64] = bytes([0xFF] * 16)
    glyph_tiles = {}
    for tile, (letter, rows) in enumerate(sorted(FONT.items()), start=4):
        glyph_tiles[letter] = tile
        chr_rom[tile * 16:tile * 16 + 8] = bytes([0] + [row << 1 for row in rows])
    nametable = bytearray(1024)

    def text(row, message):
        column = (32 - len(message)) // 2
        for character in message:
            nametable[row * 32 + column] = 0 if character == " " else glyph_tiles[character]
            column += 1

    text(3, "REDUX SELF TEST")
    text(5, "ORIGINAL NES ROM")
    text(27, "COLOR CYCLE")
    bars = [1] * 4 + [2] * 4 + [3] * 4 + [0] * 4 + [3] * 4 + [2] * 4 + [1] * 4 + [0] * 4
    for row in range(10, 24):
        nametable[row * 32:(row + 1) * 32] = bytes(bars)
    # Last 64 bytes are attribute values 0: every quadrant uses background palette 0.
    return bytes(chr_rom), bytes(nametable)


def build_rom():
    a = Assembler()
    a.label("reset")
    a.emit(0x78, note="SEI")
    a.emit(0xD8, note="CLD")
    a.emit(0xA2, 0x40, note="LDX #$40")
    a.absolute(0x8E, 0x4017, "STX $4017 ; disable APU frame IRQ")
    a.emit(0xA2, 0xFF, note="LDX #$FF")
    a.emit(0x9A, note="TXS")
    a.emit(0xE8, note="INX ; X=0")
    for register in (0x2000, 0x2001, 0x4010, 0x4015):
        a.absolute(0x8E, register, f"STX ${register:04X}")
    a.absolute(0x2C, 0x2002, "BIT $2002 ; clear PPU write latch")
    a.emit(0xA9, 0, note="LDA #$00")
    a.label("clear_ram")
    for page in range(8):
        a.absolute(0x9D, page * 256, f"STA ${page * 256:04X},X")
    a.emit(0xE8, note="INX")
    a.relative(0xD0, "clear_ram", "BNE clear_ram")
    for wait in ("wait_vblank_1", "wait_vblank_2"):
        a.label(wait)
        a.absolute(0x2C, 0x2002, "BIT $2002")
        a.relative(0x10, wait, f"BPL {wait}")

    a.emit(0xA9, 0x20, note="LDA #$20")
    a.absolute(0x8D, 0x2006, "STA $2006")
    a.emit(0xA9, 0, note="LDA #$00")
    a.absolute(0x8D, 0x2006, "STA $2006 ; nametable address $2000")
    a.immediate_address(0xA9, "nametable", high=False, note="LDA #<nametable")
    a.emit(0x85, 2, note="STA $02")
    a.immediate_address(0xA9, "nametable", high=True, note="LDA #>nametable")
    a.emit(0x85, 3, note="STA $03")
    a.emit(0xA2, 4, note="LDX #$04 ; four pages including attributes")
    a.emit(0xA0, 0, note="LDY #$00")
    a.label("copy_nametable")
    a.emit(0xB1, 2, note="LDA ($02),Y")
    a.absolute(0x8D, 0x2007, "STA $2007")
    a.emit(0xC8, note="INY")
    a.relative(0xD0, "copy_nametable", "BNE copy_nametable")
    a.emit(0xE6, 3, note="INC $03")
    a.emit(0xCA, note="DEX")
    a.relative(0xD0, "copy_nametable", "BNE copy_nametable")

    a.emit(0xA9, 0x3F, note="LDA #$3F")
    a.absolute(0x8D, 0x2006, "STA $2006")
    a.emit(0xA9, 0, note="LDA #$00")
    a.absolute(0x8D, 0x2006, "STA $2006 ; palette address $3F00")
    a.emit(0xA2, 0, note="LDX #$00")
    a.label("copy_palette")
    a.absolute(0xBD, "palette", "LDA palette,X")
    a.absolute(0x8D, 0x2007, "STA $2007")
    a.emit(0xE8, note="INX")
    a.emit(0xE0, 32, note="CPX #$20")
    a.relative(0xD0, "copy_palette", "BNE copy_palette")
    a.absolute(0x2C, 0x2002, "BIT $2002")
    a.emit(0xA9, 0, note="LDA #$00")
    a.absolute(0x8D, 0x2005, "STA $2005 ; scroll X=0")
    a.absolute(0x8D, 0x2005, "STA $2005 ; scroll Y=0")
    a.emit(0xA9, 0x80, note="LDA #$80")
    a.absolute(0x8D, 0x2000, "STA $2000 ; enable NMI, pattern table 0")
    a.emit(0xA9, 0x0A, note="LDA #$0A")
    a.absolute(0x8D, 0x2001, "STA $2001 ; background + leftmost background")
    a.label("main")
    a.absolute(0x4C, "main", "JMP main")

    a.label("nmi")
    for opcode, note in ((0x48, "PHA"), (0x8A, "TXA"), (0x48, "PHA"),
                         (0x98, "TYA"), (0x48, "PHA")):
        a.emit(opcode, note=note)
    a.emit(0xE6, 0, note="INC $00 ; frame counter")
    a.emit(0xA5, 0, note="LDA $00")
    a.emit(0x29, 0x0F, note="AND #$0F")
    a.relative(0xD0, "restore_scroll", "BNE restore_scroll ; cycle every 16 frames")
    a.emit(0xA5, 1, note="LDA $01")
    a.emit(0x18, note="CLC")
    a.emit(0x69, 1, note="ADC #$01")
    a.emit(0x29, 3, note="AND #$03")
    a.emit(0x85, 1, note="STA $01 ; palette phase")
    a.emit(0xAA, note="TAX")
    a.absolute(0x2C, 0x2002, "BIT $2002")
    a.emit(0xA9, 0x3F, note="LDA #$3F")
    a.absolute(0x8D, 0x2006, "STA $2006")
    a.emit(0xA9, 1, note="LDA #$01")
    a.absolute(0x8D, 0x2006, "STA $2006 ; background colors $3F01-$3F03")
    for index in range(3):
        a.absolute(0xBD, "cycle_colors", "LDA cycle_colors,X")
        a.absolute(0x8D, 0x2007, "STA $2007")
        if index != 2:
            a.emit(0xE8, note="INX")
    a.emit(0xA9, 0, note="LDA #$00")
    a.absolute(0x8D, 0x2006, "STA $2006")
    a.absolute(0x8D, 0x2006, "STA $2006 ; leave palette address range")
    a.label("restore_scroll")
    a.absolute(0x2C, 0x2002, "BIT $2002")
    a.emit(0xA9, 0, note="LDA #$00")
    a.absolute(0x8D, 0x2005, "STA $2005")
    a.absolute(0x8D, 0x2005, "STA $2005")
    a.emit(0xA9, 0x80, note="LDA #$80")
    a.absolute(0x8D, 0x2000, "STA $2000")
    for opcode, note in ((0x68, "PLA"), (0xA8, "TAY"), (0x68, "PLA"),
                         (0xAA, "TAX"), (0x68, "PLA"), (0x40, "RTI")):
        a.emit(opcode, note=note)
    a.label("irq")
    a.emit(0x40, note="RTI")
    a.label("program_end")

    chr_rom, nametable = build_graphics()
    a.data("palette", bytes([0x0F, 0x21, 0x16, 0x2A] * 8))
    a.data("cycle_colors", bytes([0x21, 0x16, 0x2A, 0x30] * 2))
    a.data("nametable", nametable)
    branches = a.resolve()
    if len(a.code) > PRG_SIZE - 6:
        raise ValueError("Program/data overlaps interrupt vectors")
    prg = a.code + bytes([0xEA]) * (PRG_SIZE - 6 - len(a.code))
    vectors = (a.labels["nmi"], a.labels["reset"], a.labels["irq"])
    prg.extend(struct.pack("<HHH", *vectors))
    header = b"NES\x1a" + bytes([1, 1, 0, 0]) + bytes(8)
    rom = header + prg + chr_rom
    metadata = {"title": TITLE, "original_fixture": True,
                "purpose": "Library-to-external-emulator playback validation",
                "format": "iNES mapper 0, NROM-128", "size": len(rom),
                "prg_size": PRG_SIZE, "chr_size": CHR_SIZE,
                "sha256": hashlib.sha256(rom).hexdigest(),
                "labels": {name: hex(address) for name, address in a.labels.items()},
                "vectors": {name: hex(address) for name, address in zip(("nmi", "reset", "irq"), vectors)},
                "branches": branches, "instructions": a.instructions,
                "expected_display": "REDUX SELF TEST / ORIGINAL NES ROM, color bars cycling every 16 frames",
                "audio": "disabled", "input": "not required"}
    verify_rom(rom, metadata)
    return bytes(rom), metadata


def verify_rom(rom, metadata):
    if len(rom) != 16 + PRG_SIZE + CHR_SIZE or rom[:16] != b"NES\x1a" + bytes([1, 1, 0, 0]) + bytes(8):
        raise ValueError("Invalid iNES size/header")
    prg = rom[16:16 + PRG_SIZE]
    expected = tuple(int(metadata["vectors"][name], 16) for name in ("nmi", "reset", "irq"))
    if struct.unpack_from("<HHH", prg, PRG_SIZE - 6) != expected:
        raise ValueError("Interrupt vector mismatch")
    code_end = int(metadata["labels"]["program_end"], 16)
    if not all(ORIGIN <= address < code_end for address in expected):
        raise ValueError("Vector does not point into program")
    for branch in metadata["branches"]:
        displacement = struct.unpack_from("b", prg, branch["operand_offset"])[0]
        if branch["instruction_address"] + 2 + displacement != branch["target"]:
            raise ValueError("Relative branch target mismatch")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[2] /
                        "analysis" / "playback_fixture" / "Redux Self Test.nes")
    args = parser.parse_args()
    rom, metadata = build_rom()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(rom)
    args.output.with_suffix(".json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    listing = []
    for row in metadata["instructions"]:
        code = rom[16 + row["offset"]:16 + row["offset"] + row["size"]]
        listing.append(f"{row['address']:04X}  {code.hex(' '):<9}  {row['instruction']}")
    args.output.with_suffix(".listing.txt").write_text("\n".join(listing) + "\n", encoding="utf-8")
    print(json.dumps({key: metadata[key] for key in ("title", "size", "sha256", "vectors", "expected_display")}, indent=2))


if __name__ == "__main__":
    main()
