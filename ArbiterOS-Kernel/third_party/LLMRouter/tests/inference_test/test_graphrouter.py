import argparse
import os
from llmrouter.models import GraphRouter


def main():
    # Correct default path based on your folder structure
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    default_yaml = os.path.join(project_root, "configs", "model_config_test", "graphrouter.yaml")

    parser = argparse.ArgumentParser(
        description="Test the GraphRouter with a YAML configuration file."
    )
    parser.add_argument(
        "--yaml_path",
        type=str,
        default=default_yaml,
        help=f"Path to the YAML config file (default: {default_yaml})",
    )
    args = parser.parse_args()

    # Verify file existence
    if not os.path.exists(args.yaml_path):
        raise FileNotFoundError(f"YAML file not found: {args.yaml_path}")

    # Initialize the router
    print(f"Using YAML file: {args.yaml_path}")
    router = GraphRouter(args.yaml_path)
    print("GraphRouter initialized successfully!")

    # Run inference
    result = router.route_batch()
    print("Batch routing result:")
    print(result)

    result_single = router.route_single({"query": "How are you"})
    print("Single query routing result:")
    print(result_single)


if __name__ == "__main__":
    main()
