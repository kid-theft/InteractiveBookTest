"""Background pipeline: preprocess, VLM observation, SAM segmentation, index build.

Phase 3 speed work:
* Qwen keyframe calls run concurrently (they are independent).
* ``analyze()`` skips objects whose real SAM prediction already succeeded and
  whose mask rows are complete -> re-analysis / re-segmentation only pays for
  what is actually missing (resume).
* ``/segment`` can target a single entity via ``POST`` body filtering.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import math
import time
from sqlalchemy import select
from . import db
from .config import cfg
from .media import preprocess
from .qwen import QwenVLMProvider, pixels
from .sam import ReplicateSAMProvider


def stage(task, name, progress, error=""):
    db.update(db.tasks, task, stage=name, progress=progress, error=error)


def error_code(exc):
    if isinstance(exc, ValueError) and type(exc) is ValueError:
        return str(exc)[:100]
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    detail = ""
    if status == 429:
        detail = " (Replicate 速率限制/额度，请降低 SAM_CONCURRENCY 或稍后重试)"
    return f"{type(exc).__name__} {status or ''}{detail}".strip()


def prepare(vid, task):
    try:
        stage(task, "preprocessing", 5)
        video = db.get(db.videos, vid)
        data, keys = preprocess(Path(video["video_path"]))
        with db.engine.begin() as con:
            con.execute(db.frames.insert(), [dict(id=db.uid(), video_id=vid, **f) for f in keys])
            con.execute(db.videos.update().where(db.videos.c.id == vid).values(**data, status="playable"))
        stage(task, "completed", 100)
    except Exception as exc:
        db.update(db.videos, vid, status="failed")
        stage(task, "failed", 0, error_code(exc))


BACKGROUND_WORDS = ("场景", "背景", "小径", "夜景", "森林", "路径", "天空", "地面")


def is_background(obj):
    text = f"{obj.label} {obj.category}"
    return any(word in text for word in BACKGROUND_WORDS)


def select_keys(keys):
    """Evenly cap the number of expensive Qwen calls; always keep first/last."""
    cap = cfg.qwen_max_keyframes
    if not cap or len(keys) <= cap:
        return keys
    if cap == 1:
        return [keys[0]]
    idx = sorted({round(i * (len(keys) - 1) / (cap - 1)) for i in range(cap)})
    return [keys[i] for i in idx]


def observe(video, task):
    """Two-pass entity association, with concurrent Qwen keyframe calls."""
    raw_keys = db.rows(select(db.frames).where(db.frames.c.video_id == video["id"]).order_by(db.frames.c.frame_index))
    keys = select_keys(raw_keys)
    provider = QwenVLMProvider()
    sightings = []
    failures = 0
    done = 0

    def call(key):
        scene = provider.analyze(Path(key["path"]).read_bytes())
        return key["frame_index"], scene

    with ThreadPoolExecutor(max_workers=cfg.qwen_concurrency) as pool:
        futures = {pool.submit(call, k): k for k in keys}
        for future in as_completed(futures):
            key = futures[future]
            done += 1
            stage(task, "vlm_analyzing", 5 + int(40 * done / len(keys)))
            try:
                frame, scene = future.result()
            except Exception as exc:
                failures += 1
                stage(task, "vlm_analyzing", 5 + int(40 * done / len(keys)),
                      f"Qwen 关键帧 {key['frame_index']} 解析失败: {error_code(exc)}")
                continue
            for obj in scene.objects:
                try:
                    box = pixels(obj.bbox_2d, video["width"], video["height"])
                except ValueError:
                    continue
                sightings.append(dict(frame=frame, obj=obj, box=box,
                                      center=[(box[0] + box[2]) // 2, (box[1] + box[3]) // 2],
                                      scene=scene.scene_description))
    stage(task, "entity_association", 48)
    if failures == len(keys) or not sightings:
        raise ValueError("Qwen 未能解析任何关键帧")

    clusters = []
    for s in sightings:
        o = s["obj"]
        best = None
        for i, c in enumerate(clusters):
            if (c["label"], c["category"]) == (o.label, o.category) and \
               math.dist(s["center"], c["last_center"]) <= math.hypot(video["width"], video["height"]) * 0.4:
                best = i
                break
        if best is None:
            clusters.append(dict(label=o.label, category=o.category, items=[s], last_center=s["center"]))
        else:
            c = clusters[best]
            c["items"].append(s)
            c["last_center"] = s["center"]

    limit = min(cfg.video_max_objects, cfg.sam_max_objects)

    def score(c):
        foreground = 0.35 if is_background(c["items"][0]["obj"]) else 1.0
        conf = sum(i["obj"].confidence for i in c["items"]) / len(c["items"])
        return len(c["items"]) * conf * foreground

    clusters.sort(key=score, reverse=True)
    chosen = clusters[:limit]
    tracks, history = [], []
    for c in chosen:
        best = max(c["items"], key=lambda s: s["obj"].confidence)
        o = best["obj"]
        tracks.append(dict(id=db.uid(), video_id=video["id"], version=video["version"], label=o.label,
                           category=o.category, description=o.description, attributes=o.attributes,
                           action=o.action, seed_frame=best["frame"], seed_point=best["center"],
                           prediction={"status": "pending"}))
    for c, t in zip(chosen, tracks):
        for s in c["items"]:
            history.append(dict(id=db.uid(), video_id=video["id"], version=video["version"], entity_id=t["id"],
                                frame_index=s["frame"], bbox=s["box"], confidence=s["obj"].confidence, scene=s["scene"]))
    if tracks:
        with db.engine.begin() as con:
            con.execute(db.entities.insert(), tracks)
            con.execute(db.observations.insert(), history)
    return tracks


def masks_complete(video, entity):
    """Resume check: a successful prediction with all expected frames present."""
    pred = entity.get("prediction") or {}
    if pred.get("status") != "success" or pred.get("missing_frames"):
        return False
    seed = entity.get("seed_frame") or 0
    interval = cfg.sam_output_interval
    expected = {seed + i for i in range(0, video["frame_count"] - seed, interval)}
    rows = db.rows(select(db.masks.c.frame_index).where(
        db.masks.c.video_id == video["id"], db.masks.c.video_version == video["version"],
        db.masks.c.track_id == entity["id"]))
    return expected <= {r["frame_index"] for r in rows}


def segment_one(video, entity, provider):
    started = time.monotonic()
    directory = Path(video["playback_path"]).parent / "masks" / entity["id"] / db.uid()
    info = {}

    def record(data):
        info.update(data)
        db.update(db.entities, entity["id"], prediction=dict(info))

    try:
        saved, result = provider.segment(video, entity, directory, record)
        with db.engine.begin() as con:
            con.execute(db.masks.delete().where(db.masks.c.track_id == entity["id"]))
            if saved:
                con.execute(db.masks.insert(), [dict(id=db.uid(), video_id=video["id"], video_version=video["version"],
                                                     track_id=entity["id"], source="replicate_sam2", **m) for m in saved])
            con.execute(db.entities.update().where(db.entities.c.id == entity["id"]).values(prediction=result))
        return result["status"]
    except Exception as exc:
        status = "partial" if isinstance(exc, ValueError) else "failed"
        record(dict(status=status, error=error_code(exc), elapsed_sec=round(time.monotonic() - started, 2)))
        return status


def analyze(vid, task, index_id, segment_only, entity_id=None):
    started = time.monotonic()
    try:
        video = db.get(db.videos, vid)
        if segment_only:
            tracks = db.rows(select(db.entities).where(db.entities.c.video_id == vid,
                                                       db.entities.c.version == video["version"]))
            if entity_id:
                tracks = [t for t in tracks if t["id"] == entity_id]
            if not tracks:
                raise ValueError("没有可分割对象")
        else:
            tracks = observe(video, task)
        # Resume: never re-pay for a real SAM prediction that already succeeded.
        pending, reused = [], []
        for t in tracks:
            (reused if masks_complete(video, t) else pending).append(t)
        if reused:
            stage(task, "reusing_index", 50, f"复用 {len(reused)} 个已完成对象，待处理 {len(pending)} 个")
        stage(task, "sam_segmenting", 50)
        outcomes = ["success"] * len(reused)
        if pending:
            provider = ReplicateSAMProvider()
            with ThreadPoolExecutor(max_workers=cfg.sam_concurrency) as pool:
                futures = [pool.submit(segment_one, video, e, provider) for e in pending]
                for future in as_completed(futures):
                    outcomes.append(future.result())
                    stage(task, "sam_segmenting", 50 + int(45 * len(outcomes) / len(tracks)))
        stage(task, "building_index", 97)
        status = "success" if all(s == "success" for s in outcomes) else "partial"
        if all(s == "failed" for s in outcomes):
            status = "failed"
        error = "" if status == "success" else "请查看对象的分割任务详情"
    except Exception as exc:
        status, error = "failed", error_code(exc)
    db.update(db.indices, index_id, status=status)
    db.update(db.videos, vid, status="playable")
    # Timing is measured by the polling client; we deliberately avoid altering
    # the existing SQLite schema (create_all does not ALTER live tables).
    stage(task, "completed" if status == "success" else status, 100, error)
