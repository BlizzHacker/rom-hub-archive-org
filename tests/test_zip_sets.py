"""Offline fixtures for Archive.org nested ZIP file search and multi-file games."""
import unittest
from urllib.parse import quote, urlencode, unquote

from archive_org.file_sets import FileSetError, FileSets, SetSource
from archive_org.importer import Importer
from archive_org.search import Search


class Reply:
    def __init__(self, content, status_code=200):
        self.status_code = status_code
        self.content = content
        self.text = content if isinstance(content, str) else ""

    def json(self):
        return self.content


class FakeHTTP:
    def __init__(self, identifier, archive, members):
        self.identifier = identifier
        self.archive = archive
        self.files = [{"name": archive, "size": "123456789"}]
        self.members = members
        self.requests = []
        self.zip_status = 200
        self.playlists = {}

    def get(self, url):
        self.requests.append(url)
        prefix = "https://archive.org/metadata/" + self.identifier
        if url == prefix + "/metadata":
            return Reply({"result": {"collection": ["softwarelibrary"]}})
        if url == prefix + "/files_count":
            return Reply({"result": len(self.files)})
        if url == prefix + "/d1":
            return Reply({"result": "ia800408.us.archive.org"})
        if url == prefix + "/dir":
            return Reply({"result": "/16/items/" + self.identifier})
        if url.startswith(prefix + "/files?"):
            from urllib.parse import parse_qs
            parts = parse_qs(url.partition("?")[2])
            start, count = int(parts["start"][0]), int(parts["count"][0])
            return Reply({"result": self.files[start:start+count]})
        zip_url = ("https://ia800408.us.archive.org/view_archive.php?archive=/16/items/" +
                   self.identifier + "/" + quote(self.archive, safe="/"))
        if url == zip_url:
            links = "".join(
                '<tr><td><a href="//archive.org/download/' + self.identifier + "/" +
                quote(self.archive, safe="/") + "/" + quote(name, safe="") +
                '">file</a></td></tr>'
                for name in self.members
            )
            return Reply("<html><table>" + links + "</table></html>", self.zip_status)
        if url.startswith(zip_url + "&file="):
            member = unquote(url[len(zip_url + "&file="):])
            if member in self.playlists:
                return Reply(self.playlists[member])
        download_base = ("https://archive.org/download/" + self.identifier + "/" +
                         quote(self.archive, safe="/") + "/")
        if url.startswith(download_base):
            member = unquote(url[len(download_base):])
            if member in self.playlists:
                return Reply("", 302)
        raise AssertionError(url)


class ZipSetTests(unittest.TestCase):
    def test_crash_bandicoot_2_chd_and_m3u_in_named_psx_zip(self):
        archive = "Sony - PS1 (A-L).zip"
        game = "Crash Bandicoot 2 - Cortex Strikes Back (USA)"
        http = FakeHTTP("retro-roms-best-set", archive,
                        [game + ".chd", game + ".m3u", "Other Game.chd"])
        config = {"file_sets": [f"retro-roms-best-set/{archive}:psx"]}
        http.playlists[game + ".m3u"] = game + ".chd\n"
        source = SetSource.parse(config["file_sets"][0])
        self.assertEqual(source.archive, archive)
        self.assertEqual(source.prefix, "")
        results = FileSets(http, config).search("Crash Bandicoot 2", "psx", 10)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].url,
                         "https://archive.org/download/retro-roms-best-set/" +
                         quote(archive) + "/" + quote(game + ".chd"))
        plan = Importer(type("Ctx", (), {"http": http, "config": config})()).plan(results[0])
        self.assertEqual({f.filename for f in plan.files}, {game + ".chd", game + ".m3u"})
        self.assertEqual(plan.platform, "psx")
        self.assertIn("https://ia800408.us.archive.org/view_archive.php?archive=/16/items/retro-roms-best-set/" +
                      quote(archive, safe="/") + "&file=" + quote(game + ".m3u", safe=""),
                      http.requests)
        self.assertEqual({f.url.rsplit("/", 1)[-1] for f in plan.files},
                         {quote(game + ".chd"), quote(game + ".m3u")})

    def test_tiny_best_set_go_expansion_nested_psx_folder(self):
        archive = "tiny-best-set-go-expansion-128-games.zip"
        game = "Super Puzzle Fighter II Turbo (USA).chd"
        http = FakeHTTP("tiny-best-set-go", archive,
                        ["Roms/PS/" + game, "Roms/FC/Other Game.zip"])
        config = {"file_sets": [f"tiny-best-set-go/{archive}/Roms/PS:psx"]}
        results = FileSets(http, config).search("Super Puzzle Fighter II Turbo", "psx", 10)
        self.assertEqual(len(results), 1)
        expected = ("https://archive.org/download/tiny-best-set-go/" + archive + "/" +
                    quote("Roms/PS/" + game, safe=""))
        self.assertEqual(results[0].url, expected)
        self.assertEqual(FileSets(http, config).plan(results[0]).files[0].url, expected)
        self.assertEqual(FileSets(http, config).search("Other Game", "psx", 10), [])

    def test_multidisc_chd_group_and_playlist(self):
        archive = "PSX.zip"
        files = ["Final Fantasy VII (USA) (Disc 1).chd",
                 "Final Fantasy VII (USA) (Disc 2).chd",
                 "Final Fantasy VII (USA) (Disc 3).chd",
                 "Final Fantasy VII (USA).m3u",
                 "Final Fantasy VIII (USA).chd"]
        http = FakeHTTP("set-one", archive, files)
        config = {"file_sets": ["set-one/PSX.zip:psx"]}
        http.playlists["Final Fantasy VII (USA).m3u"] = "\ufeff#EXTM3U\r\n" + "\r\n".join(files[:3]) + "\r\n"
        results = FileSets(http, config).search("Final Fantasy VII", "psx", 10)
        self.assertEqual(len(results), 3)  # individual discs searchable
        plan = FileSets(http, config).plan(results[0])
        self.assertEqual({f.filename for f in plan.files}, set(files[:4]))
        self.assertEqual(len(plan.files), 4)

    def test_zip_overlap_resolves_before_platform_filter(self):
        for mapping in (["set-one/PS2.zip:psx", "set-one/PS2.zip/PS2:ps2"],
                        ["set-one/PS2.zip/PS2:ps2", "set-one/PS2.zip:psx"]):
            http = FakeHTTP("set-one", "PS2.zip",
                            ["PS2/Some Game.iso", "PSX/Other Game.chd"])
            sets = FileSets(http, {"file_sets": mapping})
            self.assertEqual(sets.search("Some Game", "psx", 10), [])
            matches = sets.search("Some Game", "ps2", 10)
            self.assertEqual(len(matches), 1)
            self.assertEqual(sets.plan(matches[0]).platform, "ps2")

    def test_playlist_must_reference_selected_disc_and_exact_images(self):
        files = ["Game (Disc 1).chd", "Game (Disc 2).chd",
                 "Game (Disc 2).iso", "Game.m3u"]
        http = FakeHTTP("set-one", "PSX.zip", files)
        http.playlists["Game.m3u"] = (
            "\ufeff#EXTM3U\r\n#EXTINF:1,Disc\r\n"
            "./Game (Disc 1).chd\r\nGame (Disc 2).chd\r\n"
            "Game (Disc 2).chd\r\n"
        )
        sets = FileSets(http, {"file_sets": ["set-one/PSX.zip:psx"]})
        first = sets.search("Game", "psx", 10)[0]
        plan = sets.plan(first)
        self.assertEqual([f.filename for f in plan.files],
                         ["Game.m3u", "Game (Disc 1).chd", "Game (Disc 2).chd"])
        alternative = next(r for r in sets.search("Game", "psx", 10)
                           if r.source_id.endswith(".iso"))
        with self.assertRaisesRegex(FileSetError, "not referenced"):
            sets.plan(alternative)

    def test_playlist_refuses_incomplete_or_unsafe_references(self):
        http = FakeHTTP("set-one", "PSX.zip",
                        ["Game (Disc 1).chd", "Game.m3u"])
        sets = FileSets(http, {"file_sets": ["set-one/PSX.zip:psx"]})
        selected = sets.search("Game", "psx", 10)[0]
        invalid = [
            ("Game (Disc 1).chd\nGame (Disc 2).chd\n", "missing"),
            ("../Game (Disc 1).chd\n", "unsupported"),
            ("https://archive.org/a.chd\n", "unsupported"),
            ("/Game (Disc 1).chd\n", "unsupported"),
            ("Subfolder/Game (Disc 1).chd\n", "unsupported"),
            ("Game (Disc 1).cue\n", "unsupported"),
        ]
        for contents, message in invalid:
            with self.subTest(contents=contents):
                http.playlists["Game.m3u"] = contents
                with self.assertRaisesRegex(FileSetError, message):
                    sets.plan(selected)
        http.playlists["Game.m3u"] = "Game (Disc 1).chd"
        http.zip_status = 200
        original = http.get
        def rejected(url):
            if url.endswith("Game.m3u"):
                return Reply("", 403)
            return original(url)
        http.get = rejected
        with self.assertRaisesRegex(FileSetError, "HTTP 403"):
            sets.plan(selected)

    def test_parser_rejects_foreign_and_parent_links(self):
        archive = "Sony - PS1 (A-L).zip"
        http = FakeHTTP("retro-roms-best-set", archive, ["Game.chd"])
        original = http.get
        def malicious(url):
            response = original(url)
            if "/view_archive.php?archive=" in url:
                response.text += (
                    '<a href="//example.org/download/retro-roms-best-set/' +
                    quote(archive) + '/Fake.chd">evil</a>'
                    '<a href="//archive.org/download/other/' +
                    quote(archive) + '/Fake.chd">cross item</a>'
                    '<a href="//archive.org/download/retro-roms-best-set/' +
                    quote(archive) + '/%2E%2E%2Fescape.chd">traversal</a>'
                )
            return response
        http.get = malicious
        config = {"file_sets": [f"retro-roms-best-set/{archive}:psx"]}
        results = FileSets(http, config).search("", "psx", 20)
        self.assertEqual([r.title for r in results], ["Game"])

    def test_rejects_locked_listing_with_clear_error(self):
        http = FakeHTTP("set-one", "games.zip", ["Game.chd"])
        http.zip_status = 403
        sets = FileSets(http, {"file_sets": ["set-one/games.zip:psx"]})
        with self.assertRaisesRegex(FileSetError, "HTTP 403"):
            sets.search("Game", "psx", 10)

    def test_provider_returns_zip_member_even_without_emulator_index(self):
        http = FakeHTTP("set-one", "PS2.zip", ["Test PS2 Game.iso"])
        config = {"file_sets": ["set-one/PS2.zip:ps2"]}
        # A full result quota avoids an unrelated item-level advanced search.
        result = Search(type("Ctx", (), {"http": http, "config": config})()).search(
            "Test PS2 Game", "ps2", 1
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].platform, "ps2")
        plan = Importer(type("Ctx", (), {"http": http, "config": config})()).plan(result[0])
        self.assertEqual(plan.files[0].filename, "Test PS2 Game.iso")


if __name__ == "__main__":
    unittest.main()
