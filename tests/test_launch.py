from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from redux.launch import build_command, launch_game


class LaunchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.game = self.root / "Game with spaces & symbols (USA).cue"
        self.game.write_text('FILE "track.bin" BINARY\n', encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def test_build_command_preserves_argument_boundaries(self):
        template = ["--fullscreen", "{game}", "--folder={game_dir}", "literal & ; $() ^ % !"]
        command = build_command(sys.executable, template, self.game)
        self.assertEqual(command, [str(Path(sys.executable).resolve()), "--fullscreen",
                                  str(self.game), f"--folder={self.root}", "literal & ; $() ^ % !"])
        self.assertEqual(template[1], "{game}")

    def test_rejects_command_line_string_and_unknown_fields(self):
        with self.assertRaises(TypeError):
            build_command(sys.executable, '"{game}" --fullscreen', self.game)
        for template in [["{unknown}"], ["{game.parent}"], ["{game!r}"], ["{game:>10}"], ["{game"], ["{game_dir}"]]:
            with self.subTest(template=template), self.assertRaises(ValueError):
                build_command(sys.executable, template, self.game)

    def test_rejects_missing_or_directory_inputs(self):
        with self.assertRaises(FileNotFoundError):
            build_command(self.root/"missing.exe", ["{game}"], self.game)
        with self.assertRaises(FileNotFoundError):
            build_command(sys.executable, ["{game}"], self.root/"missing.cue")
        with self.assertRaises(ValueError):
            build_command(sys.executable, ["{game}"], self.root)
        with self.assertRaises(ValueError):
            build_command(sys.executable, ["{game}", "bad\0argument"], self.game)

    def test_launch_uses_explicit_argv_and_no_shell(self):
        with patch("redux.launch.subprocess.Popen") as start:
            process = launch_game(sys.executable, ["{game}"], self.game)
            self.assertIs(process, start.return_value)
            self.assertEqual(start.call_args.args[0][1], str(self.game))
            self.assertIs(start.call_args.kwargs["shell"], False)
            self.assertEqual(start.call_args.kwargs["cwd"], str(Path(sys.executable).resolve().parent))

    def test_invalid_working_directory_never_starts_process(self):
        with patch("redux.launch.subprocess.Popen") as start:
            with self.assertRaises(ValueError):
                launch_game(sys.executable, ["{game}"], self.game, cwd=self.game)
            start.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows script behavior")
    def test_windows_batch_script_rejected(self):
        script = self.root/"fake.cmd"
        script.write_text("@echo off\n",encoding="utf-8")
        with self.assertRaises(ValueError):
            build_command(script,["{game}"],self.game)

    def test_real_child_receives_spaces_and_metacharacters_literally(self):
        helper = self.root/"capture arguments.py"
        helper.write_text("import json, pathlib, sys\npathlib.Path('argv.json').write_text(json.dumps(sys.argv[1:]), encoding='utf-8')\n", encoding="utf-8")
        literal = "& echo injected ; $(command) ^ %PATH% !value!"
        process = launch_game(sys.executable, [str(helper), "{game}", literal, "{game_dir}"],
                              self.game, cwd=self.root)
        self.assertEqual(process.wait(timeout=10),0)
        observed=json.loads((self.root/"argv.json").read_text(encoding="utf-8"))
        self.assertEqual(observed,[str(self.game),literal,str(self.root)])


if __name__=="__main__":unittest.main()
