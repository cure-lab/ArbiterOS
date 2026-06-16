"""
Threshold Router Trainer
"""

import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader, TensorDataset
from tqdm import tqdm

from llmrouter.models.base_trainer import BaseTrainer


class ThresholdRouterTrainer(BaseTrainer):
    """
    Trainer for ThresholdRouter.

    Trains a difficulty estimator to predict which queries are hard/easy.
    Uses supervision from routing data (which model performed best on each query).
    """

    def __init__(self, router, config: dict, device: str = "cpu"):
        """Initialize trainer."""
        super().__init__(router, config, device)

        # Move model to device
        self.router.model = self.router.model.to(device)

        # Extract training hyperparameters
        hparam = config.get('hparam', {})
        self.learning_rate = hparam.get('learning_rate', 0.001)
        self.num_epochs = hparam.get('train_epoch', 10)
        self.batch_size = hparam.get('batch_size', 32)

        # Setup optimizer
        self.optimizer = optim.Adam(
            self.router.model.parameters(),
            lr=self.learning_rate
        )

        # Loss function
        self.criterion = nn.BCELoss()

        print(f"✅ ThresholdRouterTrainer initialized")
        print(f"   Device: {device}")
        print(f"   Learning rate: {self.learning_rate}")
        print(f"   Epochs: {self.num_epochs}")
        print(f"   Batch size: {self.batch_size}")

    def prepare_training_data(self):
        """
        Prepare training data from routing data.

        Creates labels based on which model performed best:
        - label = 0 if small model was best (easy query)
        - label = 1 if large model was best (hard query)
        """
        if not hasattr(self.router, 'routing_data_train'):
            raise ValueError("No training routing data found")

        embeddings = []
        labels = []

        for item in self.router.routing_data_train:
            # Get query embedding
            if 'embedding' in item:
                embedding = item['embedding']
            elif hasattr(self.router, 'query_embeddings'):
                # Try to get from query embeddings
                query_id = item.get('query_id') or item.get('id')
                if query_id in self.router.query_embeddings:
                    embedding = self.router.query_embeddings[query_id]
                else:
                    continue
            else:
                continue

            # Determine label based on best model
            best_model = item.get('best_llm') or item.get('ground_truth')
            if not best_model:
                continue

            # Label: 0 for small model, 1 for large model
            if best_model == self.router.small_model:
                label = 0.0
            elif best_model == self.router.large_model:
                label = 1.0
            else:
                # For other models, use a heuristic or skip
                # Here we skip queries routed to other models
                continue

            embeddings.append(embedding)
            labels.append(label)

        if not embeddings:
            raise ValueError("No valid training data found")

        # Convert to tensors
        embeddings_tensor = torch.tensor(embeddings, dtype=torch.float32)
        labels_tensor = torch.tensor(labels, dtype=torch.float32).unsqueeze(1)

        print(f"📊 Prepared {len(embeddings)} training samples")
        print(f"   Easy (label=0): {(labels_tensor == 0).sum().item()}")
        print(f"   Hard (label=1): {(labels_tensor == 1).sum().item()}")

        return embeddings_tensor, labels_tensor

    def train(self) -> None:
        """Train the difficulty estimator."""
        print("\n" + "=" * 70)
        print("Training ThresholdRouter")
        print("=" * 70)

        # Prepare data
        embeddings, labels = self.prepare_training_data()

        # Create data loader
        dataset = TensorDataset(embeddings, labels)
        train_loader = DataLoader(
            dataset,
            batch_size=self.batch_size,
            shuffle=True
        )

        # Training loop
        self.router.model.train()

        for epoch in range(self.num_epochs):
            total_loss = 0.0
            num_batches = 0

            progress_bar = tqdm(train_loader, desc=f"Epoch {epoch + 1}/{self.num_epochs}")

            for batch_embeddings, batch_labels in progress_bar:
                # Move to device
                batch_embeddings = batch_embeddings.to(self.device)
                batch_labels = batch_labels.to(self.device)

                # Forward pass
                predictions = self.router.model(batch_embeddings)

                # Compute loss
                loss = self.criterion(predictions, batch_labels)

                # Backward pass
                self.optimizer.zero_grad()
                loss.backward()
                self.optimizer.step()

                # Track loss
                total_loss += loss.item()
                num_batches += 1

                # Update progress bar
                progress_bar.set_postfix({'loss': f'{loss.item():.4f}'})

            avg_loss = total_loss / num_batches
            print(f"Epoch {epoch + 1}/{self.num_epochs} - Average Loss: {avg_loss:.4f}")

        # Save model
        save_path = self.config.get('model_path', {}).get('save_model_path')
        if save_path:
            self.router.save_router(save_path)
            print(f"💾 Model saved to {save_path}")

        print("\n✅ Training complete!")
        print("=" * 70)
