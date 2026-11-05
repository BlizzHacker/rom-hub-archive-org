"""Offline tests for Archive.org file-manifest search and exact file grabbing.

Run with the ROM Hub SDK installed: python -m unittest discover -s tests
"""
import unittest
from unittest.mock import patch
from archive_org.file_sets import FileSetError, FileSets, PAGE_SIZE, SetSource
from archive_org.importer import Importer
from archive_org.search import Search


class Response:
    status_code = 200

    def __init__(self, data, text=None):
        self.data = data
        self.text = text

    def json(self):
        return self.data


class ArchiveHTTP:
    def __init__(self, items):
        self.items = items
        self.requests = []

    def get(self, url, params=None):
        self.requests.append(url)
        if url.startswith("https://archive.org/download/"):
            from urllib.parse import unquote
            identifier, _, name = url[len("https://archive.org/download/"):].partition("/")
            return Response(None, self.items[identifier].get("texts", {})[unquote(name)])
        root = "https://archive.org/metadata/"
        assert url.startswith(root), url
        item_path = url[len(root):]
        identifier, sep, suffix = item_path.partition("/")
        assert sep, url
        details = self.items[identifier]
        if suffix == "metadata":
            return Response({"result": details.get("metadata", {"collection": ["softwarelibrary"]})})
        if suffix == "files_count":
            return Response({"result": len(details["files"])})
        if suffix.startswith("files?"):
            from urllib.parse import parse_qs
            params = parse_qs(suffix.partition("?")[2])
            offset = int(params["start"][0])
            count = int(params["count"][0])
            return Response({"result": details["files"][offset:offset + count]})
        raise AssertionError(url)


def catalog(files, specs=None, metadata=None):
    http = ArchiveHTTP({"set-one": {"files": files, "metadata": metadata or
                                      {"collection": ["softwarelibrary"]}}})
    config = {"file_sets": specs or ["set-one:psx"]}
    return FileSets(http, config), http


def game(name, size="2048", **extra):
    return {"name": name, "size": size, **extra}


class FileSetTests(unittest.TestCase):
    def test_search_matches_game_filename_not_parent_item_title(self):
        sets, http = catalog([
            game("Crash Bandicoot 2 (USA).chd"),
            game("Crash Bandicoot 2 (USA).cue"),
            game("Crash Bandicoot 2 (USA).bin"),
            game("Crash Bandicoot 3 (USA).chd"),
            game("set-one_meta.xml"),
        ])
        hits = sets.search("Crash Bandicoot 2", "psx", 10)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].title, "Crash Bandicoot 2 (USA)")
        self.assertEqual(hits[0].platform, "psx")
        self.assertEqual(hits[0].source_id,
                         "set:set-one:Crash%20Bandicoot%202%20%28USA%29.chd")
        self.assertFalse(sets.search("Crash Bandicoot 2", "ps2", 10))
        self.assertTrue(any("/files_count" in path for path in http.requests))
        # One manifest read can answer multiple searches in the same process.
        self.assertEqual(len(http.requests), 3)

    def test_import_revalidates_exact_file_and_uses_direct_download(self):
        sets, _ = catalog([game("PSX/Crash Bandicoot 2 (Europe).7z", "1777")],
                          specs=["set-one/PSX:psx"])
        hit = sets.search("Crash Bandicoot 2", "psx", 10)[0]
        plan = sets.plan(hit)
        self.assertEqual(plan.platform, "psx")
        self.assertEqual(len(plan.files), 1)
        self.assertEqual(plan.files[0].filename, "Crash Bandicoot 2 (Europe).7z")
        self.assertEqual(plan.files[0].size_bytes, 1777)
        self.assertEqual(plan.files[0].url,
                         "https://archive.org/download/set-one/PSX/Crash%20Bandicoot%202%20%28Europe%29.7z")

    def test_missing_file_never_downloaded(self):
        sets, http = catalog([game("Game (USA).zip")])
        result = sets.search("Game", "psx", 10)[0]
        http.items["set-one"]["files"] = []
        refreshed = FileSets(http, {"file_sets": ["set-one:psx"]})
        with self.assertRaisesRegex(FileSetError, "no longer"):
            refreshed.plan(result)

    def test_direct_item_m3u_fetches_exact_referenced_discs(self):
        files = [game("PSX/Chronicles (Disc 1).chd"),
                 game("PSX/Chronicles (Disc 2).chd"),
                 game("PSX/Chronicles (Disc 2).iso"),
                 game("PSX/Chronicles.m3u")]
        sets, http = catalog(files, specs=["set-one/PSX:psx"])
        http.items["set-one"]["texts"] = {
            "PSX/Chronicles.m3u":
                "#EXTM3U\r\nChronicles (Disc 1).chd\r\n"
                "Chronicles (Disc 2).chd\r\n"
        }
        disc = next(r for r in sets.search("Chronicles", "psx", 10)
                    if "Disc%201" in r.source_id)
        plan = sets.plan(disc)
        self.assertEqual([f.filename for f in plan.files],
                         ["Chronicles.m3u", "Chronicles (Disc 1).chd",
                          "Chronicles (Disc 2).chd"])
        self.assertNotIn("Chronicles (Disc 2).iso",
                         [f.filename for f in plan.files])

    def test_nested_folders_have_explicit_platforms(self):
        sets, _ = catalog([game("PSX/Game.zip"), game("PS2/Game.iso")],
                          specs=["set-one/PSX:psx", "set-one/PS2:ps2"])
        self.assertEqual({x.platform for x in sets.search("game", None, 10)},
                         {"psx", "ps2"})
        self.assertEqual(sets.search("game", "ps2", 10)[0].platform, "ps2")

    def test_longest_scope_owns_platform_before_filtering(self):
        entries = [game("Game.chd"), game("PS2/Another Game.iso")]
        for mapping in (["set-one:psx", "set-one/PS2:ps2"],
                        ["set-one/PS2:ps2", "set-one:psx"]):
            sets, _ = catalog(entries, specs=mapping)
            self.assertEqual([r.title for r in sets.search("Another Game", "psx", 10)], [])
            hits = sets.search("Another Game", "ps2", 10)
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0].platform, "ps2")
            self.assertEqual(sets.plan(hits[0]).platform, "ps2")
            self.assertEqual(sets.search("Another Game", None, 10)[0].platform, "ps2")

    def test_failures_are_isolated_with_context(self):
        healthy = {"files": [game("Working Game.chd")]}
        broken = {"files": [game("Missing Game.chd")]}
        base = ArchiveHTTP({"good": healthy, "bad": broken})
        original_get = base.get
        def get(url, params=None):
            if "/metadata/bad/" in url:
                raise RuntimeError("response too large at broker")
            return original_get(url, params=params)
        base.get = get
        sets = FileSets(base, {"file_sets": ["bad:psx", "good:psx"]})
        outcome = sets.search_outcome("Working", "psx", 10)
        self.assertEqual(len(outcome.results), 1)
        self.assertIn("bad", outcome.failures[0])
        self.assertIn("response too large", outcome.failures[0])
        self.assertEqual(len(sets.search("Working", "psx", 10)), 1)
        with self.assertRaisesRegex(FileSetError, "bad"):
            FileSets(base, {"file_sets": ["bad:psx"]}).search("Working", "psx", 10)

    def test_item_search_survives_failed_sets_and_reverse(self):
        http = ArchiveHTTP({"bad": {"files": [game("Missing Game.chd")]},
                            "good": {"files": [game("Working Game.chd")]}})
        original_get = http.get
        def fail_bad(url, params=None):
            if "/metadata/bad/" in url:
                raise RuntimeError("broker transport failed")
            return original_get(url, params=params)
        http.get = fail_bad
        config = {"file_sets": ["bad:psx"]}
        ctx = type("Ctx", (), {"http": http, "config": config})()
        fake_doc = {"identifier": "item-good", "title": "Working Game",
                    "emulator": "psx", "item_size": 123}
        class IndexStub:
            included_notes = True
            def __init__(self, *args, **kwargs):
                pass
            def fetch(self, query, remaining):
                return [fake_doc]
        with patch("archive_org.search.Index", IndexStub):
            results = Search(ctx).search("Working Game", "psx", 10)
        self.assertEqual([r.source_id for r in results], ["item-good"])

        from archive_org.index import IndexUnavailable
        class FailIndex(IndexStub):
            def fetch(self, query, remaining):
                raise IndexUnavailable("index is offline")
        ctx.config = {"file_sets": ["good:psx"]}
        with patch("archive_org.search.Index", FailIndex):
            results = Search(ctx).search("Working Game", "psx", 10)
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].source_id.startswith("set:"))
        with patch("archive_org.search.Index", FailIndex):
            with self.assertRaisesRegex(Exception, "offline"):
                Search(type("Ctx", (), {"http": http, "config": {}})()).search(
                    "Working Game", "psx", 10)

    def test_pagination_reaches_games_at_end(self):
        entries = [game(f"Misc {n:04}.zip") for n in range(PAGE_SIZE)]
        entries.append(game("Target At End.chd"))
        sets, http = catalog(entries)
        results = sets.search("target at end", "psx", 10)
        self.assertEqual(len(results), 1)
        self.assertEqual(len([url for url in http.requests if "/files?" in url]), 2)

    def test_rejects_unusable_names_and_private_entries(self):
        sets, _ = catalog([game("../escape.zip"),
                           game("/absolute.iso"),
                           game("file\\evil.zip"),
                           game("Hidden.iso", is_dark="true"),
                           game("Game.zip")])
        self.assertEqual([x.title for x in sets.search("", "psx", 10)], ["Game"])

    def test_rejects_incomplete_or_ambiguous_source_ids(self):
        sets, _ = catalog([game("Game.zip")])
        found = sets.search("Game", "psx", 10)[0]
        for source_id in ("set:set-one:%2e%2e%2fGame.zip",
                          "set:set-one:Wrong.zip",
                          "set:other:Game.zip",
                          "set:set-one:Game%2Ezip"):
            found.source_id = source_id
            with self.subTest(source_id=source_id), self.assertRaises(FileSetError):
                sets.plan(found)

    def test_rejects_invalid_config_and_oversize_manifest(self):
        for entry in ("set-one/../PSX:psx", "set-one:PSX",
                      "/root:psx", "set-one/x//y:psx"):
            with self.subTest(entry=entry), self.assertRaises(FileSetError):
                SetSource.parse(entry)
        sets, _ = catalog([game("Game.zip")] * 3)
        sets.max_files = 2
        with self.assertRaisesRegex(FileSetError, "exceeding"):
            sets.search("Game", "psx", 10)

    def test_rom_hub_search_and_importer_dispatch_for_disc_platform(self):
        http = ArchiveHTTP({"set-one": {"files": [game("Game Name.chd")]}})
        ctx = type("Context", (), {"http": http,
                                    "config": {"file_sets": ["set-one:ps2"]}})()
        matches = Search(ctx).search("game name", "ps2", 5)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].platform, "ps2")
        plan = Importer(ctx).plan(matches[0])
        self.assertEqual(plan.platform, "ps2")
        self.assertEqual(plan.files[0].filename, "Game Name.chd")

    def test_item_search_works_without_sets(self):
        # Default behavior stays unchanged: no file metadata calls.
        class IndexResponse:
            status_code = 200
            def json(self):
                return {"response": {"numFound": 0, "docs": []}}
        class Http:
            def __init__(self):
                self.requests = []
            def get(self, url, params=None):
                self.requests.append(url)
                return IndexResponse()
        http = Http()
        search = Search.__new__(Search)
        search.ctx = type("Context", (), {"http": http, "config": {}})()
        self.assertEqual(search.search("Game", "psx", 5), [])
        self.assertTrue(all("advancedsearch.php" in u for u in http.requests))


if __name__ == "__main__":
    unittest.main()
