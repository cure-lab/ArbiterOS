## Complete Installation and Execution Setup

### 1. Installation

#### 1.1 OpenClaw

Install OpenClaw
```bash
# This may cost arround 5 minits for installation
curl -fsSL https://openclaw.ai/install.sh | bash
# and then it will directly start setup
```

After the setup, you will find the **.openclaw** folder in your workspace.

Setup the configuration file
```bash
nano ~/.openclaw/openclaw.json
```

Here is the example setting for openclaw.json
```json
"models": {
    "providers": {
      "arbiteros": {
        "baseUrl": "http://127.0.0.1:4000/v1",
        "apiKey": "sk-zk...",
        "api": "openai-completions",
        "authHeader": false,
        "models": [
          {
            "id": "gpt-5.2",
            "name": "GPT-5.2",
            "reasoning": false,
            "input": [
              "text"
            ],
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
        "primary": "arbiteros/gpt-5.2"
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
      "workspace": "/root/.openclaw/workspace",
      "compaction": {
        "mode": "safeguard"
      },
      "maxConcurrent": 4,
      "subagents": {
        "maxConcurrent": 8
      }
    }
},
```

NOTE: You should check the real workspace path in the configuration file, or the modifications of the models and agents will not take effect.
```json
"workspace": "/root" or "/home/admin/..." or "/home/user/...",
```

Then run the onboarding wizard:
```bash
openclaw onboard
```

When completing the onboarding wizard, you should:

"Set Model/auth provider to **Skip for now** and **Filter models by provider** to arbiteros (or whatever provider name you used in openclaw.json)."

OpenClaw is running.

#### 1.2 ArbiterOS

0. Download ArbiterOS:
```bash
git clone -b trace-vis https://github.com/DavidChen-PKU/ArbiterOS-Kernel.git
```

1. **Requirements**: Python 3.12+, uv.
```bash
# Enter the project
cd ArbiterOS-Kernel

# Install dependencies (creates .venv and installs poe task runner)
uv sync --group dev
```

2. **Set Config**: Edit `litellm_config.yaml` to add your models. Each entry under `model_list` should specify:

- **`model_name`**: ID exposed to clients (used in OpenClaw as `models[].id`)
- **`litellm_params.model`**: LiteLLM format, e.g. `openai/gpt-5.2`
- **`litellm_params.api_key`**: Your API key for the upstream provider
- **`litellm_params.api_base`**:  API base URL

Proxy URL: [http://localhost:4000](http://localhost:4000). Send client requests there to use this proxy with the logging and kernel above.

3. **Config Langfuse vars**: 

```bash
cd ArbiterOS-Kernel
cp .env.example .env
```

Edit `.env` with real keys. `arbiteros_kernel.litellm_callback` and `arbiteros_kernel.langfuse_replay` both auto-load `.env` (so you don’t need to `export` manually), but exporting works too:

```bash
export LANGFUSE_PUBLIC_KEY="pk-lf-..."
export LANGFUSE_SECRET_KEY="sk-lf-..."
export LANGFUSE_BASE_URL="http://localhost:3000"
# export LANGFUSE_HOST="http://localhost:3000" # Backward-compatible alias
```

You can find the langfuse public and secret keys in next step.

#### 1.3 Langfuse

**Requirements:**
```
node 24
you have installed docker
```

1. Download:
```bash
git clone -b dev https://github.com/ChangranXU/langfuse.git
```

2. Makersure you have the permission to access docker:
```bash
docker ps
```

if not, it will output:
```json
permission denied while trying to connect to the Docker daemon socket at unix:///var/run/docker.sock: Get "http://%2Fvar%2Frun%2Fdocker.sock/v1.45/containers/json": dial unix /var/run/docker.sock: connect: permission denied
```

you can also check by using:
```bash
grep docker /etc/group
```
if this account is in the docker group, it should be in the group list:
```json
docker:x:992:admin,root
```

if you are not in the docker group, please ask your manager for help you to add your account to the groups of docker:
```bash
sudo usermod -aG docker $USER
# change $USER to you account name
```

after that, please re-login to your account, and you will see:

```json
# grep docker /etc/group
docker:x:992:admin,root,test1
```

and
```json
# docker ps

CONTAINER ID   IMAGE                               COMMAND                  CREATED        STATUS                  PORTS                                                          NAMES
c3faa8940c24   clickhouse/clickhouse-server:25.8   "/entrypoint.sh"         25 hours ago   Up 25 hours             127.0.0.1:8123->8123/tcp, 127.0.0.1:9000->9000/tcp, 9009/tcp   langfuse-clickhouse
76eb80b2b46d   postgres:17                         "docker-entrypoint.s…"   25 hours ago   Up 25 hours (healthy)   127.0.0.1:5432->5432/tcp                                       langfuse-postgres
4fc4ec675e05   redis:7.2.4                         "docker-entrypoint.s…"   25 hours ago   Up 25 hours             127.0.0.1:6379->6379/tcp                                       langfuse-redis
55e88faa273d   cgr.dev/chainguard/minio            "sh -c 'mkdir -p /da…"   25 hours ago   Up 25 hours (healthy)   127.0.0.1:9090->9000/tcp, 127.0.0.1:9091->9001/tcp             langfuse-minio
b79ed87f0609   searxng/searxng:latest              "/usr/local/searxng/…"   3 weeks ago    Up 2 days                                                                              searxng
```

2. Create .env

please create the .env file:
```bash
cp .env.test.example .env
```

3. Start local Langfuse

```bash
cd langfuse
pnpm run infra:dev:up
pnpm i
```

3. First time setup:

3.1 Initialize the database:
```bash
pnpm --filter=shared run db:deploy
```

3.2 Create ClickHouse dev tables.
```bash
# makesure the permission issues, you have installed clickhouse and you have the access to the clickhouse
pnpm --filter=shared run ch:dev-tables
```

if you did not install the clickhouse, please do:
```bash
# ubuntu/debian: cat /etc/os-release
sudo apt-get update
sudo apt-get install -y clickhouse-client
```
```bash
# CentOS / Rocky / Alma / RHEL: cat /etc/os-release
sudo yum install -y clickhouse-client  # 或 sudo dnf install -y clickhouse-client

or
sudo yum clean all
sudo yum makecache
sudo yum install -y --nogpgcheck clickhouse-client
```

4. start the dev server:
```bash
pnpm run dev:web
```

you may found the problem of when you run pnpm run dev:web: 
```json
web:dev: Import trace:
web:dev:   Instrumentation:
web:dev:     ./web/src/initialize.ts
web:dev:     ./web/src/instrumentation.ts
web:dev:
web:dev: https://nextjs.org/docs/messages/module-not-found
web:dev:
web:dev:
web:dev:  ⨯ ./web/src/initialize.ts:4:1
web:dev: Module not found: Can't resolve '@langfuse/shared/src/server/auth/apiKeys'
web:dev:   2 | import { createUserEmailPassword } from "@/src/features/auth-credentials/lib/credentialsServerUtils";
web:dev:   3 | import { prisma } from "@langfuse/shared/src/db";
web:dev: > 4 | import { createAndAddApiKeysToDb } from "@langfuse/shared/src/server/auth/apiKeys";
web:dev:     | ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
web:dev:   5 | import { hasEntitlementBasedOnPlan } from "@/src/features/entitlements/server/hasEntitlement";
web:dev:   6 | import { getOrganizationPlanServerSide } from "@/src/features/entitlements/server/getPlan";
web:dev:   7 | import { CloudConfigSchema } from "@langfuse/shared";
web:dev:
web:dev:
web:dev:
web:dev: Import trace:
web:dev:   Instrumentation:
web:dev:     ./web/src/initialize.ts
web:dev:     ./web/src/instrumentation.ts
web:dev:
web:dev: https://nextjs.org/docs/messages/module-not-found
web:dev:
web:dev:
web:dev:  ⨯ ./web/src/initialize.ts:4:1
web:dev: Module not found: Can't resolve '@langfuse/shared/src/server/auth/apiKeys'
web:dev:   2 | import { createUserEmailPassword } from "@/src/features/auth-credentials/lib/credentialsServerUtils";
web:dev:   3 | import { prisma } from "@langfuse/shared/src/db";
web:dev: > 4 | import { createAndAddApiKeysToDb } from "@langfuse/shared/src/server/auth/apiKeys";
web:dev:     | ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
web:dev:   5 | import { hasEntitlementBasedOnPlan } from "@/src/features/entitlements/server/hasEntitlement";
web:dev:   6 | import { getOrganizationPlanServerSide } from "@/src/features/entitlements/server/getPlan";
web:dev:   7 | import { CloudConfigSchema } from "@langfuse/shared";
```

or
```json
worker:dev: Error: Cannot find module '/home/xiangyu/langfuse/worker/node_modules/@langfuse/shared/dist/src/index.js'
worker:dev:     at createEsmNotFoundErr (node:internal/modules/cjs/loader:1458:15)
worker:dev:     at finalizeEsmResolution (node:internal/modules/cjs/loader:1447:9)
worker:dev:     at resolveExports (node:internal/modules/cjs/loader:679:14)
worker:dev:     at Module._findPath (node:internal/modules/cjs/loader:746:31)
worker:dev:     at node:internal/modules/cjs/loader:1406:27
worker:dev:     at nextResolveSimple (/home/xiangyu/langfuse/node_modules/.pnpm/tsx@4.20.5/node_modules/tsx/dist/register-D46fvsV_.cjs:4:1004)
worker:dev:     at /home/xiangyu/langfuse/node_modules/.pnpm/tsx@4.20.5/node_modules/tsx/dist/register-D46fvsV_.cjs:3:2630
worker:dev:     at /home/xiangyu/langfuse/node_modules/.pnpm/tsx@4.20.5/node_modules/tsx/dist/register-D46fvsV_.cjs:3:1542
worker:dev:     at resolveTsPaths (/home/xiangyu/langfuse/node_modules/.pnpm/tsx@4.20.5/node_modules/tsx/dist/register-D46fvsV_.cjs:4:760)
worker:dev:     at /home/xiangyu/langfuse/node_modules/.pnpm/tsx@4.20.5/node_modules/tsx/dist/register-D46fvsV_.cjs:4:1102 {
worker:dev:   code: 'MODULE_NOT_FOUND',
worker:dev:   path: '/home/xiangyu/langfuse/worker/node_modules/@langfuse/shared'
worker:dev: }
worker:dev:
```
when you run pnpm -w run dev:worker

This means it cannot find the files, you should pre-compile these files by using:
```bash
pnpm -w run build --filter @langfuse/shared
```

Before this, if you use sudo, please:
```bash
sudo chown -R xiangyu:xiangyu /home/xiangyu/langfuse
```

**ERROR 3** : 
```
Module not found: Can't resolve '../endpoint/EndpointParameters'
...
Unable to watch .../dist-es/endpoint
Caused by:
- OS file watch limit reached.
```

**Solution**:

Temporary solution:
```bash
sudo sysctl -w fs.inotify.max_user_watches=524288
sudo sysctl -w fs.inotify.max_user_instances=1024
```

Permanent solution:
```bash
echo "fs.inotify.max_user_watches=524288" | sudo tee -a /etc/sysctl.d/99-inotify.conf
echo "fs.inotify.max_user_instances=1024" | sudo tee -a /etc/sysctl.d/99-inotify.conf
sudo sysctl --system
```


Then you can continue to run the development environment to finish the setup.

```bash
pnpm run dev:web
```

It will start building and takes around 3 minitues to get into the main page.

### 2. Execution

1. First, you should start the ArbiterOS kernel:

```bash
cd ArbiterOS-Kernel
uv run poe litellm
```

2. Then, you should start the Langfuse:
```bash
cd langfuse

pnpm run dev:web
# or
pnpm run dev:web-no-turbo
```

3. Finally, you can start the OpenClaw gateway as before:
```bash
openclaw gateway status

openclaw gateway start
# or based on the status of the gateway
openclaw gateway restart
```
