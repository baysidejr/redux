"""Structural tests for the original NES fixture; never run an emulator."""

import hashlib
import struct
import unittest

from tools.homebrew.generate_self_test import build_rom, ORIGIN, PRG_SIZE, CHR_SIZE


class HomebrewFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rom, cls.metadata = build_rom()
        cls.prg = cls.rom[16:16 + PRG_SIZE]
        cls.chr = cls.rom[16 + PRG_SIZE:]

    def test_image_is_trainerless_mapper_zero_with_exact_declared_banks(self):
        self.assertEqual(self.rom[:4], b"NES\x1a")
        prg_banks, chr_banks, flags6, flags7 = self.rom[4:8]
        self.assertEqual((prg_banks, chr_banks), (1, 1))
        self.assertEqual((flags6 >> 4) | (flags7 & 0xF0), 0)
        self.assertEqual(flags6 & 0x06, 0)  # No trainer or battery-backed data.
        self.assertEqual(len(self.rom), 16 + prg_banks * 16384 + chr_banks * 8192)
        self.assertEqual(len(self.chr), CHR_SIZE)

    def test_interrupt_vectors_target_real_instructions_in_mirrored_prg(self):
        nmi, reset, irq = struct.unpack_from("<HHH", self.prg, 0x3FFA)
        self.assertEqual(reset, 0x8000)
        self.assertEqual(self.prg[reset - ORIGIN], 0x78)  # SEI.
        self.assertEqual(self.prg[nmi - ORIGIN], 0x48)    # PHA.
        self.assertEqual(self.prg[irq - ORIGIN], 0x40)    # RTI.
        instructions = {row["address"] for row in self.metadata["instructions"]}
        self.assertTrue({nmi, reset, irq}.issubset(instructions))

    def test_relative_branches_land_on_instruction_boundaries(self):
        instructions = {row["address"] for row in self.metadata["instructions"]}
        self.assertGreaterEqual(len(self.metadata["branches"]), 7)
        for branch in self.metadata["branches"]:
            address = branch["instruction_address"]
            with self.subTest(address=hex(address)):
                offset = address - ORIGIN
                self.assertIn(self.prg[offset], [0x10, 0xD0])
                displacement = int.from_bytes(self.prg[offset + 1:offset + 2], "little", signed=True)
                self.assertEqual(address + 2 + displacement, branch["target"])
                self.assertIn(branch["target"], instructions)

    def test_color_bar_tiles_encode_three_distinct_pixel_values(self):
        self.assertEqual(self.chr[:16], bytes(16))
        for tile, color in [(1, 1), (2, 2), (3, 3)]:
            offset = tile * 16
            low = self.chr[offset:offset + 8]
            high = self.chr[offset + 8:offset + 16]
            for y in range(8):
                pixels = [((low[y] >> x) & 1) | (((high[y] >> x) & 1) << 1) for x in range(8)]
                self.assertEqual(pixels, [color] * 8)

    def test_nametable_has_text_and_color_bars_with_palette_zero_attributes(self):
        offset = int(self.metadata["labels"]["nametable"], 16) - ORIGIN
        nametable = self.prg[offset:offset + 1024]
        self.assertEqual(len(nametable), 1024)
        self.assertEqual(nametable[960:], bytes(64))
        self.assertTrue(any(tile >= 4 for tile in nametable[3 * 32:4 * 32]))
        self.assertEqual(set(nametable[10 * 32:24 * 32]), {0, 1, 2, 3})

    def test_generator_is_reproducible(self):
        again, metadata = build_rom()
        self.assertEqual(again, self.rom)
        self.assertEqual(hashlib.sha256(again).hexdigest(), metadata["sha256"])
        self.assertTrue(metadata["original_fixture"])


if __name__ == "__main__":
    unittest.main()
