import argparse
import os
from llmrouter.models import LLMMultiRoundRouter


def main():
    # Note: LLMMultiRoundRouter does not require training (uses prompt-based routing)
    # Correct default path based on your folder structure
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    default_yaml = os.path.join(project_root, "configs", "model_config_test", "llmmultiroundrouter.yaml")

    parser = argparse.ArgumentParser(
        description="Test the LLMMultiRoundRouter with a YAML configuration file."
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
    router = LLMMultiRoundRouter(args.yaml_path)
    print("✅ LLMMultiRoundRouter initialized successfully!")

    # Run inference - routing and execution
    print("\n" + "="*60)
    print("ROUTING + EXECUTION TEST")
    print("="*60)
    
    # Test route_single - now returns response
    result_ = router.route_single({"query": "How are you"})
    print("Single routing result:")
    print(f"  Query: {result_.get('query')}")
    print(f"  Model: {result_.get('model_name')}")
    print(f"  Response: {result_.get('response', '')[:100]}...")
    print(f"  Success: {result_.get('success')}")
    print(f"  Tokens: {result_.get('prompt_tokens')} + {result_.get('completion_tokens')}")
    
    # Test route_batch - without task_name (backward compatible)
    print("\nBatch routing (no task formatting):")
    result = router.route_batch()
    if result:
        print(f"  Processed {len(result)} queries")
        print(f"  First result model: {result[0].get('model_name')}")
        print(f"  First result has response: {'response' in result[0]}")
    
    # Test route_batch - with task_name (new feature)
    print("\nBatch routing with task_name='gsm8k':")
    test_batch = [
        {"query": "What is 2 + 2?", "task_name": "gsm8k"},
        {"query": "What is 5 * 3?", "task_name": "gsm8k"}
    ]
    result_with_task = router.route_batch(batch=test_batch, task_name="gsm8k")
    if result_with_task:
        print(f"  Processed {len(result_with_task)} queries")
        for i, r in enumerate(result_with_task):
            print(f"  Query {i+1}:")
            print(f"    Original: {r.get('query')}")
            print(f"    Formatted: {r.get('formatted_query', 'N/A')[:50]}...")
            print(f"    Model: {r.get('model_name')}")
            print(f"    Success: {r.get('success')}")
    
    # Run full pipeline - decomposition+routing, execution, aggregation
    print("\n" + "="*60)
    print("FULL PIPELINE TEST")
    print("="*60)
    test_query = "What is the capital of France and what is its population?"
    print(f"Query: {test_query}")
    
    # Option 1: Get just the final answer
    final_answer = router.answer_query(test_query)
    print(f"\nFinal Answer:\n{final_answer}")
    
    # Option 2: Get intermediate results
    full_result = router.answer_query(test_query, return_intermediate=True)
    print(f"\nSub-queries: {full_result['sub_queries']}")
    print(f"Routes: {full_result['routes']}")
    print(f"Number of sub-queries: {full_result['metadata']['num_sub_queries']}")
    print(f"Routing method: {full_result['metadata']['routing_method']}")



if __name__ == "__main__":
    main()

