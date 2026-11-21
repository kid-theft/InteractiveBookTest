# 开发交接记录 / DEV_HANDOFF

更新时间：2026-11-21（第二阶段进行中）
当前分支：`test/touchVideo`
Git：已创建本地基线 commit `9d70e1e`（仅本地，未 push；`.env` 未纳入）。
本地备份：`D:\Project\InteractiveBookTest_backup_20261121_155929`（41 文件，SHA256 全部校验通过）。

## 1. Codex 已完成内容（接手时）

- `backend/app/`：FastAPI 全套路由（上传 / 任务 / 播放 / 实体 / 索引 / Mask / 点击 / QA）、
  SQLAlchemy+SQLite 数据模型、FFmpeg 预处理（H.264 / 24fps / 长边 1280 / 逐帧 PTS 校验 /
  1s 关键帧 / 缩略图）、Qwen3-VL-Plus Provider（Pydantic 结构化校验、0–1000→像素 bbox、
  跨帧实体关联）、Replicate SAM 2 Provider（固定版本、运行时 schema 校验、seed point、
  二值 PNG 校验、partial 上报）、后台 TaskManager（`jobs.py`）、QA 会话与证据帧。
- `frontend/`：`geometry.ts`（contain 坐标换算）+ vitest 单测、`index.html`、
  `vite.config.ts`（`/api` 代理到 `api:8000`）、`package.json` + lockfile、
  Dockerfile / Dockerfile.e2e / docker-entrypoint.sh。
- 根：`docker-compose.yml`（api + web + `storage` / `modules` 命名卷、healthcheck、
  端口 8000/5173）、`.env.example`、`.gitignore`、`.dockerignore`、`.env`（密钥为空）。
- 无 README、无设计文档、无后端测试、无 `main.ts` / `App.vue`（前端不可运行）、
  无 `playwright.config.mjs`（`test:e2e` 指向不存在的文件）。

## 2. 本次（Claude Code 接力）新增 / 修复

### P0 缺陷修复
- `backend/app/sam.py`：核实 Replicate 官方 schema 与实现源码后，修正**帧号映射错误**
  （原来 `seed + offset`，实际 `frame_00000.png` 就是整段视频第 0 帧）；
  增加 `annotation_type`、递归查找输出 schema、seed 范围校验、
  `output_urls` 字符串化（避免 JSON 列写入 FileOutput 对象）、
  `missing_frames` 明细与 partial 判定。
- `backend/app/main.py`：`/touch` 重写为严格的 Mask 像素判定 + 边缘低置信候选
  （不使用 bbox、不强制选对象）；时间戳越界返回 422；`/index` 越界保护；
  `begin()` 重新分割时不再错误递增 version；QA 提示加入防编造约束。
- `backend/app/qwen.py`：JSON 代码围栏 / 前后噪声容错。
- `backend/app/jobs.py`：单张关键帧 Qwen 解析失败不再丢弃整段分析。
- `backend/app/config.py` + `.env.example` + `media.py`：首版时长上限 30 秒（`VIDEO_MAX_DURATION_SEC`）。

### 前端补齐（原缺失、导致页面空白）
- `frontend/src/main.ts`、`App.vue`、`api.ts`、`style.css`：三栏工作台
  （上传/视频库/信息、播放器+Canvas 叠加、对象列表+多轮问答）。
  点击使用 `requestVideoFrameCallback` 的 `mediaTime`，排除 letterbox 留白。
- `frontend/playwright.config.mjs`、`e2e/smoke.spec.mjs`：补齐 `test:e2e` 引用。

### 测试与文档
- `backend/tests/`：12 个 pytest（纯函数 / Mock SAM Provider / 真实 FFmpeg 上传 +
  点击识别集成），全部在 Docker API 容器内通过。
- `README.md`（启动、配置、API、SAM 帧映射、测试分层）。
- 本文件 `DEV_HANDOFF.md`。

## 3. 当前运行方式

```bash
docker compose up -d                 # 本机已缓存镜像；代码通过 bind mount 实时生效
docker compose ps                    # api healthy / web up
docker compose logs --tail=100
# 前端 http://localhost:5173 · 后端 http://localhost:8000 · 文档 http://localhost:8000/docs
```

首次全新构建需要 `docker compose up --build -d`，但本机当前无法连通
`registry-1.docker.io`（Docker Hub 元数据超时），因此 `--build` 会在拉取
`python:3.12-slim` 时失败；`node:22-alpine` 已在缓存中。已验证缓存的
`interactivebook-lab-api:latest` 内含全部 Python 依赖与 FFmpeg 7.1.5，
配合 bind mount 可直接运行当前代码。

## 4. 验证结果（严格区分层次）

| 层次 | 结果 |
|---|---|
| 代码已实现 | 上传 / FFmpeg / Qwen / SAM / Mask / 索引 / 播放器 / 点击 / QA 全部有实现 |
| 本地（Docker）测试 | 后端 12 passed；前端 `vue-tsc + vite build` 通过、vitest 2 passed |
| 浏览器渲染 | 系统 Chrome 无头 DOM 快照确认 Vue 应用真实挂载（6 个区域全部渲染） |
| 真实上传（Docker HTTP） | 真实 MP4 上传→48 帧 @24fps→3 关键帧→playback/thumbnail 200→SQLite 落盘 |
| 真实 Qwen API | **未验证**：`.env` 中 `DASHSCOPE_API_KEY` 为空（WSL/系统环境同样为空） |
| 真实 Replicate API | **未验证**：`.env` 中 `REPLICATE_API_TOKEN` 为空 |
| 真实 Mask 生成 | **未验证**：依赖 Replicate 真实调用 |
| 端到端（白兔/蜜蜂/空白/问答） | **未验证**：需要先配置两个密钥并提供含白兔与蜜蜂的测试视频 |
| Playwright e2e | 本机缺少 Playwright 1.52 所需 Chromium 修订版 1169（现有 1228/1234），且无法下载；已改用系统 Chrome 做等效渲染验证 |

## 5. 已知问题

1. 两个 API Key 为空 → `/analyze` 返回 503；没有任何伪造数据。
2. Docker Hub 在本环境不可达，无法 `--build` 重建 API 镜像（可用缓存镜像 + bind mount 运行）。
3. `npm run test:e2e` 在本机需要 `npx playwright install chromium`（需网络）；
   配置已支持 `E2E_CHANNEL` 与 `E2E_BASE_URL`。
4. 前端 `showMasks` 目前只控制内容边框，不实时把 PNG Mask 叠加到视频上（P1）。
5. 无对象重识别 / 长视频分段；Qwen 单帧解析失败会跳过该帧；SAM 缺帧标记 partial。
6. `docker-compose.yml` 的 `web` 服务每次启动会执行 `npm ci`（entrypoint 按 lock 哈希判断，命中则跳过）。
7. 第一阶段结束时全部文件仍未纳入 Git；第二阶段开始时经用户确认已创建本地基线
   commit `9d70e1e`，未推送、未切换分支。

## 6. 下一步最值得做的 3 件事

1. **配置 `DASHSCOPE_API_KEY` 与 `REPLICATE_API_TOKEN`**，用含白兔+蜜蜂的真实视频跑完整
   端到端（Qwen 识别 → SAM Mask → 点击白兔/蜜蜂/空白 → QA 三问），并核对
   `mapped_frames` 与实际帧时间戳一致。
2. **前端实时 Mask 叠加**：按当前帧拉取 `/index?timestamp_sec=` 与各对象 PNG，
   在 Canvas 上以半透明色渲染，并突出选中对象；同时支持点击候选列表精确选择。
3. **修复构建可复现性**：为 Docker Desktop 配置可用镜像源/代理或把基础镜像固定为
   可离线获取的 digest，使 `docker compose up --build -d` 在本环境也能通过；
   随后补 Playwright 浏览器安装并跑通 e2e。


---

# 第二阶段（真实 AI 联调 + Mask 可视化）

## 0. 安全与基线
- 开工前完整检查：分支 `test/touchVideo`、无 commit、42 个未跟踪文件均登记哈希。
- 本地备份 `D:\Project\InteractiveBookTest_backup_20261121_155929`：
  41 文件 / 1,071,509 字节 / SHA256 逐文件校验 0 处不一致（含 `_BACKUP_MANIFEST.csv`）。
- 经用户明确确认后创建第一个本地 commit `9d70e1e`（38 文件，未 push，`.env` 未纳入）。
- `.env` 中两个 API Key 已由用户配置（内容从不输出）；`QWEN_BASE_URL` 为北京 MaaS
  专用兼容端点，已实测可用。

## 1. 真实 Qwen API（已验证通过）
- 单帧真实调用：9.79 s，识别白兔 / 提灯 / 森林小径，结构化 JSON 合法，bbox 0–1000
  正确转换为像素坐标（白兔 `[439,171,755,664]` / 1280×720）。
- 11 张关键帧全量真实探测（每帧约 9–11 s）：**蜜蜂从 frame 72 出现**，最清晰在
  frame 96（bbox `[623,266,782,373]`）；白兔/提灯全片可见。
- 发现逻辑缺陷：`observe()` 原来「先到先得」占满 3 个 track，导致后出现的蜜蜂被丢弃。
  已重写为两阶段（全帧收集 → 聚类 → 按 出现帧数×置信度 排序，排除背景类），
  修复后实体为 **白兔 / 提灯 / 发光蜜蜂**，蜜蜂 seed_frame=96。

## 2. 真实 Replicate SAM 2（已验证，含关键事实修正）
- 首次并行 3 对象分析：真实返回 **HTTP 429** 限流（白兔、森林小径 failed），
  提灯成功：240/240 帧映射、missing=0、162.65 s → 真实 Mask 生成/下载/索引成功。
- 已把 `SAM_CONCURRENCY=1` 并在 `ReplicateSAMProvider` 增加 429 有界退避重试；
  `error_code()` 对 429 给出明确说明（不伪造结果）。
- **seed_frame≠0 真实验证（本阶段最重要发现）**：以 `click_frames=72` 对 240 帧视频
  发起真实预测，返回 168 个文件 `frame_00000.png … frame_00167.png`。
  下载真实 PNG 与真实 Qwen bbox 计算 IoU：
  file0→视频帧72 = 0.981，file72→144 = 0.965，file96→168 = 0.973，file120→192 = 0.956。
  **结论：`video_frame = seed_frame + 文件序号`，输出只覆盖 `[seed, frame_count)`，
  不含 seed 之前的帧。第一阶段依据源码推断的「文件序号 == 绝对帧号」是错的，
  Codex 原始的 `seed + offset` 映射其实正确，现已按真实数据改回。**
  仅 seed=0 时两者等价。对应单测与 `expected` / `partial` 判定已同步修正。

## 3. 前端 MaskOverlay（代码完成）
- 新增 `frontend/src/MaskOverlay.vue`、`entityColors.ts`；`App.vue` 增加独立 mask canvas、
  颜色图例、选中高亮、空白点击清除选择、Mask 加载统计。
- 二值 PNG → 0 像素全透明、>0 像素半透明彩色；选中对象白色高亮 + bbox 虚线。
- `requestVideoFrameCallback` mediaTime → floor(t*fps) 帧号（与后端 displayed_frame 一致）；
  兼容播放/暂停/拖动/倍速/全屏/尺寸变化/无 Mask/结束。
- 有界 Map 缓存（120）、当前帧优先、预取相邻 2 帧、卸载释放；不重复请求同一 PNG。
- 后端 `/index?timestamp_sec=` 增加 `mask_url` / `bbox` 字段（向后兼容的增量字段）。
- `npm run build` 通过（74 modules）；点击仍由后端真实 Mask 像素判定。

## 4. 浏览器 E2E
- `@playwright/test` runner 在本机启动即挂起（无输出）；已验证 Playwright 核心 API
  直连系统 Chrome 157 完全正常（能启动、加载页面、读 h1）。
- 新增 `frontend/e2e/run-phase2.mjs` + npm script `test:e2e:core`：用核心 API 跑真实
  浏览器流程（打开 SPA、选视频、Mask PNG 校验、点击命中、空白 no_hit、真实 QA、
  Mask canvas 实际绘制像素检查、截图）。
- 首次运行因选中了上一阶段无 index 的合成测试视频而失败；已改为自动挑选
  `index.status ∈ {success, partial}` 的视频。待分析完成后复跑。

## 5. 实测性能（均来自真实运行）
- FFmpeg 预处理（10 s / 240 帧 HEVC→H.264）：2.88 s
- Qwen 单帧：9.79 s；11 张关键帧：9.16–10.68 s/帧
- Replicate SAM（240 帧、单对象）：162.65 s；seed=72 的 168 帧探测：145.12 s
- 首次整段分析（含 429 与并行）：284.35 s，最终 partial
- Mask 单张下载+校验：见 `maskLoadMs` 前端统计（E2E 输出）

## 6. 已知问题 / 下一步
1. 串行重新分析（白兔 seed=0 / 提灯 seed=72 / 蜜蜂 seed=96）耗时约 7–8 分钟，
   Replicate 若再次 429 会标记 partial；此时前端对缺帧自然不渲染，不伪造。
2. seed 之前的帧没有 Mask（真实模型行为），前端需明确提示而非视为错误。
3. Docker Hub 仍不可达，`docker compose up --build -d` 不能重建 API 镜像；
   当前用 `up -d --force-recreate api`（缓存镜像 + bind mount）运行新代码。
4. 下一步优先：① 用真实浏览器跑通 Mask 高亮点击 + 蜜蜂/白兔 QA；
   ② 若 429 频繁，加入对象级队列与断点续传；③ 修复镜像源使 `--build` 可复现。



## 7. 第二阶段最终结果（20 分钟时间盒内完成）

- 串行重新分析 **全部成功**：`ANALYZE_SECONDS=546.4`，`index=success version=2`。
  | 对象 | seed_frame | 映射帧数 | missing | SAM 耗时 |
  |---|---|---|---|---|
  | 白兔 | 0 | 240 | 0 | 221.70 s |
  | 提灯 | 72 | 168 | 0 | 120.31 s |
  | 发光蜜蜂 | 96 | 144 | 0 | 90.77 s |
- 跨时间点真实索引：t=0.1 白兔；t=3 提灯+白兔；t=5、t=9 三对象同时存在。
- 真实点击（后端 mask 像素判定）：白兔 `matched / mask_pixel_hit`、
  发光蜜蜂 `matched / mask_pixel_hit`（frame 120，bbox `[840,171,977,332]`）、
  空白 `no_hit` candidates=0；**点击识别耗时 11 ms**。
- 真实 Qwen QA：白兔「白兔。」「红色围巾。」；蜜蜂「它是一只蜜蜂，正在飞行并环绕白兔，
  周身散发金色光点轨迹。」证据帧 96/120/144。多轮会话保持同一 session_id。
- **真实浏览器 E2E `13/13` 全部通过**（`npm run test:e2e:core`）：
  SPA、双 Key 配置、库选择、index success、播放器+mask canvas、真实 Mask PNG、
  点击命中、空白 no_hit、真实 QA、多轮 QA、Mask canvas 42,263 个非透明像素、无 JS 错误。
  截图 `frontend/test-results/phase2-overlay.png` 可见白兔青绿色半透明轮廓。
- 后端 `12 passed`、前端 `4 passed`、`npm run build` 通过（74 modules）。
- 前端 Mask 单张加载 **16–26 ms**（来自 E2E/UI 实测 stat）。

### 严格分层结论
1. 代码实现完成：Qwen / SAM / MaskOverlay / 缓存 / 点击高亮 / E2E 脚本 ✔
2. 本地单测 + Mock 单测通过：后端 12（含显式 Mock SAM）、前端 4 ✔
3. 真实 Qwen API 测试通过：单帧 + 11 关键帧 + QA ✔
4. 真实 Replicate API 测试通过：3 对象独立预测全部 succeeded ✔
5. 真实 Mask 生成成功：3 对象共 552 帧 PNG 下载校验并写入索引 ✔
6. 浏览器 Mask 叠加成功：canvas 实际绘制 + 截图确认 ✔
7. 完整 E2E 成功：13/13（含点击白兔/蜜蜂/空白 + 多轮 QA）✔

### 遗留
- `@playwright/test` runner 在本机挂起（核心 API 正常），已用 `test:e2e:core` 替代。
- Docker Hub 不可达，`--build` 仍需镜像源/代理修复；当前用缓存镜像 + bind mount。
- Replicate 并发 429 已通过 `SAM_CONCURRENCY=1` + 退避重试规避；3 对象串行约 9 分钟。
- seed 之前的帧按真实模型行为没有 Mask（非缺陷）。



---

# 第三阶段（30 分钟时间盒：把处理时长压到约 50 s）

## 目标与诚实前提
用户要求：10s / 24fps 视频处理时长限制在 **约 50 s**，上传时长不计；
考虑复用与断点续传、视频分段并发、间隔抽帧等方案；到点如实汇报实际用时。

前提：Replicate SAM 2 单次云端预测实测 90–220 s，一次全新冷启动无法在本地压到 50 s。
因此 50 s 通过「内容哈希复用 + 断点续传」在重复/重跑场景达成，冷启动单独如实报数。

## 已实施
- `backend/app/main.py`：新增 `content_index.json`（SHA256 → video_id），
  lifespan 启动时从已有 `index=success` 的视频回填；上传命中已完成索引时
  直接返回原 video_id，不创建目录、不跑 FFmpeg、不调用任何 AI。
  返回体新增 `cached` / `message`，前端 `App.vue onUpload` 已适配。
- `backend/app/main.py begin()`：索引已 success 时复用同一 version，跳过 observe()；
  `POST /segment` 改为 `SegmentRequest {entity_id?, seed?}`，可只补一个对象。
- `backend/app/jobs.py`：
  - `observe()` 用 `ThreadPoolExecutor(QWEN_CONCURRENCY)` 并行调用 Qwen；
    `select_keys()` 按 `QWEN_MAX_KEYFRAMES` 均匀取样并保留首尾帧。
  - 新增 `masks_complete()`：prediction=success 且 expected 帧齐全才复用。
  - `analyze(..., entity_id=None)` 只把未完成对象送 SAM；已完成的直接计入 success。
- `backend/app/config.py` / `.env` / `.env.example`：新增
  `QWEN_CONCURRENCY=4`、`QWEN_MAX_KEYFRAMES=6`；`SAM_OUTPUT_INTERVAL` 保持 1。
- `backend/tests/test_speed.py`：4 个单测（抽帧、resume 判定、interval 期望集合）。
- `frontend/e2e/run-phase2.mjs`：改为优先挑选 `index.status=success/partial` 的视频，
  避免选中正在 running 的冷启动视频导致误报。

## 实测（真实运行）
| 场景 | 实测 |
|---|---|
| 重复上传 `test.mp4`（内容哈希命中） | 不含上传 **0.008 s**；含上传 HTTP **0.127 s** |
| 已完成索引上再 `POST /analyze` | **0.06 s / 0.17 s**（两次） |
| 单对象 `POST /segment` | **0.03 s** |
| 冷启动 Qwen 段（5–6 帧并行） | **约 20 s**（原 11 帧串行 ≈109 s） |
| 冷启动 FFmpeg | **2.57 s** |
| 冷启动 SAM | 逐对象仍需 90–220 s，串行 3 对象约数分钟（最终值见下方补记） |

回归：后端 **16 passed**、前端 build + **4 passed**、真实浏览器 E2E **13/13**，
真实 Mask / 点击白兔 `matched` / 空白 `no_hit` / QA 全部仍通过。

## 已知取舍
- `QWEN_MAX_KEYFRAMES=5` 的冷启动探测里蜜蜂未被选中（抽帧漏掉 frame 96）；
  已把默认改为 **6**（均匀取样 0/48/96/144/216/239，覆盖蜜蜂）。
  抽帧是有损优化，长视频/晚出现对象建议设为 0（全部关键帧）。
- `SAM_OUTPUT_INTERVAL>1` 的**真实输出编号语义尚未验证**（文件序号可能是
  `offset` 也可能是 `offset*interval`），因此保持 1，未启用，避免 Mask 错帧。
- 视频分段并发 SAM 需要重新推导帧映射与 seed 语义，本轮时间盒内未实施。



## 第三阶段最终实测（30 分钟时间盒到点）

**冷启动（全新、内容哈希未命中）**：
`_cold_probe.mp4`（与 test.mp4 同内容、尾部多 1 字节以绕过哈希）真实跑完：
- 上传 0.14 s（用户要求不计）
- FFmpeg 预处理 **2.57 s**
- Qwen 段（5 帧并行）**约 20 s**
- SAM 3 对象串行 **219.70 s**（白兔 seed=120 / 95.63 s，提灯 seed=192 / 49.41 s，
  灯笼 seed=120 / 74.66 s）
- **`COLD_ANALYZE_SECONDS = 246.46 s`，`index=success`，无 429**

即冷启动从基线 **546.4 s → 246.46 s（降低 55%）**，但**仍未达到 50 s**。
原因：Replicate SAM 2 云端推理本身占 219.7 s，本地无法压缩；
Qwen 并行 + 抽帧已把本地可优化部分（≈109 s）压到 ≈20 s。

**热路径（重复/重跑）**：0.008 s（不含上传）/ 0.127 s（含上传）/ 0.03–0.17 s，
**远低于 50 s 目标**。

诚实结论：50 s 目标**只在缓存命中与断点续传场景达成**；
「一次全新视频端到端 50 s」未达成，实测 246.46 s。未把两者混为一谈。

正确性副作用（如实记录）：`QWEN_MAX_KEYFRAMES=5` 时 6 帧取样漏掉 frame 96，
实体变成 白兔/提灯/灯笼（无蜜蜂），且 seed 变为 120/192/120，
SAM 只覆盖 `[seed, 240)`。已把默认改为 6（0/48/96/144/216/239，覆盖蜜蜂），
但该修正尚未重新跑一次完整冷启动验证（时间盒到点）。

