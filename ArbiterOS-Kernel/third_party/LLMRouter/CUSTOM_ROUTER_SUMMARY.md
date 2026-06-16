# Custom Router Plugin System - Implementation Summary

## 🎯 Implementation Goal

Enable users to **add custom router implementations without modifying the core codebase**.

## ✅ Completed Work

### 1. Core Plugin System

**New File:** `llmrouter/plugin_system.py`

**Features:**
- 🔍 Automatic discovery of custom routers
- ✅ Validation of router implementations
- 📦 Registration into the system
- 🔧 Support for multiple discovery strategies

**Key Classes:**
```python
class PluginRegistry:
    - discover_plugins(plugin_dir, verbose)  # Discover plugins
    - _load_router_from_directory()          # Load router
    - _validate_router_class()               # Validate interface
    - register_to_dict()                     # Register to dictionary
```

### 2. CLI Integration

**Modified Files:**
- `llmrouter/cli/router_inference.py` (inference)
- `llmrouter/cli/router_train.py` (training)

**Modifications:** Added plugin discovery and registration code

```python
# ============================================================================
# Plugin System Integration
# ============================================================================
from llmrouter.plugin_system import discover_and_register_plugins

plugin_registry = discover_and_register_plugins(verbose=False)

for router_name, router_class in plugin_registry.discovered_routers.items():
    ROUTER_REGISTRY[router_name] = router_class
# ============================================================================
```

### 3. Example Routers

#### RandomRouter (Simple Example)
- 📁 `custom_routers/randomrouter/`
- Function: Randomly select LLM
- Use: Baseline comparison

#### ThresholdRouter (Advanced Example)
- 📁 `custom_routers/thresholdrouter/`
- Function: Route based on difficulty estimation
- Features: Complete training pipeline

### 4. Complete Documentation

- 📖 `custom_routers/README.md` - Quick start guide

---

## 📂 Complete File Structure

```
LLMRouter/
│
├── llmrouter/
│   ├── plugin_system.py              ⭐ NEW - Plugin system core
│   ├── cli/
│   │   ├── router_inference.py       🔧 MODIFIED - Integrated plugins
│   │   └── router_train.py           🔧 MODIFIED - Integrated plugins
│   └── models/
│       └── meta_router.py            Existing base class
│
├── custom_routers/                   ⭐ NEW - Custom router directory
│   ├── __init__.py
│   ├── README.md                     ⭐ NEW - Usage guide
│   │
│   ├── randomrouter/                 ⭐ NEW - Example 1
│   │   ├── __init__.py
│   │   ├── router.py                 Random routing implementation
│   │   ├── trainer.py                Trainer (no-op)
│   │   └── config.yaml               Config example
│   │
│   └── thresholdrouter/              ⭐ NEW - Example 2
│       ├── __init__.py
│       ├── router.py                 Difficulty estimation router
│       ├── trainer.py                Complete trainer
│       └── config.yaml               (optional)
│
└── tests/test_plugin_system.py       ⭐ NEW - Test script
```

---

## 🔑 Core Design

### 1. Plugin Discovery Mechanism

**Automatic Search Paths:**
```
1. ./custom_routers/          (project directory, recommended)
2. ~/.llmrouter/plugins/      (user directory)
3. $LLMROUTER_PLUGINS         (environment variable)
```

**Discovery Strategy:**
- Scan subdirectories
- Look for `router.py` or `model.py`
- Find classes ending with `Router`
- Optionally load `Trainer` class from `trainer.py`

### 2. Router Interface Requirements

**Must Implement:**
```python
class YourRouter(MetaRouter):
    def __init__(self, yaml_path: str):
        super().__init__(model=..., yaml_path=yaml_path)

    def route_single(self, query_input: dict) -> dict:
        # Return dict containing 'model_name'
        pass

    def route_batch(self, batch: list) -> list:
        # Return list of results
        pass
```

**Optional (for training support):**
```python
class YourRouterTrainer(BaseTrainer):
    def train(self) -> None:
        # Training logic
        pass
```

### 3. Zero-Invasive Integration

**Principle:**
- Use Python's dynamic imports
- Register to existing `ROUTER_REGISTRY` at runtime
- Zero modifications to existing code (only integration code added)

---

## 💻 Usage Examples

### Creating a Custom Router

```python
# custom_routers/my_router/router.py
from llmrouter.models.meta_router import MetaRouter
import torch.nn as nn

class MyRouter(MetaRouter):
    def __init__(self, yaml_path: str):
        model = nn.Identity()
        super().__init__(model=model, yaml_path=yaml_path)
        self.llm_names = list(self.llm_data.keys())

    def route_single(self, query_input: dict) -> dict:
        # Simple example: route based on query length
        query = query_input['query']

        if len(query) < 50:
            selected = self.llm_names[0]  # Short query -> small model
        else:
            selected = self.llm_names[-1]  # Long query -> large model

        return {
            "query": query,
            "model_name": selected,
            "predicted_llm": selected,
        }

    def route_batch(self, batch: list) -> list:
        return [self.route_single(q) for q in batch]
```

### Using Custom Router

```bash
# Inference
llmrouter infer --router my_router \
  --config custom_routers/my_router/config.yaml \
  --query "What is machine learning?"

# Training (if has trainer)
llmrouter train --router my_router \
  --config custom_routers/my_router/config.yaml

# List all routers
llmrouter list-routers
```

---

## 🎨 Design Pattern Examples

### 1. Rule-Based Routing
```python
def route_single(self, query_input):
    query = query_input['query'].lower()

    if 'code' in query:
        return {"model_name": "code-specialist"}
    elif len(query) < 50:
        return {"model_name": "small-fast-model"}
    else:
        return {"model_name": "large-model"}
```

### 2. Embedding-Based Routing
```python
from llmrouter.utils import get_longformer_embedding

def route_single(self, query_input):
    embedding = get_longformer_embedding(query_input['query'])
    similarity = self._compute_similarity(embedding)
    best_model = max(similarity, key=similarity.get)
    return {"model_name": best_model}
```

### 3. Cost-Optimized Routing
```python
def route_single(self, query_input):
    difficulty = self._estimate_difficulty(query_input)

    # Select cheapest model that can handle the difficulty
    for model in sorted(self.llm_data.items(), key=lambda x: x[1]['cost']):
        if model[1]['capability'] >= difficulty:
            return {"model_name": model[0]}
```

### 4. Ensemble Routing
```python
def route_single(self, query_input):
    # Get predictions from multiple sub-routers
    votes = [r.route_single(query_input) for r in self.sub_routers]

    # Majority voting
    from collections import Counter
    model_counts = Counter(v['model_name'] for v in votes)
    best_model = model_counts.most_common(1)[0][0]

    return {"model_name": best_model}
```

---

## 🧪 Testing Methods

### 1. Unit Testing
```python
from custom_routers.my_router import MyRouter

router = MyRouter("custom_routers/my_router/config.yaml")
result = router.route_single({"query": "test"})
assert "model_name" in result
```

### 2. Integration Testing
```bash
# Route-only test
llmrouter infer --router my_router \
  --config config.yaml \
  --query "test" \
  --route-only

# Complete test (including API call)
llmrouter infer --router my_router \
  --config config.yaml \
  --query "test" \
  --verbose
```

### 3. Debug Mode
```python
from llmrouter.plugin_system import discover_and_register_plugins

registry = discover_and_register_plugins(
    plugin_dirs=['custom_routers'],
    verbose=True  # Show detailed discovery process
)
```

---

## 🌟 Key Advantages

### 1. Zero-Invasive
- ✅ No core code modifications
- ✅ Only integration code added (5-10 lines)
- ✅ Existing functionality completely unaffected

### 2. Automation
- ✅ Automatic discovery
- ✅ Automatic validation
- ✅ Automatic registration

### 3. Flexibility
- ✅ Multiple discovery paths supported
- ✅ Both training and inference supported
- ✅ Complex router implementations supported

### 4. Ease of Use
- ✅ Same usage as built-in routers
- ✅ Rich examples and documentation
- ✅ Clear error messages

---

## 📊 Code Statistics

### New Code
- `llmrouter/plugin_system.py`: ~400 lines
- CLI integration code: ~30 lines (total)
- Example routers: ~600 lines
- Documentation: ~1000 lines

### Modified Code
- `router_inference.py`: +15 lines
- `router_train.py`: +15 lines

### Total
- New: ~2000 lines
- Modified: ~30 lines
- Invasiveness: **Very Low**

---

## 🚀 Usage Flow Summary

```bash
# Step 1: Create router directory
mkdir -p custom_routers/awesome_router

# Step 2: Implement router
cat > custom_routers/awesome_router/router.py << 'EOF'
from llmrouter.models.meta_router import MetaRouter
import torch.nn as nn

class AwesomeRouter(MetaRouter):
    def __init__(self, yaml_path: str):
        super().__init__(model=nn.Identity(), yaml_path=yaml_path)
        self.llm_names = list(self.llm_data.keys())

    def route_single(self, query_input: dict) -> dict:
        # Your routing logic
        return {
            "query": query_input['query'],
            "model_name": self.llm_names[0],
            "predicted_llm": self.llm_names[0],
        }

    def route_batch(self, batch: list) -> list:
        return [self.route_single(q) for q in batch]
EOF

# Step 3: Create configuration
cat > custom_routers/awesome_router/config.yaml << 'EOF'
data_path:
  llm_data: 'data/example_data/llm_candidates/default_llm.json'
api_endpoint: 'https://integrate.api.nvidia.com/v1'
EOF

# Step 4: Use it!
llmrouter infer --router awesome_router \
  --config custom_routers/awesome_router/config.yaml \
  --query "Hello, world!"
```

---

## 📚 Documentation Index

1. **Quick Start**: `custom_routers/README.md`
2. **API Documentation**: Inline documentation in `llmrouter/plugin_system.py`

---

## 🎓 Recommended Learning Path

1. 📖 Read `custom_routers/README.md`
2. 🔍 Check `RandomRouter` example (simplest)
3. 💡 Understand `ThresholdRouter` example (trainable)
4. 🛠️ Create your own simple router
5. 📈 Gradually add complex features
6. 🚀 Share with the community

---

## ✅ Verification Checklist

- [x] Plugin system core implementation
- [x] CLI integration
- [x] Simple example router (RandomRouter)
- [x] Advanced example router (ThresholdRouter)
- [x] Complete documentation
- [x] Usage guide
- [x] Test script
- [x] Zero-invasive verification

---

## 🎉 Summary

With this plugin system, users can now:

1. ✅ **Easy Extension** - Create custom routers in minutes
2. ✅ **Seamless Integration** - Usage identical to built-in routers
3. ✅ **Flexible Deployment** - Multiple discovery paths and configuration
4. ✅ **Rapid Iteration** - No core code changes, quick experimentation

**Core Value:** Making LLMRouter a truly extensible framework! 🚀

---

## 📞 Support

- GitHub Issues: https://github.com/ulab-uiuc/LLMRouter/issues
- Example Code: `custom_routers/`
- Detailed Docs: `docs/CUSTOM_ROUTERS.md`
