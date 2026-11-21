"""Pure-function tests: no external services, no fake AI results."""
import io
import numpy as np
import pytest
from PIL import Image

from app.media import displayed_frame
from app.qwen import ObjectEvidence, Scene, pixels
from app.sam import output_frame, read_binary, find_output_schema


def test_qwen_bbox_conversion_and_validation():
    assert pixels([0, 0, 1000, 1000], 640, 360) == [0, 0, 640, 360]
    assert pixels([250, 250, 500, 750], 640, 360) == [160, 90, 320, 270]
    with pytest.raises(ValueError, match="qwen_bbox_out_of_range"):
        pixels([0, 0, 1001, 100], 640, 360)
    with pytest.raises(ValueError, match="qwen_empty_bbox"):
        pixels([500, 0, 500, 100], 640, 360)


def test_qwen_scene_schema_rejects_bad_objects():
    scene = Scene.model_validate({
        "scene_description": "一只白兔和一只蜜蜂",
        "objects": [{"label": "白兔", "category": "动物", "description": "白色兔子",
                     "attributes": {"配饰": "眼镜"}, "action": "坐着",
                     "bbox_2d": [100, 100, 400, 600], "confidence": 0.9}],
    })
    assert scene.objects[0].label == "白兔"
    with pytest.raises(Exception):
        ObjectEvidence.model_validate({"label": "", "category": "", "description": "",
                                       "attributes": {}, "action": "", "bbox_2d": [0, 0, 1, 1],
                                       "confidence": 2})


def test_displayed_frame_uses_pts_bisect_and_rejects_out_of_range():
    pts = [0, 41667, 83333, 125000, 166667, 208333, 250000]
    assert displayed_frame(pts, 0.0, 7 / 24) == 0
    assert displayed_frame(pts, 0.10, 7 / 24) == 2
    assert displayed_frame(pts, 0.24, 7 / 24) == 5
    assert displayed_frame(pts, 1.0, 7 / 24) is None
    assert displayed_frame([], 0.1, 1.0) is None


def test_sam_output_frame_is_absolute_video_frame_not_seed_offset():
    # The model source enumerates the whole video from frame 0; click_frames does
    # not shift output numbering. seed=48 must NOT turn frame_00000 into frame 48.
    base = "https://replicate.delivery/xk9/frame_{:05d}.png?sig=abc"
    count, interval = 120, 1
    for i in (0, 1, 24, 119):
        assert output_frame(base.format(i), count, interval) == i
    assert output_frame(base.format(120), count, interval) is None   # beyond video
    assert output_frame("https://evil.example/frame_00001.png", count, interval) is None
    assert output_frame("https://replicate.delivery/x/mask_00001.png", count, interval) is None
    assert output_frame(base.format(3), count, 2) is None             # interval mismatch


def test_find_output_schema_handles_inline_and_component_shapes():
    inline = {"paths": {"/predictions": {"post": {"responses": {"200": {
        "content": {"application/json": {"schema": {"properties": {
            "output": {"type": "array", "items": {"type": "string", "format": "uri"}}}}}}}}}}}}
    assert find_output_schema(inline)["type"] == "array"
    assert find_output_schema({"components": {"schemas": {"PredictionResponse": {
        "properties": {"output": {"type": "array", "items": {"format": "uri"}}}}}}})["items"]["format"] == "uri"
    assert find_output_schema({"nothing": True}) is None


def test_read_binary_accepts_only_0_255_png_at_video_resolution():
    buf = io.BytesIO()
    Image.fromarray(np.array([[0, 255], [255, 0]], dtype=np.uint8), mode="L").save(buf, format="PNG")
    mask, area = read_binary(buf.getvalue(), (2, 2))
    assert area == 2 and mask.size == (2, 2)
    bad = io.BytesIO()
    Image.fromarray(np.array([[0, 128], [255, 0]], dtype=np.uint8), mode="L").save(bad, format="PNG")
    with pytest.raises(ValueError, match="mask_not_binary"):
        read_binary(bad.getvalue(), (2, 2))
    with pytest.raises(ValueError, match="mask_format_or_dimensions"):
        read_binary(buf.getvalue(), (4, 4))
