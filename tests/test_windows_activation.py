"""Shell activation parsing and real user-protected Windows pipe forwarding."""
import ctypes
from ctypes import wintypes
import json
from dataclasses import replace
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

from redux.windows_activation import (
    HOME_ACTIVATION, SingleInstance, _pipe_client, _receive, _user_sid,
    append_activation_log, parse_activation,
)


class ActivationParsingTests(unittest.TestCase):
    def test_home_uri_forces_fullscreen_and_preserves_unknown_arguments(self):
        activation = parse_activation([HOME_ACTIVATION, "--shell-extra", "42"])
        self.assertTrue(activation.home)
        self.assertTrue(activation.fullscreen)
        self.assertFalse(activation.startup)
        self.assertEqual(activation.remaining, ("--shell-extra", "42"))

    def test_startup_flags_anywhere_do_not_change_existing_cli_arguments(self):
        activation = parse_activation(["--library", "C:\\Games", "gui", "--fullscreen", "--startup"])
        self.assertTrue(activation.fullscreen)
        self.assertTrue(activation.startup)
        self.assertEqual(activation.remaining, ("--library", "C:\\Games", "gui"))

    def test_cli_is_unchanged_without_shell_markers(self):
        argv = ["--library", "library", "scan", "games"]
        activation = parse_activation(argv)
        self.assertEqual(activation.remaining, tuple(argv))
        self.assertEqual(activation.argv, tuple(argv))
        self.assertFalse(activation.home or activation.startup or activation.fullscreen)

    def test_case_insensitive_home_uri(self):
        self.assertTrue(parse_activation([HOME_ACTIVATION.upper()]).home)

    def test_log_contains_activation_parent_and_injected_platform_status(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "launches.jsonl"
            activation = parse_activation([HOME_ACTIVATION])
            with patch("redux.windows_activation.parent_process_details",
                       return_value={"pid": 111, "executable": "C:\\Windows\\sihost.exe"}):
                result = append_activation_log(activation, package="Redux_1.0_x64__test",
                                               fse_active=True, path=target)
            self.assertEqual(result, target)
            record = json.loads(target.read_text())
            self.assertEqual(record["parent"]["pid"], 111)
            self.assertEqual(record["argv"], [HOME_ACTIVATION])
            self.assertTrue(record["home"])
            self.assertTrue(record["fse_active"])
            self.assertEqual(record["package_full_name"], "Redux_1.0_x64__test")

    def test_forwarded_log_keeps_original_process_and_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "launches.jsonl"
            activation = replace(parse_activation([HOME_ACTIVATION]), source_pid=201,
                                 source_parent_pid=111, source_parent_executable="C:\\Windows\\sihost.exe")
            with patch("redux.windows_activation.parent_process_details", side_effect=AssertionError("wrong parent")):
                result = append_activation_log(activation, path=target)
            self.assertEqual(result, target)
            record = json.loads(target.read_text())
            self.assertEqual(record["pid"], 201)
            self.assertEqual(record["receiver_pid"], os.getpid())
            self.assertTrue(record["forwarded"])
            self.assertEqual(record["parent"], {"pid": 111, "executable": "C:\\Windows\\sihost.exe"})

    def test_unwritable_log_does_not_block_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "parent"
            target.write_text("file, not directory")
            self.assertIsNone(append_activation_log(parse_activation([]), path=target / "log"))

    def test_failed_diagnostics_helper_does_not_block_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("redux.windows_activation.parent_process_details", side_effect=RuntimeError("query failed")):
                self.assertIsNone(append_activation_log(parse_activation([]), path=Path(directory) / "log"))

    def test_default_log_uses_shared_windows_data_resolver_not_inherited_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            ordinary = Path(directory) / "ordinary-user-data" / "ReduxMediaLibrary"
            inherited_package_path = Path(directory) / "Xbox-package-view"
            with patch("redux.windows_paths.redux_user_data", return_value=ordinary), \
                 patch.dict(os.environ, {"LOCALAPPDATA": str(inherited_package_path)}):
                result = append_activation_log(parse_activation([HOME_ACTIVATION]))
            self.assertEqual(result, ordinary / "logs" / "activations.jsonl")
            self.assertTrue(result.is_file())
            self.assertFalse(inherited_package_path.exists())

    def test_non_windows_instance_has_no_windows_side_effects(self):
        with patch("redux.windows_activation.sys.platform", "linux"):
            instance = SingleInstance()
            self.assertTrue(instance.acquire(parse_activation([])))
            instance.close()


@unittest.skipUnless(sys.platform == "win32", "Windows mutex and named pipes required")
class WindowsSingleInstanceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.namespace = "Redux.Test." + uuid.uuid4().hex
        self.owner = SingleInstance(self.namespace, self.temporary.name)
        self.assertTrue(self.owner.acquire(parse_activation([])))

    def tearDown(self):
        self.owner.close()
        self.temporary.cleanup()

    def test_second_activation_reaches_owner_without_another_instance(self):
        other = SingleInstance(self.namespace, self.temporary.name)
        try:
            activation = parse_activation([HOME_ACTIVATION, "--from-shell"])
            self.assertFalse(other.acquire(activation))
            self.assertEqual(self.owner.activations.get(timeout=2), activation)
            self.assertTrue(self.owner.activations.empty())
        finally:
            other.close()

    def test_forwarding_grants_foreground_permission_before_owner_handles_it(self):
        other = SingleInstance(self.namespace, self.temporary.name)
        try:
            with patch("redux.windows_activation._allow_foreground", return_value=False) as allow:
                self.assertFalse(other.acquire(parse_activation([HOME_ACTIVATION])))
            allow.assert_called_once_with(os.getpid())
            self.assertTrue(self.owner.activations.get(timeout=2).home)
        finally:
            other.close()

    def test_pipe_acl_grants_current_user_system_admin_and_excludes_everyone(self):
        security = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        with _pipe_client(self.owner._address) as connection:
            descriptor = ctypes.c_void_p()
            security.GetSecurityInfo.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.DWORD,
                                                 ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                                 ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
            security.GetSecurityInfo.restype = wintypes.DWORD
            self.assertEqual(security.GetSecurityInfo(connection.fileno(), 6, 4, None, None, None, None,
                                                      ctypes.byref(descriptor)), 0)
            try:
                text = wintypes.LPWSTR()
                security.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
                    ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(wintypes.LPWSTR), ctypes.c_void_p]
                security.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype = wintypes.BOOL
                self.assertTrue(security.ConvertSecurityDescriptorToStringSecurityDescriptorW(
                    descriptor, 1, 4, ctypes.byref(text), None))
                try:
                    # SDDL may abbreviate the built-in Administrator SID as
                    # LA. Inspect binary ACE SIDs rather than textual aliases.
                    present, defaulted = wintypes.BOOL(), wintypes.BOOL()
                    acl = ctypes.c_void_p()
                    security.GetSecurityDescriptorDacl.argtypes = [ctypes.c_void_p,
                        ctypes.POINTER(wintypes.BOOL), ctypes.POINTER(ctypes.c_void_p),
                        ctypes.POINTER(wintypes.BOOL)]
                    security.GetSecurityDescriptorDacl.restype = wintypes.BOOL
                    self.assertTrue(security.GetSecurityDescriptorDacl(descriptor,
                        ctypes.byref(present), ctypes.byref(acl), ctypes.byref(defaulted)))
                    self.assertTrue(present.value and acl.value)
                    ace_count = ctypes.c_ushort.from_address(acl.value + 4).value
                    security.GetAce.argtypes = [ctypes.c_void_p, wintypes.DWORD,
                                                ctypes.POINTER(ctypes.c_void_p)]
                    security.GetAce.restype = wintypes.BOOL
                    security.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p,
                                                               ctypes.POINTER(wintypes.LPWSTR)]
                    security.ConvertSidToStringSidW.restype = wintypes.BOOL
                    kernel.LocalFree.argtypes = [ctypes.c_void_p]
                    sids = []
                    for index in range(ace_count):
                        ace, sid_text = ctypes.c_void_p(), wintypes.LPWSTR()
                        self.assertTrue(security.GetAce(acl, index, ctypes.byref(ace)))
                        self.assertEqual(ctypes.c_ubyte.from_address(ace.value).value, 0)
                        self.assertTrue(security.ConvertSidToStringSidW(ace.value + 8,
                                                                      ctypes.byref(sid_text)))
                        try:
                            sids.append(sid_text.value)
                        finally:
                            kernel.LocalFree(ctypes.cast(sid_text, ctypes.c_void_p))
                    self.assertIn(_user_sid(), sids)
                    self.assertIn(";;;SY)", text.value)
                    self.assertIn(";;;BA)", text.value)
                    self.assertNotIn(";;;WD)", text.value)
                    self.assertNotIn(";;;AU)", text.value)
                finally:
                    kernel.LocalFree.argtypes = [ctypes.c_void_p]
                    kernel.LocalFree(ctypes.cast(text, ctypes.c_void_p))
            finally:
                kernel.LocalFree(descriptor)

    def test_forwarding_is_independent_of_runtime_folder_or_package_redirection(self):
        blocked = Path(self.temporary.name) / "ordinary-file"
        blocked.write_text("not a folder")
        other = SingleInstance(self.namespace, blocked / "different-package-view")
        try:
            self.assertFalse(other.acquire(parse_activation([HOME_ACTIVATION])))
            self.assertTrue(self.owner.activations.get(timeout=2).home)
            self.assertFalse((Path(self.temporary.name) / "runtime").exists())
        finally:
            other.close()

    def test_real_second_process_forwards_arguments_and_exits(self):
        code = (
            "from redux.windows_activation import SingleInstance,parse_activation,parent_process_details;"
            "import sys,os,json;"
            "print(json.dumps({'pid':os.getpid(),'parent':parent_process_details()}));"
            "i=SingleInstance(sys.argv[1],sys.argv[2]);"
            "owner=i.acquire(parse_activation(sys.argv[3:]));"
            "i.close();"
            "raise SystemExit(9 if owner else 0)"
        )
        process = subprocess.run([sys.executable, "-c", code, self.namespace,
                                  self.temporary.name, HOME_ACTIVATION, "--fullscreen"],
                                 capture_output=True, text=True, timeout=10,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(process.returncode, 0, process.stderr)
        received = self.owner.activations.get(timeout=2)
        self.assertTrue(received.home)
        self.assertTrue(received.fullscreen)
        self.assertEqual(received.argv, (HOME_ACTIVATION, "--fullscreen"))
        self.assertIsInstance(received.source_pid, int)
        self.assertNotEqual(received.source_pid, os.getpid())
        sender = json.loads(process.stdout)
        self.assertEqual(received.source_pid, sender["pid"])
        self.assertEqual(received.source_parent_pid, sender["parent"]["pid"])
        self.assertEqual(received.source_parent_executable, sender["parent"]["executable"])

    def test_forwarded_activation_preserves_origin_parent_metadata(self):
        other = SingleInstance(self.namespace, self.temporary.name)
        try:
            with patch("redux.windows_activation.parent_process_details", return_value={"pid": 711, "executable": "C:\\Windows\\sihost.exe"}):
                self.assertFalse(other.acquire(parse_activation([HOME_ACTIVATION])))
            received = self.owner.activations.get(timeout=2)
            self.assertEqual(received.source_pid, os.getpid())
            self.assertEqual(received.source_parent_pid, 711)
            self.assertEqual(received.source_parent_executable, "C:\\Windows\\sihost.exe")
        finally:
            other.close()

    def test_invalid_payload_does_not_kill_listener(self):
        with _pipe_client(self.owner._address) as connection:
            connection.send_bytes(b'[]')
        # Wait for the server to recycle its next listening pipe.
        other = SingleInstance(self.namespace, self.temporary.name)
        try:
            self.assertFalse(other.acquire(parse_activation(["--startup"])))
            self.assertTrue(self.owner.activations.get(timeout=2).startup)
        finally:
            other.close()

    def test_invalid_argument_types_are_rejected(self):
        with _pipe_client(self.owner._address) as connection:
            connection.send_bytes(json.dumps({"argv": [123]}).encode())
            self.assertFalse(_receive(connection)["accepted"])
        self.assertTrue(self.owner.activations.empty())

    def test_owner_close_releases_pipe_and_allows_new_owner(self):
        self.owner.close()
        replacement = SingleInstance(self.namespace, self.temporary.name)
        try:
            self.assertTrue(replacement.acquire(parse_activation([])))
        finally:
            replacement.close()

class ActivationCleanupTests(unittest.TestCase):
    def test_concurrent_shutdown_closes_one_connection_once(self):
        from unittest.mock import Mock
        instance = SingleInstance('Redux.Test.Cleanup')
        connection = Mock()
        instance._connection = connection
        barrier = threading.Barrier(3)
        failures = []

        def shutdown():
            try:
                barrier.wait(timeout=2)
                instance._close_connection(connection)
            except BaseException as error:
                failures.append(error)

        workers = [threading.Thread(target=shutdown) for _ in range(2)]
        for worker in workers:
            worker.start()
        barrier.wait(timeout=2)
        for worker in workers:
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive())
        self.assertEqual(failures, [])
        connection.close.assert_called_once_with()
        self.assertIsNone(instance._connection)


if __name__ == "__main__":
    unittest.main()
