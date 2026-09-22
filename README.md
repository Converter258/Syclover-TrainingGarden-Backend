# Syclover Training Garden Backend

当前版本：**Alpha0.0.6-hotfix.2**。每个账号最多同时运行 2 个题目环境；停止或过期后可继续启动。

版本：**Alpha0.0.6-hotfix.2**

FastAPI + SQLite 实现的训练平台 API，包含认证与权限、独立 CTF/AWDP 题库、ZIP 即时镜像构建、Docker 实例、Flag 计分、攻防独立血榜、Markdown Hints、附件、AWDP Check/Fix/Patch 工作流、后台实例回收和独立排行榜。

初始化不会创建演示题目，生产题库需要由管理员自行创建。根管理员用户名固定为 `Syclover`，密码由 `SYCL_ADMIN_PASSWORD` 首次初始化时设置；普通管理员只能管理题目、题目内容和题目标签。Alpha0.0.6 还提供个人资料、方向、成就徽章和密码修改接口。

## 题目端口范围（Alpha0.0.6-hotfix.2）

`SYCL_INSTANCE_PORT_RANGE_START` / `SYCL_INSTANCE_PORT_RANGE_END` 定义题目容器的宿主机端口窗口，平台会**从这个窗口里分配**端口：

- 未配置范围时保持原行为：`docker run -p <绑定地址>::<容器端口>`，由 Docker 从宿主机临时端口范围（`net.ipv4.ip_local_port_range`，通常 `32768-60999`）里挑一个。
- 配置范围后，平台自己挑选窗口内的空闲端口并显式下发 `-p <绑定地址>:<宿主机端口>:<容器端口>`。因此只开放 `30000-30079` 这样的 80 个端口也能正常训练，不需要放开整个临时端口段。
- 候选端口顺序是随机轮转的，避免并发启动都抢同一个端口；如果该端口在此期间被别人占用（Docker 返回 `port is already allocated`），会自动顺延到窗口内的下一个端口，并把上一次尝试留下的半成品容器清理掉。
- 只有端口冲突才会重试；镜像缺失、容器启动即退出等真实故障立即失败，不会被掩盖。
- 窗口内没有空闲端口时返回明确错误：`No free host port in the configured range <起>-<止>: all N port(s) are in use. Widen SYCL_INSTANCE_PORT_RANGE_START/END or wait for instances to expire.` 同步启动返回 `503`，异步启动会写入实例的 `error_message`。
- 窗口大小就是同时在线实例数的上限（每账号最多 2 个），按「并发选手数 × 2」估算。
- 平台只发布 TCP；配置了范围后原来的「发布端口不在范围内」校验仍然保留作为兜底。

## 邀请码注册（Alpha0.0.6-hotfix.1）

`POST /api/v1/auth/register` 现在要求 `invite_code` 字段，注册接口不再是公开入口：

```json
{"username": "newbie", "password": "PlayerPass123!", "invite_code": "SYC-2S8E-YTX3-U7A5"}
```

| 情况 | 响应 |
| --- | --- |
| 缺少 `invite_code` | `422` |
| 邀请码不存在、格式不合法或已被作废 | `400` |
| 邀请码已被使用 | `409` |
| 用户名已被占用 | `409`（邀请码不会被消耗） |

邀请码接口（`app/api/routes/invites.py`，仅 `admin` / `root_admin` 可访问，选手返回 `403`）：

| 方法与路径 | 说明 |
| --- | --- |
| `POST /api/v1/invites` | 生成邀请码，`{"count": 1-50, "note": "可选备注"}`，返回含明文邀请码的列表 |
| `GET /api/v1/invites?state=all\|unused\|used` | 列出邀请码，包含生成者、使用者与时间 |
| `DELETE /api/v1/invites/{id}` | 作废未使用的邀请码；已使用的邀请码返回 `409` 并保留作为审计 |

实现要点（`app/services/invites.py`）：

- 邀请码形如 `SYC-XXXX-XXXX-XXXX`，正文 12 位取自剔除易混字符的 31 字符表（约 59 bit 熵），使用 `secrets` 生成。
- 输入会归一化：忽略大小写、连字符，并允许省略 `SYC` 前缀。
- 单次使用由事务内的条件更新保证：`UPDATE invite_codes SET used_by = ?, used_at = ? WHERE id = ? AND used_at IS NULL`。查找、建号与占用在同一事务中完成，并发或重复注册只会有一次成功。
- 是否已使用以 `used_at` 判定，而不是 `used_by`；因此删除账号（外键置空）不会让邀请码重新可用。
- `invite_codes` 表由 `Database.initialize()` 自动创建，升级既有数据库无需手工迁移。

AWDP 防御选手上传 `patch.zip`：Pwn 包必须包含根目录 `fix.sh` 和一个修改后的二进制，Web 包必须包含根目录 `fix.sh` 和至少一个源码或资源文件。部署时平台将内容覆盖到容器 `/app` 并执行 `fix.sh`。

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

题目容器的发布端口由 `SYCL_INSTANCE_BIND_ADDRESS`（默认 `127.0.0.1`，仅接受字面 IP）决定，`SYCL_INSTANCE_PORT_RANGE_START` / `SYCL_INSTANCE_PORT_RANGE_END` 则给出平台分配宿主机端口的窗口（见上文）。启动时 `app/services/reaper.py` 会运行后台回收任务：按 TTL 停止过期实例，并清理数据库中已无活动记录的题目容器，`SYCL_INSTANCE_REAPER_ENABLED` 与 `SYCL_INSTANCE_REAPER_INTERVAL_SECONDS` 控制其行为。

Flag 采用模板化注入（`app/services/flags.py`）：`SYC{<RANDOM>}` 会为每个实例生成独立密钥，并通过 `FLAG` 环境变量注入容器；不符合 `PREFIX{...}` 格式的 Flag 会按 `SYCL_FLAG_PREFIX` 重写为随机模板。提交时同时比对实例密钥与题目摘要，实例密钥仅对归属者可见。

运行测试：

```bash
pytest -q
```

当前测试集 90 项：`tests/test_invites.py` 覆盖邀请制注册、一码一用、格式归一化、权限隔离、作废规则，以及账号删除后邀请码不会被回收；`tests/test_port_range.py` 覆盖端口窗口内的分配、占用顺延、窗口耗尽、单端口固定、未配置范围时保持 Docker 自选，以及非端口类故障不重试。

所有配置项及示例见 `.env.example`。
