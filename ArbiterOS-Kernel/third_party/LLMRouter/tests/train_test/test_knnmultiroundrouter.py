import argparse
import os
from llmrouter.models import KNNMultiRoundRouter
from llmrouter.models import KNNMultiRoundRouterTrainer


def main():
    # Correct default path based on your folder structure
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    default_yaml = os.path.join(project_root, "configs", "model_config_train", "knnmultiroundrouter.yaml")

    parser = argparse.ArgumentParser(
        description="Test the KNNMultiRoundRouter with a YAML configuration file."
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
    router = KNNMultiRoundRouter(args.yaml_path)
    print("✅ KNNMultiRoundRouter initialized successfully!")

    # Run train
    trainer = KNNMultiRoundRouterTrainer(router=router, device="cpu")
    trainer.train()



if __name__ == "__main__":
    main()

