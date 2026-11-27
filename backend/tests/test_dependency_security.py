"""Regression guards for patched runtime dependencies (no test-only packages)."""
from importlib.metadata import version
import unittest


class DependencySecurityTests(unittest.TestCase):
    def test_fastapi_includes_form_redos_fix(self):
        self.assertGreaterEqual(
            tuple(map(int, version("fastapi").split("."))), (0, 109, 1)
        )

    def test_starlette_includes_form_url_and_static_security_fixes(self):
        self.assertGreaterEqual(
            tuple(map(int, version("starlette").split("."))), (1, 3, 1)
        )

    def test_multipart_includes_all_reported_security_fixes(self):
        self.assertGreaterEqual(
            tuple(map(int, version("python-multipart").split("."))), (0, 0, 31)
        )

    def test_dotenv_includes_symlink_write_fix(self):
        self.assertGreaterEqual(
            tuple(map(int, version("python-dotenv").split("."))), (1, 2, 2)
        )


class FormCompatibilityTests(unittest.TestCase):
    def setUp(self):
        # Use an isolated app; never import the production app before its DB fixture.
        from fastapi import FastAPI, Request
        from fastapi.testclient import TestClient

        app = FastAPI()

        @app.post("/form")
        async def form(request: Request):
            async with request.form(max_fields=1) as fields:
                return dict(fields)

        @app.post("/upload")
        async def upload(request: Request):
            async with request.form() as fields:
                file = fields["file"]
                return {
                    "filename": file.filename,
                    "content_type": file.content_type,
                    "size": len(await file.read()),
                    "title": fields["title"],
                }

        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def test_urlencoded_field_limit_is_enforced(self):
        response = self.client.post("/form", data={"first": "1", "second": "2"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("Too many fields", response.json()["detail"])

    def test_oversized_multipart_text_field_is_rejected(self):
        response = self.client.post(
            "/form", files={"title": (None, "x" * (1024 * 1024 + 1))}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("maximum size", response.json()["detail"])

    def test_multipart_upload_and_disk_spooling_preserve_metadata_and_content(self):
        for size in (16, 1024 * 1024 + 1):
            with self.subTest(size=size):
                response = self.client.post(
                    "/upload",
                    data={"title": "Collection import"},
                    files={"file": ("collection.csv", b"x" * size, "text/csv")},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {
                    "filename": "collection.csv", "content_type": "text/csv",
                    "size": size, "title": "Collection import",
                })


if __name__ == "__main__":
    unittest.main()
