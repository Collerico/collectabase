"""Import response security tests with isolated routers and an in-memory DB."""
import importlib.util
import os
import sqlite3
import sys
import tempfile
import types
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


SECRET = "internal-password SELECT private_table /srv/private/collection.db"


class ImportSecurityTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:", check_same_thread=False)
        self.addCleanup(self.connection.close)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("CREATE TABLE platforms (id INTEGER PRIMARY KEY, name TEXT, type TEXT)")
        self.connection.execute("INSERT INTO platforms (name) VALUES ('Known')")
        self.connection.execute("""CREATE TABLE games (
            id INTEGER PRIMARY KEY, title TEXT, platform_id INTEGER, item_type TEXT,
            barcode TEXT, region TEXT, condition TEXT, completeness TEXT,
            purchase_price REAL, current_value REAL, purchase_date TEXT, notes TEXT,
            genre TEXT, description TEXT, developer TEXT, publisher TEXT,
            release_date TEXT, location TEXT, is_wishlist INTEGER
        )""")
        self.failure_target = None
        self.failure_type = RuntimeError
        self.db = types.SimpleNamespace(execute=self.execute, commit=self.connection.commit)

    def execute(self, sql, parameters=()):
        normalized = " ".join(sql.split())
        if self.failure_target and normalized.startswith(self.failure_target) and parameters[0] == "Broken":
            raise self.failure_type(SECRET)
        return self.connection.execute(sql, parameters)

    def client_for(self, relative_path, module_name):
        # Load a private module copy: never cache app/database configuration for other tests.
        module_path = Path(__file__).resolve().parents[1] / relative_path
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        module = importlib.util.module_from_spec(spec)
        database = types.ModuleType("backend.database")
        database.get_db = lambda: nullcontext(self.db)
        uploads = tempfile.TemporaryDirectory()
        self.addCleanup(uploads.cleanup)
        with patch.dict(sys.modules, {"backend.database": database}), patch.dict(
            os.environ, {"UPLOADS_DIR": uploads.name}
        ):
            spec.loader.exec_module(module)
        app = FastAPI()
        app.include_router(module.router)
        client = TestClient(app)
        self.addCleanup(client.close)
        return client

    def test_clz_game_failure_is_redacted_and_later_rows_import(self):
        client = self.client_for("clz_import.py", "backend._clz_import_security_test")
        self.failure_target = "INSERT INTO games"
        with self.assertLogs("backend._clz_import_security_test", level="ERROR") as logs:
            response = client.post("/api/import/clz", files={
                "file": ("items.csv", "Title,Platform\nBroken,Known\nGood,Known\n,Known\n", "text/csv")
            })
        self.assertIn(SECRET, "\n".join(logs.output))
        self.assertIsNotNone(logs.records[0].exc_info)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(SECRET, response.text)
        self.assertEqual(response.json(), {
            "imported": 1, "skipped": 2,
            "errors": ["Line 2: Broken → Could not import item"],
        })
        self.assertEqual(self.connection.execute("SELECT title FROM games").fetchone()[0], "Good")


    def test_clz_platform_failure_is_redacted_without_skipping_valid_items(self):
        client = self.client_for("clz_import.py", "backend._clz_import_security_test")
        self.failure_target = "INSERT OR IGNORE INTO platforms"
        with self.assertLogs("backend._clz_import_security_test", level="ERROR") as logs:
            response = client.post("/api/import/clz", files={
                "file": ("items.csv", "Title,Platform\nFirst,Broken\nSecond,Known\n", "text/csv")
            })
            self.assertNotIn(SECRET, response.text)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "imported": 2, "skipped": 0,
            "errors": ["Line 2: Could not create platform 'Broken'"],
        })
        self.assertIn(SECRET, "\n".join(logs.output))
        self.assertIsNotNone(logs.records[0].exc_info)
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM games").fetchone()[0], 2)


    def test_csv_internal_errors_are_redacted_but_validation_and_counts_are_preserved(self):
        client = self.client_for("api/routes/import_export.py", "backend.api.routes._import_security_test")
        self.failure_target = "INSERT INTO games"
        for exception_type in (RuntimeError, ValueError):
            with self.subTest(exception_type=exception_type):
                self.failure_type = exception_type
                self.connection.execute("DELETE FROM games")
                with self.assertLogs("backend.api.routes._import_security_test", level="ERROR") as logs:
                    response = client.post("/api/import/csv", files={
                        "file": ("items.csv", (
                            "Title,Platform\nBroken,Known\nNo platform,\n,Known\n"
                            "Good,Known\nGood,Known\nLast,Known\n"
                        ), "text/csv")
                    })
                    self.assertNotIn(SECRET, response.text)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {
                    "imported": 2, "skipped_duplicates": 1,
                    "errors": [
                        "Row 2: Could not import item",
                        "Row 3: Missing platform name",
                        "Row 4: Missing title",
                    ],
                })
                self.assertIn(SECRET, "\n".join(logs.output))
                self.assertIsNotNone(logs.records[0].exc_info)
                self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM games").fetchone()[0], 2)


    def test_clz_error_list_still_caps_at_twenty(self):
        client = self.client_for("clz_import.py", "backend._clz_import_security_test")
        self.failure_target = "INSERT INTO games"
        with self.assertLogs("backend._clz_import_security_test", level="ERROR"):
            response = client.post("/api/import/clz", files={
                "file": ("items.csv", "Title,Platform\n" + "Broken,Known\n" * 25, "text/csv")
            })
        self.assertEqual(response.json(), {
            "imported": 0, "skipped": 25,
            "errors": [f"Line {line}: Broken → Could not import item" for line in range(2, 22)],
        })

    def test_csv_success_keeps_null_errors(self):
        client = self.client_for("api/routes/import_export.py", "backend.api.routes._import_security_test")
        response = client.post("/api/import/csv", files={
            "file": ("items.csv", "Title,Platform\nGood,Known\n", "text/csv")
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "imported": 1, "skipped_duplicates": 0, "errors": None,
        })


if __name__ == "__main__":
    unittest.main()
