import torch
import torch.nn.functional as F
from torch_geometric.nn import GeneralConv
from torch_geometric.data import Data
import torch.nn as nn
from torch.optim import AdamW


class FeatureAlign(nn.Module):
    """
    Feature alignment module: map query and LLM features to the same dimension.
    Removed task feature processing.
    """
    def __init__(self, query_feature_dim, llm_feature_dim, common_dim):
        super(FeatureAlign, self).__init__()
        self.query_transform = nn.Linear(query_feature_dim, common_dim)
        self.llm_transform = nn.Linear(llm_feature_dim, common_dim)

    def forward(self, query_features, llm_features):
        aligned_query_features = self.query_transform(query_features)
        aligned_llm_features = self.llm_transform(llm_features)
        aligned_features = torch.cat([aligned_query_features, aligned_llm_features], 0)
        return aligned_features


class EncoderDecoderNet(torch.nn.Module):
    """
    Encoder-Decoder network for graph-based routing.
    Removed task information, edge features only use performance (in_edges=1).
    """
    def __init__(self, query_feature_dim, llm_feature_dim, hidden_features, in_edges=1):
        super(EncoderDecoderNet, self).__init__()
        self.in_edges = in_edges
        self.model_align = FeatureAlign(query_feature_dim, llm_feature_dim, hidden_features)
        self.encoder_conv_1 = GeneralConv(
            in_channels=hidden_features,
            out_channels=hidden_features,
            in_edge_channels=in_edges
        )
        self.encoder_conv_2 = GeneralConv(
            in_channels=hidden_features,
            out_channels=hidden_features,
            in_edge_channels=in_edges
        )
        self.edge_mlp = nn.Linear(in_edges, in_edges)
        self.bn1 = nn.BatchNorm1d(hidden_features)
        self.bn2 = nn.BatchNorm1d(hidden_features)

    def forward(self, query_features, llm_features, edge_index, edge_mask=None,
                edge_can_see=None, edge_weight=None):
        if edge_mask is not None:
            edge_index_mask = edge_index[:, edge_can_see]
            edge_index_predict = edge_index[:, edge_mask]
            if edge_weight is not None:
                edge_weight_mask = edge_weight[edge_can_see]

        edge_weight_mask = F.leaky_relu(self.edge_mlp(edge_weight_mask.reshape(-1, self.in_edges)))
        edge_weight_mask = edge_weight_mask.reshape(-1, self.in_edges)

        x_ini = self.model_align(query_features, llm_features)
        x = F.leaky_relu(self.bn1(self.encoder_conv_1(x_ini, edge_index_mask, edge_attr=edge_weight_mask)))
        x = self.bn2(self.encoder_conv_2(x, edge_index_mask, edge_attr=edge_weight_mask))

        edge_predict = F.sigmoid(
            (x_ini[edge_index_predict[0]] * x[edge_index_predict[1]]).mean(dim=-1)
        )
        return edge_predict


class FormData:
    """
    Data formatting class: convert raw data to PyG Data object.
    Removed task_id processing, edge features only use performance.
    """
    def __init__(self, device):
        self.device = device

    def formulation(self, query_feature, llm_feature, org_node, des_node,
                    edge_feature, label, edge_mask, train_mask, valide_mask, test_mask):
        query_features = torch.tensor(query_feature, dtype=torch.float).to(self.device)
        llm_features = torch.tensor(llm_feature, dtype=torch.float).to(self.device)

        query_indices = list(range(len(query_features)))
        llm_indices = [i + len(query_indices) for i in range(len(llm_features))]

        des_node = [(i + 1 + org_node[-1]) for i in des_node]
        edge_index = torch.tensor([org_node, des_node], dtype=torch.long).to(self.device)

        # Edge features only use performance
        edge_weight = torch.tensor(edge_feature, dtype=torch.float).reshape(-1, 1).to(self.device)

        data = Data(
            query_features=query_features,
            llm_features=llm_features,
            edge_index=edge_index,
            edge_attr=edge_weight,
            query_indices=query_indices,
            llm_indices=llm_indices,
            label=torch.tensor(label, dtype=torch.float).to(self.device),
            edge_mask=edge_mask,
            train_mask=train_mask,
            valide_mask=valide_mask,
            test_mask=test_mask
        )
        return data


class GNNPredictor:
    """
    GNN Predictor: encapsulates model training, validation and prediction logic.
    """
    def __init__(self, query_feature_dim, llm_feature_dim, hidden_features_size,
                 in_edges_size, config, device):
        self.model = EncoderDecoderNet(
            query_feature_dim=query_feature_dim,
            llm_feature_dim=llm_feature_dim,
            hidden_features=hidden_features_size,
            in_edges=in_edges_size
        ).to(device)

        self.config = config
        self.device = device
        self.optimizer = AdamW(
            self.model.parameters(),
            lr=config.get('learning_rate', 0.001),
            weight_decay=config.get('weight_decay', 1e-4)
        )
        self.criterion = torch.nn.BCELoss()
        self.num_llms = config.get('llm_num', 10)

    def train_validate(self, data, data_validate):
        """Train and validate the model."""
        best_result = -1
        save_path = self.config.get('model_path', 'gnn_model.pt')

        self.train_mask = torch.tensor(data.train_mask, dtype=torch.bool)
        self.valide_mask = torch.tensor(data.valide_mask, dtype=torch.bool)

        train_epochs = self.config.get('train_epoch', 100)
        batch_size = self.config.get('batch_size', 4)
        train_mask_rate = self.config.get('train_mask_rate', 0.3)

        for epoch in range(train_epochs):
            # Training phase
            self.model.train()
            loss_mean = 0
            mask_train = data.edge_mask

            for _ in range(batch_size):
                mask = mask_train.clone().bool()
                random_mask = torch.rand(mask.size()) < train_mask_rate
                random_mask = random_mask.to(torch.bool)
                mask = torch.where(mask & random_mask, torch.tensor(False, dtype=torch.bool), mask)
                mask = mask.bool()
                edge_can_see = torch.logical_and(~mask, self.train_mask)

                self.optimizer.zero_grad()
                predicted_edges = self.model(
                    query_features=data.query_features,
                    llm_features=data.llm_features,
                    edge_index=data.edge_index,
                    edge_mask=mask,
                    edge_can_see=edge_can_see,
                    edge_weight=data.edge_attr
                )
                loss = self.criterion(predicted_edges.reshape(-1), data.label[mask].reshape(-1))
                loss_mean += loss

            loss_mean = loss_mean / batch_size
            loss_mean.backward()
            self.optimizer.step()

            # Validation phase
            self.model.eval()
            mask_validate = data_validate.edge_mask.clone().to(torch.bool)
            edge_can_see = self.train_mask

            with torch.no_grad():
                predicted_edges_validate = self.model(
                    query_features=data_validate.query_features,
                    llm_features=data_validate.llm_features,
                    edge_index=data_validate.edge_index,
                    edge_mask=mask_validate,
                    edge_can_see=edge_can_see,
                    edge_weight=data_validate.edge_attr
                )

                observe_edge = predicted_edges_validate.reshape(-1, self.num_llms)
                value_validate = data_validate.edge_attr[mask_validate].reshape(-1, self.num_llms)

                row_indices = torch.arange(len(value_validate))
                max_idx = torch.argmax(observe_edge, 1).cpu()
                result_validate = value_validate[row_indices, max_idx].mean()

                # Save best model
                if result_validate > best_result:
                    best_result = result_validate
                    torch.save(self.model.state_dict(), save_path)

                if epoch % 10 == 0:
                    print(f"Epoch {epoch}: train_loss={loss_mean:.4f}, val_result={result_validate:.4f}")

        print(f"Training completed. Best validation result: {best_result:.4f}")
        return best_result

    def predict(self, data):
        """
        Prediction mode: return the best LLM index for each query.
        Used for route_single and route_batch.
        """
        self.model.eval()
        mask = data.edge_mask.clone().to(torch.bool)
        # In prediction mode, all training edges are visible
        edge_can_see = torch.logical_or(data.train_mask, data.valide_mask).to(torch.bool)

        with torch.no_grad():
            edge_predict = self.model(
                query_features=data.query_features,
                llm_features=data.llm_features,
                edge_index=data.edge_index,
                edge_mask=mask,
                edge_can_see=edge_can_see,
                edge_weight=data.edge_attr
            )

        edge_predict = edge_predict.reshape(-1, self.num_llms)
        max_idx = torch.argmax(edge_predict, 1)

        return max_idx