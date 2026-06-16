import os
import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader

from llmrouter.models.base_trainer import BaseTrainer
from llmrouter.utils import save_model, get_longformer_embedding

from .router import BilinearMF


class PairwiseDataset(Dataset):
    """Dataset for pairwise training samples."""

    def __init__(self, pairs, query_embedding_data=None):
        self.pairs = pairs
        self.query_embedding_data = query_embedding_data
        self.use_precomputed = query_embedding_data is not None

    def __len__(self):
        return len(self.pairs)

    def __getitem__(self, idx):
        sample = self.pairs[idx]
        winner = sample["winner"]
        loser = sample["loser"]

        # Get embedding
        if self.use_precomputed and "embedding_id" in sample:
            emb = self.query_embedding_data[sample["embedding_id"]]
            if isinstance(emb, np.ndarray):
                emb = torch.from_numpy(emb).float()
            else:
                emb = emb.float()
        else:
            # Fallback: return query text for on-the-fly computation
            # This path is slower but still supported
            emb = get_longformer_embedding(sample["query"]).float()

        return {
            "winner": torch.tensor(winner, dtype=torch.long),
            "loser": torch.tensor(loser, dtype=torch.long),
            "embedding": emb,
        }


class MFRouterTrainer(BaseTrainer):
    """
    MFRouterTrainer
    Trains BilinearMF using pairwise logistic loss:
        L = BCE( δ(win,q) − δ(loss,q), 1 )

    Supports batch training for faster convergence.
    """

    def __init__(self, router, optimizer=None, device="cpu"):
        super().__init__(router=router, optimizer=optimizer, device=device)

        self.router = router
        self.pairs = router.pairs
        self.dim = router.dim
        self.text_dim = router.text_dim
        self.device = device

        # Use precomputed embeddings if available (much faster than calling Longformer)
        self.query_embedding_data = getattr(router, 'query_embedding_data', None)

        # -----------------------
        # MATCH mlptrainer.py: save path only
        # -----------------------
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        self.save_model_path = os.path.join(
            project_root, router.cfg["model_path"]["save_model_path"]
        )

        # Build bilinear MF model
        self.model = BilinearMF(
            dim=self.dim,
            num_models=len(router.model_to_idx),
            text_dim=self.text_dim,
        ).to(device)

        hparam = router.cfg["hparam"]
        self.lr = hparam.get("lr", 1e-3)
        self.epochs = hparam.get("epochs", 3)
        self.noise_alpha = hparam.get("noise_alpha", 0.0)
        self.batch_size = hparam.get("batch_size", 64)

        self.optimizer = torch.optim.Adam(self.model.parameters(), lr=self.lr)

        if self.query_embedding_data is not None:
            print("[MFRouterTrainer] Initialized with precomputed embeddings (fast mode).")
        else:
            print("[MFRouterTrainer] Initialized (will compute embeddings on-the-fly).")
        print(f"[MFRouterTrainer] Batch size: {self.batch_size}")

    # ---------------------------------------------------------
    # Full training loop with batch processing
    # ---------------------------------------------------------
    def train(self):
        model = self.model
        optimizer = self.optimizer
        loss_fn = nn.BCEWithLogitsLoss()

        # Create dataset and dataloader for efficient batch training
        dataset = PairwiseDataset(self.pairs, self.query_embedding_data)
        dataloader = DataLoader(
            dataset,
            batch_size=self.batch_size,
            shuffle=True,
            num_workers=0,  # Keep 0 for compatibility with precomputed embeddings
            pin_memory=(self.device != "cpu"),
        )

        num_batches = len(dataloader)
        print(f"[MFRouterTrainer] Training with {len(self.pairs)} samples, "
              f"{num_batches} batches per epoch")

        for epoch in range(self.epochs):
            epoch_losses = []

            for batch in dataloader:
                # Move batch to device
                win_ids = batch["winner"].to(self.device)
                loss_ids = batch["loser"].to(self.device)
                q_emb = batch["embedding"].to(self.device)

                # Project embeddings
                q_emb_proj = model.project_text(q_emb)

                # Optional noise for regularization
                if self.noise_alpha > 0:
                    q_emb_proj = q_emb_proj + torch.randn_like(q_emb_proj) * self.noise_alpha

                # Forward pass (batched)
                logit = model(win_ids, loss_ids, q_emb_proj)
                target = torch.ones_like(logit)

                # Backward pass
                optimizer.zero_grad()
                loss = loss_fn(logit, target)
                loss.backward()
                optimizer.step()

                epoch_losses.append(loss.item())

            print(
                f"[MFRouterTrainer] Epoch {epoch+1}/{self.epochs} "
                f"- Loss={np.mean(epoch_losses):.6f}"
            )

        # ---------------------------------------------------------
        # Save model (MATCH MLPRouter format)
        # ---------------------------------------------------------
        save_model(model.state_dict(), self.save_model_path)
        print(f"[MFRouterTrainer] Model saved to {self.save_model_path}")
