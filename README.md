# Video Interactive Lab（纸书智伴）

基于 **Qwen3-VL-Plus（阿里云百炼）** 与 **Replicate SAM 2 Video** 的交互式视频识别与问答 MVP。

上传 MP4 → FFmpeg 标准化 → Qwen 关键帧理解 / 对象识别 → SAM 2 逐帧分割 →
保存二值 PNG Mask → 建立 Video Object Index → 浏览器播放 → 点击对象识别 → Qwen 问答。

## 技术栈

| 层 | 技术 |
|---|---|
| 前端 | Vue 3 + TypeScript + Vite + Canvas 叠加层 |
| 后端 | Python 3.12 + FastAPI + SQLAlchemy + SQLite |
| 视频 | FFmpeg / FFprobe（容器内） |
| VLM | `qwen3-vl-plus`，OpenAI-compatible API |
| 分割 | `meta/sam-2-video`（固定版本 `33432afd…`） |
| 容器 | Docker + Docker Compose |

## 快速启动

```bash
# 1. 填写密钥（不要提交到 Git）
cp .env.example .env
# 编辑 .env：DASHSCOPE_API_KEY=...  REPLICATE_API_TOKEN=...

# 2. 启动（首次需要能访问 Docker Hub 拉取 python:3.12-slim / node:22-alpine）
docker compose config
docker compose up --build -d
docker compose ps
docker compose logs --tail=100
```

访问：

- 前端：http://localhost:5173
- 后端：http://localhost:8000
- API 文档：http://localhost:8000/docs
- 健康检查：http://localhost:8000/api/v1/health

如果镜像已在本机缓存、但无法连接 Docker Hub，可用 `docker compose up -d` 直接启动：
`backend/` 与 `frontend/` 都是 bind mount，容器内运行的是当前工作区代码。
若需要离线重建 API 镜像，可覆盖基础镜像参数：
`docker compose build --build-arg PYTHON_BASE=<本地已缓存的基础镜像> api`。

## 使用流程

1. 打开 http://localhost:5173，左侧选择一段 ≤30 秒、≤100 MB 的 MP4/MOV/AVI。
2. 上传后自动：FFmpeg 校验 → 转 H.264 / 24fps / 长边 1280 / 保持宽高比 →
   逐帧 PTS 校验 → 每 1 秒关键帧 → 缩略图。播放与 SAM 推理使用**同一个** `playback.mp4`。
3. 配置好两个密钥后，点「开始 / 重新分析」：Qwen 识别最多 3 个主要对象（含
   bbox / 属性 / 动作 / 置信度）→ 每个对象独立调用一次 SAM 2 → 下载并校验
   0/255 PNG Mask → 写入 `frame_mask` 索引。
4. 播放视频并点击画面：前端用 `requestVideoFrameCallback` 取得实际显示帧的
   `mediaTime`，按 `object-fit: contain` 排除留白后把坐标归一化发给后端；
   后端按 `mask[y,x] > 0` 判定，唯一命中 → `matched`，多命中/边缘 → `ambiguous`，
   空白 → `no_hit`（**绝不回退到 bbox，也绝不强行选对象**）。
5. 选中对象后在右侧提问，Qwen 依据当前帧画面 + 对象属性 + 近邻关键帧观察 +
   最近 4 轮历史作答；证据不足时回答「视频中无法确定」。

## 配置说明（`.env.example`）

| 变量 | 默认 | 说明 |
|---|---|---|
| `DASHSCOPE_API_KEY` | 空 | 百炼 API Key（**仅后端读取，不暴露给浏览器**） |
| `QWEN_BASE_URL` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | OpenAI 兼容端点 |
| `QWEN_MODEL` | `qwen3-vl-plus` | VLM 模型 |
| `REPLICATE_API_TOKEN` | 空 | Replicate Token |
| `REPLICATE_SAM_MODEL` | `meta/sam-2-video` | 分割模型 |
| `VIDEO_STORAGE_DIR` | `/app/storage` | 媒体/Mask/SQLite 根目录（命名卷） |
| `DATABASE_URL` | `sqlite:////app/storage/app.db` | SQLite |
| `VIDEO_MAX_SIZE_MB` | 100 | 上传上限 |
| `VIDEO_MAX_DURATION_SEC` | 30 | 首版时长上限 |
| `VIDEO_CANONICAL_FPS` | 24 | 标准帧率 |
| `VIDEO_SAMPLE_INTERVAL` | 1.0 | 关键帧间隔（秒） |
| `VIDEO_MAX_OBJECTS` / `SAM_MAX_OBJECTS` | 3 | 最多对象数 |
| `SAM_OUTPUT_INTERVAL` | 1 | 输出每 N 帧 |

## 数据模型（SQLite，见 `backend/app/db.py`）

`video` / `video_frame` / `video_entity` / `entity_observation` / `video_index` /
`frame_mask` / `task` / `chat_session` / `chat_message`。

`frame_mask` 关联：`video_id, video_version, frame_index, pts_us, track_id,
mask_ref, bbox, area, source`。媒体文件保存在 `storage` 命名卷，DB 只存路径。

## API（前缀 `/api/v1`）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 服务与密钥配置状态 |
| POST | `/videos` | 上传视频（multipart `file`） |
| GET | `/videos` | 视频库 |
| GET | `/videos/{id}` | 详情 + 关键帧 |
| GET | `/videos/{id}/playback` | 标准化 MP4 |
| GET | `/videos/{id}/thumbnail` | 缩略图 |
| GET | `/tasks/{id}` | 任务进度 |
| POST | `/videos/{id}/analyze` | Qwen + SAM 全流程 |
| POST | `/videos/{id}/segment` | 仅重跑分割（可传 seed 修正） |
| GET | `/videos/{id}/entities` | 对象列表（含分割状态） |
| GET | `/videos/{id}/index?timestamp_sec=` | 索引状态 / 当前帧 Mask |
| GET | `/videos/{id}/masks/{entity_id}/{frame}` | PNG Mask |
| POST | `/videos/{id}/touch` | 点击识别 |
| POST | `/videos/{id}/qa` | 多轮问答 |

## 测试

```bash
# 后端（在容器内运行，含真实 FFmpeg 上传/预处理集成测试）
docker compose exec -T api python -m pytest -q /app/tests

# 前端类型检查 + 构建 + 单元测试（主机或容器）
cd frontend && npm ci && npm run build && npm test

# Playwright 冒烟（需先安装匹配版本的浏览器：npx playwright install chromium）
cd frontend && npm run test:e2e
```

测试分层说明：

- `backend/tests/test_core.py`：纯函数（bbox 转换、PTS→帧、SAM 输出帧映射、PNG 校验）。
- `backend/tests/test_sam_provider.py`：**显式 Mock** 的 Replicate 客户端，验证
  frame_0 映射、Mask 落盘、partial 上报；这是单元测试，不是真实 API 调用。
- `backend/tests/test_api.py`：真实 FFmpeg 生成 MP4 的上传/预处理集成测试；
  Mask 为**显式测试夹具**，用于验证点击识别层，不冒充 AI 结果。
- 前端 `geometry.test.ts`：`object-fit: contain` 坐标换算。

## SAM 2 帧映射（重要）

依据模型页与实现源码 `zsxkib/segment-anything-2@video/predict.py`：
输出文件名为 `frame_{frame_idx:05d}.png`，`frame_idx` 从**整段视频第 0 帧**枚举；
`click_frames` 只决定 seed point 施加在哪一帧，**不偏移输出编号**。
`output_frame_interval=N` 时只有 `frame_idx % N == 0` 的帧会输出。
无法验证映射的文件一律报错并把任务标记为 `partial`，绝不伪造 Mask。

## 已知限制

- 无密钥时 `/analyze` 返回 503，不伪造对象或 Mask。
- 空 Mask（对象在该帧不可见）会跳过写入；帧缺失会标记 `partial` 并列出 `missing_frames`。
- 首版最多 3 个对象、时长 30 秒、24fps；无对象跟踪重识别（仅标签+中心距离关联）。
- QA 为纯文本问答；没有向量库、没有多模态视频流输入。
- Playwright 需要匹配其版本的 Chromium；本环境缺少该修订版且网络受限，
  浏览器渲染已用系统 Chrome 无头 DOM 快照独立验证。
