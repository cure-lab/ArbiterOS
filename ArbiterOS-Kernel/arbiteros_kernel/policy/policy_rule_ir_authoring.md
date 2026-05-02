# Policy Rule IR Authoring Guide

本文档面向根据用户自然语言生成自定义 policy 的 Agent / LLM。

V1 只支持 **unary tool-call policy**：每条规则只判断当前这一次 tool call 是否应该被拦截。不要在 V1 中生成跨步骤、source/sink、历史 taint、未来动作、用户确认流程、或 relational policy。

目标链路：

1. 用户用自然语言描述 policy。
2. Agent 读取本文档，生成 `policy_rule_ir.json`。
3. Policy 侧 validator 校验 JSON。
4. 如果规则需要当前 parser 没有的低维字段，在 `required_metadata` 中声明。
5. Kernel/parser 侧根据 `required_metadata` 为当前 tool call 补充 `security_type.custom.policy_metadata`。
6. Policy 侧把 IR 编译成用户 unary gate runtime rule，写入 `user_unary_gate_rules.json`，再和内置规则一起执行。

重要边界：

- `policy_rule_ir.json` 是中间表示，不是 runtime 规则文件。
- 内置规则放在 `unary_gate_rules.json`；用户自定义规则放在 `user_unary_gate_rules.json`。
- `policy.json` 中 `unary_gate.user_rules_enabled` 控制是否加载用户规则文件。
- 不要把本文档里的 IR 直接写入任何 runtime rules 文件，必须先通过 validator/compiler。
- Runtime 编译后才会变成 `selector + predicate + effect + message` 的 unary gate rule。

## 1. V1 Scope

V1 规则必须满足：

- `kind` 固定为 `unary_tool_call`。
- 规则只检查当前 tool call 的 tool name、action、参数摘要、路径摘要、安全标签、以及 kernel 补充的业务 metadata。
- 规则不能依赖“之前读过什么”“之后发给谁”“是否由外部网页驱动”等跨步骤关系。
- 规则 effect 当前只生成 `BLOCK`。

如果用户需求明显依赖历史信息流，例如“读了 secret 后不能发邮件”，V1 Agent 应返回无法表达，并把需求标记为 future relational policy，而不是硬写成 unary rule。

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
| `rules` | 是 | unary tool-call 规则列表 |
| `required_metadata` | 否 | 当前 parser 没有、但规则需要 kernel/parser 补充的 metadata |

每条 rule：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `id` | 是 | 稳定唯一 ID，建议 `USER-<DOMAIN>-<NUMBER>` |
| `kind` | 是 | 固定 `unary_tool_call` |
| `enabled` | 是 | 是否启用 |
| `title` | 是 | 简短英文标题 |
| `description` | 是 | 精确描述触发条件 |
| `effect` | 是 | V1 固定 `BLOCK` |
| `severity` | 否 | `LOW` / `MEDIUM` / `HIGH` / `CRITICAL` |
| `message` | 是 | 面向用户的中文拦截说明 |
| `rule.selector` | 否 | 限定 tool / instruction type / category |
| `rule.predicate` | 是 | 当前 tool-call context 上的判定条件 |

## 3. Tool-Call Context

predicate 只能引用两类字段：

1. Built-in metadata：policy 侧每个 tool call 都能提供。
2. Required metadata：当前规则声明后，由 kernel/parser 为当前 tool call 补充。

### 3.1 Built-in Metadata

这些字段不需要声明，predicate 可以直接使用：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `tool_name` | string | 原始 tool 名 |
| `canonical_tool_name` | string | policy 规范化 tool 名 |
| `tool_call_id` | string | 当前 tool call id |
| `action` | string | 参数中的 action，policy 侧会转为大写 |
| `instruction_type` | string | `READ` / `WRITE` / `EXEC` / `RESPOND` 等 |
| `instruction_category` | string | instruction category |
| `trustworthiness` | level | 当前 instruction 可信度 |
| `confidentiality` | level | 当前 instruction 敏感度 |
| `prop_trustworthiness` | level | 传播后的可信度 |
| `prop_confidentiality` | level | 传播后的敏感度 |
| `confidence` | level | parser 置信度 |
| `authority` | string | authority label |
| `risk` | level | `LOW` / `UNKNOWN` / `HIGH` |
| `reversible` | boolean | 是否可回退 |
| `destructive` | boolean | 是否破坏性 |
| `approval_required` | boolean | parser 是否标记需要审批 |
| `review_required` | boolean | parser 是否标记需要复核 |
| `tags` | string array | parser / security metadata 标签 |
| `arg_total_str_len` | number | tool arguments 中字符串总长度 |
| `arg_text_upper` | string | tool arguments JSON 的大写文本视图 |
| `has_external_url` | boolean | 参数中是否包含 `http://` 或 `https://` |
| `path_hint` | string | 主要路径参数，大写 |
| `path_basename` | string | 主要路径 basename，大写 |
| `path_dirname` | string | 主要路径 dirname，大写 |
| `direct_target_basenames` | string array | write/edit 等直接目标文件名 |
| `exec_path_tokens` | string array | exec parser 提取的路径 token |
| `exec_write_targets` | string array | exec parser 推断的写入目标 |
| `exec_write_target_basenames` | string array | exec/process 推断写入目标文件名 |
| `custom_io_kind` | string | parser custom 中的 io kind |
| `custom_flow_role` | string | parser custom 中的 flow role |
| `custom_taint_role` | string | parser custom 中的 taint role |

Level 顺序：

```text
LOW < UNKNOWN < HIGH
```

如果用户说“至少中等”，V1 中用 `ge ... UNKNOWN`。如果用户说“高风险”，用 `risk == HIGH` 或 `risk in ["HIGH", "CRITICAL"]`，但当前 kernel 核心 level 主要是 `LOW/UNKNOWN/HIGH`。

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

`on_missing` 可选值：

| 值 | 行为 |
| --- | --- |
| `validation_error` | 安装/启用前必须确认 kernel 已能提供该字段 |
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
- 字段应由 kernel 写到当前 instruction 的 `security_type.custom.policy_metadata`。
- policy 侧会把 `policy_metadata` 扁平合并进当前 tool-call context。
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
```

`var` 必须引用 built-in metadata 或 `required_metadata.field`。

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
{ "ge":  [{ "var": "confidence" }, { "const": "UNKNOWN" }] }
{ "lt":  [{ "var": "risk" }, { "const": "HIGH" }] }
{ "le":  [{ "var": "trustworthiness" }, { "const": "LOW" }] }
```

字段缺失时，比较运算不应触发。对于 required metadata，compiler 会根据 `on_missing` 自动加保护条件。

### 4.4 Membership and Text Operators

```json
{ "in": [{ "var": "action" }, { "const": ["SEND", "BROADCAST"] }] }
{ "not_in": [{ "var": "path_basename" }, { "const": ["README.MD"] }] }
{ "contains": [{ "var": "arg_text_upper" }, { "const": "TOKEN" }] }
{ "intersects": [{ "var": "tags" }, { "const": ["SECRET_LIKE", "HIGH_RISK"] }] }
```

约定：

- 对关键词匹配，优先用 `arg_text_upper`，常量也写大写。
- 对 enum/action，常量写大写。
- 多关键词用 `any + contains`。

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
              "in": [
                { "var": "action" },
                { "const": ["ACT", "UPLOAD", "DIALOG"] }
              ]
            },
            {
              "any": [
                { "contains": [{ "var": "arg_text_upper" }, { "const": "PASSWORD" }] },
                { "contains": [{ "var": "arg_text_upper" }, { "const": "TOKEN" }] },
                { "contains": [{ "var": "arg_text_upper" }, { "const": "OPENCLAW" }] },
                { "contains": [{ "var": "arg_text_upper" }, { "const": "LITELLM_CONFIG" }] }
              ]
            }
          ]
        }
      }
    }
  ],
  "required_metadata": []
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
        "kind": "llm_lowering",
        "paths": ["to", "cc", "bcc"]
      },
      "normalization": "Count recipients whose email domain is outside the configured organization domains.",
      "on_missing": "validation_error",
      "required_for_rules": ["USER-EMAIL-EXTERNAL-RECIPIENTS-001"]
    }
  ]
}
```

## 7. Agent Translation Procedure

1. 判断是否能用 V1 表达：
   - 只看当前 tool call -> 继续。
   - 依赖历史 source/sink/taint/未来动作 -> V1 不支持。
2. 选择 selector：
   - 明确工具名时写 `selector.tools`。
   - 泛执行类动作可写 `selector.instruction_types: ["EXEC"]`。
3. 列出条件：
   - 阈值 -> `gt/ge/lt/le`
   - 动作枚举 -> `in`
   - 关键词 -> `contains`
   - 多条件同时满足 -> `all`
   - 多条件任一满足 -> `any`
4. 检查每个 `var`：
   - built-in metadata 里有 -> 直接使用。
   - built-in metadata 里没有 -> 必须加入 `required_metadata`。
5. 写清楚 `on_missing`：
   - 不确定 kernel 是否已有字段 -> `validation_error`
   - 字段缺失时不触发即可 -> `no_match`
   - 缺字段本身危险 -> `fail_closed`
6. 输出纯 JSON：
   - 不要 Markdown。
   - 不要注释。
   - 不要省略必填字段。

## 8. Common Mistakes

- 生成 `kind: "unary"`：V1 必须是 `unary_tool_call`。
- 生成 relational/source/sink 结构：V1 不支持。
- 在 predicate 中使用未声明的业务字段。
- 在 `required_metadata.field` 中覆盖 built-in 字段，例如 `risk`、`tool_name`。
- 让新 metadata 修改核心安全字段。新 metadata 只能补充业务语义。
- 把 `policy_rule_ir.json` 直接写入 `unary_gate_rules.json` 或 `user_unary_gate_rules.json`。
- 对 `arg_text_upper` 使用小写关键词，导致匹配不稳定。
- 对缺失字段不写 `on_missing`。
