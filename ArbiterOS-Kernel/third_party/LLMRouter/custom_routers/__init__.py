"""
Custom Routers Directory
=========================

This directory contains user-defined custom router implementations.

Each router should be in its own subdirectory with the following structure:

    custom_routers/
    └── my_router/
        ├── __init__.py      # (Optional) Export router class
        ├── router.py        # Router implementation
        ├── trainer.py       # (Optional) Trainer implementation
        └── config.yaml      # (Optional) Example configuration

The router class should inherit from llmrouter.models.MetaRouter and
implement the required methods: route_single() and route_batch().
"""
