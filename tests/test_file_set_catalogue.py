"""Host catalogue integration: cached set members retain live import identity."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from rom_hub.census import Catalogue, build
from rom_hub_sdk import CensusPage, CensusRecord, CensusUnit

from archive_org.census import Census
from archive_org.file_sets import FileSets
from archive_org.importer import Importer
from test_zip_sets import FakeHTTP


class CatalogueProvider:
    def __init__(self, http, mappings):
        self.http = http
        self.config = {"file_sets": mappings}
        ctx = type("Context", (), {"http": http, "config": self.config})()
        self.census = Census(ctx)
        self.ctx = ctx
        self.item = CensusUnit(unit_id="item-only", label="item title",
                               kind="roms", declared_total=1)

    def census_scope(self):
        file_unit_id, identifier, archive = self.census._file_units()[0]
        return [self.item, CensusUnit(
            unit_id=file_unit_id, label="file members", kind="roms",
            declared_total=None,
        )]

    def census_page(self, unit, cursor):
        if unit.unit_id == self.item.unit_id:
            return CensusPage(records=[CensusRecord(
                record_id="item-title-only", title="Same Game (Europe)",
                platform="psx",
            )], declared_total=1)
        return self.census.enumerate(unit, cursor)

    def search(self, *args):
        raise AssertionError("live search must never be used")

    def close(self):
        pass


class CatalogueIntegrationTests(unittest.TestCase):
    def test_cached_item_and_zip_results_and_import(self):
        http = FakeHTTP("set-one", "PSX.zip", [
            "Same Game (USA).chd", "Other Game.chd",
        ])
        mappings = ["set-one/PSX.zip:psx"]
        provider = CatalogueProvider(http, mappings)
        with TemporaryDirectory() as temp:
            with Catalogue(Path(temp) / "catalogue.db") as catalog:
                build(lambda: provider, catalog, slug="archive-org")
                matches = catalog.results("Same Game", platform="psx")
                self.assertEqual(len(matches), 2)
                zip_result = next(r for r in matches if r.source_id.startswith("zip:"))
                live_result = FileSets(http, provider.config).search(
                    "Same Game", "psx", 10)[0]
                self.assertEqual(zip_result.source_id, live_result.source_id)
                self.assertEqual(zip_result.platform, live_result.platform)
                from_cache = Importer(provider.ctx).plan(zip_result)
                from_live = Importer(provider.ctx).plan(live_result)
                self.assertEqual(from_cache, from_live)

    def test_changed_mapping_and_removal_clear_cached_members(self):
        http = FakeHTTP("set-one", "PSX.zip", ["PS2/Example Game.iso"])
        with TemporaryDirectory() as temp:
            with Catalogue(Path(temp) / "catalogue.db") as catalog:
                first = CatalogueProvider(http, ["set-one/PSX.zip:psx"])
                build(lambda: first, catalog, slug="archive-org")
                self.assertEqual(catalog.results("Example", platform="psx")[0].platform, "psx")
                changed = CatalogueProvider(http, [
                    "set-one/PSX.zip:psx", "set-one/PSX.zip/PS2:ps2",
                ])
                build(lambda: changed, catalog, slug="archive-org")
                self.assertFalse(catalog.results("Example", platform="psx"))
                self.assertEqual(catalog.results("Example", platform="ps2")[0].platform, "ps2")

if __name__ == "__main__":
    unittest.main()
