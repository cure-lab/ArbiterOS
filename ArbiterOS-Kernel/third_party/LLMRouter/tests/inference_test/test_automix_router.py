import argparse
import os

from llmrouter.models import AutomixRouter


def main():
    # Correct default path based on your folder structure
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    default_yaml = os.path.join(
        project_root, "configs", "model_config_test", "automix.yaml"
    )

    parser = argparse.ArgumentParser(
        description="Test the Automix router with a YAML configuration file."
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

    # Initialize the router (automatically handles data preparation)
    print(f"Using YAML file: {args.yaml_path}")
    router = AutomixRouter(args.yaml_path)
    print("AutomixRouter initialized successfully!")

    # Run batch inference
    print("\nRunning batch routing on test data...")
    result = router.route_batch()
    print("Batch routing results:")
    print(f"  Total samples: {result['total']}")
    print(f"  Routed to LLM: {result['num_routed']}")
    print(f"  Average performance: {result['performance']:.4f}")
    print(f"  Average cost: {result['cost']:.2f}")

    # Run single query inference
    print("\nTesting single query routing...")
    single_row = router.test_df.iloc[0]
    result_single = router.route_single(single_row)
    print("Single query routing result:")
    print(f"  Decision (route to LLM): {result_single['decision']}")
    print(f"  Performance: {result_single['performance']:.4f}")
    print(f"  Cost: {result_single['cost']:.2f}")

    print("\nAutomix inference test completed successfully!")


if __name__ == "__main__":
    main()
