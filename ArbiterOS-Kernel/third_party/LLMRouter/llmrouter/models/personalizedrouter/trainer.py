import os
import torch
from llmrouter.models.base_trainer import BaseTrainer


class PersonalizedRouterTrainer(BaseTrainer):
    def __init__(self, router, optimizer=None, device=None):
        super().__init__(router=router, optimizer=optimizer, device=device)

        self.router = router
        self.device = device if device else ("cuda" if torch.cuda.is_available() else "cpu")

        project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        model_path_config = router.cfg.get("model_path", {}) or {}

        self.ini_model_path = os.path.join(
            project_root, model_path_config.get("ini_model_path", "models/personalized_init.pt")
        )
        self.save_model_path = os.path.join(
            project_root, model_path_config.get("save_model_path", "models/personalizedrouter.pt")
        )

        self.gnn_predictor = router.gnn_predictor
        if optimizer is not None:
            self.gnn_predictor.optimizer = optimizer

    def loss_func(self, outputs, batch):
        raise NotImplementedError

    def train(self, dataloader=None):
        if os.path.exists(self.ini_model_path) and self.ini_model_path.endswith(".pt"):
            state_dict = torch.load(self.ini_model_path, map_location="cpu")
            self.gnn_predictor.model.load_state_dict(state_dict)

        train_data, val_data = self.router.get_training_data()
        test_data = self.router.get_test_data()

        save_dir = os.path.dirname(self.save_model_path)
        if save_dir and not os.path.exists(save_dir):
            os.makedirs(save_dir)

        self.gnn_predictor.config["model_path"] = self.save_model_path
        self.gnn_predictor.config["llm_num"] = self.router.num_llms
        self.gnn_predictor.config["user_num"] = self.router.num_users

        result = self.gnn_predictor.train_validate(
            data=train_data,
            data_validate=val_data,
            data_for_test=test_data,
        )
        return result
