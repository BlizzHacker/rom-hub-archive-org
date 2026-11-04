"""Offline fixtures for Archive.org nested ZIP file search and multi-file games."""
import unittest
from urllib.parse import quote, urlencode

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

    def get(self, url):
        self.requests.append(url)
        prefix = "https://archive.org/metadata/" + self.identifier
        if url == prefix + "/metadata":
            return Reply({"result": {"collection": ["softwarelibrary"]}})
        if url == prefix + "/files_count":
            return Reply({"result": len(self.files)})
        if url.startswith(prefix + "/files?"):
            from urllib.parse import parse_qs
            parts = parse_qs(url.partition("?")[2])
            start, count = int(parts["start"][0]), int(parts["count"][0])
            return Reply({"result": self.files[start:start+count]})
        zip_url = ("https://archive.org/download/" + self.identifier + "/" +
                   quote(self.archive, safe="/") + "/")
        if url == zip_url:
            links = "".join(
                '<tr><td><a href="//archive.org/download/' + self.identifier + "/" +
                quote(self.archive, safe="/") + "/" + quote(name, safe="") +
                '">file</a></td></tr>'
                for name in self.members
            )
            return Reply("<html><table>" + links + "</table></html>", self.zip_status)
        raise AssertionError(url)


class ZipSetTests(unittest.TestCase):
    def test_crash_bandicoot_2_chd_and_m3u_in_named_psx_zip(self):
        archive = "Sony - PS1 (A-L).zip"
        game = "Crash Bandicoot 2 - Cortex Strikes Back (USA)"
        http = FakeHTTP("retro-roms-best-set", archive,
                        [game + ".chd", game + ".m3u", "Other Game.chd"])
        config = {"file_sets": [f"retro-roms-best-set/{archive}:psx"]}
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
        results = FileSets(http, config).search("Final Fantasy VII", "psx", 10)
        self.assertEqual(len(results), 3)  # individual discs searchable
        plan = FileSets(http, config).plan(results[0])
        self.assertEqual({f.filename for f in plan.files}, set(files[:4]))
        self.assertEqual(len(plan.files), 4)

    def test_parser_rejects_foreign_and_parent_links(self):
        archive = "Sony - PS1 (A-L).zip"
        http = FakeHTTP("retro-roms-best-set", archive, ["Game.chd"])
        original = http.get
        def malicious(url):
            response = original(url)
            if url.endswith(".zip/"):
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
