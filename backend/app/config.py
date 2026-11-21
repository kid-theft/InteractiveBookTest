from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    dashscope_api_key: str = ""
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_model: str = "qwen3-vl-plus"
    replicate_api_token: str = ""
    replicate_sam_model: str = "meta/sam-2-video"
    video_storage_dir: Path = Path("/app/storage")
    database_url: str = "sqlite:////app/storage/app.db"
    video_max_size_mb: int = Field(100, ge=1, le=200)
    video_max_duration_sec: int = Field(30, ge=1, le=60)
    video_max_objects: int = Field(3, ge=1, le=3)
    video_sample_interval: float = Field(1, ge=0.25, le=10)
    video_canonical_fps: int = Field(24, ge=1, le=60)
    sam_max_objects: int = Field(3, ge=1, le=3)
    sam_concurrency: int = Field(2, ge=1, le=3)
    sam_output_interval: int = Field(1, ge=1, le=60)


cfg = Settings()
