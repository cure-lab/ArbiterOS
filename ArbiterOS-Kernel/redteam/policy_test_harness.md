# Policy Test Harness

**做什么**：用 JSON 描述「历史步骤 + 本轮 assistant 响应」，harness 解析成 instruction，再跑 user-approval 预处理与 policy 检查。

**怎么跑**：

```bash
cd <REPO_ROOT>
uv run python -m arbiteros_kernel.policy_test_harness redteam/<spec.json>
# 需要打印 instruction 时：--dump-instructions
```

**注意**：如果你直接手跑 harness，传入的 case 文件里路径必须已经适配当前机器。  
`redteam/_automation/run_cases.py` 会在运行前自动渲染 case，因此跨机器批量测试时，优先使用 runner。

---

## 1. 根上的三个字段

```json
{
  "trace_id": "可选",
  "prior": [],
  "current": { "role": "assistant", "content": "…", "tag": {} }
}
```


| 字段         | 目的                                                                                                                   |
| ---------- | -------------------------------------------------------------------------------------------------------------------- |
| `trace_id` | 可选。这次回放的标识，默认 `"policy-test-trace"`。                                                                                 |
| `prior`    | **先于 `current` 发生的步骤**，按时间顺序：多轮 `assistant`、`kind: "tool"` 等，用来还原「过本轮 policy 之前」的历史。                                 |
| `current`  | **本轮要过 policy 的那一条 assistant 消息**（通常是你关心的最后一条模型输出）。必须带 `tag`；`current.tag` 里不能写 §3 里那三项（只写在 `prior`），否则 harness 会报错。 |


instruction 不在 JSON 里手写，全部由 harness 从消息解析得到。

---

## 2. `prior` 里每一步：`kind: "assistant"` 与 `kind: "tool"`

### 2.1 纯文本（只有 `content`，没有 `tool_calls`）

**目的**：表示模型这一轮**只说了话**，没有发起工具。

**和 `tag` 的常见关系**：这一类里**最常**需要打 `**policy_confirmation_ask`**（问是否保护等问句）、以及 `policy_protected` 相关语义（按你的设计打在对应 instruction 上）；也就是 **§3 里和「话术 / 策略结果」强相关**的两项，一般出现在 **2.1** 这种纯文案 assistant 步上。

**规则（harness 会校验）**：

- `content` 必须是 **JSON 字符串**，解析后 **同时包含**三个键：`category`、`topic`、`content`（与线上 cognitive 结构一致）。
- **不要**在同一条 `message` 里再写 `tool_calls`；要先说话再调工具 → 拆成 **两条** `assistant`。

示例：

```json
{
  "kind": "assistant",
  "message": {
    "role": "assistant",
    "content": "{\"category\":\"COGNITIVE_CORE__RESPOND\",\"topic\":\"demo\",\"content\":\"Hello.\"}",
    "tag": {}
  }
}
```

### 2.2 纯工具调用（只有 `tool_calls`，没有用户可见的 `content`）

**目的**：表示模型这一轮**只发起了工具**，还没有工具返回。

**和 `tag` 的常见关系**：这一类里**一般只会**出现 `**user_approved`**（用户放行后，模型再发起的那次工具调用等）；**通常不会**在这里打 `policy_confirmation_ask` / `policy_protected`（那两样更常落在 **2.1** 纯文本上）。

**规则（harness 会校验）**：

- 每个 `tool_calls[]` 里 `function.arguments` 解析成 JSON 后，**必须**带 `reference_tool_id`（数组；无上游依赖写 `[]`）。
- 同上，**不要**和一段非空 `content` 写在同一条里。

示例：

```json
{
  "kind": "assistant",
  "message": {
    "role": "assistant",
    "tool_calls": [
      {
        "id": "call_read_demo",
        "type": "function",
        "function": {
          "name": "read",
          "arguments": "{\"file_path\":\"/tmp/x\",\"reference_tool_id\":[]}"
        }
      }
    ],
    "tag": {}
  }
}
```

### 2.3 `kind: "tool"` — 工具执行结果

**目的**：接在「assistant 已发起对应 `tool_call_id`」之后，表示**工具已跑完**，返回体不是模型自然语言。

**和 `tag` 的常见关系**：`**tag` 一般都是空的 `{}`**；需要打 `policy_protected` 等标记时，更常见是在 **2.1** 或按链路打在 **2.2**，而不是 tool 结果这一步（除非你在刻意复现某种边界回放）。

**规则（harness 会校验）**：

- 返回体写在 `result`（不要用 `content`，避免和 assistant 文本混淆；旧写法 `content` 仍兼容）。
- `arguments` 里同样**必须**含 `reference_tool_id`（可 `[]`）。

```json
{
  "kind": "tool",
  "tool_call_id": "call_read_demo",
  "tool_name": "read",
  "arguments": { "file_path": "/tmp/x", "reference_tool_id": [] },
  "result": "工具返回的字符串",
  "tag": {}
}
```

---

## 3. `tag`：三个可写进 instruction 的字段

**目的**：解析出来的 instruction 默认不带线上 callback 才写的标记；`tag` 把键值合并到「本步新产生的最后一条 instruction」上。无标记写 `"tag": {}`。

**写在哪儿**：`assistant` 放在 `message` 里，与 `role` / `content` / `tool_calls` 同级；`kind: "tool"` 放在该步最外层，与 `kind` 同级。

`reference_tool_id` 不写在 `tag` 里，写在工具的 `arguments` 里（见 §2）。

下面三项只应出现在 `prior` 的 `tag` 里；不要写在 `current.tag`。


| 字段                        | 干什么                                                                    |
| ------------------------- | ---------------------------------------------------------------------- |
| `policy_confirmation_ask` | 标记「这一条是在问用户是否接受保护」类话术；user-approval 逻辑会把它从「最近若干条有效 instruction」里排除。    |
| `user_approved`           | 为 `true` 时表示用户放行；与 `reference_tool_id` 等一起在 user-approval 预处理里参与提升/传播。 |
| `policy_protected`        | 非空字符串时表示该条 instruction 走了「被 policy 保护/拦截」的路径；参与排除与后续逻辑。                |


**先后关系（instruction 链上，harness 会校验）**：

1. **先**出现一条带 `policy_confirmation_ask: true` 的 instruction（问句多在 **2.1** 纯文本上打 `tag`）。
2. **紧挨着的下一条** instruction **必须二选一**：带 `user_approved: true`（常见于 **2.2** 里放行后再发起的 tool），或带 **非空字符串** 的 `policy_protected`。
3. **同一条** instruction **不要**同时打 `policy_confirmation_ask` 与 `user_approved` / `policy_protected`（结果写在**下一条**）。
4. **整条 instruction 列表的最后一条**不能单独只有 `policy_confirmation_ask`（后面必须还有一条承接 2）。

---

## 4. 完整示例

示例文件：

- [`policy_test_sample.json`](policy_test_sample.json)

如果你需要的是“怎么按规则写一个新 case”，而不是“harness 字段定义”，请继续看：

- [`case_json_construction_guide.md`](case_json_construction_guide.md)

**说明**：`write` 等工具在 `arguments` 的 JSON 里可能也有一个叫 `content` 的参数，那是**工具参数**，和 assistant 外层的 `content` JSON 字符串不是一回事。
