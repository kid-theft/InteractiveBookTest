# 开发交接记录 / DEV_HANDOFF

更新时间：2026-11-21
当前分支：`test/touchVideo`（**仓库无任何 commit，全部文件均为未跟踪状态**）

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
7. 全部文件仍未纳入 Git（无 commit）；未做任何提交 / 推送 / 分支切换 / 清理。

## 6. 下一步最值得做的 3 件事

1. **配置 `DASHSCOPE_API_KEY` 与 `REPLICATE_API_TOKEN`**，用含白兔+蜜蜂的真实视频跑完整
   端到端（Qwen 识别 → SAM Mask → 点击白兔/蜜蜂/空白 → QA 三问），并核对
   `mapped_frames` 与实际帧时间戳一致。
2. **前端实时 Mask 叠加**：按当前帧拉取 `/index?timestamp_sec=` 与各对象 PNG，
   在 Canvas 上以半透明色渲染，并突出选中对象；同时支持点击候选列表精确选择。
3. **修复构建可复现性**：为 Docker Desktop 配置可用镜像源/代理或把基础镜像固定为
   可离线获取的 digest，使 `docker compose up --build -d` 在本环境也能通过；
   随后补 Playwright 浏览器安装并跑通 e2e。
