import bisect
from fractions import Fraction
import json
import math
from pathlib import Path
import subprocess

from .config import cfg


def run(*args, timeout=120):
    return subprocess.run(args, capture_output=True, check=True, timeout=timeout).stdout


def inspect_video(path):
    info = json.loads(run("ffprobe", "-v", "error", "-protocol_whitelist", "file", "-show_streams",
                          "-show_format", "-of", "json", str(path), timeout=20))
    stream = next(s for s in info["streams"] if s["codec_type"] == "video")
    fmt = set(info["format"]["format_name"].split(","))
    duration = float(info["format"]["duration"])
    if not fmt.intersection({"avi", "mov", "mp4"}) or not 0 < duration <= cfg.video_max_duration_sec:
        raise ValueError(f"仅支持 {cfg.video_max_duration_sec} 秒以内的 MP4、MOV 和 AVI")
    if not 0 < stream["width"] * stream["height"] <= 3840 * 2160:
        raise ValueError("输入视频超过 4K 像素上限")
    return stream, duration


def displayed_frame(pts, seconds, duration):
    if not pts or not 0 <= seconds < duration:
        return None
    idx = bisect.bisect_right(pts, round(seconds * 1e6)) - 1
    return idx if idx >= 0 else None


def jpeg_at(path, frame):
    return run("ffmpeg", "-v", "error", "-i", str(path), "-vf", f"select=eq(n\\,{frame})",
               "-frames:v", "1", "-f", "image2pipe", "-vcodec", "mjpeg", "-", timeout=30)


def preprocess(original):
    root = original.parent
    stream, _ = inspect_video(original)
    playback = root / "playback.mp4"
    fps = cfg.video_canonical_fps
    run("ffmpeg", "-y", "-v", "error", "-protocol_whitelist", "file", "-i", str(original),
        "-map", "0:v:0", "-map", "0:a:0?", "-vf",
        f"scale=w='min(iw,1280)':h='min(ih,1280)':force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1,fps={fps}",
        "-c:v", "libx264", "-threads", "2", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-movflags", "+faststart", str(playback))
    canonical, _ = inspect_video(playback)
    timing = json.loads(run("ffprobe", "-v", "error", "-select_streams", "v:0", "-show_frames",
                      "-show_entries", "frame=best_effort_timestamp_time", "-of", "json", str(playback)))
    pts = [round(float(f["best_effort_timestamp_time"])*1e6) for f in timing["frames"]]
    if not pts or any(abs(t - round(i*1e6/fps)) > 1 for i,t in enumerate(pts)):
        raise ValueError("标准化帧时间异常")
    step = max(1, round(cfg.video_sample_interval * fps))
    keys = []
    for i in sorted({*range(0,len(pts),step), len(pts)-1}):
        dest = root / f"key_{i:05}.jpg"
        dest.write_bytes(jpeg_at(playback, i))
        keys.append(dict(frame_index=i, pts_us=pts[i], path=str(dest)))
    (root / "thumbnail.jpg").write_bytes(Path(keys[0]["path"]).read_bytes())
    return dict(playback_path=str(playback), canonical_fps=fps, frame_count=len(pts), pts=pts,
                duration=len(pts)/fps, width=canonical["width"], height=canonical["height"],
                original_fps=float(Fraction(stream["avg_frame_rate"] or stream["r_frame_rate"]))), keys
