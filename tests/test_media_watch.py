"""Media transitions, retry timing and bounded worker shutdown without hardware."""
import ctypes
from dataclasses import dataclass
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from redux.media_watch import (
    MediaProbe, MediaWatcher, WindowsMediaProbe,
    FILE_READ_ATTRIBUTES, GENERIC_READ,
    IOCTL_STORAGE_CHECK_VERIFY, IOCTL_STORAGE_CHECK_VERIFY2,
)
from redux.optical import NoMediaError, OpticalError


@dataclass(frozen=True)
class Identity:
    fingerprint: str
    is_virtual: bool = False
    source_image: str | None = None


class Clock:
    def __init__(self): self.value = 100.0
    def __call__(self): return self.value
    def advance(self, amount=1.5): self.value += amount


class Harness:
    def __init__(self):
        self.clock = Clock()
        self.drives = ["G:"]
        self.probes = {"G:": MediaProbe("ready", 1, False, "MATSHITA")}
        self.identities = {"G:": Identity("saturn-disc")}
        self.calls = []
        self.forgotten = []
        self.enumeration_error = None
        self.watcher = MediaWatcher(drive_provider=self.drive_provider,
            prober=self, identifier=self.identify, clock=self.clock)

    def drive_provider(self):
        if self.enumeration_error: raise self.enumeration_error
        return self.drives

    def __call__(self, drive): return self.probes[drive]
    def forget(self, drive): self.forgotten.append(drive)
    def identify(self, drive):
        self.calls.append(drive)
        value = self.identities[drive]
        if isinstance(value, Exception): raise value
        return value
    def scan(self, advance=0):
        self.clock.advance(advance)
        self.watcher.scan_once()
        return self.watcher.poll_events()


class MediaTransitionTests(unittest.TestCase):
    def test_startup_existing_virtual_precedes_physical_and_stable_media_is_not_read_again(self):
        h = Harness()
        h.drives = ["G:", "F:"]
        h.probes["F:"] = MediaProbe("ready", 4, True, "WinCDEmu")
        h.identities["F:"] = Identity("diablo", True, "F:/diablo.iso")
        events = h.scan()
        self.assertEqual([e.drive for e in events], ["F:", "G:"])
        self.assertEqual([e.kind for e in events], ["inserted", "inserted"])
        self.assertTrue(all(e.startup for e in events))
        self.assertTrue(events[-1].physical)
        self.assertEqual(events[0].source_image, "F:/diablo.iso")
        first_observed = h.watcher.snapshot()["G:"].observed_at
        for _ in range(25): self.assertEqual(h.scan(1.5), [])
        self.assertEqual(h.calls, ["F:", "G:"])
        self.assertEqual(h.watcher.snapshot()["G:"].observed_at, first_observed)

    def test_counter_change_reidentifies_even_if_same_disc_is_reinserted_between_polls(self):
        h = Harness(); h.scan()
        h.probes["G:"] = MediaProbe("ready", 2, False, "MATSHITA")
        events = h.scan(1.5)
        self.assertEqual([e.kind for e in events], ["changed"])
        self.assertFalse(events[0].startup)
        self.assertEqual(events[0].fingerprint, "saturn-disc")
        self.assertEqual(h.calls, ["G:", "G:"])

    def test_virtual_removal_does_not_remove_physical_snapshot(self):
        h = Harness(); h.drives = ["F:", "G:"]
        h.probes["F:"] = MediaProbe("ready", 1, True, "WinCDEmu")
        h.identities["F:"] = Identity("diablo", True)
        h.scan(); h.probes["F:"] = MediaProbe("empty", reason="no_media")
        event, = h.scan(1.5)
        self.assertEqual((event.kind, event.drive), ("removed", "F:"))
        self.assertEqual(set(h.watcher.snapshot()), {"G:"})

    def test_broken_virtual_identity_does_not_prevent_physical_observation(self):
        h = Harness(); h.drives = ["F:", "G:"]
        h.probes["F:"] = MediaProbe("ready", 1, True, "WinCDEmu")
        h.identities["F:"] = TypeError("unexpected virtual decoder failure")
        events = h.scan()
        self.assertEqual([(e.kind, e.drive) for e in events], [("error", "F:"), ("inserted", "G:")])
        self.assertEqual(set(h.watcher.snapshot()), {"G:"})

    def test_missing_source_image_does_not_assert_physical_transport(self):
        h = Harness(); h.probes["G:"] = MediaProbe("ready", 1)
        event, = h.scan()
        self.assertIsNone(event.is_virtual)
        self.assertFalse(event.physical)

    def test_definitive_absence_and_reinsertion_rearm_without_repeat_removal_notices(self):
        h = Harness(); h.scan()
        h.probes["G:"] = MediaProbe("empty", reason="no_media")
        self.assertEqual([e.kind for e in h.scan(1.5)], ["removed"])
        self.assertEqual(h.watcher.snapshot(), {})
        self.assertEqual(h.scan(1.5), [])
        h.probes["G:"] = MediaProbe("ready", 1, False, "MATSHITA")
        event, = h.scan(1.5)
        self.assertEqual(event.kind, "inserted")
        self.assertFalse(event.startup)

    def test_new_usb_drive_is_observed_and_unplug_forgets_drive_cache(self):
        h = Harness(); h.drives = []; self.assertEqual(h.scan(), [])
        h.drives = ["G:"]; event, = h.scan(1.5)
        self.assertFalse(event.startup)
        h.drives = []; event, = h.scan(1.5)
        self.assertEqual((event.kind, event.reason), ("removed", "device_removed"))
        self.assertEqual(h.forgotten, ["G:"])
        h.drives = ["G:"]
        self.assertEqual(h.scan(1.5)[0].kind, "inserted")

    def test_transient_not_ready_retains_identity_but_persistent_unavailability_clears_distinctly(self):
        h = Harness(); h.scan()
        h.probes["G:"] = MediaProbe("pending", reason="not_ready")
        self.assertEqual(h.scan(1.5), [])
        self.assertEqual(h.scan(3), [])
        self.assertIn("G:", h.watcher.snapshot())
        h.probes["G:"] = MediaProbe("ready", 1, False, "MATSHITA")
        self.assertEqual(h.scan(1.5), [])
        h.probes["G:"] = MediaProbe("pending", reason="not_ready")
        h.scan(1.5)
        event, = h.scan(6)
        self.assertEqual((event.kind, event.reason), ("removed", "not_ready_timeout"))
        h.probes["G:"] = MediaProbe("ready", 1, False, "MATSHITA")
        self.assertEqual(h.scan(1.5)[0].kind, "inserted")

    def test_busy_or_read_error_does_not_claim_ejection(self):
        h = Harness(); h.scan()
        h.probes["G:"] = MediaProbe("pending", reason="busy")
        self.assertEqual(h.scan(30), [])
        self.assertEqual(h.scan(30), [])
        h.probes["G:"] = MediaProbe("error", error="Access denied", reason="access_denied")
        event, = h.scan(30)
        self.assertEqual(event.kind, "error")
        self.assertEqual(h.scan(30), [])
        self.assertIn("G:", h.watcher.snapshot())

    def test_media_changed_verify_hint_rearms_after_ready(self):
        h = Harness(); h.scan()
        h.probes["G:"] = MediaProbe("pending", reason="media_changed")
        self.assertEqual(h.scan(1.5), [])
        h.probes["G:"] = MediaProbe("ready", 1, False, "MATSHITA")
        self.assertEqual(h.scan(1.5)[0].kind, "changed")

    def test_identification_spinup_retries_are_deferred_without_noise(self):
        h = Harness()
        h.identities["G:"] = NoMediaError("Not ready", winerror=21)
        self.assertEqual(h.scan(), [])
        self.assertEqual(h.scan(.5), [])
        self.assertEqual(h.calls, ["G:"])
        self.assertEqual(h.scan(1), [])
        self.assertEqual(h.calls, ["G:", "G:"])
        h.identities["G:"] = Identity("saturn-disc")
        self.assertEqual(h.scan(1), [])
        self.assertEqual(h.scan(2)[0].kind, "inserted")

    def test_unsupported_change_counter_uses_bounded_fallback_and_deduplicates_same_identity(self):
        h = Harness(); h.probes["G:"] = MediaProbe("unknown", is_virtual=False)
        h.scan()
        for _ in range(9): self.assertEqual(h.scan(1.5), [])
        self.assertEqual(len(h.calls), 1)
        self.assertEqual(h.scan(1.5), [])
        self.assertEqual(len(h.calls), 2)
        h.identities["G:"] = Identity("new-disc")
        event, = h.scan(15)
        self.assertEqual((event.kind, event.fingerprint), ("changed", "new-disc"))

    def test_enumeration_failure_keeps_last_identity_and_error_is_deduplicated(self):
        h = Harness(); h.scan(); h.enumeration_error = OSError("drive enumeration failed")
        event, = h.scan(1.5)
        self.assertEqual(event.reason, "enumeration_error")
        self.assertEqual(h.scan(1.5), [])
        self.assertIn("G:", h.watcher.snapshot())
        h.enumeration_error = None
        self.assertEqual(h.scan(1.5), [])

    def test_identity_error_retains_last_media_deduplicates_and_recovers(self):
        h = Harness(); h.scan()
        h.probes["G:"] = MediaProbe("ready", 2, False, "MATSHITA")
        h.identities["G:"] = OpticalError("Drive cannot read header", reason="read_error")
        event, = h.scan(1.5)
        self.assertEqual(event.reason, "read_error")
        self.assertEqual(h.scan(1.5), [])
        self.assertIn("G:", h.watcher.snapshot())
        h.identities["G:"] = Identity("new-disc")
        event, = h.scan(3)
        self.assertEqual((event.kind, event.fingerprint), ("changed", "new-disc"))

    def test_empty_at_startup_does_not_make_later_insertion_a_startup_event(self):
        h = Harness(); h.probes["G:"] = MediaProbe("empty")
        self.assertEqual(h.scan(), [])
        h.probes["G:"] = MediaProbe("ready", 1)
        self.assertFalse(h.scan(1.5)[0].startup)

    def test_invalid_parameters_are_rejected(self):
        for parameter in ["interval", "fallback_interval", "not_ready_timeout"]:
            for value in [True, float("nan"), float("inf"), -1, "bad"]:
                with self.subTest(parameter=parameter, value=value), self.assertRaises(ValueError):
                    MediaWatcher(**{parameter: value})
        for timeout in [True, float("nan"), float("inf"), -1, 6]:
            with self.assertRaises(ValueError): MediaWatcher().close(timeout)

    def test_blocked_identifier_does_not_block_ui_close_or_emit_after_close(self):
        entered = threading.Event(); release = threading.Event(); h = Harness()
        def blocked(drive):
            entered.set(); release.wait(3); return Identity("late")
        h.watcher._identifier = blocked
        h.watcher.start()
        try:
            self.assertTrue(entered.wait(1))
            started = time.monotonic()
            self.assertFalse(h.watcher.close(.02))
            self.assertLess(time.monotonic()-started, .25)
            self.assertEqual(h.watcher.poll_events(), [])
            release.set()
            self.assertTrue(h.watcher.close(1))
            self.assertEqual(h.watcher.poll_events(), [])
            with self.assertRaises(RuntimeError): h.watcher.start()
        finally:
            release.set(); h.watcher.close(1)


class NativeAPI:
    def __init__(self, answers):
        self.answers = list(answers); self.calls = []; self.opens = []; self.closed = []
        self.error = 0
        self.ctypes = SimpleNamespace(byref=ctypes.byref, c_void_p=ctypes.c_void_p,
            get_last_error=lambda: self.error)
        self.wintypes = SimpleNamespace(DWORD=ctypes.c_uint32)
        self.kernel = SimpleNamespace(CreateFileW=self.open, DeviceIoControl=self.query,
            CloseHandle=self.closed.append)
    def open(self, path, access, share, security, disposition, flags, template):
        self.opens.append((path, access, share, disposition, flags)); return len(self.opens)
    def query(self, handle, code, source, source_size, output, size, returned, overlap):
        self.calls.append((handle, code, source_size, size))
        success, value, number = self.answers.pop(0); self.error = number
        ctypes.cast(returned, ctypes.POINTER(ctypes.c_uint32))[0] = 4 if value is not None else 0
        if value is not None: ctypes.cast(output, ctypes.POINTER(ctypes.c_uint32))[0] = value
        return success


class NativeProbeTests(unittest.TestCase):
    def test_verify2_is_attributes_only_and_reuses_standard_product_metadata(self):
        api = NativeAPI([(True, 7, 0), (True, 7, 0)])
        probe = WindowsMediaProbe(api=api)
        with patch("redux.mounted_images._product", return_value="MATSHITA BD-MLT UJ272") as product:
            first = probe("G:"); second = probe("G:")
        self.assertEqual((first.state, first.change_count, first.is_virtual), ("ready", 7, False))
        self.assertEqual(first, second); product.assert_called_once()
        self.assertEqual([item[1] for item in api.opens], [FILE_READ_ATTRIBUTES]*2)
        self.assertEqual([item[1] for item in api.calls], [IOCTL_STORAGE_CHECK_VERIFY2]*2)
        self.assertTrue(all(item[2:] == (3, 3, 0) for item in api.opens))
        self.assertEqual(api.closed, [1, 2])

    def test_unsupported_verify2_falls_back_to_read_only_verify_and_caches_choice(self):
        api = NativeAPI([(False, None, 1), (True, 8, 0), (True, 8, 0)])
        probe = WindowsMediaProbe(api=api)
        with patch("redux.mounted_images._product", return_value="WinCDEmu"):
            self.assertTrue(probe("F:").is_virtual); probe("F:")
        self.assertEqual([item[1] for item in api.opens], [FILE_READ_ATTRIBUTES, GENERIC_READ, GENERIC_READ])
        self.assertEqual([item[1] for item in api.calls],
            [IOCTL_STORAGE_CHECK_VERIFY2, IOCTL_STORAGE_CHECK_VERIFY, IOCTL_STORAGE_CHECK_VERIFY])
        self.assertEqual(api.closed, [1, 2, 3])

    def test_native_error_classifications_do_not_conflate_not_ready_busy_and_no_media(self):
        expected = {21:("pending", "not_ready"), 32:("pending", "busy"),
            1112:("empty", "no_media"), 1110:("pending", "media_changed"),
            1167:("missing", "device_removed"), 5:("error", "access_denied")}
        for number, result in expected.items():
            with self.subTest(number=number):
                probe = WindowsMediaProbe._failed(number, False, "MATSHITA")
                self.assertEqual((probe.state, probe.reason), result)

    def test_success_without_counter_is_explicitly_unknown_count(self):
        api = NativeAPI([(True, None, 0), (False, None, 1), (True, None, 0)])
        watcher_probe = WindowsMediaProbe(api=api)
        with patch("redux.mounted_images._product", return_value=None):
            probe = watcher_probe("G:")
            watcher_probe("G:")
        self.assertEqual(probe.state, "ready")
        self.assertIsNone(probe.change_count)
        self.assertIsNone(probe.is_virtual)
        self.assertEqual([item[1] for item in api.calls],
            [IOCTL_STORAGE_CHECK_VERIFY2, IOCTL_STORAGE_CHECK_VERIFY, IOCTL_STORAGE_CHECK_VERIFY2])

    def test_verify2_without_counter_uses_ordinary_verify_counter_on_future_polls(self):
        api = NativeAPI([(True, None, 0), (True, 2, 0), (True, 2, 0)])
        probe = WindowsMediaProbe(api=api)
        with patch("redux.mounted_images._product", return_value="MATSHITA"):
            self.assertEqual(probe("G:").change_count, 2)
            self.assertEqual(probe("G:").change_count, 2)
        self.assertEqual([item[1] for item in api.calls],
            [IOCTL_STORAGE_CHECK_VERIFY2, IOCTL_STORAGE_CHECK_VERIFY, IOCTL_STORAGE_CHECK_VERIFY])


if __name__ == "__main__": unittest.main()
