"""Physical media selection and stale asynchronous metadata/artwork guards."""
from types import SimpleNamespace
import unittest

from redux.media_identity import MediaIdentity
from redux.now_playing import DiscRegistry


def event(kind, drive="G:", fingerprint="disc-a", virtual=False, title="Observed game", time=1):
    identity = None if kind in {"removed", "error"} else MediaIdentity(title, "saturn", "MK-81113", "Saturn CD", "header")
    return SimpleNamespace(kind=kind, drive=drive, fingerprint=fingerprint, identity=identity,
                           is_virtual=virtual, monotonic_at=time, startup=time == 1)


class DiscRegistryTests(unittest.TestCase):
    def test_startup_virtual_yields_to_physical_and_later_virtual_cannot_steal_it(self):
        registry = DiscRegistry()
        self.assertIsNone(registry.active)
        self.assertTrue(registry.apply(event("inserted", "H:", "image", True)))
        self.assertEqual(registry.active.drive, "H:")
        self.assertTrue(registry.apply(event("inserted", "G:", "physical", False, time=2)))
        self.assertEqual(registry.active.drive, "G:")
        self.assertFalse(registry.apply(event("inserted", "I:", "image-two", True, time=3)))
        self.assertEqual(registry.active.drive, "G:")
        self.assertTrue(registry.apply(event("removed", "G:", "physical", time=4)))
        self.assertEqual(registry.active.drive, "I:")

    def test_newest_physical_wins_and_removal_chooses_remaining_physical_before_virtual(self):
        registry = DiscRegistry()
        registry.apply(event("inserted", "H:", "image", True))
        registry.apply(event("inserted", "G:", "first", time=2))
        self.assertTrue(registry.apply(event("inserted", "F:", "second", time=3)))
        self.assertEqual(registry.active.drive, "F:")
        self.assertFalse(registry.apply(event("removed", "H:", "image", True, time=4)))
        self.assertEqual(registry.active.drive, "F:")
        self.assertTrue(registry.apply(event("removed", "F:", "second", time=5)))
        self.assertEqual(registry.active.drive, "G:")
        self.assertTrue(registry.apply(event("removed", "G:", "first", time=6)))
        self.assertIsNone(registry.active)

    def test_duplicate_observation_preserves_priority_metadata_and_artwork(self):
        registry = DiscRegistry()
        registry.apply(event("inserted", "G:", "first"))
        registry.update("g:\\", "first", {"title": "Canonical title", "year": 1996},
                        {"cover_path": "cover.png", "hero_path": "hero.png"})
        registry.apply(event("inserted", "F:", "second", time=2))
        self.assertFalse(registry.apply(event("changed", "G:", "first", time=3)))
        self.assertEqual(registry.active.drive, "F:")
        registry.apply(event("removed", "F:", "second", time=4))
        self.assertEqual((registry.active.title, registry.active.cover_path, registry.active.hero_path),
                         ("Canonical title", "cover.png", "hero.png"))

    def test_replacement_clears_old_art_and_rejects_delayed_worker_or_removal(self):
        registry = DiscRegistry()
        registry.apply(event("inserted"))
        self.assertTrue(registry.update("G:", "disc-a", {"title": "Canonical A"}, {"hero_path": "a.png"}))
        self.assertTrue(registry.apply(event("changed", fingerprint="disc-b", title="Observed B", time=2)))
        self.assertEqual(registry.active.title, "Observed B")
        self.assertIsNone(registry.active.hero_path)
        self.assertEqual(registry.active.metadata, {})
        self.assertFalse(registry.update("G:", "disc-a", {"title": "Wrong game"}, {"hero_path": "a.png"}))
        self.assertFalse(registry.apply(event("removed", fingerprint="disc-a", time=3)))
        self.assertEqual(registry.active.fingerprint, "disc-b")
        self.assertFalse(registry.apply(event("changed", fingerprint="disc-a", time=1)))

    def test_errors_and_incomplete_events_never_clear_valid_disc(self):
        registry = DiscRegistry()
        registry.apply(event("inserted"))
        before = registry.active
        for incoming in (event("error", time=2), event("pending", time=3),
                         {"kind": "changed", "drive": "G:", "identity": None},
                         {"kind": "inserted", "drive": "G:", "identity": object()},
                         {"kind": "removed", "drive": "bad"}):
            self.assertFalse(registry.apply(incoming))
            self.assertEqual(registry.active, before)

    def test_metadata_title_overrides_header_and_returned_metadata_is_independent(self):
        registry = DiscRegistry()
        registry.apply(event("inserted"))
        metadata = {"title": "World Series Baseball II", "tags": ["sports"]}
        self.assertTrue(registry.update("g", "disc-a", metadata, {"cover_path": "cover.png"}))
        metadata["tags"].append("caller mutation")
        active = registry.active
        active.metadata["tags"].append("snapshot mutation")
        self.assertEqual(registry.active.metadata["tags"], ["sports"])
        self.assertEqual(registry.active.title, "World Series Baseball II")
        self.assertEqual(registry.active.identity.title, "Observed game")
        self.assertFalse(registry.update("G:", "disc-a", artwork={"cover_path": None, "hero_path": None}))
        self.assertEqual(registry.active.cover_path, "cover.png")

    def test_nonactive_updates_are_retained_without_changing_visible_selection(self):
        registry = DiscRegistry()
        registry.apply(event("inserted", "H:", "image", True))
        registry.apply(event("inserted", "G:", "physical", time=2))
        self.assertFalse(registry.update("H:", "image", {"title": "Image title"}, {"hero_path": "image.png"}))
        registry.apply(event("removed", "G:", "physical", time=3))
        self.assertEqual((registry.active.title, registry.active.hero_path), ("Image title", "image.png"))


if __name__ == "__main__":
    unittest.main()
