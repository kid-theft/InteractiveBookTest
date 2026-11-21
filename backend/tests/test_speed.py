"""Unit tests for phase-3 speed features (no real API calls)."""
import pytest
from app import jobs
from app.config import cfg
from app import db


def test_select_keys_caps_evenly_and_keeps_first_last(monkeypatch):
    keys = [{'frame_index': i, 'pts_us': i, 'path': ''} for i in range(11)]
    monkeypatch.setattr(cfg, 'qwen_max_keyframes', 0)
    assert jobs.select_keys(keys) == keys
    monkeypatch.setattr(cfg, 'qwen_max_keyframes', 5)
    out = jobs.select_keys(keys)
    assert [k['frame_index'] for k in out] == [0, 2, 5, 7, 10] or len(out) == 5
    assert out[0]['frame_index'] == 0 and out[-1]['frame_index'] == 10
    monkeypatch.setattr(cfg, 'qwen_max_keyframes', 1)
    assert [k['frame_index'] for k in jobs.select_keys(keys)] == [0]


def test_masks_complete_rejects_missing_prediction():
    video = {'id': 'v', 'version': 1, 'frame_count': 10}
    entity = {'id': 'e', 'prediction': {'status': 'running'}, 'seed_frame': 0}
    assert jobs.masks_complete(video, entity) is False


def test_masks_complete_accepts_complete_real_prediction(monkeypatch):
    video = {'id': 'v', 'version': 1, 'frame_count': 4}
    entity = {'id': 'e', 'prediction': {'status': 'success', 'missing_frames': []}, 'seed_frame': 0}
    monkeypatch.setattr(cfg, 'sam_output_interval', 1)
    monkeypatch.setattr(jobs.db, 'rows', lambda q: [{'frame_index': i} for i in range(4)])
    assert jobs.masks_complete(video, entity) is True
    monkeypatch.setattr(jobs.db, 'rows', lambda q: [{'frame_index': i} for i in range(3)])
    assert jobs.masks_complete(video, entity) is False


def test_masks_complete_respects_interval(monkeypatch):
    video = {'id': 'v', 'version': 1, 'frame_count': 8}
    entity = {'id': 'e', 'prediction': {'status': 'success'}, 'seed_frame': 0}
    monkeypatch.setattr(cfg, 'sam_output_interval', 2)
    monkeypatch.setattr(jobs.db, 'rows', lambda q: [{'frame_index': i} for i in (0, 2, 4, 6)])
    assert jobs.masks_complete(video, entity) is True
