"""Offline regression tests for indexed Archive.org file sets."""
import unittest

from rom_hub_sdk import SearchResult

from archive_org.census import Census, SKIP_SET_EXCLUDED, SKIP_SET_PARENT_ZIP
from archive_org.file_sets import FileSets
from archive_org.importer import Importer
from test_file_sets import ArchiveHTTP, game
from test_zip_sets import FakeHTTP


class FakeIndex:
    def total(self, query):
        return 10 if "NOT item_size:" not in query else 0


class CensusSetTests(unittest.TestCase):
    @staticmethod
    def census(http, mappings):
        ctx = type("Context", (), {
            "http": http,
            "config": {"file_sets": mappings},
        })()
        census = Census(ctx)
        census._index = lambda: FakeIndex()
        return census

    def set_unit(self, census):
        return next(unit for unit in census.scope()
                    if unit.unit_id.startswith("file-set:"))

    def test_direct_id_and_platform_agree_with_search_and_import(self):
        http = ArchiveHTTP({"set-one": {"files": [
            game("PSX/Game.chd"), game("PS2/Game.iso"), game("notes.txt"),
        ]}})
        mappings = ["set-one:psx", "set-one/PS2:ps2"]
        census = self.census(http, mappings)
        unit = self.set_unit(census)
        page = census.enumerate(unit, None)
        self.assertIsNone(page.cursor)
        self.assertEqual(page.declared_total, 3)
        self.assertEqual(page.skipped.get(SKIP_SET_EXCLUDED), 1)
        self.assertEqual({r.platform for r in page.records}, {"psx", "ps2"})
        sets = FileSets(http, {"file_sets": mappings})
        for record in page.records:
            search = sets.search(record.title, record.platform, 10)
            hit = next(r for r in search if r.source_id == record.record_id)
            self.assertEqual(record.platform, hit.platform)
            plan = Importer(type("Context", (), {
                "http": http, "config": {"file_sets": mappings}
            })()).plan(hit)
            self.assertEqual(plan.platform, record.platform)

    def test_direct_cursor_survives_fresh_process(self):
        entries = [game(f"Game {i:04}.chd") for i in range(402)]
        http = ArchiveHTTP({"set-one": {"files": entries}})
        census = self.census(http, ["set-one:psx"])
        unit = self.set_unit(census)
        first = census.enumerate(unit, None)
        self.assertEqual(len(first.records), 400)
        self.assertEqual(first.declared_total, 402)
        self.assertIsNotNone(first.cursor)
        second = self.census(http, ["set-one:psx"]).enumerate(unit, first.cursor)
        self.assertEqual(len(second.records), 2)
        self.assertEqual(second.declared_total, 402)
        self.assertIsNone(second.cursor)
        self.assertEqual(len({r.record_id for r in first.records + second.records}), 402)

    def test_scope_change_moves_unit_and_platform(self):
        http = ArchiveHTTP({"set-one": {"files": [game("PS2/Game.iso")]}})
        old = self.census(http, ["set-one:psx"])
        new = self.census(http, ["set-one:psx", "set-one/PS2:ps2"])
        old_unit = self.set_unit(old)
        new_unit = self.set_unit(new)
        self.assertNotEqual(old_unit.unit_id, new_unit.unit_id)
        self.assertEqual(old.enumerate(old_unit, None).records[0].platform, "psx")
        self.assertEqual(new.enumerate(new_unit, None).records[0].platform, "ps2")

    def test_configured_zip_is_one_container_not_a_direct_game(self):
        http = FakeHTTP("set-one", "PSX.zip", ["Inside Game.chd", "readme.txt"])
        http.files.append({"name": "Loose Game.chd", "size": "1234"})
        mappings = ["set-one:psx", "set-one/PSX.zip:psx"]
        sets = FileSets(http, {"file_sets": mappings})
        results = sets.search("", "psx", 10)
        self.assertEqual({r.title for r in results}, {"Loose Game", "Inside Game"})
        self.assertEqual(len(results), 2)
        self.assertFalse(any(r.source_id == "set:set-one:PSX.zip" for r in results))

        census = self.census(http, mappings)
        file_units = [u for u in census.scope() if u.unit_id.startswith("file-set:")]
        self.assertEqual(len(file_units), 2)
        pages = [census.enumerate(unit, None) for unit in file_units]
        direct = next(page for page in pages if page.declared_total is not None)
        zipped = next(page for page in pages if page.declared_total is None)
        self.assertEqual(direct.declared_total, 2)
        self.assertEqual([r.title for r in direct.records], ["Loose Game"])
        self.assertEqual(direct.skipped[SKIP_SET_PARENT_ZIP], 1)
        self.assertEqual([r.title for r in zipped.records], ["Inside Game"])
        self.assertEqual(zipped.skipped[SKIP_SET_EXCLUDED], 1)
        self.assertEqual({r.record_id for page in pages for r in page.records},
                         {r.source_id for r in results})

    def test_zip_member_id_and_resumability(self):
        http = FakeHTTP("set-one", "PSX.zip", [
            "PSX/Game.chd", "PS2/Game.iso", "image.png",
        ])
        mappings = ["set-one/PSX.zip:psx", "set-one/PSX.zip/PS2:ps2"]
        census = self.census(http, mappings)
        unit = self.set_unit(census)
        page = census.enumerate(unit, None)
        self.assertIsNone(page.declared_total)  # No independent member denominator.
        self.assertEqual(len(page.records), 2)
        self.assertEqual(page.skipped[SKIP_SET_EXCLUDED], 1)
        sets = FileSets(http, {"file_sets": mappings})
        for record in page.records:
            hits = sets.search(record.title, record.platform, 10)
            hit = next(r for r in hits if r.source_id == record.record_id)
            self.assertEqual(sets.plan(hit).platform, record.platform)


if __name__ == "__main__":
    unittest.main()
