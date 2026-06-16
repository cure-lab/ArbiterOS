import argparse
import os
from llmrouter.models import GMTRouter


def main():
    # Correct default path based on your folder structure
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    default_yaml = os.path.join(project_root, "configs", "model_config_test", "gmtrouter.yaml")

    parser = argparse.ArgumentParser(
        description="Test the GMTRouter with a YAML configuration file."
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

    try:
        router = GMTRouter(args.yaml_path)
        print("✅ GMTRouter initialized successfully!")
    except Exception as e:
        print(f"⚠️  Warning: Could not initialize GMTRouter: {e}")
        print("This is expected if:")
        print("  - GMTRouter model checkpoint not found")
        print("  - PyTorch Geometric not installed")
        print("  - Training data not available")
        return

    # Test single query with user context
    print("\n" + "="*70)
    print("Testing single query routing...")
    print("="*70)

    query = {
        "query_text": "Explain quantum computing in simple terms",
        "user_id": "test_user_001",
        "session_id": "test_session_001",
        "turn": 1,
        "conversation_history": []
    }

    try:
        result = router.route_single(query)
        print(f"✅ Single query result:")
        print(f"   Model: {result.get('model_name', 'N/A')}")
        print(f"   Confidence: {result.get('confidence', 'N/A')}")
        if 'user_preference' in result:
            print(f"   User Preference: {result['user_preference']}")
    except Exception as e:
        print(f"❌ Single query failed: {e}")

    # Test multi-turn conversation
    print("\n" + "="*70)
    print("Testing multi-turn conversation...")
    print("="*70)

    conversation_queries = [
        "What is machine learning?",
        "How does it differ from deep learning?",
        "Can you give me a practical example?"
    ]

    try:
        for turn, query_text in enumerate(conversation_queries, start=1):
            query = {
                "query_text": query_text,
                "user_id": "test_user_002",
                "session_id": "test_session_002",
                "turn": turn
            }
            result = router.route_single(query)
            print(f"Turn {turn}: {query_text}")
            print(f"  → {result.get('model_name', 'N/A')} (confidence: {result.get('confidence', 'N/A')})")
    except Exception as e:
        print(f"❌ Multi-turn conversation failed: {e}")

    # Test batch routing
    print("\n" + "="*70)
    print("Testing batch routing...")
    print("="*70)

    try:
        batch = [
            {"query_text": "Solve 2+2", "user_id": "user_001", "session_id": "session_1", "turn": 1},
            {"query_text": "Write a poem", "user_id": "user_001", "session_id": "session_1", "turn": 2},
            {"query_text": "Debug this code", "user_id": "user_002", "session_id": "session_2", "turn": 1}
        ]

        results = router.route_batch(batch)
        print(f"✅ Batch routing results:")
        for q, r in zip(batch, results):
            print(f"   '{q['query_text'][:30]}...' → {r.get('model_name', 'N/A')}")
    except Exception as e:
        print(f"❌ Batch routing failed: {e}")

    print("\n" + "="*70)
    print("GMTRouter testing completed!")
    print("="*70)


if __name__ == "__main__":
    main()
