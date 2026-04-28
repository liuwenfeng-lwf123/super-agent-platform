# Runtime Backends

## 概览

当前执行层已经通过通用 `RuntimeBackend` 协议和 `RuntimeManager` 解耦为统一入口。

| 组件 | 作用 |
|---|---|
| `RuntimeBackend` | 统一约束执行、工作区、输出、文件历史、shadow workspace 能力 |
| `SandboxDelegatingRuntimeBackend` | 复用现有 `SandboxExecutor` 文件/工作区能力的委托基类 |
| `RuntimeManager` | 注册 backend、维护 thread 绑定、统一路由执行与工作区操作 |
| `LocalRuntimeBackend` | 默认本地 backend，直接委托 `SandboxExecutor` |
| `DockerRuntimeBackend` | 通过 Docker CLI 提供容器执行 |
| `SSHRuntimeBackend` | 通过 SSH CLI 提供远端执行 |

线程与 backend 的绑定关系持久化在：`backend/data/thread_runtime_backends.json`

## 目录约定

每个线程的本地工作目录位于：`backend/data/workspaces/{thread_id}`

| 子目录 / 文件 | 说明 |
|---|---|
| `workspace/` | 代码执行与文件读写的主工作区 |
| `outputs/` | 生成物输出目录 |
| `uploads/` | 上传文件目录 |
| `claude_env.sh` | Bash 环境桥接文件 |

## Backend 能力矩阵

| Backend | 类型 | 执行语言 | 工作区 | 文件历史 | Shadow Workspace | 备注 |
|---|---|---|---|---|---|---|
| `local` | `builtin` | python / javascript / bash | 支持 | 支持 | 支持 | 默认 backend |
| `docker` | `container` | python / javascript / bash | 支持 | 支持 | 支持 | 通过 Docker CLI 运行 |
| `ssh` | `remote` | python / javascript / bash | 支持 | 支持 | 支持 | 通过 SSH CLI + `rsync` 增量同步，失败回退 `tar` |

## Runtime API

Base URL: `http://localhost:8001/api`

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/runtimes` | 列出所有 runtime backend 及健康信息 |
| `POST` | `/runtimes/{backend_name}/prewarm` | 触发 backend 预热；当前仅 `docker` 支持 |
| `GET` | `/threads/{thread_id}/runtime` | 查看线程当前绑定的 runtime |
| `POST` | `/threads/{thread_id}/runtime` | 绑定线程 runtime，Body: `{ "backend": "docker" }` |
| `DELETE` | `/threads/{thread_id}/runtime` | 清除线程 runtime 绑定并回退到默认 backend |

## 执行语义

### Local

- 直接委托 `SandboxExecutor`
- 复用现有工作区、文件历史、shadow workspace、Speculation 能力
- macOS 下可通过 `sandbox-exec` 做 OS 级隔离

### Docker

- 通过 `docker run --rm -i` 执行
- 将线程根目录挂载到容器内 `/thread`
- 容器工作目录固定映射到 `/thread/workspace`
- Bash 环境桥接文件位于：`/thread/claude_env.sh`
- 支持持久 `cwd`
- 支持健康检查、镜像本地可见性检查、镜像预热

### SSH

- 通过 `ssh` CLI 执行远端命令
- 本地线程目录优先通过 `rsync` 增量同步，失败时回退到 `tar` 全量同步
- 远端线程目录位于：`{SSH_RUNTIME_REMOTE_BASE_DIR}/{thread_id}`
- 远端工作目录位于：`{SSH_RUNTIME_REMOTE_BASE_DIR}/{thread_id}/workspace`
- 远端 Bash 环境桥接文件位于：`{SSH_RUNTIME_REMOTE_BASE_DIR}/{thread_id}/claude_env.sh`
- 支持持久 `cwd`
- 支持远端能力探测：`python3` / `node` / `bash` / `tar` / `rsync`
- `GET /api/runtimes` 中会暴露当前有效同步策略与本地/远端 `rsync` 可用性

## Docker 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `DOCKER_RUNTIME_PYTHON_IMAGE` | `python:3.11-slim` | Python 执行镜像 |
| `DOCKER_RUNTIME_NODE_IMAGE` | `node:20-bookworm-slim` | JavaScript 执行镜像 |
| `DOCKER_RUNTIME_BASH_IMAGE` | `python:3.11-slim` | Bash 执行镜像 |
| `DOCKER_RUNTIME_NETWORK` | `bridge` | Docker 网络模式 |
| `DOCKER_RUNTIME_MEMORY` | `1g` | 容器内存限制 |
| `DOCKER_RUNTIME_CPUS` | `1.0` | CPU 配额 |
| `DOCKER_RUNTIME_PIDS_LIMIT` | `256` | PID 限制 |
| `DOCKER_RUNTIME_READ_ONLY_ROOT` | `true` | 是否启用只读根文件系统 |
| `DOCKER_RUNTIME_TMPFS_SIZE_MB` | `256` | `/tmp` tmpfs 大小 |
| `DOCKER_RUNTIME_CAP_DROP_ALL` | `true` | 是否丢弃全部 Linux capabilities |
| `DOCKER_RUNTIME_NO_NEW_PRIVILEGES` | `true` | 是否启用 `no-new-privileges` |
| `DOCKER_RUNTIME_HEALTH_CACHE_TTL_SECONDS` | `10` | 健康检查缓存 TTL |
| `DOCKER_RUNTIME_PREWARM_TIMEOUT_SECONDS` | `900` | `docker pull` 超时时间 |

## SSH 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `SSH_RUNTIME_HOST` | 空 | 远端主机；未配置时 backend 不可用 |
| `SSH_RUNTIME_USER` | 空 | SSH 用户 |
| `SSH_RUNTIME_PORT` | `22` | SSH 端口 |
| `SSH_RUNTIME_IDENTITY_FILE` | 空 | 私钥文件 |
| `SSH_RUNTIME_REMOTE_BASE_DIR` | `~/hermes-runtime` | 远端线程根目录 |
| `SSH_RUNTIME_CONNECT_TIMEOUT_SECONDS` | `5` | SSH 连接超时 |
| `SSH_RUNTIME_STRICT_HOST_KEY_CHECKING` | `accept-new` | Host key 检查策略 |
| `SSH_RUNTIME_HEALTH_CACHE_TTL_SECONDS` | `10` | 健康检查缓存 TTL |

## Docker 预热

```bash
curl -X POST http://localhost:8001/api/runtimes/docker/prewarm
```

返回字段说明：

| 字段 | 说明 |
|---|---|
| `success` | 是否全部完成 |
| `status` | `ready` / `partial` / `failed` / `unavailable` |
| `pulled_images` | 本次拉取成功的镜像 |
| `pull_errors` | 拉取失败明细 |
| `images_local` | 当前镜像本地存在情况 |
| `missing_roles` | 仍然缺失的角色 |

## 健康检查

`GET /api/runtimes` 返回的各 backend 描述中包含 `health` 字段。

### Docker health

| 字段 | 含义 |
|---|---|
| `cli_available` | 本机是否存在 Docker CLI |
| `daemon_available` | Docker daemon 是否可达 |
| `server_version` | Docker 服务端版本 |
| `images_local` | python/javascript/bash 镜像是否在本地 |

### SSH health

| 字段 | 含义 |
|---|---|
| `cli_available` | 本机是否存在 SSH CLI |
| `host_configured` | 是否配置了远端 host |
| `target` | 实际连接目标 |
| `connection_available` | SSH 连通性 |
| `remote_capabilities` | 远端 `python` / `javascript` / `bash` / `tar` / `rsync` 能力 |

### SSH sync

| 字段 | 含义 |
|---|---|
| `strategy` | 当前有效同步策略：`rsync` 或 `tar` |
| `local_rsync_available` | 本机是否存在 `rsync` CLI |
| `remote_rsync_available` | 远端是否存在 `rsync` |

## 推荐运维流程

### 启用 Docker backend

1. 确认本机有 `docker` CLI
2. 确认 Docker daemon 正常运行
3. 通过 `/api/runtimes` 检查 `docker.health`
4. 调用 `/api/runtimes/docker/prewarm` 预热镜像
5. 将目标线程绑定到 `docker`

### 启用 SSH backend

1. 配置 `SSH_RUNTIME_HOST`
2. 如需指定用户、端口、私钥，再补充 `SSH_RUNTIME_USER`、`SSH_RUNTIME_PORT`、`SSH_RUNTIME_IDENTITY_FILE`
3. 通过 `/api/runtimes` 检查 `ssh.health`
4. 确认 `remote_capabilities.python/bash/tar` 为 `true`
5. 如希望启用增量同步，再确认 `sync.local_rsync_available=true` 且 `sync.remote_rsync_available=true`
6. 将目标线程绑定到 `ssh`

## 常见故障排查

| 现象 | 排查点 |
|---|---|
| `Docker CLI not available` | 本机是否安装 Docker CLI，`docker` 是否在 PATH 中 |
| `Docker daemon unavailable` | Docker Desktop / daemon 是否启动，当前用户是否有权限 |
| `SSH runtime host not configured` | 是否设置 `SSH_RUNTIME_HOST` |
| `SSH connection unavailable` | `ssh -o BatchMode=yes <target>` 是否能无交互连通 |
| `remote_capabilities.tar=false` | 远端缺少 `tar`，SSH backend 无法同步工作区 |
| `remote_capabilities.rsync=false` | 远端缺少 `rsync`，会自动回退到 `tar` |
| `sync.strategy=tar` | 检查本机 `rsync` 是否安装、远端 `rsync` 是否存在、以及最近一次 `rsync` stderr |
| SSH 执行成功但文件未回传 | 检查远端目录权限、远端 `tar` 可用性、同步阶段 stderr |

## 测试

### 严格回归

```bash
python -W error -m pytest tests/test_runtime_backends.py tests/test_runtime_e2e.py tests/test_channels.py tests/test_trajectory.py -q
python -W error -m pytest tests/test_runtime_backends.py tests/test_acp_lite.py tests/test_api_integration.py tests/test_telegram_transport.py -q
python -W error -m pytest -q
```

### SSH 真 E2E（条件触发）

`tests/test_runtime_backends.py` 中的 `TestSSHRuntimeBackendE2E` 默认尝试 loopback SSH。

可选测试环境变量：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `SSH_RUNTIME_E2E_HOST` | `localhost` | 测试目标主机 |
| `SSH_RUNTIME_E2E_USER` | 当前系统用户 | 测试用户 |
| `SSH_RUNTIME_E2E_PORT` | `22` | 测试端口 |
| `SSH_RUNTIME_E2E_IDENTITY_FILE` | 空 | 测试私钥 |
| `SSH_RUNTIME_E2E_STRICT_HOST_KEY_CHECKING` | `accept-new` | host key 策略 |

如果当前机器未启用无密码 loopback SSH，测试会自动 `skip`，不会影响默认回归。
