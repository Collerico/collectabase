import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient


class FrontendPathSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="collectabase_frontend_security_")
        cls.base = Path(cls.temporary.name)
        cls.dist = cls.base / "dist"
        cls.dist.mkdir()
        (cls.dist / "index.html").write_text("<html>SPA fallback</html>", encoding="utf-8")
        (cls.dist / "manifest.json").write_text('{"name":"Collectabase"}', encoding="utf-8")
        (cls.base / "outside.txt").write_text("PRIVATE_TEST_MARKER", encoding="utf-8")
        sibling = cls.base / "dist-other"
        sibling.mkdir()
        (sibling / "outside.txt").write_text("PRIVATE_TEST_MARKER", encoding="utf-8")
        (cls.dist / "external-link").symlink_to(cls.base / "outside.txt")
        (cls.dist / "subdir").mkdir()
        with patch.dict(os.environ, {"UPLOADS_DIR": str(cls.base / "uploads")}):
            from backend import main
        cls.main = main
        cls.root_patch = patch.object(main, "FRONTEND_DIR_RESOLVED", cls.dist)
        cls.static_patch = patch.object(main, "frontend_files", StaticFiles(directory=cls.dist), create=True)
        cls.root_patch.start()
        cls.static_patch.start()
        app = FastAPI()
        app.add_api_route("/", main.root, methods=["GET"])
        app.add_api_route("/{path:path}", main.catch_all, methods=["GET"])
        cls.client = TestClient(app, raise_server_exceptions=False)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        cls.static_patch.stop()
        cls.root_patch.stop()
        cls.temporary.cleanup()

    def assert_fallback(self, path):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.text, "<html>SPA fallback</html>")
        self.assertNotIn("PRIVATE_TEST_MARKER", response.text)

    def test_malformed_null_path_falls_back_without_server_error(self):
        self.assert_fallback("/bad%00name")

    def test_overlong_path_falls_back_without_server_error(self):
        self.assert_fallback("/" + "x" * 5000)

    def test_root_and_spa_deep_links_keep_index_fallback(self):
        for path in ("/", "/games/123", "/missing-file.png", "/subdir"):
            with self.subTest(path=path):
                self.assert_fallback(path)

    def test_existing_frontend_file_is_served(self):
        response = self.client.get("/manifest.json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"name": "Collectabase"})

    def test_traversal_and_sibling_prefix_cannot_escape_frontend(self):
        for path in ("/%2e%2e/outside.txt", "/%2e%2e/dist-other/outside.txt", "/subdir/%2e%2e/%2e%2e/outside.txt", "/%2f" + str(self.base / "outside.txt").lstrip("/")):
            with self.subTest(path=path):
                self.assert_fallback(path)

    def test_external_symlink_is_not_served(self):
        self.assert_fallback("/external-link")

    def test_unknown_api_routes_remain_json_404(self):
        response = self.client.get("/api/not-a-real-endpoint")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"]["code"], "not_found")


if __name__ == "__main__":
    unittest.main()
