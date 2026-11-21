"""Test bootstrap: isolate storage/DB before any app module is imported."""
import os
from pathlib import Path
import tempfile

TMP = Path(tempfile.mkdtemp(prefix="vil-test-"))
os.environ["VIDEO_STORAGE_DIR"] = str(TMP / "storage")
os.environ["DATABASE_URL"] = "sqlite:///" + str(TMP / "test.db")
os.environ["DASHSCOPE_API_KEY"] = ""
os.environ["REPLICATE_API_TOKEN"] = ""

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from app import db  # noqa: E402

db.meta.create_all(db.engine)


def make_client():
    return TestClient(app)


def storage_root():
    return TMP / "storage"
