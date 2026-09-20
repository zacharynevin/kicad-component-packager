import io
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from partshelf.components import Source
from partshelf.files import PartShelfError
from partshelf.progress import ProgressStore, report, track
from partshelf.sources import download
from partshelf.web import Application

VENDOR = Path(__file__).resolve().parents[1] / "examples" / "vendor"


class ProgressTests(unittest.TestCase):
    def test_download_reports_actual_bytes_with_and_without_content_length(self):
        payload = b"x" * (3 * 65536 + 17)
        for header in (str(len(payload)), None, "invalid"):
            with self.subTest(content_length=header):
                response = io.BytesIO(payload)
                response.headers = {"Content-Type": "application/zip"}
                if header is not None:
                    response.headers["Content-Length"] = header
                response.geturl = lambda: "https://example.org/library.zip"
                events = []
                with mock.patch("partshelf.sources.public_url"), mock.patch("partshelf.sources.request.build_opener") as opener:
                    opener.return_value.open.return_value = response
                    with track(events.append, interval=0):
                        data, _, _ = download("https://example.org/library.zip")
                self.assertEqual(data, payload)
                self.assertTrue(any(0 < e["completed"] < len(payload) for e in events))
                self.assertEqual(events[-1]["completed"], len(payload))
                self.assertEqual(events[-1]["total"], len(payload) if header == str(len(payload)) else None)
                self.assertTrue(events[-1]["done"])
                self.assertFalse(events[-1]["failed"])

    def test_throttling_flushes_counts_on_stage_change_and_failure(self):
        events = []
        with self.assertRaisesRegex(ValueError, "interrupted"):
            with track(events.append, interval=1000):
                report("download", "Downloading", 0, unit="bytes")
                for count in range(1, 101):
                    report("download", "Downloading", count, unit="bytes")
                report("parse", "Parsing", 0, 500, "components")
                report("parse", "Parsing", 120, 500, "components")
                raise ValueError("interrupted")
        self.assertEqual([(e["stage"], e["completed"]) for e in events],
                         [("download", 0), ("download", 100), ("parse", 0), ("parse", 120)])
        self.assertTrue(events[-1]["failed"])
        report("outside", "Must not leak into the finished request")
        self.assertEqual(len(events), 4)

    def test_progress_is_readable_during_catalog_work_and_isolated_between_threads(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(Path(temp), Path(temp) / "catalog")
            ready, release = threading.Event(), threading.Event()
            def work():
                with app.mutex, app.progress.operation("worker"):
                    report("parse", "Parsing", 4, 10, "components")
                    ready.set()
                    release.wait(3)
            thread = threading.Thread(target=work)
            thread.start()
            try:
                self.assertTrue(ready.wait(3))
                self.assertEqual(app.progress.get("worker")["completed"], 4)
                events = []
                with track(events.append):
                    report("download", "Other request", 99, unit="bytes")
                self.assertEqual(app.progress.get("worker")["completed"], 4)
                self.assertEqual(events[-1]["completed"], 99)
            finally:
                release.set()
                thread.join(3)
            self.assertTrue(app.progress.get("worker")["done"])
        store = ProgressStore(limit=2)
        for operation in ("a", "b", "c"):
            with store.operation(operation):
                report("read", "Reading", 1, 1, "files")
        self.assertEqual(store.get("a"), {})
        self.assertEqual(len(store.entries), 2)
        with self.assertRaises(ValueError):
            store.get("x" * 81)

    def test_component_progress_counts_skipped_parts_and_finishes_without_fake_totals(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Application(VENDOR, Path(temp) / "catalog")
            events = []
            with track(events.append, interval=0):
                result = app.inspect({"path": str(VENDOR)})
            final = events[-1]
            self.assertEqual((final["stage"], final["completed"], final["total"]), ("check-components", 3, 3))
            source = app.sessions[result["session"]]["source"]
            source.footprints.pop(result["symbols"][0]["footprint"])
            events = []
            with track(events.append, interval=0):
                imported = app.post("import-library", {"session": result["session"], "prefix": "progress"})
            self.assertEqual(imported["imported"] + len(imported["skipped"]), 3)
            self.assertGreater(len(imported["skipped"]), 0)
            self.assertEqual((events[-1]["completed"], events[-1]["total"]), (3, 3))
            self.assertEqual([e["completed"] for e in events[:-1]], [0, 1, 2, 3])

    def test_cli_board_import_directs_users_to_connection_review(self):
        with self.assertRaisesRegex(PartShelfError, "Import dialog to choose external connections"):
            Source.read({"Qualia.brd": b'<?xml version="1.0"?><eagle><drawing><board/></drawing></eagle>'})


if __name__ == "__main__":
    unittest.main()
