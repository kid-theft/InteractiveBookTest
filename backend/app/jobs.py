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
    db.update(db.tasks,task,stage=name,progress=progress,error=error)


def error_code(exc):
    if isinstance(exc,ValueError) and type(exc) is ValueError:
        return str(exc)[:100]
    status = getattr(exc,"status_code",None) or getattr(exc,"status",None)
    return f"{type(exc).__name__} {status or ''}".strip()


def prepare(vid, task):
    try:
        stage(task,"preprocessing",5)
        video = db.get(db.videos,vid)
        data, keys = preprocess(Path(video["video_path"]))
        with db.engine.begin() as con:
            con.execute(db.frames.insert(),[dict(id=db.uid(),video_id=vid,**f) for f in keys])
            con.execute(db.videos.update().where(db.videos.c.id==vid).values(**data,status="playable"))
        stage(task,"completed",100)
    except Exception as exc:
        db.update(db.videos,vid,status="failed")
        stage(task,"failed",0,error_code(exc))


def observe(video, task):
    provider = QwenVLMProvider()
    keys = db.rows(select(db.frames).where(db.frames.c.video_id==video["id"]).order_by(db.frames.c.frame_index))
    tracks = []
    history = []
    failures = 0
    for n,key in enumerate(keys):
        stage(task,"vlm_analyzing",5+int(40*n/len(keys)))
        try:
            scene = provider.analyze(Path(key["path"]).read_bytes())
        except Exception as exc:
            # A single unreadable frame must not discard the rest of the video.
            failures += 1
            stage(task,"vlm_analyzing",5+int(40*n/len(keys)),f"Qwen 关键帧 {key['frame_index']} 解析失败: {error_code(exc)}")
            continue
        used = set()
        for obj in sorted(scene.objects,key=lambda o:-o.confidence):
            box = pixels(obj.bbox_2d,video["width"],video["height"])
            center = [(box[0]+box[2])//2,(box[1]+box[3])//2]
            choices = [(math.dist(center,t["last_center"]),i) for i,t in enumerate(tracks)
                       if i not in used and (t["label"],t["category"])==(obj.label,obj.category)]
            if choices and min(choices)[0] <= math.hypot(video["width"],video["height"])*0.4:
                i = min(choices)[1]
            else:
                if len(tracks) >= min(cfg.video_max_objects,cfg.sam_max_objects):
                    continue
                i = len(tracks)
                tracks.append(dict(id=db.uid(),video_id=video["id"],version=video["version"],label=obj.label,
                                   category=obj.category,description=obj.description,attributes=obj.attributes,
                                   action=obj.action,seed_frame=key["frame_index"],seed_point=center,
                                   prediction={"status":"pending"}))
            used.add(i)
            tracks[i]["last_center"]=center
            history.append(dict(id=db.uid(),video_id=video["id"],version=video["version"],entity_id=tracks[i]["id"],
                                frame_index=key["frame_index"],bbox=box,confidence=obj.confidence,scene=scene.scene_description))
    stage(task,"entity_association",48)
    if failures == len(keys):
        raise ValueError("Qwen 未能解析任何关键帧")
    for track in tracks:
        del track["last_center"]
    if tracks:
        with db.engine.begin() as con:
            con.execute(db.entities.insert(),tracks)
            con.execute(db.observations.insert(),history)
    return tracks


def segment_one(video, entity, provider):
    started = time.monotonic()
    directory = Path(video["playback_path"]).parent / "masks" / entity["id"] / db.uid()
    info = {}
    def record(data):
        info.update(data)
        db.update(db.entities,entity["id"],prediction=dict(info))
    try:
        saved, result = provider.segment(video,entity,directory,record)
        with db.engine.begin() as con:
            con.execute(db.masks.delete().where(db.masks.c.track_id==entity["id"]))
            if saved:
                con.execute(db.masks.insert(),[dict(id=db.uid(),video_id=video["id"],video_version=video["version"],
                                  track_id=entity["id"],source="replicate_sam2",**m) for m in saved])
            con.execute(db.entities.update().where(db.entities.c.id==entity["id"]).values(prediction=result))
        return result["status"]
    except Exception as exc:
        status = "partial" if isinstance(exc,ValueError) else "failed"
        record(dict(status=status,error=error_code(exc),elapsed_sec=round(time.monotonic()-started,2)))
        return status


def analyze(vid, task, index_id, segment_only):
    try:
        video = db.get(db.videos,vid)
        tracks = (db.rows(select(db.entities).where(db.entities.c.video_id==vid,db.entities.c.version==video["version"]))
                  if segment_only else observe(video,task))
        if not tracks:
            raise ValueError("没有可分割对象")
        stage(task,"sam_segmenting",50)
        provider = ReplicateSAMProvider()
        outcomes = []
        with ThreadPoolExecutor(max_workers=cfg.sam_concurrency) as pool:
            futures = [pool.submit(segment_one,video,e,provider) for e in tracks]
            for future in as_completed(futures):
                outcomes.append(future.result())
                stage(task,"sam_segmenting",50+int(45*len(outcomes)/len(tracks)))
        stage(task,"building_index",97)
        status = "success" if all(s=="success" for s in outcomes) else "partial"
        if all(s=="failed" for s in outcomes):
            status = "failed"
        error = "" if status=="success" else "请查看对象的分割任务详情"
    except Exception as exc:
        status,error = "failed",error_code(exc)
    db.update(db.indices,index_id,status=status)
    db.update(db.videos,vid,status="playable")
    stage(task,"completed" if status=="success" else status,100,error)
