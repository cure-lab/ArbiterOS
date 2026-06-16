from abc import ABC, abstractmethod
from typing import Any
import torch


class BaseTrainer(ABC):
    """
    BaseTrainer (Abstract Class)
    ----------------------------
    Defines a unified interface for all router trainers.

    Each subclass should implement its own:
        - `loss_func()`: defines the loss computation logic.
        - `train()`: defines the training loop.
    """

    def __init__(
        self,
        router: torch.nn.Module,
        optimizer: torch.optim.Optimizer | None = None,
        device: str = "cuda",
        **kwargs: Any,
    ):
        """
        Initialize the base trainer.

        Args:
            router (nn.Module):
                A router model (e.g., MetaRouter subclass instance).
            optimizer (torch.optim.Optimizer | None):
                Optional optimizer. If None, the subclass may define one.
            device (str):
                Device to place the model (e.g., "cuda", "cpu").
            **kwargs:
                Extra keyword arguments for future extensions.
        """
        self.router = router
        self.optimizer = optimizer
        self.device = device
        self.kwargs = kwargs

    # ------------------------------------------------------------------
    # Abstract methods (must be implemented in subclasses)
    # ------------------------------------------------------------------

    def loss_func(self, outputs: Any, batch: Any) -> torch.Tensor:
        """
        Compute task-specific loss.

        Subclasses must implement this method.
        """
        raise NotImplementedError("Subclasses must implement loss_func()")

    @abstractmethod
    def train(self, dataloader: Any = None):
        """
        Define the full training loop.

        Subclasses must implement this method.

        Args:
            dataloader (Any, optional):
                Optional dataloader for training data. Some trainers
                may use data from the router itself instead.
        """
        pass



