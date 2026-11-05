"""Automatic item discovery through live search, census and import planning."""
import unittest
from unittest.mock import patch

from archive_org.file_sets import FileSets, FileSetError, SetSource
from archive_org.census import Census, CensusUnavailable
from archive_org.set_platforms import infer_platform, within_depth
from test_zip_sets import FakeHTTP, Reply
from test_file_set_census import FakeIndex


class AutomaticTests(unittest.TestCase):
    def fixture(self, archive='Sony - PSP (A-G).zip', members=None):
        http = FakeHTTP('set-one', archive, members or ['Crisis Core (USA).iso'])
        config = {'file_sets': ['set-one']}
        ctx = type('Context', (), {'http': http, 'config': config})()
        return http, config, ctx

    def test_item_discovers_archive_and_preserves_exact_plan(self):
        http, config, ctx = self.fixture()
        sets = FileSets(http, config)
        hit = sets.search('Crisis Core', 'psp', 10)[0]
        self.assertEqual(hit.platform, 'psp')
        plan = sets.plan(hit)
        self.assertEqual(plan.files[0].url, hit.url)
        self.assertTrue(hit.source_id.startswith('zip:set-one:'))
        self.assertEqual(len(plan.files), 1)
        census = Census(ctx)
        census._index = lambda: FakeIndex()
        unit = next(u for u in census.scope() if u.unit_id.startswith('file-set:'))
        first = census.enumerate(unit, None)
        self.assertEqual(first.records, [])
        fresh = Census(ctx)
        second = fresh.enumerate(unit, first.cursor)
        self.assertEqual(second.records[0].record_id, hit.source_id)
        self.assertEqual(second.records[0].platform, hit.platform)
        self.assertIsNone(second.cursor)
        self.assertIsNone(second.declared_total)

    def test_mixed_folders_inner_archive_and_depth(self):
        members = ['Roms/PS/Game.chd', 'Roms/FC/Game.zip',
                   'a/b/c/d/PS/Five.chd', 'a/b/c/d/e/PS/Six.chd', 'Unknown.iso']
        http, config, _ = self.fixture('games.zip', members)
        hits = FileSets(http, config).search('', None, 20)
        self.assertEqual({(h.title, h.platform) for h in hits},
                         {('Game', 'psx'), ('Game', 'nes'), ('Five', 'psx')})
        self.assertTrue(within_depth('a/b/games.zip', 'c/d/PS/Game.chd'))
        self.assertFalse(within_depth('a/b/games.zip', 'c/d/e/PS/Game.chd'))

    def test_conflicts_and_archive_suffixes(self):
        self.assertEqual(infer_platform('Sony - PS1 (Update 1).zip', 'Game.chd'), 'psx')
        self.assertIsNone(infer_platform('Sony - PS1 (A-L).zip', 'PSP/Game.iso'))
        self.assertIsNone(infer_platform('games.zip', 'Final Fantasy PSP.iso'))

    def test_explicit_override_and_changed_platform_refusal(self):
        http, config, _ = self.fixture()
        hit = FileSets(http, config).search('', 'psp', 10)[0]
        config['file_sets'].append('set-one/Sony - PSP (A-G).zip:psx')
        sets = FileSets(http, config)
        self.assertEqual(sets.search('', None, 10)[0].platform, 'psx')
        with self.assertRaisesRegex(FileSetError, 'platform changed'):
            sets.plan(hit)

    def test_restricted_archive_preserves_direct_hits_and_fails_census(self):
        http, config, ctx = self.fixture()
        http.files.append({'name': 'PS/Working.chd'})
        http.zip_status = 403
        outcome = FileSets(http, config).search_outcome('Working', None, 10)
        self.assertEqual(outcome.results[0].title, 'Working')
        self.assertIn('HTTP 403', outcome.failures[0])
        census = Census(ctx)
        census._index = lambda: FakeIndex()
        unit = next(u for u in census.scope() if u.unit_id.startswith('file-set:'))
        page = census.enumerate(unit, None)
        with self.assertRaisesRegex(CensusUnavailable, 'HTTP 403'):
            census.enumerate(unit, page.cursor)

    def test_unauthorized_metadata_and_login_html_are_controlled(self):
        http, config, _ = self.fixture()
        for response in (Reply('', 401), Reply('<html>Log in</html>')):
            with patch.object(http, 'get', return_value=response):
                outcome = FileSets(http, config).search_outcome('', None, 10)
                self.assertFalse(outcome.results)
                self.assertTrue(outcome.failures)

    def test_item_only_and_archive_scope_parse(self):
        self.assertIsNone(SetSource.parse('set-one').platform)
        source = SetSource.parse('set-one/folder/games.zip/Roms')
        self.assertEqual((source.archive, source.prefix), ('folder/games.zip', 'Roms'))

    def test_cached_auto_result_reaches_same_import_plan(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from rom_hub.census import Catalogue, build
        from test_file_set_catalogue import CatalogueProvider
        http, config, _ = self.fixture()
        provider = CatalogueProvider(http, config['file_sets'])
        with TemporaryDirectory() as temp:
            with Catalogue(Path(temp) / 'catalogue.db') as catalogue:
                build(lambda: provider, catalogue, slug='archive-org')
                cached = catalogue.results('Crisis Core', platform='psp')[0]
                sets = FileSets(http, config)
                live = sets.search('Crisis Core', 'psp', 10)[0]
                self.assertEqual(cached.source_id, live.source_id)
                self.assertEqual(sets.plan(cached), sets.plan(live))

    def test_multiple_archives_continue_after_restricted_listing(self):
        first = FakeHTTP('set-one', 'Sony - PS1 (A-L).zip', ['Final Fantasy VIII.chd'])
        second = FakeHTTP('set-one', 'Sony - PSP (A-G).zip', ['Crisis Core.iso'])
        first.files += second.files
        second.files = first.files
        first.zip_status = 401
        get = first.get
        def route(url):
            if 'view_archive.php' in url and 'PSP' in url:
                return second.get(url)
            return get(url)
        first.get = route
        result = FileSets(first, {'file_sets': ['set-one']}).search_outcome('', None, 10)
        self.assertEqual([r.title for r in result.results], ['Crisis Core'])
        self.assertIn('HTTP 401', result.failures[0])

    def test_changed_archive_manifest_refuses_cursor(self):
        http, config, ctx = self.fixture()
        census = Census(ctx)
        census._index = lambda: FakeIndex()
        unit = next(u for u in census.scope() if u.unit_id.startswith('file-set:'))
        first = census.enumerate(unit, None)
        http.files.append({'name': 'New.zip'})
        with self.assertRaisesRegex(CensusUnavailable, 'manifest changed'):
            Census(ctx).enumerate(unit, first.cursor)

    def test_malformed_manifest_entries_are_skipped(self):
        http, config, ctx = self.fixture()
        http.files.extend([{}, {'name': None}])
        sets = FileSets(http, config)
        self.assertEqual(len(sets.search('', 'psp', 10)), 1)
        census = Census(ctx)
        census._index = lambda: FakeIndex()
        unit = next(u for u in census.scope() if u.unit_id.startswith('file-set:'))
        self.assertEqual(sum(census.enumerate(unit, None).skipped.values()), 3)
