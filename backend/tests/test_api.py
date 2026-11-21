"""API integration tests.

Uses a real (synthetic) MP4 processed by real FFmpeg for upload/preprocess.
Mask PNGs here are explicit test fixtures for the index/touch layer only — they
are NOT presented as Qwen or Replicate results. Real AI calls are separate.
"""
import io
import shutil
import subprocess
import time
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from conftest import make_client, storage_root
from app import db
from app.config import cfg

FFMPEG = shutil.which("ffmpeg")


def make_mp4(path: Path, seconds=1, fps=24, size=(320, 180)):
    """Generate a real H.264 MP4 with FFmpeg's testsrc (no model involved)."""
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi",
        "-i", f"testsrc=size={size[0]}x{size[1]}:rate={fps}:duration={seconds}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
    ], check=True, timeout=60)


def wait_task(client, task_id, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = client.get(f"/api/v1/tasks/{task_id}").json()
        if task["stage"] in ("completed", "failed", "partial"):
            return task
        time.sleep(0.2)
    raise AssertionError("task timeout")


@pytest.fixture(scope="module")
def prepared():
    if not FFMPEG:
        pytest.skip("ffmpeg not available in this environment")
    src = storage_root() / "sample.mp4"
    src.parent.mkdir(parents=True, exist_ok=True)
    make_mp4(src)
    client = make_client()
    with src.open("rb") as f:
        res = client.post("/api/v1/videos", files={"file": ("sample.mp4", f, "video/mp4")})
    assert res.status_code == 202, res.text
    ids = res.json()
    task = wait_task(client, ids["task_id"])
    assert task["stage"] == "completed", task
    details = client.get(f"/api/v1/videos/{ids['video_id']}").json()
    assert details["status"] == "playable"
    assert details["canonical_fps"] == cfg.video_canonical_fps == 24
    assert details["frame_count"] >= 24
    assert details["width"] <= 1280 and details["height"] <= 1280
    assert len(details["keyframes"]) >= 1
    return client, ids["video_id"], details


def seed_index(client, vid, details, masks_by_frame):
    """Insert entity + mask fixtures directly into the index tables."""
    version = details["version"]
    eid = db.uid()
    with db.engine.begin() as con:
        con.execute(db.entities.insert().values(
            id=eid, video_id=vid, version=version, label="白兔", category="动物",
            description="一只戴眼镜的白兔", attributes={"配饰": "眼镜"}, action="坐着",
            seed_frame=0, seed_point=[10, 10], prediction={"status": "success"}))
        con.execute(db.indices.insert().values(id=f"{vid}-{version}", video_id=vid,
                                               version=version, status="success"))
        rows = []
        mask_dir = storage_root() / "videos" / vid / "fixture_masks"
        mask_dir.mkdir(parents=True, exist_ok=True)
        for frame, arr in masks_by_frame.items():
            p = mask_dir / f"{frame:06}.png"
            Image.fromarray(arr, mode="L").save(p)
            rows.append(dict(id=db.uid(), video_id=vid, video_version=version,
                             frame_index=frame, pts_us=details["pts"][frame] if details.get("pts") else int(frame / 24 * 1e6),
                             track_id=eid, mask_ref=str(p), bbox=[0, 0, arr.shape[1], arr.shape[0]],
                             area=int(np.count_nonzero(arr)), source="test_fixture"))
        con.execute(db.masks.insert(), rows)
    return eid


def test_upload_preprocess_and_touch_matched_no_hit_ambiguous(prepared):
    client, vid, details = prepared
    # The details endpoint does not expose pts; fetch the row directly.
    video_row = db.get(db.videos, vid)
    details = dict(details, pts=video_row["pts"])
    w, h = details["width"], details["height"]

    rabbit = np.zeros((h, w), dtype=np.uint8)
    rabbit[20:80, 20:100] = 255                 # 白兔身体
    # A second object mask overlaps the rabbit -> ambiguous hit in that region.
    bee2 = np.zeros((h, w), dtype=np.uint8)
    bee2[40:60, 40:70] = 255

    eid = seed_index(client, vid, details, {0: rabbit})
    # Add a second entity+mask on the same frame for ambiguous/edge checks.
    eid2 = db.uid()
    with db.engine.begin() as con:
        con.execute(db.entities.insert().values(
            id=eid2, video_id=vid, version=details["version"], label="蜜蜂", category="昆虫",
            description="一只蜜蜂", attributes={}, action="飞", seed_frame=0,
            seed_point=[220, 115], prediction={"status": "success"}))
        p = storage_root() / "videos" / vid / "fixture_masks" / "bee_0.png"
        Image.fromarray(bee2, mode="L").save(p)
        con.execute(db.masks.insert().values(dict(
            id=db.uid(), video_id=vid, video_version=details["version"], frame_index=0,
            pts_us=details["pts"][0], track_id=eid2, mask_ref=str(p),
            bbox=[40, 40, 70, 60], area=int(np.count_nonzero(bee2)), source="test_fixture")))

    def touch(x, y, t=0.01):
        return client.post(f"/api/v1/videos/{vid}/touch", json={
            "timestamp_sec": t, "x_norm": x / w, "y_norm": y / h}).json()

    # 1) 白兔身体内部 → 唯一命中（重叠区域在 x=50,y=50 会双命中，所以选非重叠点）
    r = touch(80, 50)
    assert r["status"] == "matched", r
    assert r["selected_entity"]["name"] == "白兔", r
    assert r["match_method"] == "mask_pixel_hit"

    # 2) 空白背景 → no_hit，绝不强制选对象
    r = touch(300, 170)
    assert r["status"] == "no_hit", r
    assert r["selected_entity"] is None and r["candidates"] == []
    assert r["match_method"] is None

    # 3) 重叠区域 → ambiguous 候选（两个 Mask 同时命中）
    r = touch(50, 50)
    assert r["status"] == "ambiguous", r
    assert {c["name"] for c in r["candidates"]} >= {"白兔", "蜜蜂"}, r

    # 4) 边缘附近 → 低置信度候选，而不是伪造确定命中
    r = touch(18, 50)
    assert r["status"] in ("ambiguous", "no_hit"), r
    if r["status"] == "ambiguous":
        assert r["match_method"] == "mask_edge_candidate"
        assert all(c["confidence"] < 1 for c in r["candidates"])

    # 5) 时间越界 → 422 而不是 500
    res = client.post(f"/api/v1/videos/{vid}/touch", json={
        "timestamp_sec": 999, "x_norm": 0.1, "y_norm": 0.1})
    assert res.status_code == 422, res.text

    # 6) index/mask 接口可用
    idx = client.get(f"/api/v1/videos/{vid}/index?timestamp_sec=0.01").json()
    assert idx["frame_index"] == 0 and len(idx["masks"]) == 2
    m = client.get(f"/api/v1/videos/{vid}/masks/{eid}/0")
    assert m.status_code == 200 and m.headers["content-type"] == "image/png"
    assert Image.open(io.BytesIO(m.content)).size == (w, h)


def test_upload_rejects_unsupported_extension_and_bad_content(prepared):
    client, _, _ = prepared
    assert client.post("/api/v1/videos", files={"file": ("a.txt", b"hello", "text/plain")}).status_code == 415
    assert client.post("/api/v1/videos", files={"file": ("bad.mp4", b"not a video", "video/mp4")}).status_code == 422


def test_analysis_requires_configured_keys(prepared):
    client, vid, _ = prepared
    res = client.post(f"/api/v1/videos/{vid}/analyze")
    assert res.status_code == 503, res.text   # keys intentionally empty in tests
    assert "密钥" in res.json()["detail"]


def test_health_reports_configuration(prepared):
    client, _, _ = prepared
    h = client.get("/api/v1/health").json()
    assert h["status"] == "ok"
    assert h["qwen_configured"] is False and h["replicate_configured"] is False
