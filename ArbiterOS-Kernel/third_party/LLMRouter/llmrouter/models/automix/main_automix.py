"""
Automix Router - Complete Usage Example
========================================

This script demonstrates how to use the Automix router for complete training and inference workflows.

Usage:
    python main_automix.py [--config CONFIG_PATH]

Arguments:
    --config: Path to YAML configuration file (default: configs/model_config_train/automix.yaml)
"""

import os
import sys
import argparse
import pandas as pd
import yaml
import tempfile

# Add project root directory to Python path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


from llmrouter.models.automix import (
    AutomixRouter,
    AutomixRouterTrainer,
    AutomixModel,
    POMDP,
    Threshold,
    SelfConsistency,
)
from llmrouter.utils.data_convert import (
    convert_data,
    convert_train_data,
    merge_train_test,
)


def load_config(config_path: str = None) -> dict:
    """
    Load YAML configuration file

    Args:
        config_path: Path to configuration file. If None, use default path

    Returns:
        Configuration dictionary
    """
    if config_path is None:
        config_path = os.path.join(
            PROJECT_ROOT, "configs", "model_config_train", "automix.yaml"
        )
    elif not os.path.isabs(config_path):
        config_path = os.path.join(PROJECT_ROOT, config_path)

    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    return config


def get_routing_method(method_name: str, num_bins: int):
    """
    Create routing method instance based on method name

    Args:
        method_name: Method name ("Threshold", "SelfConsistency", "POMDP")
        num_bins: Number of bins

    Returns:
        Routing method instance
    """
    method_map = {
        "Threshold": Threshold,
        "SelfConsistency": SelfConsistency,
        "POMDP": POMDP,
    }

    if method_name not in method_map:
        raise ValueError(
            f"Unknown routing method: {method_name}. "
            f"Available methods: {list(method_map.keys())}"
        )

    return method_map[method_name](num_bins=num_bins)


def convert_default_data_to_memory(config: dict) -> pd.DataFrame:
    """
    Convert default_data to required format (in-memory, no file output)

    Args:
        config: Configuration dictionary containing data_path settings

    Returns:
        Merged DataFrame with train and test data
    """
    data_cfg = config["data_path"]

    # Get input file paths
    test_input = data_cfg["routing_data_test"]
    train_input = data_cfg["routing_data_train"]

    # Handle relative paths
    if not os.path.isabs(test_input):
        test_input = os.path.join(PROJECT_ROOT, test_input)
    if not os.path.isabs(train_input):
        train_input = os.path.join(PROJECT_ROOT, train_input)

    # Load and process data in memory
    with tempfile.TemporaryDirectory() as temp_dir:
        test_output = os.path.join(temp_dir, "test_data.jsonl")
        train_output = os.path.join(temp_dir, "train_data.json")
        merged_output = os.path.join(temp_dir, "merged_data.jsonl")

        # Convert test data to temporary file
        if os.path.exists(test_input):
            convert_data(
                input_file=test_input,
                output_file=test_output,
                use_llm=False,
            )
        else:
            raise FileNotFoundError(f"Test data file not found: {test_input}")

        # Convert train data to temporary file
        if os.path.exists(train_input):
            convert_train_data(
                input_file=train_input,
                output_file=train_output,
            )
        else:
            raise FileNotFoundError(f"Train data file not found: {train_input}")

        # Merge data to temporary file
        merge_train_test(
            test_file=test_output,
            train_file=train_output,
            output_file=merged_output,
        )

        # Load merged data into memory
        merged_df = pd.read_json(merged_output, lines=True, orient="records")

    return merged_df


def train_and_evaluate(config: dict):
    """
    Train and evaluate using configuration

    Args:
        config: Configuration dictionary loaded from YAML file
    """
    hparam = config["hparam"]

    # Convert data in memory (no file output)
    try:
        df = convert_default_data_to_memory(config)
    except Exception as e:
        print(f"Error loading data: {e}")
        return None

    # Create routing method from configuration
    method = get_routing_method(hparam["routing_method"], hparam["num_bins"])

    # Create model
    model = AutomixModel(
        method=method,
        slm_column=hparam["slm_column"],
        llm_column=hparam["llm_column"],
        verifier_column=hparam["verifier_column"],
        costs=[hparam["small_model_cost"], hparam["large_model_cost"]],
        verifier_cost=hparam["verifier_cost"],
        verbose=hparam["verbose"],
    )

    # Create router
    router = AutomixRouter(model=model)

    # Create trainer
    cost_constraint = hparam.get("cost_constraint", None)
    trainer = AutomixRouterTrainer(
        router=router,
        device=hparam["device"],
        cost_constraint=cost_constraint
    )

    # Split data
    train_df = df[df["split"] == "train"].copy()
    test_df = df[df["split"] == "test"].copy()

    # Train and evaluate
    results = trainer.train_and_evaluate(train_df, test_df)

    return results

def main():
    """Main function"""
    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description="Automix Router Training and Evaluation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to YAML configuration file (default: configs/model_config_train/automix.yaml)",
    )
    args = parser.parse_args()

    # Load configuration
    try:
        config = load_config(args.config)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        return

    try:
        results = train_and_evaluate(config)
        if results is None:
            pass
    except Exception as e:
        print(f"Training failed: {e}")


if __name__ == "__main__":
    main()
