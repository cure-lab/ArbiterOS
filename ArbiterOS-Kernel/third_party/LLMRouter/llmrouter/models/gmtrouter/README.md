# GMTRouter - Graph-based Multi-Turn Personalized Router

## Paper Reference

- **[GMTRouter: Personalized LLM Router over Multi-turn User Interactions](https://arxiv.org/abs/2511.08590)**
  - Xie, E., et al. (2025). arXiv:2511.08590.
  - Proposes graph-based personalized routing with user preference learning.

## ⚠️ Important Notice

**GMTRouter uses a fundamentally different architecture and data format from other routers in LLMRouter.**

- **Original Repository**: https://github.com/ulab-uiuc/GMTRouter
- **Training Status**: ✅ **Fully integrated into LLMRouter** - train and infer using LLMRouter CLI
- **Data Format**: Special JSONL format with embeddings and ratings (see below)

## Overview

GMTRouter is a personalized LLM router designed for multi-turn conversations. It uses **heterogeneous graph neural networks (HeteroGNN)** to learn user preferences and optimize model selection across conversation sessions.

### Key Differences from Other Routers

| Aspect | GMTRouter | Other Routers (KNN, MLP, etc.) |
|--------|-----------|-------------------------------|
| **Architecture** | Heterogeneous GNN with 5 node types | Single model (classifier, ranker) |
| **Data Format** | Special JSONL with embeddings & ratings | Standard query-response pairs |
| **Learning** | Pairwise preference learning | Classification/ranking |
| **Personalization** | Per-user preference embeddings | No personalization |
| **Multi-turn** | Built-in conversation tracking | Single-turn or basic history |
| **Graph Structure** | 21 edge types, 5 node types | No graph structure |

## Architecture

### Heterogeneous Graph Structure

GMTRouter models routing as a **heterogeneous graph** with 5 node types:

1. **User Nodes**: Learned user preference embeddings (initialized as zeros, updated during training)
2. **Session Nodes**: Conversation session representations (track multi-turn interactions)
3. **Query Nodes**: Query embeddings from Pre-trained Language Models (PLMs)
4. **LLM Nodes**: Model embeddings from PLMs
5. **Response Nodes**: Response quality representations (rating-scaled)

### 21 Edge Types

The graph includes 21 directed edge types modeling relationships:

- **User-Session**: `own`, `owned_by`
- **Query-Response**: `answered_by`, `answered_to`
- **Temporal**: `next`, `prev` (for sessions and queries)
- **LLM Relations**: `receive`, `generate`, `response_to`
- And 13 more types...

### Model Components

1. **HeteroGNN**: Uses HGT (Heterogeneous Graph Transformer) layers
   - 2 layers for single-turn tasks
   - 3 layers for multi-turn conversations
   - Aggregates information across heterogeneous node types

2. **PreferencePredictor**: Cross-attention mechanism
   - Scores LLM candidates based on user embeddings and query context
   - Outputs preference scores for each model

## Data Format

### JSONL Structure

GMTRouter requires a **special JSONL format** (NOT standard LLMRouter format):

```json
{
  "judge": "user_001",
  "model": "gpt-4",
  "question_id": "12345",
  "turn": 1,
  "conversation": [
    {
      "query": "What is machine learning?",
      "query_emb": [0.123, -0.456, 0.789, ...],
      "response": "Machine learning is a subset of AI...",
      "rating": 4.5
    }
  ],
  "model_emb": [0.234, -0.567, 0.891, ...],
  "encoder": "sentence-transformers/all-mpnet-base-v2"
}
```

### Field Descriptions

| Field | Type | Description |
|-------|------|-------------|
| `judge` | string | User identifier (e.g., "user_001") |
| `model` | string | LLM model name (e.g., "gpt-4", "claude-2") |
| `question_id` | string | Unique question/task identifier |
| `turn` | int | Turn number in multi-turn conversation (1, 2, 3, ...) |
| `conversation` | array | List of conversation turns (see below) |
| `model_emb` | array | LLM embedding vector from PLM |
| `encoder` | string | PLM model used for embeddings (optional) |

### Conversation Turn Structure

Each turn in the `conversation` array contains:

```json
{
  "query": "Query text",
  "query_emb": [0.1, 0.2, ...],    // Query embedding from PLM
  "response": "Response text",      // Optional
  "rating": 4.5                     // Quality score (0-5 or 0-1)
}
```

## Data Preparation

### Step 1: Download Dataset

Download the GMTRouter dataset from Google Drive:

```bash
# Dataset link (check GMTRouter repository for latest link)
# https://drive.google.com/file/d/[GMTRouter_dataset_id]

# Download to your local machine
wget "https://drive.google.com/uc?export=download&id=[dataset_id]" -O GMTRouter_dataset.tar.gz
```

### Step 2: Extract Data

```bash
# Extract the archive
tar -xzvf GMTRouter_dataset.tar.gz

# Move data folder to repository root
mv data ./
```

### Step 3: Verify Data Structure

After extraction, you should have:

```
./data/
├── chatbot_arena/
│   ├── training_set.jsonl
│   ├── valid_set.jsonl
│   └── test_set.jsonl
├── gsm8k/
│   ├── training_set.jsonl
│   ├── valid_set.jsonl
│   └── test_set.jsonl
├── mmlu/
│   ├── training_set.jsonl
│   ├── valid_set.jsonl
│   └── test_set.jsonl
└── mt_bench/
    ├── training_set.jsonl
    ├── valid_set.jsonl
    └── test_set.jsonl
```

### Supported Datasets

- **chatbot_arena**: Real user preferences from Chatbot Arena
- **gsm8k**: Grade school math problems
- **mmlu**: Massive Multitask Language Understanding benchmark
- **mt_bench**: Multi-turn conversation benchmark

## CLI Usage

GMTRouter can be used via the `llmrouter` command-line interface:

### Training

```bash
# Train the GMTRouter (GPU recommended)
llmrouter train --router gmtrouter --config configs/model_config_train/gmtrouter.yaml --device cuda

# Train with quiet mode
llmrouter train --router gmtrouter --config configs/model_config_train/gmtrouter.yaml --device cuda --quiet
```

### Inference

```bash
# Route a single query
llmrouter infer --router gmtrouter --config configs/model_config_test/gmtrouter.yaml \
    --query "Explain quantum computing in simple terms"

# Route queries from a file
llmrouter infer --router gmtrouter --config configs/model_config_test/gmtrouter.yaml \
    --input queries.jsonl --output results.json

# Route only (without calling LLM API)
llmrouter infer --router gmtrouter --config configs/model_config_test/gmtrouter.yaml \
    --query "Solve this calculus problem" --route-only
```

### Interactive Chat

```bash
# Launch chat interface
llmrouter chat --router gmtrouter --config configs/model_config_test/gmtrouter.yaml

# Launch with custom port
llmrouter chat --router gmtrouter --config configs/model_config_test/gmtrouter.yaml --port 8080

# Create a public shareable link
llmrouter chat --router gmtrouter --config configs/model_config_test/gmtrouter.yaml --share
```

---

## Training GMTRouter

### ✅ Training Fully Integrated into LLMRouter

GMTRouter training is now **fully integrated** into LLMRouter. You can train using the standard LLMRouter CLI:

```bash
# Train GMTRouter using LLMRouter CLI
llmrouter train --router gmtrouter --config configs/model_config_train/gmtrouter.yaml
```

### Training Configuration

Edit `configs/model_config_train/gmtrouter.yaml`:

```yaml
dataset:
  name: mt_bench          # Choose: chatbot_arena, gsm8k, mmlu, mt_bench
  path: ./data

train:
  epochs: 350             # Training epochs
  lr: 5e-4                # Learning rate (5e-4 recommended)
  prediction_count: 256   # Pairwise predictions per batch
  objective: auc          # Metric: auc or accuracy
  binary: true            # Pairwise comparison learning
  eval_every: 5           # Validation frequency
  seed: 136               # Random seed

gmt_config:
  num_gnn_layers: 2       # HGT layers (2 for single-turn, 3 for multi-turn)
  hidden_dim: 128         # Hidden dimension for node embeddings
  dropout: 0.1            # Dropout rate for regularization
  personalization: true   # Enable user preference learning

checkpoint:
  root: ./models
  save_every: 25          # Checkpoint frequency

data_path:
  training_set: ./data/mt_bench/training_set.jsonl
  valid_set: ./data/mt_bench/valid_set.jsonl
  test_set: ./data/mt_bench/test_set.jsonl

model_path:
  save_model_path: ./saved_models/gmtrouter/gmtrouter.pt
  load_model_path: ./saved_models/gmtrouter/gmtrouter.pt
```

### What Happens During Training

1. **Data Loading**: Automatic format detection validates GMTRouter JSONL format
2. **Graph Construction**: Builds heterogeneous graph with 5 node types and 21 edge types
3. **Model Initialization**: Creates HeteroGNN + PreferencePredictor architecture
4. **Pairwise Learning**: Trains on pairwise comparisons (winner vs loser)
5. **Evaluation**: Validates on AUC or accuracy every N epochs
6. **Checkpointing**: Saves best model and regular checkpoints

### Training Output

```
======================================================================
GMTRouter Training
======================================================================
Loading training data from ./data/mt_bench/training_set.jsonl...
Detected format: gmtrouter
Building heterogeneous graph...
  - Users: 150, Sessions: 450, Queries: 1200, LLMs: 8, Responses: 1200
  - Edge types: 21
  - Pairwise comparisons: 3600

Training Configuration:
  Device: cuda
  Epochs: 350
  Learning Rate: 5e-4
  Hidden Dim: 128
  GNN Layers: 2
  Objective: auc
  Binary Classification: True

Epoch 5/350 - Train Loss: 0.4523, Train AUC: 0.7245 - Val Loss: 0.4012, Val AUC: 0.7856
  → Saved best model to ./saved_models/gmtrouter/gmtrouter.pt
...
Training completed!
Best AUC: 0.8934 at epoch 245
```

## Using GMTRouter in LLMRouter

### Inference Setup

```python
from llmrouter.models.gmtrouter import GMTRouter

# Initialize with test config
router = GMTRouter(yaml_path='configs/model_config_test/gmtrouter.yaml')
```

### Single Query Routing

```python
# Route with user context
query = {
    "query_text": "Explain quantum computing in simple terms",
    "user_id": "user_123",          # Required for personalization
    "session_id": "session_456",    # Optional
    "turn": 1,                       # Optional
    "conversation_history": []       # Optional: previous turns
}

result = router.route_single(query)
print(result)
# {
#   "model_name": "gpt-4",
#   "confidence": 0.87,
#   "user_preference": 0.92,
#   "reasoning": "Selected based on user user_123's learned preferences..."
# }
```

### Multi-Turn Conversation

```python
user_id = "user_789"
session_id = "session_123"

conversation = [
    "What is machine learning?",
    "How does it differ from deep learning?",
    "Can you give me a practical example?"
]

for turn, query_text in enumerate(conversation, start=1):
    query = {
        "query_text": query_text,
        "user_id": user_id,
        "session_id": session_id,
        "turn": turn
    }

    result = router.route_single(query)
    print(f"Turn {turn}: {result['model_name']} (confidence: {result['confidence']:.2f})")
```

### Batch Routing

```python
batch = [
    {"query_text": "Solve 2+2", "user_id": "user_001"},
    {"query_text": "Write a poem", "user_id": "user_001"},
    {"query_text": "Debug this code", "user_id": "user_002"}
]

results = router.route_batch(batch)
for q, r in zip(batch, results):
    print(f"{q['query_text']}: {r['model_name']}")
```

### Update User Feedback

```python
# Record user feedback to improve future routing
router.update_user_feedback(
    user_id="user_123",
    query="What is AI?",
    model="gpt-4",
    rating=4.5  # User rating (0-5 scale)
)
```

## Configuration Parameters

### GMTRouter-Specific (`gmt_config`)

- **`num_gnn_layers`** (int, default: `2`)
  - Number of HGT (Heterogeneous Graph Transformer) layers in HeteroGNN
  - Recommended: 2 layers for most tasks
  - Range: 2-4

- **`hidden_dim`** (int, default: `128`)
  - Hidden dimension for graph node embeddings
  - Range: 64-256

- **`dropout`** (float, default: `0.1`)
  - Dropout rate for regularization during training
  - Range: 0.0-0.3

- **`personalization`** (bool, default: `true`)
  - Enable user preference learning
  - When enabled, requires `user_id` field in routing queries
  - Learns per-user embeddings that evolve with interactions

### Training Parameters (`train`)

- **`epochs`** (int, default: `350`)
  - Number of training epochs
  - GMTRouter typically converges in 200-350 epochs

- **`lr`** (float, default: `5e-4`)
  - Learning rate for optimizer
  - Recommended: 5e-4 (works well for most datasets)

- **`prediction_count`** (int, default: `256`)
  - Number of pairwise preference predictions per training batch
  - Higher values provide more stable gradients but slower training

- **`objective`** (string, default: `"auc"`)
  - Training objective metric
  - Options: `"auc"` (Area Under Curve) or `"accuracy"`

- **`binary`** (bool, default: `true`)
  - Use pairwise preference learning (binary classification)
  - Recommended to keep as `true` for preference-based routing

- **`eval_every`** (int, default: `5`)
  - Validation frequency in epochs
  - Model is evaluated on validation set every N epochs

- **`seed`** (int, default: `136`)
  - Random seed for reproducibility
  - Ensures consistent results across training runs

## Advantages

1. **Personalization**: Learns individual user preferences over time
2. **Multi-Turn Awareness**: Explicitly models conversation context
3. **Rich Graph Structure**: 5 node types and 21 edge types capture complex relationships
4. **Preference Learning**: Pairwise comparison training mirrors human judgment
5. **Scalable**: Efficient graph operations handle many users/sessions
6. **Adaptive**: User embeddings continuously evolve with interactions

## Limitations

1. **Complex Setup**: Requires PyTorch Geometric and specific data format
2. **Cold Start**: New users without history get generic routing
3. **Data Requirements**: Needs user interaction data with ratings
4. **Training Complexity**: Must use original repository for training
5. **Memory**: Stores user/session embeddings (can grow large)
6. **Different from LLMRouter**: Special data format incompatible with other routers

## When to Use GMTRouter

### ✅ Ideal Use Cases

- **Personalized Chatbots**: Systems serving returning users
- **Multi-User Platforms**: Applications with distinct user profiles
- **Conversational AI**: Multi-turn dialogues building on context
- **Preference-Sensitive Tasks**: Routing depends on user taste (creative writing, recommendations)
- **Long-Term Interactions**: Users engage over weeks/months

### ❌ Not Recommended When

- **Anonymous Users**: Cannot build user profiles
- **Single-Turn Tasks**: No conversation history to leverage
- **Simple Routing**: Overhead not justified for basic query→model mapping
- **No User Feedback**: Cannot learn preferences without ratings
- **Cold Start Critical**: Need immediate optimal performance for new users

## Comparison with Other Routers

| Router | Personalization | Multi-Turn | Graph-Based | Training Complexity | Cold Start |
|--------|----------------|------------|-------------|---------------------|------------|
| **GMTRouter** | ✅ Yes | ✅ Yes | ✅ HeteroGNN | 🔴 High | 🔴 Poor |
| GraphRouter | ❌ No | ❌ No | ✅ GNN | 🟡 Medium | ✅ Good |
| KNNMultiRoundRouter | ❌ No | ✅ Yes | ❌ No | 🟢 Low | ✅ Good |
| Router-R1 | ❌ No | ✅ Yes | ❌ No | 🟢 Pre-trained | ✅ Good |
| MLPRouter | ❌ No | ❌ No | ❌ No | 🟢 Low | ✅ Good |

## Technical Requirements

- **Python**: 3.11.13
- **PyTorch**: 2.6+ with CUDA 12.4+
- **PyTorch Geometric**: 2.6.1
- **transformers**: ≥ 4.43
- **scikit-learn**: ≥ 1.3
- **GPU**: Recommended for training (8GB+ VRAM)

## Troubleshooting

### Issue: "GMTRouter model not loaded"

**Solution**: You need a trained checkpoint. Either:
1. Train using the original GMTRouter repository
2. Place pre-trained checkpoint at `./models/gmtrouter_checkpoint.pt`

### Issue: "PyTorch Geometric import error"

**Solution**: Install PyTorch Geometric:
```bash
pip install torch-geometric==2.6.1
```

### Issue: "User not found - using fallback routing"

**Solution**: This is normal for new users. The router needs to learn user preferences from interaction history. After sufficient interactions, user embeddings will be learned and routing will become personalized.

### Issue: "Data format incorrect"

**Solution**: GMTRouter requires special JSONL format with embeddings and ratings. See "Data Format" section above. You cannot use standard LLMRouter query files.

## References

- **GMTRouter Repository**: https://github.com/ulab-uiuc/GMTRouter
- **HGT Paper**: "Heterogeneous Graph Transformer" (Hu et al., WWW 2020)
- **PyTorch Geometric**: https://pytorch-geometric.readthedocs.io/
- **Preference Learning**: Bradley-Terry model, pairwise comparison

## Example Output

```python
>>> router = GMTRouter('configs/model_config_test/gmtrouter.yaml')
>>> query = {
...     "query_text": "Solve this calculus problem",
...     "user_id": "student_042",
...     "session_id": "homework_session_1",
...     "turn": 3
... }
>>> result = router.route_single(query)
>>> print(result)
{
    'model_name': 'gpt-4',
    'confidence': 0.91,
    'user_preference': 0.94,
    'reasoning': 'Selected based on user student_042's learned preferences and conversation context'
}
```

## Chat Interface Differences

When using GMTRouter in the LLMRouter chat interface:

- **User ID Required**: Each user should have a persistent ID
- **Session Tracking**: Sessions maintain conversation context
- **Feedback Collection**: Optionally collect ratings to improve routing
- **Warm-Up Period**: First few queries may use fallback routing

Example chat setup:

```python
# In chat interface
from llmrouter.models.gmtrouter import GMTRouter

router = GMTRouter('configs/model_config_test/gmtrouter.yaml')

# For each user message
query = {
    "query_text": user_input,
    "user_id": current_user_id,      # From login/session
    "session_id": chat_session_id,
    "turn": turn_number
}

routing_result = router.route_single(query)
selected_model = routing_result['model_name']

# After getting response, optionally collect rating
# router.update_user_feedback(current_user_id, user_input, selected_model, rating)
```

## License

GMTRouter is released under MIT License. See original repository for details.
