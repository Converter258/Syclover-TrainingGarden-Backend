# Syclover Training Garden Backend

当前版本：**Alpha0.0.4-hotfix.1**。每个账号最多同时运行 2 个题目环境；停止或过期后可继续启动。

版本：**Alpha0.0.3-hotfix.2**

FastAPI + SQLite 实现的训练平台 API，包含认证与权限、独立 CTF/AWDP 题库、ZIP 即时镜像构建、Docker 实例、Flag 计分、攻防独立血榜、Markdown Hints、附件、AWDP Check/Fix/Patch 工作流、后台实例回收和独立排行榜。

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
export SYCL_DOCKER_MODE=mock
export SYCL_ADMIN_PASSWORD='ChangeMe123!'
uvicorn app.main:app --reload
```

API 默认监听 `http://localhost:8000`，OpenAPI 文档位于 `/docs`。实际容器训练请使用 `SYCL_DOCKER_MODE=cli`，并确保运行用户有权访问 Docker；容器化部署时镜像只安装 Docker 客户端（`docker-cli`），通过 `DOCKER_HOST=unix:///var/run/docker.sock` 访问挂载进来的守护进程 Socket。

构建 ZIP 默认上限为 128 MiB，由 `SYCL_MAX_BUILD_UPLOAD_BYTES` 控制；普通附件上限由 `SYCL_MAX_UPLOAD_BYTES` 控制。构建包必须只包含一个 `Dockerfile`，构建成功后会将生成的镜像与题目绑定。

题目容器的发布端口由 `SYCL_INSTANCE_BIND_ADDRESS`（默认 `127.0.0.1`，仅接受字面 IP）决定，可用 `SYCL_INSTANCE_PORT_RANGE_START` / `SYCL_INSTANCE_PORT_RANGE_END` 限定宿主机端口范围。启动时 `app/services/reaper.py` 会运行后台回收任务：按 TTL 停止过期实例，并清理数据库中已无活动记录的题目容器，`SYCL_INSTANCE_REAPER_ENABLED` 与 `SYCL_INSTANCE_REAPER_INTERVAL_SECONDS` 控制其行为。

Flag 采用模板化注入（`app/services/flags.py`）：`SYC{<RANDOM>}` 会为每个实例生成独立密钥，并通过 `FLAG` 环境变量注入容器；不符合 `PREFIX{...}` 格式的 Flag 会按 `SYCL_FLAG_PREFIX` 重写为随机模板。提交时同时比对实例密钥与题目摘要，实例密钥仅对归属者可见。

运行测试：

```bash
pytest -q
```

所有配置项及示例见 `.env.example`。
