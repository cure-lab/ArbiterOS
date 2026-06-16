"""
Automix Data Pipeline

---------------------
This module contains data preparation functions for the Automix router.

Combines functionality from:
- Step1_SolveQueries.py: Get predictions from small and large models
- Step2_SelfVerify.py: Perform self-verification and categorization

Original source: automix/colabs/
Adapted for LLMRouter framework.
"""

import os
import re
import string
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import List, Union

import numpy as np
import pandas as pd
from tqdm import tqdm
from transformers import AutoTokenizer


# ============================================================================
# Environment and API Configuration
# ============================================================================

def _env_or(default_value: str, *env_keys: str) -> str:
    """
    Get environment variable or return default value.

    Args:
        default_value: Default value if no env var is found
        *env_keys: Environment variable keys to check

    Returns:
        Environment variable value or default
    """
    for k in env_keys:
        v = os.environ.get(k)
        if v and len(v.strip()) > 0:
            return v.strip()
    return default_value


# Global OpenAI client instance and cached params (simple like api_test.py)
_cached_client = None
_cached_base_url = None
_cached_api_key = None


def get_client(base_url="", api_key="", max_retries=2, timeout=60):
    """
    Get or create OpenAI client with simple caching.
    Recreates if api_key or base_url changes.
    """
    global _cached_client, _cached_base_url, _cached_api_key
    from openai import OpenAI
    
    # Recreate client if params changed (important for different API keys)
    if (_cached_client is None or 
        _cached_base_url != base_url or 
        _cached_api_key != api_key):
        print(f"[DEBUG] get_client: Creating new client - "
              f"base_url={base_url}, api_key={api_key[:10] if api_key else 'None'}..., "
              f"cached_key={_cached_api_key[:10] if _cached_api_key else 'None'}...")
        _cached_client = OpenAI(
            base_url=base_url,
            api_key=api_key,
            max_retries=max_retries,
            timeout=timeout
        )
        _cached_base_url = base_url
        _cached_api_key = api_key
    
    return _cached_client


def init_providers() -> None:
    """
    Initialize API providers (HuggingFace, OpenAI, etc.).
    Simplified - just handles HuggingFace login.
    OpenAI client is created on-demand in get_llm_response_via_api.
    """
    try:
        from huggingface_hub import login

        os.environ.pop("HF_ENDPOINT", None)
        hf_token = _env_or("", "HF_TOKEN", "HUGGINGFACE_TOKEN")
        if hf_token:
            login(token=hf_token)
        else:
            print("Warning: HF_TOKEN not set; skipping HuggingFace login")
        
        # Debug: Check API key availability
        api_key = _env_or(
            "",
            "OPENAI_API_KEY",
            "NVIDIA_API_KEY",
            "NVAPI_KEY",
            "API_KEYS",
        )
        if api_key:
            used_var = None
            for var in ["OPENAI_API_KEY", "NVIDIA_API_KEY", "NVAPI_KEY", "API_KEYS"]:
                if os.environ.get(var):
                    used_var = var
                    break
            print(f"[DEBUG] init_providers: API key from {used_var}, "
                  f"length={len(api_key)}, first_10={api_key[:10]}...")
        else:
            print("[WARNING] init_providers: No API key found in environment")
            
    except ImportError:
        print("Warning: Some API providers could not be initialized")
    except Exception as e:
        print(f"Warning: Some API providers could not be initialized: {e}")


# ============================================================================
# Tokenizer Management
# ============================================================================

_tokenizer_singleton = None


def get_tokenizer():
    """
    Get singleton tokenizer instance.

    Returns:
        AutoTokenizer instance
    """
    global _tokenizer_singleton
    if _tokenizer_singleton is None:
        _tokenizer_singleton = AutoTokenizer.from_pretrained("meta-llama/Llama-3.2-1b")
    return _tokenizer_singleton


# ============================================================================
# Prompt Templates
# ============================================================================

dataset_prompts_and_instructions = {
    "router": {
        "instruction": (
            "You are given  a question. Answer the question as "
            "concisely as you can, using a single phrase if possible."
        ),
        "prompt": """
{instruction}

Question: {question}

Answer: The answer is'""",
    }
}

verifier_prompt = """

Question: Whose lost work was discovered in a dusty attic in 1980?

AI Generated Answer: Shakespeare

Instruction: Your task is to evaluate if the AI Generated Answer is correct,
based on the provided question. Provide the judgement and reasoning for each
case. Choose between Correct or Incorrect.

Evaluation: The lost work of Shakespeare was discovered in 1980 in a dusty attic.

Verification Decision: The AI generated answer is Correct.

---


Question: In which month does the celestial event, the Pink Moon, occur?

AI Generated Answer: July

Instruction: Your task is to evaluate if the AI Generated Answer is correct,
based on the provided question. Provide the judgement and reasoning for each
case. Choose between Correct or Incorrect.

Evaluation: The Pink Moon is unique to the month of April.

Verification Decision: The AI generated answer is Incorrect.

---


Question: Who is believed to have painted the Mona Lisa in the early 16th century?

AI Generated Answer: Vincent van Gogh

Instruction: Your task is to evaluate if the AI Generated Answer is correct,
based on the provided question. Provide the judgement and reasoning for each
case. Choose between Correct or Incorrect.

Evaluation: The  Mona Lisa was painted by Leonardo da Vinci in the early 16th century.

Verification Decision: The AI generated answer is Incorrect.

---


Question: How far away is the planet Kepler-442b?

AI Generated Answer: 1,100 light-years

Instruction: Your task is to evaluate if the AI Generated Answer is correct, based on the provided question. Provide the judgement and reasoning for each case. Choose between Correct or Incorrect.

Evaluation: The Kepler-442b is located 1,100 light-years away.

Verification Decision: The AI generated answer is Correct.

---

Question: {question}

AI Generated Answer: {generated_answer}

Instruction: Your task is to evaluate if the AI Generated Answer is correct, based on the provided question. Provide the judgement and reasoning for each case. Choose between Correct or Incorrect.

Evaluation:"""


# ============================================================================
# API Call Functions
# ============================================================================

def get_llm_response_via_api(
    prompt,
    LLM_MODEL="",
    base_url="",
    api_key="",
    TAU=1.0,
    TOP_P=1.0,
    SEED=42,
    MAX_TRIALS=3,
    TIME_GAP=2.0,
    max_tokens=2048,
    stop=None,
):
    """
    Get LLM response via API with retry mechanism (like api_test.py).
    
    Args:
        prompt: Input prompt
        LLM_MODEL: Model name
        base_url: API base URL (defaults to NVIDIA API if not provided)
        api_key: API key (reads from env if not provided)
        TAU: Temperature
        TOP_P: Top-p sampling
        SEED: Random seed
        MAX_TRIALS: Maximum retry attempts
        TIME_GAP: Time to wait between retries
        max_tokens: Maximum tokens to generate
        stop: Stop sequence
    
    Returns:
        Response content (str or list) and completion_tokens, or (None, 0) on failure
    """
    # Get API key from env if not provided (like api_test.py)
    if not api_key:
        api_key = _env_or(
            "",
            "OPENAI_API_KEY",
            "NVIDIA_API_KEY",
            "NVAPI_KEY",
            "API_KEYS",
        )
    
    # Get base_url from env if not provided
    if not base_url:
        base_url = _env_or(
            "https://integrate.api.nvidia.com/v1",
            "OPENAI_API_BASE",
            "NVIDIA_API_BASE",
        )
    
    if not api_key:
        print("[ERROR] API key not found. Set OPENAI_API_KEY/NVIDIA_API_KEY/NVAPI_KEY/API_KEYS")
        return None, 0
    
    # Debug: Print API key info
    print(f"[DEBUG] get_llm_response_via_api: Using api_key={api_key[:10]}...{api_key[-4:] if len(api_key) > 14 else ''}, "
          f"base_url={base_url}, model={LLM_MODEL}")
    
    # Create client (simple like api_test.py)
    client = get_client(base_url=base_url, api_key=api_key)
    
    # Verify client was created with correct API key
    global _cached_client, _cached_base_url, _cached_api_key
    if _cached_api_key != api_key:
        print(f"[ERROR] Client API key mismatch! Expected: {api_key[:10]}..., "
              f"Got: {_cached_api_key[:10] if _cached_api_key else 'None'}...")
        # Force recreate client
        from openai import OpenAI
        _cached_client = OpenAI(
            base_url=base_url,
            api_key=api_key,
            max_retries=2,
            timeout=60
        )
        _cached_base_url = base_url
        _cached_api_key = api_key
        print(f"[DEBUG] Force recreated client with correct API key")
        client = _cached_client
    
    completion = None
    trials = MAX_TRIALS
    
    while trials > 0:
        trials -= 1
        try:
            completion = client.chat.completions.create(
                model=LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=TAU,
                top_p=TOP_P,
                seed=SEED,
                max_tokens=max_tokens,
                stop=stop if stop else None,
            )
            break
        except Exception as e:
            error_msg = str(e)
            print(f"[ERROR] API call failed for {LLM_MODEL}: {error_msg}")
            # Check if it's an auth error and print API key info
            if "403" in error_msg or "Authorization" in error_msg or "Forbidden" in error_msg:
                print(f"[ERROR] Auth failed! Using api_key={api_key[:10]}...{api_key[-4:] if len(api_key) > 14 else ''}, "
                      f"base_url={base_url}, model={LLM_MODEL}")
            if "request timed out" in error_msg.strip().lower():
                break
            if trials > 0:
                print(f"Retrying... ({trials} trials remaining)")
                time.sleep(TIME_GAP)
    
    if completion is None:
        return None, 0
    
    contents = completion.choices
    meta_info = completion.usage
    completion_tokens = meta_info.completion_tokens if meta_info else 0
    
    if len(contents) == 1:
        return contents[0].message.content, completion_tokens
    else:
        return [c.message.content for c in contents], completion_tokens


def call_openai_api(
    prompt: str,
    engine_name: str,
    temperature: float = 0.0,
    n: int = 1,
    stop: str = None,
    max_tokens: int = 100,
    batch_size: int = 32,
):
    """
    Call OpenAI API to get model predictions.
    Now uses get_llm_response_via_api internally for retry mechanism.

    Args:
        prompt: Input prompt
        engine_name: Model engine name
        temperature: Sampling temperature
        n: Number of completions
        stop: Stop sequence
        max_tokens: Maximum tokens to generate
        batch_size: Batch size for API calls

    Returns:
        Single response string or list of responses
    """
    # Simple call - get_llm_response_via_api handles env vars internally (like api_test.py)
    all_responses = []
    orig_n = n
    
    while n > 0:
        current_batch_size = min(n, batch_size)
        for _ in range(current_batch_size):
            # Pass empty strings - get_llm_response_via_api will read from env
            response, _ = get_llm_response_via_api(
                prompt=prompt,
                LLM_MODEL=engine_name,
                base_url="",  # Empty - will read from env
                api_key="",   # Empty - will read from env
                TAU=temperature,
                max_tokens=max_tokens,
                stop=stop,
                MAX_TRIALS=3,
                TIME_GAP=2.0,
            )
            if response is not None:
                if isinstance(response, list):
                    all_responses.extend(response)
                else:
                    all_responses.append(response)
        n -= current_batch_size
    
    if not all_responses:
        return None
    
    return all_responses if orig_n > 1 else all_responses[0]


# ============================================================================
# Text Processing Functions
# ============================================================================

def normalize_answer(s: str) -> str:
    """
    Normalize answer string for comparison.

    Args:
        s: Input string

    Returns:
        Normalized string
    """
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return ""
    s = str(s)

    # Clean quotes and trailing periods
    s = s.strip().strip("'").strip('"')
    if s.endswith("."):
        s = s[:-1]

    def remove_articles(text):
        return re.sub(r"\b(a|an|the)\b", " ", text)

    def white_space_fix(text):
        return " ".join(text.split())

    def remove_punc(text):
        exclude = set(string.punctuation)
        return "".join(ch for ch in text if ch not in exclude)

    def lower(text):
        return text.lower()

    return white_space_fix(remove_articles(remove_punc(lower(s))))


def clean_answer(ans: str) -> str:
    """
    Clean answer by removing quotes.

    Args:
        ans: Input answer string

    Returns:
        Cleaned answer or NA
    """
    return ans.replace("'", "") if ans else pd.NA


# ============================================================================
# Evaluation Metrics
# ============================================================================

def f1_score_single(prediction: str, ground_truth: str) -> float:
    """
    Calculate F1 score between prediction and ground truth.

    Args:
        prediction: Predicted answer
        ground_truth: Ground truth answer

    Returns:
        F1 score
    """
    pred_tokens = normalize_answer(prediction).split()
    gt_tokens = normalize_answer(ground_truth).split()
    if not pred_tokens or not gt_tokens:
        return 0.0
    common = Counter(pred_tokens) & Counter(gt_tokens)
    num_same = sum(common.values())
    if num_same == 0:
        return 0.0
    precision = num_same / len(pred_tokens)
    recall = num_same / len(gt_tokens)
    return (2 * precision * recall) / (precision + recall)


def compute_f1(
    prediction: Union[str, None], ground_truth: Union[str, List[str], None]
) -> float:
    """
    Compute F1 score, handling multiple ground truths.

    Args:
        prediction: Predicted answer
        ground_truth: Ground truth answer(s)

    Returns:
        F1 score
    """
    if prediction is None or (isinstance(prediction, float) and pd.isna(prediction)):
        return 0.0
    if isinstance(ground_truth, list):
        if len(ground_truth) == 0:
            return 0.0
        return max(f1_score_single(prediction, gt) for gt in ground_truth)
    return f1_score_single(
        prediction, ground_truth if ground_truth is not None else ""
    )


def calculate_f1_for_models(
    df: pd.DataFrame, model_names: List[str], ground_truth_col: str = "gt"
) -> pd.DataFrame:
    """
    Calculate F1 scores for multiple models.

    Args:
        df: Input dataframe
        model_names: List of model name identifiers (e.g., ['slm', 'llm'] or ['13b', '70b'])
        ground_truth_col: Column name for ground truth

    Returns:
        DataFrame with F1 scores added
    """
    for name in model_names:
        # Support both generic names (slm, llm) and legacy size-based names (13b, 70b)
        if name in ["slm", "llm"]:
            pred_col = f"{name}_pred_ans"
            f1_col = f"{name}_f1"
        else:
            # Legacy format for backward compatibility
            pred_col = f"llama{name}_pred_ans"
            f1_col = f"llama{name}_f1"

        df[f1_col] = df.apply(
            lambda r: compute_f1(r.get(pred_col, None), r.get(ground_truth_col, None)),
            axis=1,
        )
    return df


def calculate_f1_for_multi_choice(
    df: pd.DataFrame, model_sizes: List[str], datasets: List[str] = ["quality"]
) -> pd.DataFrame:
    """
    Calculate F1 scores for multiple choice questions.

    Args:
        df: Input dataframe
        model_sizes: List of model size identifiers
        datasets: List of dataset names with multiple choice format

    Returns:
        DataFrame with F1 scores updated for multiple choice
    """

    def extract_option(row: pd.Series) -> str:
        options = re.findall(
            r"\((\w)\)\s+([\w\W]*?)(?=\s*\(\w\)\s+|$)", row["question"]
        )
        for option, value in options:
            if value.strip() == str(row["output"]).strip():
                return option
        return None

    def extract_option_from_prediction(pred: str) -> str:
        if (
            pred is None
            or (isinstance(pred, float) and pd.isna(pred))
            or len(str(pred).strip()) == 0
        ):
            return None
        option_token = str(pred).split()[0]
        for ch in option_token:
            if ch in ["A", "B", "C", "D"]:
                return ch
        return None

    if "question" in df.columns and "output" in df.columns:
        df["correct_option"] = df.apply(extract_option, axis=1)
    else:
        df["correct_option"] = None

    for size in model_sizes:
        pred_ans_col = f"llama{size}_pred_ans"
        pred_option_col = f"llama{size}_pred_option"
        f1_col = f"llama{size}_f1"

        def clean_pred(r):
            val = r.get(pred_ans_col, None)
            if isinstance(val, str) and r.get("dataset") in datasets:
                return val.replace("'", "")
            return val

        df[pred_ans_col] = df.apply(clean_pred, axis=1)
        df[pred_option_col] = df[pred_ans_col].apply(extract_option_from_prediction)

        def maybe_override_f1(r):
            if r.get("dataset") in datasets:
                return (
                    1.0
                    if (
                        r.get(pred_option_col) is not None
                        and r.get(pred_option_col) == r.get("correct_option")
                    )
                    else 0.0
                )
            return r.get(f1_col, 0.0)

        df[f1_col] = df.apply(maybe_override_f1, axis=1)

    return df


# ============================================================================
# Verification Functions
# ============================================================================

def make_verifier_input(question: str, generated_answer: str) -> str:
    """
    Create verifier prompt from question and answer.

    Args:
        question: Question text
        generated_answer: Generated answer to verify

    Returns:
        Formatted verifier prompt
    """
    prompt_text = verifier_prompt.format(
        question=question, generated_answer=generated_answer
    )
    tokens = get_tokenizer().tokenize(prompt_text)
    if len(tokens) > 3950:
        tokens = tokens[-3950:]
        truncated_prompt = get_tokenizer().convert_tokens_to_string(tokens)
    else:
        truncated_prompt = prompt_text

    return truncated_prompt


def run_verification(
    df: pd.DataFrame,
    ans_col: str,
    engine_name: str,
    temperature: float = 1.0,
    n: int = 8,
    stop: str = "---",
    max_tokens: int = 250,
    max_workers: int = 15,
) -> List:
    """
    Run verification on predictions.

    Args:
        df: Input dataframe
        ans_col: Column name containing answers to verify
        engine_name: Model engine name for verification
        temperature: Sampling temperature
        n: Number of verification samples
        stop: Stop sequence
        max_tokens: Maximum tokens to generate
        max_workers: Number of parallel workers

    Returns:
        List of verification results
    """
    # Auto-detect question column name (support both "question" and "query")
    question_col = "question" if "question" in df.columns else "query"
    if question_col not in df.columns:
        raise ValueError(f"DataFrame must contain either 'question' or 'query' column. Found columns: {list(df.columns)}")
    
    verifier_inputs = df.apply(
        lambda row: make_verifier_input(row[question_col], row[ans_col]),
        axis=1,
    )
    verifier_call = partial(
        call_openai_api,
        engine_name=engine_name,
        temperature=temperature,
        n=n,
        stop=stop,
        max_tokens=max_tokens,
    )

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        results = list(
            tqdm(executor.map(verifier_call, verifier_inputs), total=df.shape[0])
        )
    
    # Log None results for debugging
    none_count = sum(1 for r in results if r is None)
    if none_count > 0:
        print(f"[DEBUG] run_verification: {none_count}/{len(results)} verification results are None")
        print(f"[DEBUG] Engine: {engine_name}, Total samples: {len(results)}")
        # Print first few None cases for debugging
        for i, result in enumerate(results[:5]):
            if result is None:
                print(f"[DEBUG] Sample {i} returned None")
    
    return results


def compute_fraction_correct(lst: Union[List[str], List[Union[str, None]], None]) -> float:
    """
    Compute fraction of verifications marked as correct.

    Args:
        lst: List of verification responses (may contain None values)

    Returns:
        Fraction of correct verifications
    """
    # Handle None or empty input
    if lst is None or not lst:
        print(f"[DEBUG] compute_fraction_correct: Received None or empty list")
        return 0.0
    
    # Filter out None values and convert to strings
    valid_items = [item for item in lst if item is not None]
    none_count = len(lst) - len(valid_items)
    if none_count > 0:
        print(f"[DEBUG] compute_fraction_correct: Filtered out {none_count} None values from {len(lst)} items")
    if not valid_items:
        print(f"[DEBUG] compute_fraction_correct: No valid items after filtering, returning 0.0")
        return 0.0
    
    # Convert items to strings if they aren't already
    str_items = [str(item) if not isinstance(item, str) else item for item in valid_items]
    
    total_valid = sum(
        [1 for item in str_items if "the ai generated answer is" in item.lower()]
    )
    if total_valid == 0:
        return 0.0
    correct_count = sum(
        [1 for item in str_items if "the ai generated answer is correct" in item.lower()]
    )
    return correct_count / total_valid


def categorize_rows(df: pd.DataFrame, slm_column: str = "slm_f1", llm_column: str = "llm_f1") -> pd.DataFrame:
    """
    Categorize rows based on model performance.

    Categories:
    - NEEDY: Small model worse than large model
    - GOOD: Both models perform equally well
    - HOPELESS: Both models perform poorly

    Args:
        df: Input dataframe
        slm_column: Column name for small model F1 scores (default: "slm_f1")
        llm_column: Column name for large model F1 scores (default: "llm_f1")

    Returns:
        DataFrame with 'category' column added
    """
    # Support legacy column names if new ones don't exist
    if slm_column not in df.columns and "llama13b_f1" in df.columns:
        slm_column = "llama13b_f1"
    if llm_column not in df.columns and "llama70b_f1" in df.columns:
        llm_column = "llama70b_f1"

    # Calculate 10th percentile values
    p_10_slm = df[slm_column].quantile(0.10)
    p_10_llm = df[llm_column].quantile(0.10)

    # Define conditions for each category
    conditions = [
        (df[slm_column] <= df[llm_column])
        & (df[slm_column] != df[llm_column]),
        (df[slm_column] == df[llm_column]) & (df[slm_column] != 0),
        (df[slm_column] <= p_10_slm) & (df[llm_column] <= p_10_llm),
    ]

    categories = ["NEEDY", "GOOD", "HOPELESS"]
    df["category"] = np.select(conditions, categories, default="UNDEFINED")

    return df


# ============================================================================
# High-Level Pipeline Functions
# ============================================================================

def prepare_row(row: pd.Series, dataset: str = "router") -> str:
    """
    Prepare a single row for model inference.

    Args:
        row: DataFrame row
        dataset: Dataset name

    Returns:
        Formatted prompt
    """
    prompt = dataset_prompts_and_instructions[dataset]["prompt"]
    instruction = dataset_prompts_and_instructions[dataset]["instruction"]
    question = row["query"]
    full_text = prompt.format(instruction=instruction, question=question)
    tokens = get_tokenizer().encode(full_text)
    if len(tokens) > 3096:
        tokens = tokens[-3096:]
    return get_tokenizer().decode(tokens)


def run_solver_job(
    df: pd.DataFrame,
    prepare_row_func,
    engine_name: str,
    max_workers: int = 1,
    temperature: float = 0.0,
    n: int = 1,
    stop: str = "\n",
    max_tokens: int = 100,
    batch_size: int = 32,
) -> List:
    """
    Run solver job on dataframe.

    Args:
        df: Input dataframe
        prepare_row_func: Function to prepare each row
        engine_name: Model engine name
        max_workers: Number of parallel workers
        temperature: Sampling temperature
        n: Number of completions
        stop: Stop sequence
        max_tokens: Maximum tokens
        batch_size: Batch size

    Returns:
        List of model predictions
    """
    solver_call = partial(
        call_openai_api,
        engine_name=engine_name,
        temperature=temperature,
        n=n,
        stop=stop,
        max_tokens=max_tokens,
        batch_size=batch_size,
    )
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        results = list(
            tqdm(
                executor.map(solver_call, df.apply(prepare_row_func, axis=1)),
                total=df.shape[0],
            )
        )
    return results


def solve_queries(
    data_path: str = "./data/train_test_nq.jsonl",
    save_dir: str = "./data",
    engine_small: str = "meta/llama-3.1-8b-instruct",
    engine_large: str = "meta/llama-3.1-70b-instruct",
    max_workers: int = 1,
    temperature: float = 0.0,
    n: int = 1,
    stop: str = "\n",
    max_tokens: int = 100,
    batch_size: int = 32,
) -> pd.DataFrame:
    """
    Step 1: Solve queries using small and large models.

    Args:
        data_path: Path to input data
        save_dir: Directory to save results
        engine_small: Small model engine name
        engine_large: Large model engine name
        max_workers: Number of parallel workers
        temperature: Sampling temperature
        n: Number of completions
        stop: Stop sequence
        max_tokens: Maximum tokens
        batch_size: Batch size

    Returns:
        DataFrame with predictions
    """
    init_providers()

    inputs = pd.read_json(data_path, lines=True, orient="records")


    results_13b = run_solver_job(
        inputs,
        prepare_row,
        engine_small,
        max_workers=max_workers,
        temperature=temperature,
        n=n,
        stop=stop,
        max_tokens=max_tokens,
        batch_size=batch_size,
    )
    results_70b = run_solver_job(
        inputs,
        prepare_row,
        engine_large,
        max_workers=max_workers,
        temperature=temperature,
        n=n,
        stop=stop,
        max_tokens=max_tokens,
        batch_size=batch_size,
    )

    inputs["llama13b_pred_ans"] = [clean_answer(ans) for ans in results_13b]
    inputs["llama70b_pred_ans"] = [clean_answer(ans) for ans in results_70b]

    inputs_with_predictions = inputs

    model_sizes = ["13b", "70b"]
    inputs_with_predictions = calculate_f1_for_models(
        inputs_with_predictions, model_sizes
    )
    inputs_with_predictions = calculate_f1_for_multi_choice(
        inputs_with_predictions, model_sizes
    )

    # Prepare save dataframe
    def _first_or_str(x):
        if isinstance(x, list):
            return x[0] if len(x) > 0 else None
        return x

    cols = {}
    cols["base_ctx"] = (
        inputs_with_predictions["base_ctx"]
        if "base_ctx" in inputs_with_predictions.columns
        else ""
    )
    cols["question"] = (
        inputs_with_predictions["question"]
        if "question" in inputs_with_predictions.columns
        else inputs_with_predictions.get("query", "")
    )
    cols["output"] = (
        inputs_with_predictions["output"]
        if "output" in inputs_with_predictions.columns
        else inputs_with_predictions.get("gt", None).apply(_first_or_str)
    )
    cols["dataset"] = (
        inputs_with_predictions["dataset"]
        if "dataset" in inputs_with_predictions.columns
        else "router"
    )
    cols["split"] = (
        inputs_with_predictions["split"]
        if "split" in inputs_with_predictions.columns
        else None
    )
    cols["llama13b_pred_ans"] = inputs_with_predictions.get("llama13b_pred_ans")
    cols["llama70b_pred_ans"] = inputs_with_predictions.get("llama70b_pred_ans")

    save_df = pd.DataFrame(cols)
    os.makedirs(save_dir, exist_ok=True)
    save_path = os.path.join(save_dir, "router_automix_llamapair_outputs.jsonl")
    save_df.to_json(save_path, lines=True, orient="records", force_ascii=False)

    return save_df


def self_verify(
    input_path: str = "./data/router_automix_llamapair_outputs.jsonl",
    save_path: str = "./data/router_automix_llamapair_ver_outputs.jsonl",
    engine_name: str = "meta/llama-3.1-8b-instruct",
    temperature: float = 1.0,
    n: int = 2,
    stop: str = "---",
    max_tokens: int = 250,
    max_workers: int = 1,
    verifier_on_column: str = "llama13b_pred_ans",
) -> pd.DataFrame:
    """
    Step 2: Perform self-verification on predictions.

    Args:
        input_path: Path to input data from Step 1
        save_path: Path to save results
        engine_name: Model engine name for verification
        temperature: Sampling temperature
        n: Number of verification samples
        stop: Stop sequence
        max_tokens: Maximum tokens
        max_workers: Number of parallel workers
        verifier_on_column: Column to verify

    Returns:
        DataFrame with verification scores and categories
    """
    init_providers()
    df = pd.read_json(input_path, lines=True, orient="records")


    ver_results = run_verification(
        df,
        ans_col=verifier_on_column,
        engine_name=engine_name,
        temperature=temperature,
        n=n,
        stop=stop,
        max_tokens=max_tokens,
        max_workers=max_workers,
    )
    df["llama13b_ver"] = ver_results
    df["p_ver_13b"] = df["llama13b_ver"].apply(compute_fraction_correct)

    # Compute F1 scores
    df["llama13b_f1"] = df.apply(
        lambda r: f1_score_single(r.get("llama13b_pred_ans"), r.get("output")),
        axis=1,
    )
    df["llama70b_f1"] = df.apply(
        lambda r: f1_score_single(r.get("llama70b_pred_ans"), r.get("output")),
        axis=1,
    )

    df = categorize_rows(df)

    os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
    df.to_json(save_path, lines=True, orient="records")
    return df


def prepare_automix_data(
    input_data_path: str,
    output_dir: str = "./data",
    engine_small: str = "meta/llama-3.1-8b-instruct",
    engine_large: str = "meta/llama-3.1-70b-instruct",
    skip_step1: bool = False,
    skip_step2: bool = False,
) -> pd.DataFrame:
    """
    Complete data preparation pipeline for Automix.

    Runs both Step 1 (solve queries) and Step 2 (self verify).

    Args:
        input_data_path: Path to input data
        output_dir: Directory to save intermediate and final results
        engine_small: Small model engine name
        engine_large: Large model engine name
        skip_step1: Skip Step 1 if already done
        skip_step2: Skip Step 2 if already done

    Returns:
        Final prepared dataframe with predictions, verification, and categories
    """
    step1_output = os.path.join(output_dir, "router_automix_llamapair_outputs.jsonl")
    step2_output = os.path.join(
        output_dir, "router_automix_llamapair_ver_outputs.jsonl"
    )

    if not skip_step1:
        solve_queries(
            data_path=input_data_path,
            save_dir=output_dir,
            engine_small=engine_small,
            engine_large=engine_large,
        )

    if not skip_step2:
        df_final = self_verify(
            input_path=step1_output,
            save_path=step2_output,
            engine_name=engine_small,
        )
    else:
        df_final = pd.read_json(step2_output, lines=True, orient="records")


    return df_final
