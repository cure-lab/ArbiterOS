# Policy Rule IR Authoring Guide

本文档面向根据用户自然语言生成自定义 policy 的 Agent / LLM。

V1 支持两类自定义 policy：

- `unary_tool_call`：只判断当前这一次 tool call / response 是否应该被拦截。
- `relational_flow`：判断历史/source taint 与当前 sink instruction 之间的信息流关系，例如“读到高敏感内容后，不允许直接发消息或跨会话委托”。

V1 仍不支持复杂图查询、时间窗口、计数限速、用户确认后继续执行等状态型策略。

目标链路：

1. 用户用自然语言描述 policy。
2. Agent 读取本文档，生成 `policy_rule_ir.json`。
3. Policy 侧 validator 校验 JSON。
4. 如果规则需要当前 parser 没有的低维字段，在 `required_metadata` 中声明。
5. Runtime 根据 `required_metadata` 补齐字段：直白 tool 参数由 policy runtime 确定性抽取，复杂语义由 kernel/parser 写入 `security_type.custom.policy_metadata`。
6. Policy 侧把 IR 编译成对应 runtime rule：unary 写入 `user_unary_gate_rules.json`，relational 写入 `user_relational_flow_rules.json`。

重要边界：

- `policy_rule_ir.json` 是中间表示，不是 runtime 规则文件。
- 内置 unary 规则放在 `unary_gate_rules.json`；用户自定义 unary 规则放在 `user_unary_gate_rules.json`。
- 用户自定义 relational 规则放在 `user_relational_flow_rules.json`，由 `policy.json` 中 `relational_policy.user_rules_enabled` 控制是否加载。
- 不要把本文档里的 IR 直接写入任何 runtime rules 文件，必须先通过 validator/compiler。
- Runtime 编译后才会变成 `selector/source_selector + predicate + effect + message` 的运行时 rule。
- `user_policy_rule_ir_examples.json` 是示例包，不等于默认启用的 active policy set；active runtime 文件只应包含当前 parser/kernel 已能 lowering 的 metadata contract。

推荐编译命令：

```bash
uv run python arbiteros_kernel/policy/policy_rule_ir.py \
  --input arbiteros_kernel/policy/policy_rule_ir.json \
  --output arbiteros_kernel/policy/user_unary_gate_rules.json \
  --source user_unary_gate_rules.json \
  --target unary
```

Relational 编译命令：

```bash
uv run python arbiteros_kernel/policy/policy_rule_ir.py \
  --input arbiteros_kernel/policy/user_relational_policy_rule_ir_examples.json \
  --output arbiteros_kernel/policy/user_relational_flow_rules.json \
  --source user_relational_flow_rules.json \
  --target relational
```

与早期草稿的区别：

- 早期草稿把 `action`、`path_hint`、`arg_text_upper`、`has_external_url` 等 policy runtime 便利字段也列为 built-in。
- 当前版本将 built-in 收窄为 instruction core + security metadata。
- 来自 tool arguments 的直白字段可以声明为 `source.kind = tool_arguments`，由 policy runtime 做确定性抽取；需要语义判断、跨调用状态、标签分类的字段仍由 kernel/parser/LLM lowering 进入 `policy_metadata`。
- 内置 unary rules 和自定义 rules 使用同一套 eval context；predicate 不再区分 legacy source 或 Policy Rule IR source。
- 这样可以把“结构化参数抽取”和“语义 lowering”分开：简单参数不再额外要求 kernel 增加 metadata，复杂语义仍保持 kernel contract。

## 1. V1 Scope

V1 规则必须满足：

- `kind` 只能是 `unary_tool_call` 或 `relational_flow`。
- unary 规则只检查当前 instruction 的核心字段、安全 metadata、policy runtime 可确定性抽取的 tool argument metadata、以及 kernel/parser 补充的业务 metadata。
- relational 规则检查 source taint/history 与当前 sink instruction 之间的关系。
- 规则文件本身不要直接读取 raw arguments；需要的字段仍写进 `required_metadata`。当字段是收件人、URL、action、数量等直白结构化参数时，可标为 `tool_arguments`，由 policy runtime 统一抽取后再供 predicate 引用。
- 规则 effect 当前只生成 `BLOCK`。

如果用户需求依赖“之前读过什么、当前发给谁、是否由低可信网页驱动执行”等 source/sink 关系，优先生成 `relational_flow`，不要硬写成 unary rule。

## 2. Top-Level JSON

```json
{
  "version": 1,
  "source": "user-natural-language",
  "rules": [
    {
      "id": "USER-TRANSFER-001",
      "kind": "unary_tool_call",
      "enabled": true,
      "title": "block high value transfer",
      "description": "block bank_transfer when transfer_amount_cny is greater than 10000",
      "effect": "BLOCK",
      "severity": "HIGH",
      "message": "转账金额超过用户设定阈值，已拦截。",
      "rule": {
        "selector": {
          "tools": ["bank_transfer"]
        },
        "predicate": {
          "gt": [
            { "var": "transfer_amount_cny" },
            { "const": 10000 }
          ]
        }
      }
    }
  ],
  "required_metadata": []
}
```

Top-level 字段：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `version` | 是 | 当前固定为 `1` |
| `source` | 否 | 来源，例如 `user-natural-language` |
| `rules` | 是 | unary tool-call 或 relational-flow 规则列表 |
| `required_metadata` | 否 | 当前 parser 没有、但规则需要 kernel/parser 补充的 metadata |

每条 rule：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `id` | 是 | 稳定唯一 ID，建议 `USER-<DOMAIN>-<NUMBER>` |
| `kind` | 是 | `unary_tool_call` 或 `relational_flow` |
| `enabled` | 是 | 是否启用 |
| `title` | 是 | 简短英文标题 |
| `description` | 是 | 精确描述触发条件 |
| `effect` | 是 | V1 固定 `BLOCK` |
| `severity` | 否 | `LOW` / `MEDIUM` / `HIGH` / `CRITICAL` |
| `message` | 是 | 面向用户的中文拦截说明 |
| `rule.selector` | 否 | 限定 tool / instruction type / category |
| `rule.source` | relational 否 | 限定 source instruction |
| `rule.sink` | relational 否 | 限定当前 sink instruction / flow kind |
| `rule.predicate` | 是 | 当前 tool-call context 上的判定条件 |

## 3. Tool-Call Context

predicate 只能引用两类字段：

1. Built-in metadata：来自 instruction core 或 security metadata，policy 侧每个 tool call 都能提供。
2. Required metadata：当前规则声明后，由 kernel/parser 为当前 tool call 补充。

### 3.1 Built-in Metadata

这些字段不需要声明，predicate 可以直接使用：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `scope` | string | 当前规则评估 scope，V1 tool-call 规则中通常为 `tool` |
| `tool_name` | string | 原始 tool 名 |
| `canonical_tool_name` | string | policy 规范化 tool 名 |
| `tool_call_id` | string | 当前 tool call id |
| `missing_instruction` | boolean | 当前 tool call 是否缺少对应 lowered instruction |
| `instruction_type` | string | `READ` / `WRITE` / `EXEC` / `RESPOND` 等 |
| `instruction_category` | string | instruction category |
| `trustworthiness` | level | 当前 instruction 可信度 |
| `confidentiality` | level | 当前 instruction 敏感度 |
| `risk` | level | `LOW` / `UNKNOWN` / `HIGH` |
| `reversible` | boolean | 是否可回退 |

Policy Rule IR v1 的 built-in surface 对齐当前 kernel instruction/parser schema，只包含 instruction core 和基础 security metadata。下面这些字段虽然可能存在于 legacy unary gate runtime context 或 Python parser 实现中，但 **Policy Rule IR 不应直接引用**：

```text
raw_args
arg_text_upper
arg_total_str_len
action
path_hint
path_basename
path_dirname
direct_target_basenames
exec_path_tokens
exec_write_targets
exec_write_target_basenames
has_external_url
prop_trustworthiness
prop_confidentiality
confidence
authority
tags
review_required
approval_required
destructive
custom_io_kind
custom_flow_role
custom_taint_role
```

如果规则需要这些语义，例如 action、路径、URL、命令关键词、扫描工具名，应在 `required_metadata` 中声明一个业务字段。直白结构化字段可标为 `tool_arguments`，由 policy runtime 统一抽取；需要语义分类或上下文判断的字段再要求 kernel/parser lowering 后写入 `security_type.custom.policy_metadata`。

为了避免名称碰撞，`required_metadata.field` 也不能使用这些 runtime 内部字段名。需要类似语义时，请使用业务名，例如 `trade_confidence_high`、`operation_destructive`、`browser_action_kind`。

Level 顺序：

```text
LOW < UNKNOWN < HIGH
```

如果用户说“至少中等”，V1 中用 `ge ... UNKNOWN`。如果用户说“高风险”，用 `risk == HIGH` 或 `risk in ["HIGH", "CRITICAL"]`，但当前 kernel 核心 level 主要是 `LOW/UNKNOWN/HIGH`。

### 3.1.1 Relational Flow Built-ins

`relational_flow` 除了可以使用当前 sink 的 unary built-ins，还可以使用下面的 source/sink 字段：

| 字段 | 含义 |
| --- | --- |
| `flow_kind` | 当前 sink 的信息流类别，例如 `comm_sink`、`delegate_sink`、`exec_side_effect`、`respond_sink` |
| `source_instruction_type` / `source_instruction_category` | 最近 source instruction 的类型和类别 |
| `source_tool_name` / `source_tool_call_id` | 最近 source tool 的名称和调用 ID |
| `source_trustworthiness` / `source_confidentiality` | source taint / history 传播过来的可信度和敏感度 |
| `sink_instruction_type` / `sink_instruction_category` | 当前 sink instruction 的类型和类别 |
| `sink_tool_name` / `sink_tool_call_id` | 当前 sink tool 的名称和调用 ID |
| `sink_trustworthiness` / `sink_confidentiality` | 当前 sink instruction 的可信度和敏感度 |
| `sink_prop_trustworthiness` / `sink_prop_confidentiality` | 当前 sink instruction 的传播可信度和传播敏感度 |
| `sink_risk` / `sink_reversible` | 当前 sink 的风险等级和可逆性 |
| `respond_content_present` | 当前规则是否在直接回复输出上求值 |

Relational rule 的 `rule.source` 支持 `tools`、`instruction_types`、`categories`；`rule.sink` 额外支持 `flow_kinds`。常见 `flow_kind` 包括：

```text
comm_sink, delegate_sink, voice_sink, respond_sink,
exec_side_effect, ui_side_effect, persist_side_effect,
write_shared, write_local, read_sensitive, read_external
```

### 3.2 Required Metadata

如果用户规则需要 built-in metadata 中没有的字段，必须在 `required_metadata` 中声明。不要在 predicate 中直接使用未声明字段。

示例：

```json
{
  "field": "transfer_amount_cny",
  "type": "number",
  "description": "Transfer amount normalized to CNY.",
  "applies_to": {
    "tools": ["bank_transfer"]
  },
  "source": {
    "kind": "tool_arguments",
    "paths": ["amount", "currency"]
  },
  "normalization": "Convert supported currencies to a numeric CNY amount.",
  "on_missing": "no_match",
  "required_for_rules": ["USER-TRANSFER-001"]
}
```

字段说明：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `field` | 是 | lower snake case 字段名，例如 `transfer_amount_cny` |
| `type` | 是 | `string` / `number` / `integer` / `boolean` / `string_array` / `number_array` |
| `description` | 是 | 字段语义 |
| `applies_to.tools` | 否 | 字段适用的工具 |
| `applies_to.instruction_types` | 否 | 字段适用的 instruction type |
| `source.kind` | 是 | 字段来源 |
| `source.paths` | 否 | 从 tool arguments 读取的路径，例如 `["amount"]` |
| `source.transform` | 否 | policy runtime 的通用确定性转换，例如 `external_domains`、`external_domain_count`、`hostname`、`equals`、`nonempty`、`abs_number` |
| `normalization` | 否 | 归一化规则 |
| `on_missing` | 是 | 字段缺失时如何处理 |
| `required_for_rules` | 是 | 哪些 rule 依赖该字段 |

`source.kind` 可选值：

| 值 | 含义 |
| --- | --- |
| `tool_arguments` | 从当前 tool arguments 直接抽取或归一化 |
| `kernel_lowering` | kernel deterministic lowering 计算 |
| `llm_lowering` | kernel 侧模型根据参数语义生成 |
| `parser_custom` | 由现有 parser custom 字段派生 |
| `derived` | 由多个已有字段组合派生 |

注意：`required_metadata` 是用户可见的统一 contract。`source.kind = tool_arguments` / `derived` 的直白字段可由 policy runtime 从当前 tool arguments 确定性补齐；`kernel_lowering` / `llm_lowering` / `parser_custom` 字段仍应由 kernel/parser 写入 `security_type.custom.policy_metadata`。predicate 始终只引用扁平后的字段名，不直接读取 raw arguments。

如果字段只是示例或未来 lowering 计划，不能把它放进 active runtime bundle，除非它已经能由 policy runtime direct metadata 或 kernel/parser `custom.policy_metadata` 产出；否则应保留在 examples/spec 中，并使用 `validation_error` 或接入前检查阻止安装。

`on_missing` 可选值：

| 值 | 行为 |
| --- | --- |
| `validation_error` | 安装/启用前必须确认 kernel 已能提供该字段；如果未经确认仍被编译进 runtime，compiler 会按 fail-closed 包装，避免静默放行 |
| `no_match` | 运行时字段缺失时，该规则不触发 |
| `fail_closed` | 运行时字段缺失时，规则直接触发拦截 |

默认建议：

- 普通业务阈值用 `no_match`。
- 高危动作且缺字段会造成明显绕过时用 `fail_closed`。
- 已知当前 kernel 还没实现该字段时，用 `validation_error` 提醒接入方先补 lowering。

### 3.3 Metadata Contract

Required metadata 的约束：

- 不能覆盖 built-in metadata。
- 字段名必须 lower snake case。
- 字段值必须是 JSON scalar 或 scalar array。
- `tool_arguments` / `derived` 字段可由 policy runtime 直接补齐。
- `kernel_lowering` / `llm_lowering` / `parser_custom` 字段应由 kernel 写到当前 instruction 的 `security_type.custom.policy_metadata`。
- policy 侧会把 direct metadata 和 `policy_metadata` 扁平合并进当前 tool-call context。
- 如果字段和 built-in 字段重名，policy 侧会忽略该字段。
- 新 metadata 只能补充业务语义，不应修改核心安全标签，如 `instruction_type`、`risk`、`confidentiality`、`trustworthiness`。

Kernel 侧推荐返回形状：

```json
{
  "tool_call_id": "call_abc",
  "policy_metadata": {
    "transfer_amount_cny": 12800
  },
  "evidence": {
    "transfer_amount_cny": ["arguments.amount", "arguments.currency"]
  },
  "confidence": "HIGH"
}
```

Instruction 中推荐存储位置：

```json
{
  "security_type": {
    "custom": {
      "policy_metadata": {
        "transfer_amount_cny": 12800
      }
    }
  }
}
```

## 4. Predicate Language

### 4.1 Value Expressions

```json
{ "var": "field_name" }
{ "const": 10000 }
{ "const": "HIGH" }
{ "const": ["SEND", "BROADCAST"] }
{ "len": { "var": "email_external_domains" } }
{ "count_intersections": [{ "var": "retrieval_query_labels" }, { "const": ["SSN", "EMAIL"] }] }
```

`var` 必须引用 built-in metadata 或 `required_metadata.field`。不要引用 raw arguments 或 legacy runtime-derived fields。

### 4.2 Boolean Operators

```json
{ "all": [predicate1, predicate2] }
{ "any": [predicate1, predicate2] }
{ "not": predicate }
{ "truthy": { "var": "field" } }
{ "falsy": { "var": "field" } }
{ "exists": { "var": "field" } }
{ "missing": { "var": "field" } }
```

### 4.3 Comparison Operators

```json
{ "eq":  [{ "var": "a" }, { "const": "X" }] }
{ "ne":  [{ "var": "a" }, { "const": "X" }] }
{ "gt":  [{ "var": "amount" }, { "const": 10000 }] }
{ "ge":  [{ "var": "risk" }, { "const": "UNKNOWN" }] }
{ "lt":  [{ "var": "risk" }, { "const": "HIGH" }] }
{ "le":  [{ "var": "trustworthiness" }, { "const": "LOW" }] }
{ "between": [{ "var": "egress_payload_mb" }, { "const": 100 }, { "const": 10000 }] }
```

字段缺失时，比较运算不应触发。对于 required metadata，compiler 会根据 `on_missing` 自动加保护条件。

### 4.4 Membership and Text Operators

```json
{ "in": [{ "var": "browser_action_kind" }, { "const": ["SUBMIT", "UPLOAD"] }] }
{ "not_in": [{ "var": "target_path_basename" }, { "const": ["README.MD"] }] }
{ "contains": [{ "var": "sensitive_content_labels" }, { "const": "TOKEN" }] }
{ "intersects": [{ "var": "tags" }, { "const": ["SECRET_LIKE", "HIGH_RISK"] }] }
{ "contains_all": [{ "var": "browser_content_labels" }, { "const": ["TOKEN", "LOCAL_CONFIG"] }] }
{ "subset_of": [{ "var": "iam_requested_scopes" }, { "const": ["READ_ONLY", "LOG_VIEWER"] }] }
{ "starts_with": [{ "var": "target_hostname" }, { "const": "STAGING-" }] }
{ "ends_with": [{ "var": "target_hostname" }, { "const": ".INTERNAL" }] }
{ "matches": [{ "var": "browser_target_hostname" }, { "const": "(^|\\.)pastebin\\.com$" }] }
```

约定：

- 对关键词、路径、URL、action 等来自 tool arguments 的语义，优先声明 required metadata；直白结构化参数用 `tool_arguments`，复杂语义再交给 kernel/parser lowering。
- 对 enum/action metadata，常量写成对应 source.kind 的归一化约定值。
- 多个 lowered 标签或枚举候选用 `contains/intersects/contains_all`。
- “请求集合必须完全落在允许集合内”用 `subset_of`，通常配合 `not` 表达越权阻断。
- “命中几个风险标签”用 `count_intersections` 作为 value expression，再配合 `gt/ge`。
- `matches` 只能用于已经 lowering 后的字符串字段，例如 hostname、normalized route、branch name；不要用它去扫 raw args。
- 对数组或字符串长度，用 `len` 作为 value expression，再配合 `gt/ge/lt/le`。

## 5. Selector

Selector 用于先缩小规则范围。

```json
{
  "selector": {
    "tools": ["browser"],
    "instruction_types": ["EXEC"],
    "categories": ["EXECUTION.Env"]
  }
}
```

字段：

| 字段 | 说明 |
| --- | --- |
| `tools` | tool 名，建议使用 canonical tool 名 |
| `instruction_types` | `READ` / `WRITE` / `EXEC` 等 |
| `categories` | instruction category |

如果用户只说“所有命令”，通常 selector 写 `tools: ["exec"]`。如果用户说“所有执行类动作”，可以用 `instruction_types: ["EXEC"]`。

## 6. Examples

### Example A: 大额转账

用户自然语言：

```text
超过 10000 元人民币的转账不允许直接执行。
```

生成 IR：

```json
{
  "version": 1,
  "source": "user-natural-language",
  "rules": [
    {
      "id": "USER-TRANSFER-001",
      "kind": "unary_tool_call",
      "enabled": true,
      "title": "block high value transfer",
      "description": "block bank_transfer when transfer_amount_cny is greater than 10000",
      "effect": "BLOCK",
      "severity": "HIGH",
      "message": "转账金额超过用户设定阈值（10000 CNY），已拦截。",
      "rule": {
        "selector": {
          "tools": ["bank_transfer"]
        },
        "predicate": {
          "gt": [
            { "var": "transfer_amount_cny" },
            { "const": 10000 }
          ]
        }
      }
    }
  ],
  "required_metadata": [
    {
      "field": "transfer_amount_cny",
      "type": "number",
      "description": "Transfer amount normalized to CNY.",
      "applies_to": {
        "tools": ["bank_transfer"]
      },
      "source": {
        "kind": "tool_arguments",
        "paths": ["amount", "currency"]
      },
      "normalization": "Convert supported currencies to a numeric CNY amount.",
      "on_missing": "no_match",
      "required_for_rules": ["USER-TRANSFER-001"]
    }
  ]
}
```

### Example B: 浏览器提交敏感内容

用户自然语言：

```text
不要让浏览器把 token、密码或者 OpenClaw 配置提交到网页表单里。
```

生成 IR：

```json
{
  "version": 1,
  "source": "user-natural-language",
  "rules": [
    {
      "id": "USER-BROWSER-SENSITIVE-SUBMIT-001",
      "kind": "unary_tool_call",
      "enabled": true,
      "title": "block browser sensitive submit",
      "description": "block browser side-effect actions that contain credential or local config hints",
      "effect": "BLOCK",
      "severity": "HIGH",
      "message": "浏览器动作疑似提交密码、令牌或本地配置内容，已拦截。",
      "rule": {
        "selector": {
          "tools": ["browser"]
        },
        "predicate": {
          "all": [
            {
              "truthy": { "var": "browser_side_effect_action" }
            },
            {
              "truthy": { "var": "browser_sensitive_submit_content" }
            }
          ]
        }
      }
    }
  ],
  "required_metadata": [
    {
      "field": "browser_side_effect_action",
      "type": "boolean",
      "description": "True when the browser action may submit, upload, click, confirm, or otherwise cause a page-visible side effect.",
      "applies_to": {
        "tools": ["browser"]
      },
      "source": {
        "kind": "tool_arguments",
        "paths": ["action", "request.kind"]
      },
      "normalization": "Classify browser actions such as submit, upload, click, dialog accept, or form interaction as side-effect actions.",
      "on_missing": "fail_closed",
      "required_for_rules": ["USER-BROWSER-SENSITIVE-SUBMIT-001"]
    },
    {
      "field": "browser_sensitive_submit_content",
      "type": "boolean",
      "description": "True when browser-submitted text or file content contains credential, token, or local OpenClaw configuration hints.",
      "applies_to": {
        "tools": ["browser"]
      },
      "source": {
        "kind": "llm_lowering",
        "paths": ["text", "value", "file", "request"]
      },
      "normalization": "Detect password, token, API key, OpenClaw config, LiteLLM config, or similar sensitive local configuration content.",
      "on_missing": "validation_error",
      "required_for_rules": ["USER-BROWSER-SENSITIVE-SUBMIT-001"]
    }
  ]
}
```

### Example C: 外部收件人数量

用户自然语言：

```text
一次邮件如果发给 3 个以上公司外部收件人，就拦截。
```

生成 IR：

```json
{
  "version": 1,
  "source": "user-natural-language",
  "rules": [
    {
      "id": "USER-EMAIL-EXTERNAL-RECIPIENTS-001",
      "kind": "unary_tool_call",
      "enabled": true,
      "title": "block many external recipients",
      "description": "block send_email when external recipient count is greater than 3",
      "effect": "BLOCK",
      "severity": "MEDIUM",
      "message": "外部收件人数量超过用户设定阈值，已拦截。",
      "rule": {
        "selector": {
          "tools": ["send_email"]
        },
        "predicate": {
          "gt": [
            { "var": "email_external_recipient_count" },
            { "const": 3 }
          ]
        }
      }
    }
  ],
  "required_metadata": [
    {
      "field": "email_external_recipient_count",
      "type": "integer",
      "description": "Number of recipients outside the user's organization domain.",
      "applies_to": {
        "tools": ["send_email"]
      },
      "source": {
        "kind": "tool_arguments",
        "paths": ["to", "cc", "bcc"],
        "transform": "external_domain_count"
      },
      "normalization": "Policy runtime counts recipients whose email domain is outside the configured organization domains.",
      "on_missing": "validation_error",
      "required_for_rules": ["USER-EMAIL-EXTERNAL-RECIPIENTS-001"]
    }
  ]
}
```

### Example D: 高敏感上下文禁止外发

用户自然语言：

```text
如果当前上下文来自高敏感 source，不允许直接发消息、跨会话委托或直接回复给用户。
```

生成 IR：

```json
{
  "version": 1,
  "source": "user-natural-language",
  "rules": [
    {
      "id": "USER-FLOW-HIGH-CONF-OUTBOUND-001",
      "kind": "relational_flow",
      "enabled": true,
      "title": "block high confidentiality outbound flow",
      "description": "block high-confidentiality source context from flowing to outbound sinks",
      "effect": "BLOCK",
      "severity": "HIGH",
      "message": "高敏感上下文不允许直接流向对外可见或跨会话目标。",
      "rule": {
        "source": {},
        "sink": {
          "flow_kinds": ["comm_sink", "delegate_sink", "voice_sink", "respond_sink"]
        },
        "predicate": {
          "ge": [
            { "var": "source_confidentiality" },
            { "const": "HIGH" }
          ]
        }
      }
    }
  ],
  "required_metadata": []
}
```

## 7. Agent Translation Procedure

1. 判断是否能用 V1 表达：
   - 只看当前 tool call -> 生成 `unary_tool_call`。
   - 依赖 source/sink/taint 与当前 sink 的关系 -> 生成 `relational_flow`。
   - 依赖未来动作、时间窗口、计数状态、用户确认后继续执行 -> V1 暂不支持。
2. 选择 selector：
   - unary 明确工具名时写 `selector.tools`。
   - relational 用 `source` / `sink` 分别限定两端；sink 可用 `flow_kinds` 表示外发、委托、执行副作用等关系。
   - 泛执行类动作可写 `instruction_types: ["EXEC"]`。
3. 列出条件：
   - 阈值 -> `gt/ge/lt/le/between`
   - lowered 动作枚举 -> `in`
   - lowered 标签集合 -> `contains/intersects/contains_all`
   - 请求集合必须受限于允许集合 -> `subset_of`
   - 多个标签命中数量 -> `count_intersections + ge/gt`
   - 已归一化字符串模式 -> `matches`
   - lowered 数组或字符串长度 -> `len + gt/ge/lt/le`
   - 多条件同时满足 -> `all`
   - 多条件任一满足 -> `any`
4. 检查每个 `var`：
   - built-in metadata 里有 -> 直接使用。
   - built-in metadata 里没有 -> 必须加入 `required_metadata`。
   - 来自 tool arguments 的 action/path/url/count/domain 等直白字段 -> 声明为 `required_metadata` + `source.kind = tool_arguments`。
   - 需要语义分类、上下文状态或模型判断的字段 -> 声明为 `kernel_lowering` / `llm_lowering` / `parser_custom`。
5. 写清楚 `on_missing`：
   - 不确定 kernel 是否已有字段 -> `validation_error`
   - 字段缺失时不触发即可 -> `no_match`
   - 缺字段本身危险 -> `fail_closed`
6. 输出纯 JSON：
   - 不要 Markdown。
   - 不要注释。
   - 不要省略必填字段。

## 8. Common Mistakes

- 生成 `kind: "unary"`：V1 必须是 `unary_tool_call` 或 `relational_flow`。
- 把 source/sink 关系硬塞进 unary predicate；涉及历史 taint 或当前 sink 的策略应该使用 `relational_flow`。
- 在 predicate 中使用未声明的业务字段。
- 在 `required_metadata.field` 中覆盖 built-in 字段，例如 `risk`、`tool_name`。
- 让新 metadata 修改核心安全字段。新 metadata 只能补充业务语义。
- 把 `policy_rule_ir.json` 直接写入 `unary_gate_rules.json`、`user_unary_gate_rules.json` 或 `user_relational_flow_rules.json`。
- 在 IR predicate 中直接使用 `raw_args`、`arg_text_upper`、`action`、`path_hint` 等 legacy runtime-derived fields。
- 在单条规则里临时扫描 raw arguments；直白参数应走统一的 `tool_arguments` direct metadata，复杂语义仍走 kernel/parser lowering。
- 对缺失字段不写 `on_missing`。
