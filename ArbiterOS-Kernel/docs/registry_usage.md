# Registry

The Registry is a set of YAML rule files that the ArbiterOS Kernel consults when classifying instructions. It determines the [`TRUSTWORTHINESS`](agent_insturctions_design.md#trustworthiness--source-reliability), [`CONFIDENTIALITY`](agent_insturctions_design.md#confidentiality--data-sensitivity), [`RISK`](agent_insturctions_design.md#risk--execution-danger), and instruction type (`READ`/`WRITE`/`EXEC`) of each instruction based on the file paths and executables involved.

## Two-Layer Architecture

The registry operates as two stacked layers:

| Layer | Location | Purpose |
|-------|----------|---------|
| **Source** | `arbiteros_kernel/instruction_parsing/linux_registry/` | Default rules shipped with the package. Read-only; never modified at runtime. |
| **User** | `~/.arbiteros/instruction_parsing/linux_registry/` | User-specific overrides and automatically tracked entries. Created on first run. |

**Lookup order:** The user layer is always checked first. If no rule matches, the source layer is used as fallback. This lets users extend or override built-in rules without touching the shipped files.

The user registry files are created automatically on first run with empty rule sets, so they are safe to edit at any time.

> **Override location:** Set the `ARBITEROS_USER_REGISTRY_DIR` environment variable to use a different directory for the user layer (useful for testing or alternative deployments).

---

## Registry Files

### `exe_registry.yaml` — Instruction Type Classification

Maps executables and shell commands to [instruction types](agent_insturctions_design.md#2-actuation): `READ`, `WRITE`, or `EXEC`.

```yaml
EXEC:
  - python
  - docker
  - git push

WRITE:
  - cp
  - git commit

READ:
  - cat
  - git log
```

**Lookup priority within each layer:** `EXEC` > `WRITE` > `READ`. If no match is found in either layer, the default is `EXEC`.

Entries can be either a bare executable name (e.g. `rm`) or a two-token executable+subcommand prefix (e.g. `git clean`).

---

### `exe_risk.yaml` — Execution Risk Classification

Classifies executables as `HIGH` or `LOW` risk. Unmatched entries default to `UNKNOWN`.

```yaml
HIGH:
  - rm
  - shutdown
  - git clean

LOW:
  - ls
  - cat
  - git log
```

`HIGH` risk indicates the instruction is known to cause irreversible or destructive effects and requires explicit approval before execution. `LOW` risk means the instruction is known to be safe and read-only.

---

### `file_trustworthiness.yaml` — File Trustworthiness Classification

Classifies file paths as `HIGH` or `LOW` trustworthiness. Unmatched paths default to `UNKNOWN`.

```yaml
HIGH:
  - /usr/bin/*
  - /etc/**

LOW:
  - ~/Downloads/**
  - /tmp/**
  - "https://*"
```

**Resolution rule (worst-case wins):** `LOW` is checked first across all matching paths. If any path matches `LOW`, the result is `LOW`. This ensures untrusted content is never silently promoted to trusted.

---

### `file_confidentiality.yaml` — File Confidentiality Classification

Classifies file paths by data sensitivity as `HIGH` or `LOW`. Unmatched paths default to `UNKNOWN`.

```yaml
HIGH:
  - ~/.ssh/*
  - "*.pem"
  - .env

LOW:
  - /tmp/*
  - /usr/share/**
```

**Resolution rule (highest wins):** `HIGH` is checked first. If any path matches `HIGH`, the result is `HIGH`. This ensures sensitive data is never silently downgraded.

---

## Pattern Syntax

All file path patterns follow glob conventions:

| Pattern | Matches |
|---------|---------|
| `/etc/*` | Direct children of `/etc/` only |
| `/etc/**` | All descendants of `/etc/` recursively |
| `~/Downloads/**` | All descendants of `~/Downloads/`, `~` expands to home directory |
| `"*.pem"` | Any file with a `.pem` extension, matched against the filename only |
| `"https://*"` | Any URL with the `https://` scheme |

**Supported path formats:** Only absolute paths (e.g. `/etc/shadow`) and home-relative paths starting with `~` (e.g. `~/Downloads/**`) are supported. Bare relative paths (e.g. `project/data.json`) cannot be matched reliably without knowing the working directory and are ignored.

---

## Automatic Taint Tracking

When the agent writes a file, the kernel automatically records the file's path in the user registry with its effective taint labels. This means subsequent reads of that file will resolve to the same taint level without any manual configuration. For a full explanation of taint fields and their semantics, see [Safety & Trust Metadata](agent_insturctions_design.md#2-safety--trust-metadata).

**Example:** The agent fetches content from a web URL (`TRUSTWORTHINESS: LOW`) and writes it to `/home/user/project/data.json`. The kernel records `/home/user/project/data.json` as `LOW` trustworthiness in the user registry. The next time the agent reads that file, it is classified as `LOW` trustworthiness automatically.

**Effective label resolution:** The stored level is determined by traversing the data-dependency graph of all upstream nodes and taking the worst-case across all reachable dependencies:
- **Confidentiality:** higher level wins (`LOW < UNKNOWN < HIGH`)
- **Trustworthiness:** lower level wins (`LOW < UNKNOWN < HIGH`)

This ensures that a file's inherent sensitivity (e.g. a `.pem` file is always `HIGH` confidentiality in the source registry) is never downgraded by a low-taint write operation.

Changes to the user registry are flushed to disk automatically when the process exits.

---

## User Customization

To customize classification rules, edit the files in `~/.arbiteros/instruction_parsing/linux_registry/`. Changes take effect on the next process start.

**Common customizations:**

Add a directory as trusted (e.g. a local mirror of a verified dataset):
```yaml
# file_trustworthiness.yaml
HIGH:
  - ~/verified-datasets/**
```

Mark a custom script as high risk:
```yaml
# exe_risk.yaml
HIGH:
  - ~/scripts/deploy.sh
```

Classify a custom executable as a read-only operation:
```yaml
# exe_registry.yaml
READ:
  - /usr/local/bin/my-query-tool
```

User rules take priority over source rules, so they can also be used to downgrade a source-layer classification — for example, marking a path as `LOW` confidentiality if you know it contains only public data.
