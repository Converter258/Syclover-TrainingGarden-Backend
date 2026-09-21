# Syclover Training Garden Backend

版本：**Alpha0.0.1**

FastAPI + SQLite 实现的训练平台 API，包含认证与权限、独立 CTF/AWDP 题库、Docker 实例、Flag 计分、附件、AWDP Check/Fix/Patch 工作流和独立排行榜。

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
export SYCL_DOCKER_MODE=mock
export SYCL_ADMIN_PASSWORD='ChangeMe123!'
uvicorn app.main:app --reload
```

API 默认监听 `http://localhost:8000`，OpenAPI 文档位于 `/docs`。实际容器训练请使用 `SYCL_DOCKER_MODE=cli`，并确保运行用户有权访问 Docker。

运行测试：

```bash
pytest -q
```

所有配置项及示例见 `.env.example`。
