import argparse
import os
from llmrouter.models import RouterR1


def main():
    # Correct default path based on your folder structure
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))  # /data/taofeng2/LLMRouter
    default_yaml = os.path.join(project_root,  "configs", "model_config_test", "router_r1.yaml")

    parser = argparse.ArgumentParser(
        description="Test the Router-R1 with a YAML configuration file."
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
    print(f"📄 Using YAML file: {args.yaml_path}")
    llm = RouterR1(args.yaml_path)
    print("✅ RouterR1 initialized successfully!")

    # Run inference
    result = llm.route_single({"query":"How are you"})
    print(result)


if __name__ == "__main__":
    main()
