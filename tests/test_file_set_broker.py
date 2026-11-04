"""Real ROM Hub subprocess / HTTP broker regression for partial set failures."""
import json
from pathlib import Path
import unittest

from rom_hub.broker.host import PluginCallError, PluginProcess
from rom_hub.manifest import parse_manifest


PLUGIN_DIR = Path(__file__).resolve().parents[1]
MANIFEST = (PLUGIN_DIR / "manifest.toml").read_text(encoding="utf-8")


class SourceFetcher:
    def get(self, url, params):
        if "archive.org/metadata/broken/" in url:
            raise RuntimeError("ResponseTooLarge: broker exceeded 4 MiB")
        if url.endswith("advancedsearch.php"):
            return 200, json.dumps({"response": {"numFound": 0, "docs": []}})
        if url.startswith("https://archive.org/metadata/healthy/"):
            suffix = url.split("/healthy/", 1)[1]
            if suffix == "metadata":
                return 200, json.dumps({"result": {"collection": ["softwarelibrary"]}})
            if suffix == "files_count":
                return 200, json.dumps({"result": 1})
            if suffix.startswith("files?"):
                return 200, json.dumps({"result": [
                    {"name": "Working Game.iso", "size": "4096"},
                ]})
        raise AssertionError(f"unexpected broker HTTP request: {url}")


class BrokerTests(unittest.TestCase):
    def process(self, mappings):
        return PluginProcess(
            plugin_dir=PLUGIN_DIR,
            manifest=parse_manifest(MANIFEST),
            config={"file_sets": mappings},
            fetcher=SourceFetcher(),
            timeout=30.0,
            allow_unsandboxed=True,
        )

    def test_broker_error_does_not_erase_healthy_set(self):
        with self.process(["broken:ps2", "healthy:ps2"]) as plugin:
            hits = plugin.search("Working Game", "ps2", 1)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].source_id, "set:healthy:Working%20Game.iso")

    def test_only_broken_set_fails_with_resource_and_broker_cause(self):
        with self.process(["broken:ps2"]) as plugin:
            with self.assertRaises(PluginCallError) as failure:
                plugin.search("Working Game", "ps2", 1)
        self.assertIn("broken", str(failure.exception))
        self.assertIn("ResponseTooLarge", str(failure.exception))


if __name__ == "__main__":
    unittest.main()
