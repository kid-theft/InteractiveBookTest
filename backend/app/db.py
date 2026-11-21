"""SQLite metadata. Large media and mask bytes live in the named storage volume."""
import uuid
from sqlalchemy import (JSON, Column, Float, Integer, MetaData, String, Table,
                        UniqueConstraint, create_engine, select)
from .config import cfg

cfg.video_storage_dir.mkdir(parents=True, exist_ok=True)
engine = create_engine(cfg.database_url, connect_args={"timeout": 30, "check_same_thread": False})
meta = MetaData()


def table(name, fields, *constraints):
    return Table(name, meta, Column("id", String, primary_key=True),
                 *(Column(key, kind) for key, kind in fields.items()), *constraints)


videos = table("video", dict(name=String, video_path=String, playback_path=String, width=Integer,
                             height=Integer, original_fps=Float, canonical_fps=Integer, duration=Float,
                             frame_count=Integer, pts=JSON, status=String, version=Integer))
frames = table("video_frame", dict(video_id=String, frame_index=Integer, pts_us=Integer, path=String))
indices = table("video_index", dict(video_id=String, version=Integer, status=String))
entities = table("video_entity", dict(video_id=String, version=Integer, label=String, category=String,
                                      description=String, attributes=JSON, action=String, seed_frame=Integer,
                                      seed_point=JSON, prediction=JSON))
observations = table("entity_observation", dict(video_id=String, version=Integer, entity_id=String,
                    frame_index=Integer, bbox=JSON, confidence=Float, scene=String))
masks = table("frame_mask", dict(video_id=String, video_version=Integer, frame_index=Integer, pts_us=Integer,
              track_id=String, mask_ref=String, bbox=JSON, area=Integer, source=String),
              UniqueConstraint("video_id","video_version","frame_index","track_id"))
tasks = table("task", dict(video_id=String, stage=String, progress=Integer, error=String))
sessions = table("chat_session", dict(video_id=String, entity_id=String))
messages = table("chat_message", dict(session_id=String, question=String, answer=String, evidence=JSON, created=Float))


def uid():
    return uuid.uuid4().hex


def rows(query):
    with engine.connect() as db:
        return [dict(row) for row in db.execute(query).mappings()]


def get(tbl, key):
    found = rows(select(tbl).where(tbl.c.id == key))
    return found[0] if found else None


def update(tbl, key, **fields):
    with engine.begin() as db:
        db.execute(tbl.update().where(tbl.c.id == key).values(**fields))
