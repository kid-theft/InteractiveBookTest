"""Replicate SAM 2 video provider.

Verified against the model's published input/output schema and the implementation
source at https://github.com/zsxkib/segment-anything-2/tree/video (predict.py):

* The prediction output is an array of URI strings named ``frame_00000.png`` etc.
* ``frame_idx`` enumerates the WHOLE input video from 0; ``click_frames`` only
  selects the frame on which the seed point is applied and does NOT offset the
  output numbering. (An earlier implementation incorrectly added the seed frame
  to every output index, shifting every mask in time.)
* ``mask_type="binary"`` yields a single-channel 0/255 PNG with the video's
  original pixel dimensions.
"""
import io
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
import numpy as np
from PIL import Image
import replicate
from .config import cfg

MODEL = "meta/sam-2-video"
VERSION = "33432afdfc06a10da6b4018932893d39b0159f838b6d11dd1236dff85cc5ec1d"
REQUIRED_INPUTS = {
    "input_video", "click_coordinates", "click_labels", "click_frames",
    "click_object_ids", "mask_type", "output_video", "output_format",
    "output_frame_interval",
}


def find_output_schema(node):
    """Find the prediction 'output' property anywhere in the published schema.

    Replicate exposes PredictionResponse inline on the model page and slightly
    differently through the API, so search recursively rather than assuming a
    single fixed JSON path.
    """
    if isinstance(node, dict):
        props = node.get("properties")
        if isinstance(props, dict) and "output" in props:
            return props["output"]
        for value in node.values():
            found = find_output_schema(value)
            if found is not None:
                return found
    elif isinstance(node, list):
        for value in node:
            found = find_output_schema(value)
            if found is not None:
                return found
    return None


def output_frame(url, count, interval):
    """Map one Replicate output URI to an absolute video frame index.

    Returns None when the filename cannot be verified against the documented
    ``frame_{frame_idx:05d}.png`` convention, so callers can mark the result
    partial instead of guessing that item N equals video frame N.
    """
    parsed = urlparse(str(url))
    if parsed.scheme != "https" or not (parsed.hostname == "replicate.delivery" or
                                         (parsed.hostname or "").endswith(".replicate.delivery")):
        return None
    match = re.fullmatch(r"frame_(\d{5})\.png", Path(parsed.path).name)
    if not match:
        return None
    frame = int(match[1])
    if frame >= count or frame % interval:
        return None
    return frame


def read_binary(data, size):
    with Image.open(io.BytesIO(data)) as im:
        if im.format != "PNG" or im.size != size or im.mode not in {"1", "L"}:
            raise ValueError("mask_format_or_dimensions")
        mask = im.convert("L")
        values = np.asarray(mask)
        if not np.all((values == 0) | (values == 255)):
            raise ValueError("mask_not_binary")
        return mask, int(np.count_nonzero(values))


class ReplicateSAMProvider:
    def __init__(self):
        if not cfg.replicate_api_token:
            raise ValueError("请填写 REPLICATE_API_TOKEN")
        if cfg.replicate_sam_model not in (MODEL, MODEL + ":" + VERSION):
            raise ValueError("未经验证的 SAM 模型版本")
        self.client = replicate.Client(api_token=cfg.replicate_api_token)
        version = self.client.models.get(MODEL).versions.get(VERSION)
        schema = version.openapi_schema
        inputs = schema["components"]["schemas"]["Input"]["properties"]
        if not REQUIRED_INPUTS <= inputs.keys():
            raise ValueError("replicate_schema_changed")
        output = find_output_schema(schema)
        if not isinstance(output, dict) or output.get("type") != "array" or output.get("items", {}).get("format") != "uri":
            raise ValueError("replicate_schema_changed")

    def segment(self, video, entity, directory, report):
        started = time.monotonic()
        seed = entity["seed_frame"]
        x, y = entity["seed_point"]
        if not 0 <= seed < video["frame_count"] or not (0 <= x < video["width"]) or not (0 <= y < video["height"]):
            raise ValueError("sam_seed_out_of_range")
        with open(video["playback_path"], "rb") as file:
            prediction = self.client.predictions.create(version=VERSION, input={
                "input_video": file, "click_coordinates": f"[{x},{y}]", "click_labels": "1",
                "click_frames": str(seed), "click_object_ids": entity["id"], "mask_type": "binary",
                "annotation_type": "mask", "output_video": False, "output_format": "png",
                "output_frame_interval": cfg.sam_output_interval})
        info = dict(id=prediction.id, status="running", seed_frame=seed, seed_point=[x, y],
                    version=VERSION, model=MODEL)
        report(info)
        while prediction.status not in ("succeeded", "failed", "canceled"):
            if time.monotonic() - started > 600:
                prediction.cancel()
                raise TimeoutError("replicate_timeout")
            time.sleep(1.5)
            prediction.reload()
        if prediction.status != "succeeded":
            raise ValueError("replicate_" + prediction.status)
        urls = prediction.output
        if not isinstance(urls, list):
            raise ValueError("replicate_output_schema")
        seen, saved = set(), []
        directory.mkdir(parents=True, exist_ok=True)
        with httpx.Client(timeout=30) as http:
            for url in urls:
                frame = output_frame(url, video["frame_count"], cfg.sam_output_interval)
                if frame is None or frame in seen:
                    raise ValueError("replicate_frame_mapping_unknown")
                response = http.get(str(url))
                response.raise_for_status()
                mask, area = read_binary(response.content, (video["width"], video["height"]))
                seen.add(frame)
                if area:
                    path = directory / f"{frame:06}.png"
                    mask.save(path)
                    saved.append(dict(frame_index=frame, pts_us=video["pts"][frame], mask_ref=str(path),
                                      bbox=list(mask.getbbox()), area=area))
        expected = set(range(0, video["frame_count"], cfg.sam_output_interval))
        missing = sorted(expected - seen)
        info.update(status="success" if not missing and saved else "partial",
                    output_count=len(urls), output_urls=[str(u) for u in urls],
                    mapped_frames=sorted(seen), missing_frames=missing,
                    elapsed_sec=round(time.monotonic() - started, 2))
        return saved, info
