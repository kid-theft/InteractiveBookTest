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

# 真实浏览器 E2E（Playwright 核心 API + 系统 Chrome，无需下载浏览器）
cd frontend && npm run test:e2e:core
# 传统 runner（需匹配版本 Chromium；部分环境会挂起）
cd frontend && npm run test:e2e
```

测试分层说明：

- `backend/tests/test_core.py`：纯函数（bbox 转换、PTS→帧、SAM 输出帧映射、PNG 校验）。
- `backend/tests/test_sam_provider.py`：**显式 Mock** 的 Replicate 客户端，验证
  `seed + 文件序号` 映射、Mask 落盘、partial 上报；这是单元测试，不是真实 API 调用。
- `backend/tests/test_api.py`：真实 FFmpeg 生成 MP4 的上传/预处理集成测试；
  Mask 为**显式测试夹具**，用于验证点击识别层，不冒充 AI 结果。
- 前端 `geometry.test.ts`：`object-fit: contain` 坐标换算。

## SAM 2 帧映射（重要，已由真实 API 验证）

**2026-11-21 真实调用验证结论**：对 240 帧视频以 `click_frames=72` 发起真实预测，
返回 `frame_00000.png … frame_00167.png` 共 **168** 个文件（= 240 − 72）。
下载这些 PNG 与真实 Qwen bbox 计算 IoU：

| 输出文件 | 对应视频帧 | IoU |
|---|---|---|
| `frame_00000.png` | 72（seed） | 0.981 |
| `frame_00072.png` | 144 | 0.965 |
| `frame_00096.png` | 168 | 0.973 |
| `frame_00120.png` | 192 | 0.956 |

因此真实语义是 **`video_frame = seed_frame + 文件序号`**，输出只覆盖
`[seed_frame, frame_count)`，**不包含 seed 之前的帧**。
（第一阶段依据源码推断的「文件序号 == 绝对帧号」对 seed≠0 是错的，已按真实数据修正；
仅 seed=0 时两者恰好等价。）

`output_frame_interval=N` 时输出序号为 0, N, 2N…，映射到 `seed + k·N`。
`expected` 集合据此计算，缺帧列入 `missing_frames` 并把对象标记 `partial`；
无法验证映射的文件一律报错，绝不伪造 Mask。前端按同一规则用当前帧查询索引，
seed 之前的帧本来就没有 Mask，属于正常状态。

## 已知限制

- 无密钥时 `/analyze` 返回 503，不伪造对象或 Mask。
- 空 Mask（对象在该帧不可见）会跳过写入；帧缺失会标记 `partial` 并列出 `missing_frames`。
- 首版最多 3 个对象、时长 30 秒、24fps；无对象跟踪重识别（仅标签+中心距离关联）。
- QA 为纯文本问答；没有向量库、没有多模态视频流输入。
- Replicate 并发创建预测可能返回 429；已把 `SAM_CONCURRENCY` 默认设为 1，
  并在 `ReplicateSAMProvider` 加入有界退避重试（失败如实记为 failed，不伪造）。
- `@playwright/test` runner 在本机启动即挂起（浏览器本身正常）；
  另提供 `npm run test:e2e:core`，用 Playwright 核心 API + 系统 Chrome 直接跑真实浏览器 E2E。

## 处理耗时与提速（第三阶段）

基线：10s / 240 帧真实视频完整分析 **546.4 s**（SAM 3 对象 432.78 s ≈79%、
Qwen 11 帧 ≈109 s ≈20%、FFmpeg 2.88 s）。云端 AI 是唯一瓶颈。

已实施（均可用 `.env` 调整）：
| 机制 | 说明 | 实测 |
|---|---|---|
| 内容哈希复用 | 上传相同视频且已有 `index=success` 时直接返回该 video_id，跳过 FFmpeg/Qwen/SAM | 重新上传 `test.mp4`：**0.008 s**（不含上传）、含上传 **0.127 s** |
| 断点续传 | `POST /analyze` 在索引已完成时复用同 version；`analyze()` 跳过 `prediction=success` 且 mask 齐全的对象 | 重复 `/analyze` **0.06–0.17 s** |
| 单对象重跑 | `POST /segment` 新 body `{entity_id, seed?}`，只处理指定对象 | **0.03 s**（已成功对象直接复用） |
| Qwen 并行 | `QWEN_CONCURRENCY=4`，关键帧调用互不依赖 | 冷启动 Qwen 段 ~110 s → **~20 s** |
| 关键帧上限 | `QWEN_MAX_KEYFRAMES=6`（0=全部），均匀取样且保留首尾 | 11 帧 → 6 帧；frame 96 的蜜蜂仍被覆盖 |

诚实边界：**一次全新的、从未分析过的视频冷启动仍需数分钟**，因为 Replicate SAM 2
单次云端预测实测 90–220 s，本地无法压缩。50 s 目标在「重复上传 / 重新分析 /
补跑失败对象」场景达成；冷启动实测数字见 `DEV_HANDOFF.md`，不混为一谈。

## 前端 Mask 叠加（第二阶段）

`frontend/src/MaskOverlay.vue` 把真实 SAM 二值 PNG 渲染为半透明彩色图层：

- Mask 像素 0 → 完全透明；>0 → 对象颜色（青绿/黄/橙等按 entity id 稳定分配）。
- 选中的对象改为白色高亮 + 虚线 bbox；颜色只用于区分对象。
- 用 `requestVideoFrameCallback` 的 `mediaTime` 按标准 FPS 换算帧号（floor，与后端
  `displayed_frame` 一致）；兼容暂停、拖动、倍速、全屏、容器尺寸变化、无 Mask、视频结束。
- 缓存为有界 `Map`（最多 120 张），当前帧优先、预加载相邻 2 帧，卸载时释放；
  不在每个 rAF 重复请求同一 PNG。
- 后端 `/videos/{id}/index?timestamp_sec=` 新增 `mask_url` / `bbox` 字段供前端取图。
- 点击仍由后端 `mask[y,x] > 0` 判定；空白 `no_hit` 会清除选择。

## 真实视频验收结果（2026-11-21，`test.mp4` 10s / 240 帧）

| 项 | 结果 |
|---|---|
| FFmpeg 预处理 | 2.88 s，1280×720 / 24fps / 11 关键帧 |
| Qwen 单帧 / 11 帧 | 9.79 s / 9.16–10.68 s 每帧；识别出白兔、提灯、发光蜜蜂 |
| Replicate SAM 串行 3 对象 | 546.4 s 总计；白兔 240 帧、提灯 168 帧（seed=72）、蜜蜂 144 帧（seed=96），全部 `missing=0` |
| 索引 | `index=success`，t=5 时 3 个对象 Mask 同时存在 |
| 点击 | 白兔 / 蜜蜂均 `matched · mask_pixel_hit`；空白 `no_hit`；延迟 11 ms |
| Qwen QA | 「白兔。」「红色围巾。」「它是一只蜜蜂，正在飞行并环绕白兔…」 |
| 浏览器 E2E | `npm run test:e2e:core` **13/13**；Mask canvas 42,263 个非透明像素，截图 `frontend/test-results/phase2-overlay.png` |
| 单测 | 后端 12 passed、前端 4 passed、`npm run build` 通过 |
