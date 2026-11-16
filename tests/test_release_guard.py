from pathlib import Path
import tempfile
import unittest

from tools.release_guard import distribution_files


class ReleaseGuardTests(unittest.TestCase):
    def test_only_distribution_payloads_are_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('Redux.exe', '_internal/python314.dll', 'docs/OPERATING_GUIDE.md',
                         'tools/cores/install_official.py', 'tools/windows_xbox/README.md',
                         'tools/ReduxReceiver/ReduxReceiver.exe', 'tools/ReduxReceiver/lib/HIDMaestro-LICENSE.txt'):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'fixture')
            self.assertEqual(len(distribution_files(root)), 7)

    def test_only_the_public_root_list_may_be_a_pem(self):
        certificate = b'-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n'
        for name, content, allowed in (('_internal/certifi/cacert.pem', certificate, True),
                                       ('_internal/certifi/cacert.pem', certificate + b'-----BEGIN PRIVATE KEY-----', False),
                                       ('_internal/other/cacert.pem', certificate, False)):
            with self.subTest(name=name, allowed=allowed), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
                if allowed:
                    self.assertEqual(len(distribution_files(directory)), 1)
                else:
                    with self.assertRaises(ValueError):
                        distribution_files(directory)

    def test_user_data_and_arbitrary_files_block_packaging(self):
        for name in ('library/game.png', 'bios/firmware.bin', 'tools/cores/core.dll',
                     'accounts.dpapi', 'personal.txt', '_internal/.env', 'docs/private.key'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'fixture')
                with self.assertRaises(ValueError):
                    distribution_files(root)
