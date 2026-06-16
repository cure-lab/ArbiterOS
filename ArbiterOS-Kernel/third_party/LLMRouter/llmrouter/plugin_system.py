"""
Plugin System for LLMRouter
============================

This module provides a plugin discovery and registration system that allows
users to add custom router implementations without modifying the core codebase.

Usage:
    1. Create a directory for custom routers (e.g., custom_routers/)
    2. Implement your router following the MetaRouter interface
    3. The plugin system will automatically discover and register it

Example Directory Structure:
    custom_routers/
    ├── __init__.py
    └── my_custom_router/
        ├── __init__.py
        ├── router.py       # Contains MyCustomRouter class
        └── trainer.py      # (Optional) Contains MyCustomRouterTrainer class
"""

import os
import sys
import importlib.util
from pathlib import Path
from typing import Dict, Tuple, Any, Optional, List
import inspect


class PluginRegistry:
    """
    Central registry for managing custom router plugins.

    This class handles:
    - Discovery of custom routers from plugin directories
    - Validation of router implementations
    - Registration into the router and trainer registries
    """

    def __init__(self):
        """Initialize the plugin registry."""
        self.discovered_routers: Dict[str, Tuple[Any, Optional[Any]]] = {}
        self.plugin_paths: List[str] = []

    def discover_plugins(self, plugin_dir: str, verbose: bool = False) -> None:
        """
        Discover and load router plugins from a directory.

        Args:
            plugin_dir: Path to directory containing custom routers
            verbose: Whether to print discovery information

        The directory structure should be:
            plugin_dir/
            ├── __init__.py (optional)
            └── router_name/
                ├── __init__.py
                ├── router.py       # Must contain a Router class inheriting from MetaRouter
                └── trainer.py      # Optional: Contains a Trainer class

        Each router module should export its main class via __init__.py or
        have a class name ending with 'Router' in router.py
        """
        plugin_path = Path(plugin_dir)

        if not plugin_path.exists():
            if verbose:
                print(f"⚠️  Plugin directory not found: {plugin_dir}")
            return

        if not plugin_path.is_dir():
            if verbose:
                print(f"⚠️  Plugin path is not a directory: {plugin_dir}")
            return

        # Add plugin directory to Python path if not already there
        plugin_dir_str = str(plugin_path.resolve())
        if plugin_dir_str not in sys.path:
            sys.path.insert(0, plugin_dir_str)
            self.plugin_paths.append(plugin_dir_str)

        if verbose:
            print(f"\n🔍 Discovering plugins in: {plugin_dir}")
            print("=" * 70)

        # Scan for subdirectories (each is a potential router plugin)
        for item in plugin_path.iterdir():
            if item.is_dir() and not item.name.startswith('_'):
                self._load_router_from_directory(item, verbose=verbose)

        if verbose:
            print(f"\n✅ Discovered {len(self.discovered_routers)} custom router(s)")
            print("=" * 70)

    def _load_router_from_directory(self, router_dir: Path, verbose: bool = False) -> None:
        """
        Load a router plugin from a directory.

        Args:
            router_dir: Path to router plugin directory
            verbose: Whether to print loading information
        """
        router_name = router_dir.name.lower()

        try:
            # Try to import the router module
            router_class = self._import_router_class(router_dir)
            trainer_class = self._import_trainer_class(router_dir)

            if router_class is None:
                if verbose:
                    print(f"⚠️  Skipped {router_name}: No valid Router class found")
                return

            # Validate router class
            if not self._validate_router_class(router_class):
                if verbose:
                    print(f"❌ Skipped {router_name}: Router class validation failed")
                return

            # Register the router
            self.discovered_routers[router_name] = (router_class, trainer_class)

            if verbose:
                trainer_info = f" + {trainer_class.__name__}" if trainer_class else ""
                print(f"✅ Loaded: {router_name:25s} -> {router_class.__name__}{trainer_info}")

        except Exception as e:
            if verbose:
                print(f"❌ Error loading {router_name}: {str(e)}")

    def _import_router_class(self, router_dir: Path) -> Optional[Any]:
        """
        Import the Router class from a plugin directory.

        Tries multiple strategies:
        1. Import from __init__.py exports
        2. Look for router.py with a class ending in 'Router'
        3. Look for model.py with a class ending in 'Router'

        Args:
            router_dir: Path to router directory

        Returns:
            Router class or None if not found
        """
        module_name = router_dir.name

        # Strategy 1: Try importing from __init__.py
        try:
            spec = importlib.util.spec_from_file_location(
                module_name,
                router_dir / "__init__.py"
            )
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                # Look for a class that ends with 'Router'
                for name, obj in inspect.getmembers(module, inspect.isclass):
                    if name.endswith('Router') and not name.startswith('Meta'):
                        return obj
        except (FileNotFoundError, AttributeError, ImportError):
            pass

        # Strategy 2: Try router.py
        router_file = router_dir / "router.py"
        if router_file.exists():
            try:
                spec = importlib.util.spec_from_file_location(
                    f"{module_name}.router",
                    router_file
                )
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)

                    for name, obj in inspect.getmembers(module, inspect.isclass):
                        if name.endswith('Router') and not name.startswith('Meta'):
                            return obj
            except (ImportError, AttributeError):
                pass

        # Strategy 3: Try model.py
        model_file = router_dir / "model.py"
        if model_file.exists():
            try:
                spec = importlib.util.spec_from_file_location(
                    f"{module_name}.model",
                    model_file
                )
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)

                    for name, obj in inspect.getmembers(module, inspect.isclass):
                        if name.endswith('Router') and not name.startswith('Meta'):
                            return obj
            except (ImportError, AttributeError):
                pass

        return None

    def _import_trainer_class(self, router_dir: Path) -> Optional[Any]:
        """
        Import the Trainer class from a plugin directory (optional).

        Args:
            router_dir: Path to router directory

        Returns:
            Trainer class or None if not found
        """
        module_name = router_dir.name
        trainer_file = router_dir / "trainer.py"

        if not trainer_file.exists():
            return None

        try:
            spec = importlib.util.spec_from_file_location(
                f"{module_name}.trainer",
                trainer_file
            )
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                # Look for a class that ends with 'Trainer'
                for name, obj in inspect.getmembers(module, inspect.isclass):
                    if name.endswith('Trainer') and not name.startswith('Base'):
                        return obj
        except (ImportError, AttributeError):
            pass

        return None

    def _validate_router_class(self, router_class: Any) -> bool:
        """
        Validate that a router class implements the required interface.

        Args:
            router_class: Router class to validate

        Returns:
            True if valid, False otherwise
        """
        # Check if it has the required methods
        required_methods = ['route_single', 'route_batch']

        for method_name in required_methods:
            if not hasattr(router_class, method_name):
                return False

        return True

    def register_to_dict(self, target_dict: Dict[str, Any]) -> None:
        """
        Register discovered routers into a target registry dictionary.

        Args:
            target_dict: Dictionary to register routers into
        """
        for router_name, (router_class, trainer_class) in self.discovered_routers.items():
            # For inference registry (router only)
            if trainer_class is None:
                target_dict[router_name] = router_class
            else:
                # For training registry (router + trainer tuple)
                target_dict[router_name] = (router_class, trainer_class)

    def get_router_names(self) -> List[str]:
        """Get list of discovered router names."""
        return list(self.discovered_routers.keys())

    def get_router(self, name: str) -> Optional[Tuple[Any, Optional[Any]]]:
        """
        Get a router by name.

        Args:
            name: Router name

        Returns:
            Tuple of (RouterClass, TrainerClass) or None if not found
        """
        return self.discovered_routers.get(name.lower())


# Global plugin registry instance
_global_registry = PluginRegistry()


def discover_and_register_plugins(
    plugin_dirs: Optional[List[str]] = None,
    verbose: bool = False
) -> PluginRegistry:
    """
    Discover and register custom router plugins from specified directories.

    Args:
        plugin_dirs: List of directories to scan for plugins.
                    If None, uses default locations:
                    - ./custom_routers/
                    - ~/.llmrouter/plugins/
                    - $LLMROUTER_PLUGINS environment variable
        verbose: Whether to print discovery information

    Returns:
        PluginRegistry instance with discovered routers
    """
    if plugin_dirs is None:
        plugin_dirs = []

        # Default location 1: ./custom_routers/ (relative to current directory)
        if os.path.exists("custom_routers"):
            plugin_dirs.append("custom_routers")

        # Default location 2: ~/.llmrouter/plugins/
        home_plugins = Path.home() / ".llmrouter" / "plugins"
        if home_plugins.exists():
            plugin_dirs.append(str(home_plugins))

        # Default location 3: Environment variable
        env_plugins = os.environ.get("LLMROUTER_PLUGINS")
        if env_plugins:
            plugin_dirs.extend(env_plugins.split(":"))

    # Discover plugins from all directories
    for plugin_dir in plugin_dirs:
        _global_registry.discover_plugins(plugin_dir, verbose=verbose)

    return _global_registry


def get_plugin_registry() -> PluginRegistry:
    """Get the global plugin registry instance."""
    return _global_registry
