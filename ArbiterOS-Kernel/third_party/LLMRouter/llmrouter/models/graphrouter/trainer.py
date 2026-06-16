import torch
import os
from llmrouter.models.base_trainer import BaseTrainer


class GraphTrainer(BaseTrainer):
    """
    GraphTrainer: A trainer class for GraphRouter using GNN.

    Training workflow:
    1. Get training data and model from router
    2. Split validation set from training set
    3. Train GNN model
    4. Save best model
    """

    def __init__(self, router, optimizer=None, device=None):
        """
        Initialize GraphTrainer.

        Args:
            router: GraphRouter instance
            optimizer: Optional optimizer (if None, use AdamW from GNNPredictor)
            device: Device to use ('cuda' or 'cpu')
        """
        super().__init__(router=router, optimizer=optimizer, device=device)

        self.router = router
        self.device = device if device else ("cuda" if torch.cuda.is_available() else "cpu")

        # Get model paths
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        model_path_config = router.cfg.get("model_path", {})

        self.ini_model_path = os.path.join(
            project_root,
            model_path_config.get("ini_model_path", "models/gnn_model_init.pt")
        )
        self.save_model_path = os.path.join(
            project_root,
            model_path_config.get("save_model_path", "models/gnn_model.pt")
        )

        # Get GNN predictor
        self.gnn_predictor = router.gnn_predictor

        # Replace optimizer if provided
        if optimizer is not None:
            self.gnn_predictor.optimizer = optimizer

    def train(self):
        """
        Train the GNN model.

        Steps:
        1. Load initial model if exists
        2. Build training and validation data
        3. Train model
        4. Save best model
        """
        # Load initial model if exists
        if os.path.exists(self.ini_model_path) and self.ini_model_path.endswith(".pt"):
            state_dict = torch.load(self.ini_model_path, map_location='cpu')
            self.gnn_predictor.model.load_state_dict(state_dict)

        # Get training and validation data
        train_data, val_data = self.router.get_training_data()

        # Ensure save directory exists
        save_dir = os.path.dirname(self.save_model_path)
        if save_dir and not os.path.exists(save_dir):
            os.makedirs(save_dir)

        # Update model save path in config
        self.gnn_predictor.config['model_path'] = self.save_model_path

        # Train model
        best_result = self.gnn_predictor.train_validate(
            data=train_data,
            data_validate=val_data
        )

        return best_result




