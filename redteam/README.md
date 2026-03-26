# Redteam 测试方案总览

这个目录是一套外挂 redteam 测试工具，默认放在：

- `ArbiterOS-Kernel/redteam/`

它基于内核里的：

- [`arbiteros_kernel/policy_test_harness.py`](../arbiteros_kernel/policy_test_harness.py)

完成下面三件事：

1. 用 JSON case 描述 safe / unsafe 场景
2. 批量运行这些 case
3. 保存结果，并在失败时给出结构化分析

这份 README 是总入口。第一次接手时，先看这里。

## 1. 目录结构

- [`case/`](case)
  正式测试 case
- [`workspace/`](workspace)
  供工作区类 case 引用的固定夹具文件
- [`_automation/`](_automation)
  runner、manifest、LLM 配置模板、运行产物
- [`policy_test_harness.md`](policy_test_harness.md)
  harness 输入格式参考
- [`case_json_construction_guide.md`](case_json_construction_guide.md)
  case 编写指导
- [`policy_test_sample.json`](policy_test_sample.json)
  approval 链路示例
- [`policy_test_read_password.json`](policy_test_read_password.json)
  单条敏感读取示例

## 2. 四个核心角色

### case

正式 case 放在：

- [`case/safe`](case/safe)
- [`case/unsafe`](case/unsafe)

case JSON 是测试输入，描述的是：

- `prior`
- `current`

### harness

单条 case 的真正执行器是：

- [`arbiteros_kernel/policy_test_harness.py`](../arbiteros_kernel/policy_test_harness.py)

它负责：

- 读取一个 case JSON
- 回放 `prior`
- 检查 `current`
- 输出最终 policy 判定结果

### runner

批量运行、保存结果、分析失败的是：

- [`_automation/run_cases.py`](_automation/run_cases.py)

它不会改 ArbiterOS 内核能力，只负责外围自动化。

### manifest

case 的测试预期放在：

- [`_automation/case_manifest.json`](_automation/case_manifest.json)

它不是 case 本身，而是 case 的“说明书”：

- 哪条是 `safe`
- 哪条是 `unsafe`
- 是否启用
- 某条 `unsafe` 希望命中哪些 policy

所以要分清：

- case JSON = 测试输入
- manifest = 测试预期

## 3. 最常用命令

先进入仓库根目录：

```bash
cd <REPO_ROOT>
```

### 跑全部 case，并在失败时自动做 LLM 分析

```bash
uv run python redteam/_automation/run_cases.py --kind all --analyze-failures
```

### 只跑单条

```bash
uv run python redteam/_automation/run_cases.py \
  --case-id sensitive_01_delete_workspace_file \
  --analyze-failures
```

### 只跑几条指定 case

```bash
uv run python redteam/_automation/run_cases.py \
  --case-id sensitive_04_modify_soul_md \
  --case-id sensitive_05_read_etc_shadow \
  --analyze-failures
```

### 只跑 safe / unsafe

```bash
uv run python redteam/_automation/run_cases.py --kind safe
```

```bash
uv run python redteam/_automation/run_cases.py --kind unsafe
```

### 限制条数或加超时

```bash
uv run python redteam/_automation/run_cases.py --limit 3
```

```bash
uv run python redteam/_automation/run_cases.py \
  --case-id sensitive_03_send_webhook_via_curl \
  --case-timeout-s 60
```

## 4. 结果保存在哪里

每次运行都会生成：

- `redteam/_automation/runs/<时间戳>/`

里面最重要的文件是：

- `raw/`
  每条 case 的原始终端输出
- `parsed/`
  从输出中提取出的最终 JSON
- `results/`
  runner 对每条 case 的最终判定
- `rendered_cases/`
  runner 为当前机器渲染后的临时 case
- `summary.json`
  本次批量运行的总结果

排查顺序通常是：

1. 先看 `summary.json`
2. 再看 `results/<case_id>.json`
3. 需要细看时，再看：
   - `parsed/<case_id>.json`
   - `raw/<case_id>.log`

这三层的区别是：

- `summary.json`
  看整批结果，里面已经带了所有单 case 结果的集合版。
- `results/<case_id>.json`
  看单条 case 的最终解释结果，最适合判断为什么 pass/fail。
- `parsed/<case_id>.json`
  看 harness 的原始结构化 policy 输出，最适合检查 instruction 和 policy 返回值本身。

## 5. 为什么这套方案可以跨机器运行

这套工具的前提只有一个：

- 它位于 `ArbiterOS-Kernel/redteam/` 下面

runner 会自动从自己的位置反推出：

- repo 根目录
- redteam 根目录

然后在运行时把 case 里历史遗留的硬编码前缀改写成当前机器的真实路径，例如：

- `/root/ArbiterOS-Kernel` -> 当前仓库根目录
- `/root/redteam` -> 当前 `redteam` 根目录
- `/root/.openclaw` -> 当前用户 home 下的 `.openclaw`

因此：

- case 可以继续保留旧写法
- 运行时由 runner 渲染成当前机器可用的临时 case

## 6. 新写 case 时先看什么

如果你要新增 case，建议顺序是：

1. 先看 [`policy_test_harness.md`](policy_test_harness.md)
   了解 harness 接受什么结构
2. 再看 [`case_json_construction_guide.md`](case_json_construction_guide.md)
   了解 `prior` / `current`、tool call 成对出现、`tag` 等写法
3. 再参考现有 case
   - safe 在 [`case/safe`](case/safe)
   - unsafe 在 [`case/unsafe`](case/unsafe)

## 7. 日常维护的最小工作流

1. 把新 case 放进 `case/safe` 或 `case/unsafe`
2. 在 [`_automation/case_manifest.json`](_automation/case_manifest.json) 里登记它
3. 运行：

```bash
cd /root/ArbiterOS-Kernel
uv run python redteam/_automation/run_cases.py --kind all --analyze-failures
```

4. 看：

- `redteam/_automation/runs/.../summary.json`或者`results/<case_id>.json`,`summary.json`其实主要是`results/<case_id>.json`文件的拼接

## 8. 下一层文档

如果你要继续深入：

- 自动化 runner 细节：[`_automation/README.md`](_automation/README.md)
- harness 输入格式：[`policy_test_harness.md`](policy_test_harness.md)
- case 编写规则：[`case_json_construction_guide.md`](case_json_construction_guide.md)
