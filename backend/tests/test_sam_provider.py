"""Unit test of ReplicateSAMProvider with an explicitly mocked Replicate client.

This verifies our I/O contract (frame numbering, PNG validation, index rows and
partial reporting). It is a unit test with mocks — NOT a real Replicate call and
NOT a substitute for real API verification.
"""
import io
import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from app import sam


class FakePrediction:
    def __init__(self, frames, count):
        self.id = "pred-test"
        self.status = "succeeded"
        self.output = [f"https://replicate.delivery/abc/frame_{i:05d}.png" for i in frames]
        self._count = count

    def reload(self):
        pass

    def cancel(self):
        pass


class FakeVersions:
    def get(self, _id):
        output = {"type": "array", "items": {"type": "string", "format": "uri"}}
        return SimpleNamespace(openapi_schema={
            "components": {"schemas": {
                "Input": {"properties": {k: {} for k in sam.REQUIRED_INPUTS}},
                "PredictionResponse": {"properties": {"output": output}},
            }}})


class FakeModels:
    def get(self, _name):
        return SimpleNamespace(versions=FakeVersions())


class FakeClient:
    def __init__(self, api_token=None):
        self.models = FakeModels()
        self.predictions = self

    def create(self, version, input):
        assert version == sam.VERSION
        assert input["click_frames"] == "12"
        assert input["click_coordinates"] == "[30,40]"
        assert input["mask_type"] == "binary" and input["output_format"] == "png"
        assert input["output_frame_interval"] == 1
        # REAL behaviour: output starts at the seed frame and goes to the end.
        return FakePrediction(range(0, 48 - 12), 48)


class FakeHTTP:
    def __init__(self, *a, **kw):
        self.saved = {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url):
        frame = int(Path(url.split("?")[0]).name.split("_")[1].split(".")[0])
        arr = np.zeros((180, 320), dtype=np.uint8)
        arr[40:80, 40:120] = 255 if frame % 2 == 0 else 0   # empty on odd frames
        buf = io.BytesIO()
        Image.fromarray(arr, mode="L").save(buf, format="PNG")
        return SimpleNamespace(content=buf.getvalue(), raise_for_status=lambda: None)


@pytest.fixture
def patched(monkeypatch, tmp_path):
    # Only the token/model gates are stubbed; cfg itself stays untouched.
    monkeypatch.setattr(sam.cfg, "replicate_api_token", "r8_test")
    monkeypatch.setattr(sam.cfg, "replicate_sam_model", sam.MODEL)
    monkeypatch.setattr(sam.cfg, "sam_output_interval", 1)
    monkeypatch.setattr(sam.replicate, "Client", FakeClient)
    monkeypatch.setattr(sam.httpx, "Client", FakeHTTP)
    return tmp_path


def test_segment_maps_frame_zero_to_video_frame_zero_and_reports_partial(patched):
    provider = sam.ReplicateSAMProvider()
    video = dict(playback_path=__file__, frame_count=48, width=320, height=180,
                 pts=[round(i * 1e6 / 24) for i in range(48)])
    entity = dict(id="entity-1", seed_frame=12, seed_point=[30, 40])
    reports = []
    saved, info = provider.segment(video, entity, patched / "masks", reports.append)

    # REAL mapping: file 0 -> video frame 12 (the seed), not frame 0.
    assert saved[0]["frame_index"] == 12
    assert saved[0]["pts_us"] == video["pts"][12]
    assert [m["frame_index"] for m in saved] == list(range(12, 48, 2))
    assert all(Path(m["mask_ref"]).exists() for m in saved)
    assert info["output_count"] == 36
    assert info["mapped_frames"] == list(range(12, 48))
    assert info["missing_frames"] == []
    assert info["frames_before_seed"] == list(range(0, 12))
    assert info["mapping_verified"] == "seed_plus_filename_offset"
    assert info["status"] == "success"
    assert reports[0]["seed_frame"] == 12


def test_segment_marks_partial_when_frames_are_missing(monkeypatch, patched):
    class PartialClient(FakeClient):
        def create(self, version, input):
            return FakePrediction(range(0, 28), 48)   # 12..39 only, missing 40..47

    monkeypatch.setattr(sam.replicate, "Client", PartialClient)
    provider = sam.ReplicateSAMProvider()
    video = dict(playback_path=__file__, frame_count=48, width=320, height=180,
                 pts=[round(i * 1e6 / 24) for i in range(48)])
    entity = dict(id="entity-1", seed_frame=12, seed_point=[30, 40])
    saved, info = provider.segment(video, entity, patched / "masks2", lambda _: None)
    assert info["status"] == "partial"
    assert info["missing_frames"] == list(range(40, 48))
    assert len(saved) == 14
