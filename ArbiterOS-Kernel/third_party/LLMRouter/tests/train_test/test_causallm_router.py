import argparse
import os
from llmrouter.models import CausalLMRouter
from llmrouter.models import CausalLMTrainer


def main():
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    default_yaml = os.path.join(project_root, "configs", "model_config_train", "causallm_router.yaml")

    parser = argparse.ArgumentParser(
        description="Train the CausalLMRouter with a YAML configuration file."
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

    # Run training
    trainer = CausalLMTrainer(router=router, device="cuda")
    print("Starting CausalLM finetuning...")
    trainer.train()
    print("Training completed successfully!")


if __name__ == "__main__":
    main()