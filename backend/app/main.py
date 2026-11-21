import json
from contextlib import asynccontextmanager
from pathlib import Path
import shutil
import time
from typing import Annotated
from fastapi import FastAPI, APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
import numpy as np
from PIL import Image
from . import db
from .config import cfg
from .jobs import analyze, prepare, error_code
from .media import displayed_frame, inspect_video, jpeg_at
from .qwen import QwenVLMProvider


@asynccontextmanager
async def lifespan(app):
    db.meta.create_all(db.engine)
    with db.engine.begin() as con:
        con.execute(db.tasks.update().where(db.tasks.c.stage.not_in(["completed","partial","failed"])).values(
                    stage="failed",error="服务重启，请重试"))
        con.execute(db.indices.update().where(db.indices.c.status.in_(["running","pending"])).values(status="failed"))
        con.execute(db.videos.update().where(db.videos.c.status=="indexing").values(status="playable"))
        con.execute(db.videos.update().where(db.videos.c.status=="uploaded").values(status="failed"))
    yield


app = FastAPI(title="Video Interactive Lab", lifespan=lifespan)
app.add_middleware(CORSMiddleware,allow_origins=["http://localhost:5173","http://127.0.0.1:5173"],
                   allow_methods=["GET","POST"],allow_headers=["Content-Type"])
api = APIRouter(prefix="/api/v1")


def require(table, key):
    record = db.get(table,key)
    if not record:
        raise HTTPException(404,"记录不存在")
    return record


@api.get("/health")
def health():
    return {"status":"ok","qwen_configured":bool(cfg.dashscope_api_key),"replicate_configured":bool(cfg.replicate_api_token)}


@api.post("/videos",status_code=202)
def upload(background: BackgroundTasks,file: Annotated[UploadFile,File()]):
    name = Path(file.filename or "").name
    ext = Path(name).suffix.lower()
    if ext not in {".mp4",".mov",".avi"}:
        raise HTTPException(415,"支持 MP4、MOV、AVI")
    vid, task = db.uid(),db.uid()
    directory = cfg.video_storage_dir / "videos" / vid
    directory.mkdir(parents=True)
    original = directory / ("original"+ext)
    try:
        size = 0
        with original.open("wb") as out:
            while chunk := file.file.read(1024*1024):
                size += len(chunk)
                if size > cfg.video_max_size_mb*1024*1024:
                    raise HTTPException(413,"文件过大")
                out.write(chunk)
        inspect_video(original)
        with db.engine.begin() as con:
            con.execute(db.videos.insert().values(id=vid,name=name,video_path=str(original),version=0,status="uploaded"))
            con.execute(db.tasks.insert().values(id=task,video_id=vid,stage="uploaded",progress=0,error=""))
    except Exception as exc:
        shutil.rmtree(directory)
        if isinstance(exc,HTTPException):
            raise
        raise HTTPException(422,"视频内容、分辨率或时长不受支持") from exc
    background.add_task(prepare,vid,task)
    return {"video_id":vid,"task_id":task}


@api.get("/videos")
def library():
    return db.rows(select(db.videos.c.id,db.videos.c.name,db.videos.c.status).order_by(db.videos.c.name))


@api.get("/videos/{vid}")
def video_details(vid:str):
    v = require(db.videos,vid)
    keys=db.rows(select(db.frames.c.frame_index,db.frames.c.pts_us).where(db.frames.c.video_id==vid).order_by(db.frames.c.frame_index))
    return {k:v[k] for k in ("id","name","width","height","duration","canonical_fps","original_fps","frame_count","status","version")} | {"keyframes":keys}


@api.get("/videos/{vid}/playback")
def playback(vid:str):
    path = require(db.videos,vid)["playback_path"]
    if not path:
        raise HTTPException(409,"视频尚未准备好")
    return FileResponse(path,media_type="video/mp4")


@api.get("/videos/{vid}/thumbnail")
def thumbnail(vid:str):
    v=require(db.videos,vid)
    if not v["playback_path"]:
        raise HTTPException(409,"预处理未完成")
    return FileResponse(Path(v["playback_path"]).with_name("thumbnail.jpg"))


@api.get("/tasks/{key}")
def task_status(key:str):
    return require(db.tasks,key)


class Seed(BaseModel):
    entity_id:str
    frame_index:int=Field(ge=0)
    x:int=Field(ge=0)
    y:int=Field(ge=0)


def begin(vid,background,segment_only,seed=None):
    v=require(db.videos,vid)
    if not v["playback_path"]:
        raise HTTPException(409,"等待视频预处理")
    if not cfg.replicate_api_token or (not segment_only and not cfg.dashscope_api_key):
        raise HTTPException(503,"请在后端 .env 配置所需的百炼和 Replicate 密钥，并重建 API 容器")
    # A fresh analysis creates a new index version; re-segmentation reuses the
    # current version and its existing entities/index row.
    version=v["version"]+int(not segment_only)
    index_id=f"{vid}-{version}"
    task=db.uid()
    with db.engine.begin() as con:
        claim=con.execute(db.videos.update().where(db.videos.c.id==vid,db.videos.c.status=="playable",
                          db.videos.c.version==v["version"]).values(status="indexing",version=version))
        if not claim.rowcount:
            raise HTTPException(409,"任务正在运行")
        if segment_only:
            if not con.execute(select(db.entities.c.id).where(db.entities.c.video_id==vid,db.entities.c.version==version)).first():
                raise HTTPException(409,"请先分析视频")
            con.execute(db.indices.update().where(db.indices.c.id==index_id).values(status="running"))
            if seed:
                if seed.frame_index>=v["frame_count"] or seed.x>=v["width"] or seed.y>=v["height"]:
                    raise HTTPException(422,"种子点超出画面")
                changed=con.execute(db.entities.update().where(db.entities.c.id==seed.entity_id,
                    db.entities.c.version==version,db.entities.c.video_id==vid).values(seed_frame=seed.frame_index,seed_point=[seed.x,seed.y]))
                if not changed.rowcount:
                    raise HTTPException(404,"对象不存在")
        else:
            con.execute(db.indices.insert().values(id=index_id,video_id=vid,version=version,status="pending"))
        con.execute(db.tasks.insert().values(id=task,video_id=vid,stage="uploaded",progress=0,error=""))
    db.update(db.indices,index_id,status="running")
    background.add_task(analyze,vid,task,index_id,segment_only)
    return {"task_id":task}


@api.post("/videos/{vid}/analyze",status_code=202)
def start_analysis(vid:str,background:BackgroundTasks):
    return begin(vid,background,False)


@api.post("/videos/{vid}/segment",status_code=202)
def start_segmentation(vid:str,background:BackgroundTasks,seed:Seed|None=None):
    return begin(vid,background,True,seed)


@api.get("/videos/{vid}/entities")
def objects(vid:str):
    v=require(db.videos,vid)
    return db.rows(select(db.entities).where(db.entities.c.video_id==vid,db.entities.c.version==v["version"]))


@api.get("/videos/{vid}/index")
def index(vid:str,timestamp_sec:float|None=None):
    v=require(db.videos,vid)
    result=db.get(db.indices,f"{vid}-{v['version']}") or {"status":"pending","version":0}
    if timestamp_sec is not None and v["pts"]:
        frame=displayed_frame(v["pts"],timestamp_sec,v["duration"])
        result["frame_index"]=frame
        result["masks"]=db.rows(select(db.masks.c.track_id,db.masks.c.area,db.masks.c.pts_us).where(
                           db.masks.c.video_id==vid,db.masks.c.video_version==v["version"],db.masks.c.frame_index==frame)) if frame is not None else []
    return result


@api.get("/videos/{vid}/masks/{entity_id}/{frame}")
def mask(vid:str,entity_id:str,frame:int):
    v=require(db.videos,vid)
    matches=db.rows(select(db.masks.c.mask_ref).where(db.masks.c.video_id==vid,db.masks.c.video_version==v["version"],
                             db.masks.c.frame_index==frame,db.masks.c.track_id==entity_id))
    if not matches:
        raise HTTPException(404,"当前帧没有此对象的 Mask")
    return FileResponse(matches[0]["mask_ref"],media_type="image/png",headers={"Cache-Control":"no-store"})


class Touch(BaseModel):
    timestamp_sec:float=Field(ge=0,allow_inf_nan=False)
    x_norm:float=Field(ge=0,lt=1)
    y_norm:float=Field(ge=0,lt=1)


# Radius (in normalized units) used only for low-confidence edge candidates.
# A direct pixel hit always wins; bbox containment is NEVER used as a hit.
EDGE_RADIUS_NORM = 0.02
EDGE_RADIUS_PX_CAP = 12


def mask_hit(mask_path, width, height, x_norm, y_norm):
    """Return (direct_hit, edge_hit) from the actual binary mask pixels."""
    with Image.open(mask_path) as im:
        if im.size != (width, height):
            return False, False
        gray = im.convert("L")
        px = np.asarray(gray) > 0
        cx, cy = int(x_norm * width), int(y_norm * height)
        cx, cy = min(cx, width - 1), min(cy, height - 1)
        if px[cy, cx]:
            return True, True
        radius = min(EDGE_RADIUS_PX_CAP, max(2, round(EDGE_RADIUS_NORM * min(width, height))))
        x0, x1 = max(0, cx - radius), min(width, cx + radius + 1)
        y0, y1 = max(0, cy - radius), min(height, cy + radius + 1)
        return False, bool(px[y0:y1, x0:x1].any())


@api.post("/videos/{vid}/touch")
def touch(vid:str,p:Touch):
    v=require(db.videos,vid)
    frame=displayed_frame(v["pts"],p.timestamp_sec,v["duration"])
    if frame is None:
        raise HTTPException(422,"点击时间超出视频范围")
    direct, edge = [], []
    for m in db.rows(select(db.masks).where(db.masks.c.video_id==vid,db.masks.c.video_version==v["version"],db.masks.c.frame_index==frame)):
        if m["pts_us"]!=v["pts"][frame]:
            continue
        hit, near = mask_hit(m["mask_ref"],v["width"],v["height"],p.x_norm,p.y_norm)
        if not (hit or near):
            continue
        e=db.get(db.entities,m["track_id"])
        if not e:
            continue
        item=dict(entity_id=e["id"],name=e["label"],category=e["category"],description=e["description"],
                  area=m["area"],confidence=1.0 if hit else 0.4)
        (direct if hit else edge).append(item)
    candidates = direct + (edge if not direct else [])
    if len(direct)==1:
        status, selected, method, score = "matched", direct[0], "mask_pixel_hit", 1.0
    elif len(direct)>1:
        status, selected, method, score = "ambiguous", None, "mask_pixel_hit", 1.0
    elif edge:
        status, selected, method, score = "ambiguous", None, "mask_edge_candidate", 0.4
    else:
        status, selected, method, score = "no_hit", None, None, 0.0
    return dict(status=status,video_id=vid,frame_index=frame,timestamp_sec=p.timestamp_sec,
                selected_entity=selected,candidates=candidates,match_method=method,match_score=score)


class Question(BaseModel):
    session_id:str|None=None
    entity_id:str
    timestamp_sec:float=Field(ge=0,allow_inf_nan=False)
    question:str=Field(min_length=1,max_length=2000)


@api.post("/videos/{vid}/qa")
def qa(vid:str,p:Question):
    v=require(db.videos,vid)
    e=require(db.entities,p.entity_id)
    if (e["video_id"],e["version"])!=(vid,v["version"]):
        raise HTTPException(404,"当前索引无此对象")
    frame=displayed_frame(v["pts"],p.timestamp_sec,v["duration"])
    if frame is None:
        raise HTTPException(422,"超出视频时间")
    sid=p.session_id or db.uid()
    if p.session_id:
        s=require(db.sessions,sid)
        if (s["video_id"],s["entity_id"])!=(vid,p.entity_id):
            raise HTTPException(409,"会话与当前对象不符")
    observations=db.rows(select(db.observations).where(db.observations.c.entity_id==e["id"]))
    evidence=sorted(observations,key=lambda row:abs(row["frame_index"]-frame))[:3]
    history=db.rows(select(db.messages.c.question,db.messages.c.answer).where(db.messages.c.session_id==sid)
                    .order_by(db.messages.c.created.desc()).limit(4))[::-1]
    prompt=("你是视频内容问答助手。只能依据【当前帧画面】和给定的观察证据回答，"
            "不得凭常识或想象补充视频没有展示的信息；证据不足时明确回答“视频中无法确定”。"
            "回答用中文，简洁准确，不要复述问题。\n以下是结构化证据 JSON（question 是用户问题）：\n"
            + json.dumps(dict(time=p.timestamp_sec,entity={k:e[k] for k in ("label","attributes","action","description")},
                              nearby_observations=evidence,history=history,question=p.question),ensure_ascii=False))
    try:
        answer=QwenVLMProvider().request(jpeg_at(v["playback_path"],frame),prompt)
    except Exception as exc:
        raise HTTPException(503,error_code(exc)) from exc
    cited=sorted({frame,*[obs["frame_index"] for obs in evidence]})
    with db.engine.begin() as con:
        if not p.session_id:
            con.execute(db.sessions.insert().values(id=sid,video_id=vid,entity_id=e["id"]))
        con.execute(db.messages.insert().values(id=db.uid(),session_id=sid,question=p.question,answer=answer,evidence=cited,created=time.time()))
    return dict(session_id=sid,answer=answer,evidence_frames=cited)


app.include_router(api)
