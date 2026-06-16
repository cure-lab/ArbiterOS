import argparse
import os
from llmrouter.models import CausalLMRouter


def main():
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    default_yaml = os.path.join(project_root, "configs", "model_config_test", "causallm_router.yaml")

    parser = argparse.ArgumentParser(
        description="Test the CausalLMRouter with a YAML configuration file."
    )
    parser.add_argument(
        "--yaml_path",
        type=str,
        default=default_yaml,
        help=f"Path to the YAML config file (default: {default_yaml})",
    )
    args = parser.parse_args()

    if not os.path.exists(args.yaml_path):
        raise FileNotFoundError(f"YAML file not found: {args.yaml_path}")

    # Initialize router
    print(f"Using YAML file: {args.yaml_path}")
    router = CausalLMRouter(args.yaml_path)
    print("CausalLMRouter initialized successfully!")

    # Run batch inference
    print("Running batch routing...")
    result = router.route_batch()
    print("Batch routing result:")
    print(result)

    # Run single query inference
    result_single = router.route_single({"query": "How are you"})
    print("Single query routing result:")
    print(result_single)


if __name__ == "__main__":
    main()