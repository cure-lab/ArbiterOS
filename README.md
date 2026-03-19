## ArbiterOS + Langfuse 一键启动（Docker Compose）

这个目录把几个服务编排成一个 `docker compose up`：

- **Langfuse UI**: `http://localhost:3000`
- **ArbiterOS Kernel (LiteLLM proxy)**: `http://localhost:4000`
- 其它基础服务：Postgres / Redis / ClickHouse / MinIO

你可以：

- 作为**普通用户**：直接拉取作者构建好的 Docker 镜像，一条命令启动；
- 作为**开发者**：本地修改 `ArbiterOS-Kernel` / `langfuse` 源码，自己 build 镜像再跑。

---

### 0. 拉取代码

如果本仓库使用了 Git submodule（例如 `ArbiterOS-Kernel/`、`langfuse/`），推荐用下面的方式拉取：

- **第一次 clone**：

```bash
git clone --recurse-submodules <your-repo-url>
```

- **已经 clone 了但忘了带 submodules**：

```bash
git submodule update --init --recursive
```

---

### 1. 准备环境变量文件 `stack.env`

在仓库根目录执行：

```bash
cp stack.env.example stack.env
```

以下部分，手动设置值和自动随机设置值二选一：

然后编辑 `stack.env`，至少把这些值改成你自己的：

- **Langfuse 项目密钥**：
  - `LANGFUSE_INIT_PROJECT_PUBLIC_KEY`
  - `LANGFUSE_INIT_PROJECT_SECRET_KEY`
- **强烈建议修改的密码/密钥**：
  - `POSTGRES_PASSWORD`
  - `REDIS_AUTH`
  - `MINIO_ROOT_PASSWORD`
  - `NEXTAUTH_SECRET`
  - `SALT`
  - `ENCRYPTION_KEY`
- **Langfuse 初始账号（可选，但推荐）**：
  - `LANGFUSE_INIT_USER_EMAIL`
  - `LANGFUSE_INIT_USER_NAME`
  - `LANGFUSE_INIT_USER_PASSWORD`（默认已固定为 `ArbiterOS`，你可以自行修改）

或者（Windows / PowerShell）一条命令自动生成随机安全值并写入 `stack.env`：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\generate-stack-env.ps1 -OutFile stack.env
```

或者（Ubuntu / Linux）一条命令自动生成随机安全值并写入 `stack.env`：

```bash
chmod +x ./scripts/generate-stack-env.sh
./scripts/generate-stack-env.sh -o stack.env --email "you@example.com" --name "You"
```

> 脚本会在第一次生成时随机出一套安全密码，**再次运行时会复用已有的 Postgres 密码**，避免因为密码变化导致必须删除数据卷。

---

### 2. 启动方式一：普通用户，直接拉取 Docker 镜像

默认情况下，`compose.yaml` 会拉取作者在 Docker Hub 上构建好的镜像：

- Langfuse：
  - `docker.io/xywen22/arbiteros-langfuse-web:latest`
  - `docker.io/xywen22/arbiteros-langfuse-worker:latest`
- ArbiterOS Kernel：
  - `docker.io/xywen22/arbiteros-kernel:latest`

如果你希望改用其他仓库中的 Langfuse 镜像，可以在 `stack.env` 里覆盖镜像名（或在 shell 中 `export`）：

```env
LANGFUSE_WEB_IMAGE=docker.io/<your_dockerhub>/arbiteros-langfuse-web:latest
LANGFUSE_WORKER_IMAGE=docker.io/<your_dockerhub>/arbiteros-langfuse-worker:latest
```

然后在仓库根目录执行：

```bash
docker compose --env-file stack.env up -d
```

如果你更习惯 `.env`，也可以：

```bash
cp stack.env .env
docker compose up -d
```

> **重要**：推荐始终使用 `--env-file stack.env`（或把内容复制为 `.env`）。  
> 因为 Docker Compose 的变量替换发生在容器启动前，如果你只写 `docker compose up -d` 而没有 `.env`，  
> `compose.yaml` 中形如 `${LANGFUSE_INIT_USER_EMAIL:-}` 的变量可能会被替换为空，从而导致  
> Langfuse 初始化账号/项目不生效（表现为 Sign in 一直 Invalid credentials / 没有自动创建用户）。

---

### 3. 启动方式二：开发者，从源码本地编译

#### 3.1 只本地开发 / 修改 Langfuse

使用 `compose.dev.langfuse.yaml` 覆盖为本地 build（构建 `langfuse/` 目录中的源码）：

```bash
docker compose --env-file stack.env \
  -f compose.yaml \
  -f compose.dev.langfuse.yaml \
  up -d --build
```

#### 3.2 只本地开发 / 修改 ArbiterOS Kernel

使用 `compose.dev.yaml` 覆盖为本地 build（构建 `ArbiterOS-Kernel/` 中的 Dockerfile）：

```bash
docker compose --env-file stack.env \
  -f compose.yaml \
  -f compose.dev.yaml \
  up -d --build
```

#### 3.3 同时本地编译 Langfuse + Kernel

```bash
docker compose --env-file stack.env \
  -f compose.yaml \
  -f compose.dev.langfuse.yaml \
  -f compose.dev.yaml \
  up -d --build
```

---

### 4. 配置 ArbiterOS Kernel 模型（`litellm_config.yaml`）

ArbiterOS Kernel 实际上是一个基于 LiteLLM 的代理，模型列表和 API Key 全部由 `ArbiterOS-Kernel/litellm_config.yaml` 决定。

在启动 Docker 之前，请根据你自己的上游供应商（OpenAI / DeepSeek / Qwen 等）修改该文件中的：

- **`model_list` 中的 `model_name`**：这是对外暴露给 OpenClaw / 客户端使用的名字，例如：
  - `gpt-5.2`
  - `gpt-4o-mini`
- **对应的 `litellm_params`**：包括：
  - `model`（上游真实模型名）
  - `api_base` / `api_key` 等

你后面在 OpenClaw 中引用的模型 ID（例如 `arbiteros/gpt-5.2`）需要和这里的 `model_name` 保持一致。

如果你**已经启动了 Docker**，也可以改完再让配置生效：

```bash
docker compose --env-file stack.env down
# 修改 ArbiterOS-Kernel/litellm_config.yaml
docker compose --env-file stack.env up -d
```

---

### 5. 安装并初始化 OpenClaw

1. **安装 OpenClaw CLI**（参考 OpenClaw 官方文档，这里简要示例）：

   ```bash
   curl -fsSL https://openclaw.ai/install.sh | bash -s -- --no-onboard   # 或使用你习惯的安装方式
   ```

2. **运行 onboard 向导**（首次使用）：

   ```bash
   openclaw onboard
   ```

   这一步会在你的主目录下生成 `~/.openclaw/openclaw.json`，并完成基础配置。

3. **启动 OpenClaw gateway**（如果尚未启动）：

   ```bash
   openclaw gateway start
   ```

   默认会在 `127.0.0.1:18789` 监听。

---

### 6. 配置 OpenClaw 使用 ArbiterOS 作为默认模型

onboard 完成后，编辑 `~/.openclaw/openclaw.json`，将其中的 `models.providers` 和 `agents.defaults` 替换为类似下面的配置（请根据你自己的路径和模型名调整）：

```json
"models": {
  "providers": {
    "arbiteros": {
      "baseUrl": "http://127.0.0.1:4000/v1",
      "apiKey": "sk-xxxx",                // 这里可以填任意非空字符串，ArbiterOS 会忽略实际值
      "api": "openai-completions",
      "authHeader": false,
      "models": [
        {
          "id": "gpt-5.2",                // 与 litellm_config.yaml 中的 model_name 保持一致
          "name": "GPT-5.2",
          "reasoning": false,
          "input": ["text"],
          "cost": {
            "input": 0,
            "output": 0,
            "cacheRead": 0,
            "cacheWrite": 0
          },
          "contextWindow": 200000,
          "maxTokens": 8192,
          "compat": {
            "supportsStore": false
          }
        }
      ]
    }
  }
},
"agents": {
  "defaults": {
    "model": {
      "primary": "arbiteros/gpt-5.2"      // 默认使用的模型，前缀为 provider 名
    },
    "models": {
      "arbiteros/gpt-5.2": {
        "alias": "gpt"
      },
      "qwen-portal/coder-model": {
        "alias": "qwen"
      },
      "qwen-portal/vision-model": {},
      "openai/gpt-4": {},
      "openai/gpt-5": {},
      "qwen-portal/qwen-plus": {}
    },
    "workspace": "/home/<your-username>/.openclaw/workspace",
    "compaction": {
      "mode": "safeguard"
    },
    "maxConcurrent": 4,
    "subagents": {
      "maxConcurrent": 8
    }
  }
}
```

请特别注意：

- **`models.providers.arbiteros.models[0].id` 与 `agents.defaults.model.primary`、`agents.defaults.models` 中的 key 要与 `litellm_config.yaml` 中的 `model_name` 保持一致**（例如都叫 `gpt-5.2`）。
- `"workspace"` 建议写成绝对路径，例如：`"/home/<your-username>/.openclaw/workspace"`，避免在不同用户 / 进程下 `~` 展开成不同目录。

保存后，重启 gateway（如果需要）：

```bash
openclaw gateway stop || true
openclaw gateway start &
```

---

### 7. 打开控制台 / Langfuse 后台

- **OpenClaw Control UI（网关控制台）**：
  - 浏览器访问：`http://127.0.0.1:18789/chat?session=main`
  - 首次进入需要填写 **gateway token**：可以在 `~/.openclaw/openclaw.json` 的 `"gateway.auth.token"` 字段中找到，例如：

    ```json
    "gateway": {
      "port": 18789,
      "mode": "local",
      "bind": "loopback",
      "auth": {
        "mode": "token",
        "token": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
      }
    }
    ```

- **Langfuse 后台**：
  - 浏览器访问：`http://localhost:3000`
  - 这里可以查看请求日志、trace、分布情况，对整个 ArbiterOS + OpenClaw 的 agent 系统进行监控和分析。

- **ArbiterOS Kernel（代理接口）**：
  - 对外兼容 OpenAI API：`http://127.0.0.1:4000/v1`
  - 在 OpenClaw 配置中的 `baseUrl` 就是指向这里。

---

### 8. 常见问题（排错）

- **我想从零重新初始化（清空所有数据）**：
  - 这会删除 Postgres / MinIO / ClickHouse 等数据卷（不可恢复），适合你要重复测试 `LANGFUSE_INIT_*` 初始化逻辑时使用：

  ```bash
  docker compose --env-file stack.env down --volumes --remove-orphans
  docker compose --env-file stack.env up -d
  ```

- **Langfuse 登录失败（Invalid credentials）**：
  - 确认你是用 `docker compose --env-file stack.env up -d` 启动的；
  - 如果你想让 `LANGFUSE_INIT_*` 重新初始化（会清空数据卷）：`docker compose down -v` 后再 `up`。

- **宿主机浏览器访问地址**：
  - Langfuse：`http://localhost:3000`
  - ArbiterOS proxy：`http://localhost:4000`
  - `http://langfuse-web:3000` 只在 Docker 网络内部可访问（容器之间）。

