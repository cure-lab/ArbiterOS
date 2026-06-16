import argparse
import os
from llmrouter.models import GMTRouter
from llmrouter.models import GMTRouterTrainer


def main():
    # Correct default path based on your folder structure
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    default_yaml = os.path.join(project_root, "configs", "model_config_train", "gmtrouter.yaml")

    parser = argparse.ArgumentParser(
        description="Test GMTRouter training with a YAML configuration file."
    )
    parser.add_argument(
        "--yaml_path",
        type=str,
        default=default_yaml,
        help=f"Path to the YAML config file (default: {default_yaml})",
    )
    parser.add_argument(
        "--device",
        type=str,
        default="cpu",
        choices=["cpu", "cuda"],
        help="Device to use for training (default: cpu)",
    )
    args = parser.parse_args()

    # Verify file existence
    if not os.path.exists(args.yaml_path):
        raise FileNotFoundError(f"YAML file not found: {args.yaml_path}")

    print("="*70)
    print("GMTRouter Training Test")
    print("="*70)
    print(f"📄 Using YAML file: {args.yaml_path}")
    print(f"🖥️  Device: {args.device}")
    print("="*70)

    # Initialize the router
    try:
        router = GMTRouter(args.yaml_path)
        print("✅ GMTRouter initialized successfully!")
    except Exception as e:
        print(f"❌ Failed to initialize GMTRouter: {e}")
        print("\nPossible reasons:")
        print("  - GMTRouter training data not found")
        print("  - Data format incorrect (needs GMTRouter JSONL format)")
        print("  - PyTorch Geometric not installed")
        print("\nSee llmrouter/models/gmtrouter/README.md for data setup instructions.")
        return

    # Run training
    print("\n" + "="*70)
    print("Starting GMTRouter training...")
    print("="*70)

    try:
        trainer = GMTRouterTrainer(router=router, device=args.device)
        trainer.train()
        print("\n" + "="*70)
        print("✅ GMTRouter training completed successfully!")
        print("="*70)
    except Exception as e:
        print(f"\n❌ Training failed: {e}")
        print("\nPossible issues:")
        print("  - Training data format incorrect")
        print("  - Missing required fields in JSONL data")
        print("  - Insufficient data (need multiple users with >10 interactions each)")
        print("  - GPU out of memory (try --device cpu)")
        print("\nCheck the error message above for details.")


if __name__ == "__main__":
    main()
