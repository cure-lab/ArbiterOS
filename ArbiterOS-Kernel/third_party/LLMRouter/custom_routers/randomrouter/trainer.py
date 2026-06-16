"""
Random Router Trainer - Example Trainer Implementation
=======================================================

This is an example trainer for the RandomRouter.
Since RandomRouter doesn't actually need training, this serves as a template.
"""

from llmrouter.models.base_trainer import BaseTrainer


class RandomRouterTrainer(BaseTrainer):
    """
    Trainer for RandomRouter.

    Since RandomRouter uses random selection and doesn't learn from data,
    this trainer is a no-op (does nothing). However, it demonstrates the
    structure needed for trainable custom routers.

    For actual trainable routers, you would:
    1. Load training data in __init__
    2. Implement training logic in train()
    3. Save model weights after training
    """

    def __init__(self, router, config: dict, device: str = "cpu"):
        """
        Initialize the trainer.

        Args:
            router: RandomRouter instance
            config (dict): Configuration dictionary from YAML
            device (str): Device to use for training (e.g., 'cuda', 'cpu')
        """
        super().__init__(router, config, device)
        print("⚠️  RandomRouter does not require training (using random selection)")

    def train(self) -> None:
        """
        Train the router.

        For RandomRouter, this is a no-op since it doesn't learn from data.
        For real routers, implement your training logic here.

        Example training loop:
            for epoch in range(num_epochs):
                for batch in train_loader:
                    # Forward pass
                    outputs = self.router(batch)

                    # Compute loss
                    loss = self.compute_loss(outputs, batch)

                    # Backward pass
                    loss.backward()
                    optimizer.step()
                    optimizer.zero_grad()
        """
        print("✅ RandomRouter 'training' complete (no actual training needed)")
        print("   This router uses random selection and doesn't learn from data.")

    def compute_loss(self, outputs, batch):
        """
        Compute training loss (not used for RandomRouter).

        For trainable routers, implement your loss function here.
        Common loss functions:
            - Cross-entropy for classification-based routers
            - MSE for regression-based routers
            - Custom losses for specialized routers

        Args:
            outputs: Router outputs
            batch: Input batch with labels

        Returns:
            Loss tensor
        """
        raise NotImplementedError(
            "RandomRouter does not support loss computation. "
            "Implement this method for trainable routers."
        )
