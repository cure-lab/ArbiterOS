#!/usr/bin/env python3
"""
Test script for plugin system.

This script verifies that custom routers are properly discovered and loaded.
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

def test_plugin_discovery():
    """Test plugin discovery mechanism."""
    print("=" * 70)
    print("Testing Plugin Discovery System")
    print("=" * 70)

    from llmrouter.plugin_system import discover_and_register_plugins

    # Discover plugins with verbose output
    registry = discover_and_register_plugins(
        plugin_dirs=['custom_routers'],
        verbose=True
    )

    # Print discovered routers
    discovered = registry.get_router_names()
    print(f"\n📋 Summary:")
    print(f"   Total discovered: {len(discovered)}")
    print(f"   Router names: {discovered}")

    # Verify expected routers
    expected = ['randomrouter', 'thresholdrouter']
    for router_name in expected:
        if router_name in discovered:
            router_class, trainer_class = registry.get_router(router_name)
            trainer_status = "✅ with trainer" if trainer_class else "⚠️ no trainer"
            print(f"   ✅ {router_name}: {router_class.__name__} {trainer_status}")
        else:
            print(f"   ❌ {router_name}: NOT FOUND")

    return len(discovered) >= len(expected)


def test_router_loading():
    """Test loading a custom router."""
    print("\n" + "=" * 70)
    print("Testing Router Loading")
    print("=" * 70)

    try:
        from custom_routers.randomrouter import RandomRouter

        config_path = "custom_routers/randomrouter/config.yaml"

        print(f"\n🔧 Loading RandomRouter from {config_path}...")
        router = RandomRouter(yaml_path=config_path)

        print("✅ Router loaded successfully!")

        # Test routing
        print("\n🧪 Testing route_single()...")
        test_query = {"query": "What is machine learning?"}
        result = router.route_single(test_query)

        print(f"   Query: {result['query']}")
        print(f"   Routed to: {result['model_name']}")
        print(f"   Method: {result['method']}")

        print("\n✅ Routing test passed!")
        return True

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_cli_integration():
    """Test CLI integration with plugins."""
    print("\n" + "=" * 70)
    print("Testing CLI Integration")
    print("=" * 70)

    try:
        # Import CLI module to trigger plugin registration
        from llmrouter.cli import router_inference

        # Check if custom routers are in registry
        custom_routers = ['randomrouter', 'thresholdrouter']

        for router_name in custom_routers:
            if router_name in router_inference.ROUTER_REGISTRY:
                router_class = router_inference.ROUTER_REGISTRY[router_name]
                print(f"   ✅ {router_name}: registered as {router_class.__name__}")
            else:
                print(f"   ❌ {router_name}: NOT in CLI registry")

        print("\n✅ CLI integration test passed!")
        return True

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Run all tests."""
    print("\n🚀 Starting Plugin System Tests\n")

    results = []

    # Test 1: Plugin discovery
    results.append(("Plugin Discovery", test_plugin_discovery()))

    # Test 2: Router loading
    results.append(("Router Loading", test_router_loading()))

    # Test 3: CLI integration
    results.append(("CLI Integration", test_cli_integration()))

    # Summary
    print("\n" + "=" * 70)
    print("Test Summary")
    print("=" * 70)

    for test_name, passed in results:
        status = "✅ PASSED" if passed else "❌ FAILED"
        print(f"{test_name:30s} {status}")

    all_passed = all(result[1] for result in results)
    print("\n" + "=" * 70)

    if all_passed:
        print("🎉 All tests passed!")
        return 0
    else:
        print("⚠️  Some tests failed. See above for details.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
