from __future__ import annotations

from typing import Any, Dict, List, Optional
import os
import json
import pickle
import re
import gc

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import yaml

from llmrouter.models.meta_router import MetaRouter
from llmrouter.utils import get_longformer_embedding
from .graph_nn import form_data, GNN_prediction


class PersonalizedRouter(MetaRouter):
    def __init__(self, yaml_path: str):
        dummy_model = nn.Identity()
        super().__init__(model=dummy_model, yaml_path=None)

        if not yaml_path or not os.path.exists(yaml_path):
            raise FileNotFoundError(f"YAML file not found: {yaml_path}")

        self._yaml_path = os.path.abspath(yaml_path)
        self._yaml_dir = os.path.dirname(self._yaml_path)
        self._repo_root = self._find_repo_root(self._yaml_dir)

        with open(yaml_path, "r", encoding="utf-8") as f:
            self.cfg = yaml.safe_load(f) or {}

        weights_dict = self.cfg.get("metric", {}).get("weights", {})
        self.metric_weights = list(weights_dict.values())

        self.device = "cuda" if torch.cuda.is_available() else "cpu"

        self.gnn_params = self.cfg.get("hparam", {}) or {}
        self.embedding_dim = int(self.gnn_params.get("embedding_dim", 64))
        self.edge_dim = int(self.gnn_params.get("edge_dim", 1))
        self.num_users = int(self.gnn_params.get("user_num", 2))
        self.num_task = int(self.gnn_params.get("num_task", 4))
        self.split_ratio = self.gnn_params.get("split_ratio", [0.6, 0.2, 0.2])
        self.llm_family = self.gnn_params.get("llm_family", []) or []
        self.random_state = int(self.gnn_params.get("random_state", 42))

        self._set_seed(self.random_state)
        self._load_personalized_data()
        self._prepare_data_for_gnn()
        self._split_data()

        self.gnn_config = {
            "learning_rate": self.gnn_params.get("learning_rate", 0.001),
            "weight_decay": self.gnn_params.get("weight_decay", 1e-4),
            "train_epoch": self.gnn_params.get("train_epoch", 100),
            "batch_size": self.gnn_params.get("batch_size", 4),
            "train_mask_rate": self.gnn_params.get("train_mask_rate", 0.3),
            "llm_num": self.num_llms,
            "user_num": self.num_users,
            "num_task": self.num_task,
            "split_ratio": self.split_ratio,
            "llm_family": self.llm_family,
            "edge_dim": self.edge_dim,
            "embedding_dim": self.embedding_dim,
            "seed": self.random_state,
            "model_path": self._get_model_path("save_model_path"),
        }

        self.form_data = form_data(self.device)
        self.gnn_predictor = GNN_prediction(
            query_feature_dim=self.query_dim,
            llm_feature_dim=self.llm_dim,
            user_feature_dim=self.user_dim,
            hidden_features_size=self.embedding_dim,
            in_edges_size=self.edge_dim,
            config=self.gnn_config,
            device=self.device,
        )

        self._cached_graph_data: Optional[tuple[Any, Any, Any]] = None
        self._model_loaded = False

    def _find_repo_root(self, start_dir: str) -> str:
        cur = os.path.abspath(start_dir or os.getcwd())
        while True:
            has_pyproject = os.path.isfile(os.path.join(cur, "pyproject.toml"))
            has_git = os.path.isdir(os.path.join(cur, ".git"))
            has_llmrouter = os.path.isdir(os.path.join(cur, "llmrouter"))
            has_configs = os.path.isdir(os.path.join(cur, "configs"))
            if has_pyproject or has_git or (has_llmrouter and has_configs):
                return cur
            parent = os.path.dirname(cur)
            if parent == cur:
                return os.path.abspath(start_dir or os.getcwd())
            cur = parent

    def _get_project_root(self) -> str:
        return self._repo_root

    def _resolve_path(self, path_str: str, base_dir: Optional[str] = None) -> str:
        if os.path.isabs(path_str):
            return path_str

        candidates: List[str] = []
        if base_dir:
            candidates.append(os.path.join(base_dir, path_str))
        if getattr(self, "_yaml_dir", None):
            candidates.append(os.path.join(self._yaml_dir, path_str))
        candidates.append(os.path.abspath(path_str))
        candidates.append(os.path.join(self._get_project_root(), path_str))

        for candidate in candidates:
            if os.path.exists(candidate):
                return candidate

        return candidates[-1]

    def _load_json(self, path: str):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _load_pkl(self, path: str):
        with open(path, "rb") as f:
            return pickle.load(f)

    def _load_csv(self, path: str) -> pd.DataFrame:
        return pd.read_csv(path)

    def _select_csv_from_dir(self, dir_path: str) -> str:
        csv_files = [f for f in os.listdir(dir_path) if f.lower().endswith(".csv")]
        if not csv_files:
            raise FileNotFoundError(f"No CSV files found in directory: {dir_path}")
        if len(csv_files) == 1:
            return os.path.join(dir_path, csv_files[0])
        non_split = [f for f in csv_files if all(k not in f.lower() for k in ["train", "val", "test"])]
        chosen = non_split[0] if non_split else csv_files[0]
        return os.path.join(dir_path, chosen)

    def _load_personalized_data(self) -> None:
        data_path = self.cfg.get("data_path", {}) or {}
        routing_data_path = data_path.get("routing_data_path")

        if routing_data_path:
            abs_routing_path = self._resolve_path(routing_data_path)
            if os.path.isdir(abs_routing_path):
                abs_routing_path = self._select_csv_from_dir(abs_routing_path)
            if not os.path.exists(abs_routing_path):
                raise FileNotFoundError(f"routing_data_path not found: {abs_routing_path}")
            self.data_df = self._load_csv(abs_routing_path)
            base_dir = os.path.dirname(abs_routing_path)
        else:
            train_path = data_path.get("routing_data_train")
            val_path = data_path.get("routing_data_val")
            test_path = data_path.get("routing_data_test")
            if not train_path or not val_path or not test_path:
                raise ValueError("routing_data_train/val/test must be provided when routing_data_path is missing.")
            df_train = self._load_csv(self._resolve_path(train_path))
            df_val = self._load_csv(self._resolve_path(val_path))
            df_test = self._load_csv(self._resolve_path(test_path))
            self.data_df = pd.concat([df_train, df_val, df_test], ignore_index=True)
            base_dir = None

        llm_data_path = data_path.get("llm_data")
        llm_embedding_path = data_path.get("llm_embedding_data")
        if not llm_data_path or not llm_embedding_path:
            raise ValueError("Both llm_data and llm_embedding_data must be provided in YAML.")

        abs_llm_data = self._resolve_path(llm_data_path, base_dir=base_dir)
        abs_llm_embed = self._resolve_path(llm_embedding_path, base_dir=base_dir)

        if not os.path.exists(abs_llm_data):
            raise FileNotFoundError(f"llm_data not found: {abs_llm_data}")
        if not os.path.exists(abs_llm_embed):
            raise FileNotFoundError(f"llm_embedding_data not found: {abs_llm_embed}")

        self.llm_data = self._load_json(abs_llm_data)
        self.llm_embedding_data = self._load_pkl(abs_llm_embed)

        if isinstance(self.llm_data, dict):
            self.llm_names = list(self.llm_data.keys())
        elif isinstance(self.llm_data, list):
            self.llm_names = [
                item.get("name", f"llm_{i}") if isinstance(item, dict) else f"llm_{i}"
                for i, item in enumerate(self.llm_data)
            ]
        else:
            self.llm_names = []

        if not self.llm_names and isinstance(self.llm_embedding_data, dict):
            self.llm_names = list(self.llm_embedding_data.keys())

        self.llm_description_embedding = self._normalize_llm_embeddings(
            self.llm_embedding_data, self.llm_names
        )
        self.num_llms = int(self.llm_description_embedding.shape[0])

        if self.llm_names:
            if len(self.llm_names) < self.num_llms:
                self.llm_names.extend(
                    [f"llm_{i}" for i in range(len(self.llm_names), self.num_llms)]
                )
            else:
                self.llm_names = self.llm_names[: self.num_llms]
        if not self.llm_names:
            self.llm_names = [f"llm_{i}" for i in range(self.num_llms)]

    def _normalize_llm_embeddings(self, embedding_obj: Any, llm_names: List[str]) -> np.ndarray:
        if isinstance(embedding_obj, dict):
            embeddings = [embedding_obj[name] for name in llm_names if name in embedding_obj]
            return np.asarray(embeddings, dtype=np.float32)
        if isinstance(embedding_obj, (list, tuple, np.ndarray)):
            return np.asarray(embedding_obj, dtype=np.float32)
        raise ValueError("Unsupported llm_embedding_data format.")

    def _set_seed(self, seed: int) -> None:
        import random

        random.seed(seed)
        np.random.seed(seed)
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed(seed)
            torch.cuda.manual_seed_all(seed)
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.benchmark = False

    def _parse_vec(self, s: Any) -> np.ndarray:
        if isinstance(s, (list, tuple, np.ndarray)):
            arr = np.asarray(s, dtype=np.float32)
            return arr if arr.ndim == 1 else arr[0]
        s = (s or "").strip()
        s1 = re.sub(r"\s+", ", ", s)
        try:
            arr = json.loads(s1)
        except Exception:
            s2 = s1.replace("[[,", "[[").replace(", ,", ", ")
            arr = json.loads(s2)
        arr = np.asarray(arr, dtype=np.float32)
        return arr if arr.ndim == 1 else arr[0]

    def _prepare_data_for_gnn(self) -> None:
        df = self.data_df
        if "query" not in df.columns:
            raise ValueError("routing data must contain 'query' column.")
        if "llm" not in df.columns:
            raise ValueError("routing data must contain 'llm' column.")

        self.query_list = df["query"].values
        first_idx = df.drop_duplicates(subset=["query"], keep="first").index.to_numpy()

        if "query_embedding" not in df.columns:
            raise ValueError("routing data must contain 'query_embedding' column.")

        self.query_embedding_list = np.vstack(
            [self._parse_vec(df.at[i, "query_embedding"]) for i in first_idx]
        ).astype(np.float32)

        if "task_description_embedding" in df.columns:
            self.task_embedding_list = np.vstack(
                [self._parse_vec(df.at[i, "task_description_embedding"]) for i in first_idx]
            ).astype(np.float32)
        else:
            self.task_embedding_list = np.zeros(
                (self.query_embedding_list.shape[0], self.llm_description_embedding.shape[1]),
                dtype=np.float32,
            )

        self.user_embedding = np.eye(self.num_users, dtype=np.float32)

        effect_col = "effect" if "effect" in df.columns else None
        cost_col = "cost" if "cost" in df.columns else None
        pref_col = "performance_preference" if "performance_preference" in df.columns else None

        if effect_col is None:
            raise ValueError("routing data must contain 'effect' column.")

        self.effect_list = df[effect_col].to_numpy(dtype=np.float32)
        self.cost_list = df[cost_col].to_numpy(dtype=np.float32) if cost_col else np.zeros(len(df), dtype=np.float32)
        self.preference_list = df[pref_col].to_numpy(dtype=np.float32) if pref_col else np.ones(len(df), dtype=np.float32)
        self.best_llm_list = df["best_llm"].to_numpy() if "best_llm" in df.columns else np.zeros(len(df))

        self.query_mapping = {q: idx for idx, q in enumerate(df["query"].drop_duplicates().tolist())}
        unique_llms = df["llm"].drop_duplicates().tolist()
        self.llm_mapping = {llm: idx for idx, llm in enumerate(unique_llms)}

        self.num_unique_query = int(len(df) / (self.num_llms * self.num_users))

        self.query_dim = self.query_embedding_list.shape[1]
        self.llm_dim = self.llm_description_embedding.shape[1]
        self.user_dim = self.user_embedding.shape[1]

        if self.task_embedding_list.shape[1] != self.llm_dim:
            raise ValueError("task_description_embedding dim must match llm_embedding dim.")

        gc.collect()

    def _split_data(self) -> None:
        self.query_per_user = int(len(self.data_df) / self.num_users)
        self.unique_query_per_user = int(len(self.data_df) / self.num_users / self.num_task)
        split_ratio = self.split_ratio

        train_size = int(self.unique_query_per_user * split_ratio[0])
        val_size = int(self.unique_query_per_user * split_ratio[1])
        test_size = int(self.unique_query_per_user * split_ratio[2])

        train_idx: List[int] = []
        validate_idx: List[int] = []
        test_idx: List[int] = []

        for user_id in range(self.num_users):
            for task_id in range(self.num_task):
                start_idx = user_id * self.query_per_user + task_id * self.unique_query_per_user
                train_idx.extend(range(start_idx, start_idx + train_size))
                validate_idx.extend(range(start_idx + train_size, start_idx + train_size + val_size))
                test_idx.extend(range(start_idx + train_size + val_size, start_idx + train_size + val_size + test_size))

        self.combined_edge = np.concatenate(
            (self.cost_list.reshape(-1, 1), self.effect_list.reshape(-1, 1)), axis=1
        )

        for i, (preference, effect, cost) in enumerate(
            zip(self.preference_list, self.effect_list, self.cost_list)
        ):
            self.effect_list[i] = preference * effect - (1 - preference) * cost

        effect_re = self.effect_list.reshape(self.num_users, -1, self.num_llms)
        eye_3d = np.tile(np.eye(self.num_llms), (self.num_users, 1, 1))
        optim_index = np.argmax(effect_re, axis=2)
        eye_3ds = np.split(eye_3d, self.num_users, axis=0)
        optim_indices = np.split(optim_index, self.num_users, axis=0)
        label_list = []
        for i in range(self.num_users):
            eye_3ds[i] = np.squeeze(eye_3ds[i], axis=0)
            optim_indices[i] = np.squeeze(optim_indices[i], axis=0)
            label_i = eye_3ds[i][optim_indices[i]]
            label_list.append(label_i)

        self.label = np.array(label_list).reshape(-1, 1)

        self.edge_org_id = [self.query_mapping[query] for query in self.query_list]
        repeat = len(self.edge_org_id) // self.num_llms
        self.edge_des_id = list(range(self.num_llms)) * repeat
        remainder = len(self.edge_org_id) - len(self.edge_des_id)
        if remainder > 0:
            self.edge_des_id += list(range(remainder))

        self.mask_train = torch.zeros(len(self.edge_org_id))
        self.mask_train[train_idx] = 1
        self.mask_validate = torch.zeros(len(self.edge_org_id))
        self.mask_validate[validate_idx] = 1
        self.mask_test = torch.zeros(len(self.edge_org_id))
        self.mask_test[test_idx] = 1

    def _get_model_path(self, key: str) -> str:
        project_root = self._get_project_root()
        model_paths = self.cfg.get("model_path", {}) or {}
        return os.path.join(project_root, model_paths.get(key, f"models/{key}.pt"))

    def _ensure_model_loaded(self) -> None:
        if self._model_loaded:
            return
        load_model_path = self._get_model_path("load_model_path")
        if os.path.exists(load_model_path):
            state_dict = torch.load(load_model_path, map_location="cpu")
            self.gnn_predictor.model.load_state_dict(state_dict)
        self._model_loaded = True

    def _build_graph_data(self):
        train_data = self.form_data.formulation(
            task_id=self.task_embedding_list,
            query_feature=self.query_embedding_list,
            llm_feature=self.llm_description_embedding,
            user_feature=self.user_embedding,
            org_node=self.edge_org_id,
            des_node=self.edge_des_id,
            edge_feature=self.effect_list,
            label=self.label,
            edge_mask=self.mask_train,
            combined_edge=self.combined_edge,
            train_mask=self.mask_train,
            valide_mask=self.mask_validate,
            test_mask=self.mask_test,
            llm_mapping=self.llm_mapping,
            best_llm=self.best_llm_list,
            cost_list=self.cost_list,
            config=self.gnn_config,
        )
        val_data = self.form_data.formulation(
            task_id=self.task_embedding_list,
            query_feature=self.query_embedding_list,
            llm_feature=self.llm_description_embedding,
            user_feature=self.user_embedding,
            org_node=self.edge_org_id,
            des_node=self.edge_des_id,
            edge_feature=self.effect_list,
            label=self.label,
            edge_mask=self.mask_validate,
            combined_edge=self.combined_edge,
            train_mask=self.mask_train,
            valide_mask=self.mask_validate,
            test_mask=self.mask_test,
            llm_mapping=self.llm_mapping,
            best_llm=self.best_llm_list,
            cost_list=self.cost_list,
            config=self.gnn_config,
        )
        test_data = self.form_data.formulation(
            task_id=self.task_embedding_list,
            query_feature=self.query_embedding_list,
            llm_feature=self.llm_description_embedding,
            user_feature=self.user_embedding,
            org_node=self.edge_org_id,
            des_node=self.edge_des_id,
            edge_feature=self.effect_list,
            label=self.label,
            edge_mask=self.mask_test,
            combined_edge=self.combined_edge,
            train_mask=self.mask_train,
            valide_mask=self.mask_validate,
            test_mask=self.mask_test,
            llm_mapping=self.llm_mapping,
            best_llm=self.best_llm_list,
            cost_list=self.cost_list,
            config=self.gnn_config,
        )
        return train_data, val_data, test_data

    def _get_cached_graph_data(self):
        if self._cached_graph_data is None:
            self._cached_graph_data = self._build_graph_data()
        return self._cached_graph_data

    def get_training_data(self):
        train_data, val_data, _ = self._get_cached_graph_data()
        return train_data, val_data

    def get_test_data(self):
        _, _, test_data = self._get_cached_graph_data()
        return test_data

    def _prepare_query_embedding(self, query: Dict[str, Any]) -> np.ndarray:
        if "query_embedding" in query:
            emb = np.asarray(query["query_embedding"], dtype=np.float32)
        else:
            query_text = query.get("query", "")
            emb = get_longformer_embedding(query_text)
            if isinstance(emb, torch.Tensor):
                emb = emb.cpu().numpy()
            emb = np.asarray(emb, dtype=np.float32)
        if emb.ndim > 1:
            emb = emb[0]
        return emb

    def _prepare_task_embedding(self, query: Dict[str, Any]) -> np.ndarray:
        if "task_embedding" in query:
            emb = np.asarray(query["task_embedding"], dtype=np.float32)
            if emb.ndim > 1:
                emb = emb[0]
            return emb
        if "task_description_embedding" in query:
            emb = np.asarray(query["task_description_embedding"], dtype=np.float32)
            if emb.ndim > 1:
                emb = emb[0]
            return emb
        return np.zeros(self.llm_dim, dtype=np.float32)

    def _make_user_feature(self, user_id: int) -> np.ndarray:
        user_id = int(user_id)
        user_id = max(0, min(user_id, self.num_users - 1))
        one_hot = np.zeros((1, self.num_users), dtype=np.float32)
        one_hot[0, user_id] = 1.0
        return one_hot

    def _predict_edges(self, data) -> torch.Tensor:
        self.gnn_predictor.model.eval()
        mask = data.edge_mask.clone().detach().bool()
        train_mask = data.train_mask
        if isinstance(train_mask, torch.Tensor):
            train_mask = train_mask.clone().detach().bool()
        else:
            train_mask = torch.as_tensor(train_mask, dtype=torch.bool)

        valide_mask = data.valide_mask
        if isinstance(valide_mask, torch.Tensor):
            valide_mask = valide_mask.clone().detach().bool()
        else:
            valide_mask = torch.as_tensor(valide_mask, dtype=torch.bool)
        edge_can_see = torch.logical_or(train_mask, valide_mask)
        with torch.no_grad():
            edge_predict = self.gnn_predictor.model(
                task_id=data.task_id,
                query_features=data.query_features,
                llm_features=data.llm_features,
                user_features=data.user_features,
                edge_index=data.edge_index,
                edge_mask=mask,
                edge_can_see=edge_can_see,
                edge_weight=data.combined_edge,
            )
        return edge_predict

    def route_single(self, query: Dict[str, Any]) -> Dict[str, Any]:
        self._ensure_model_loaded()

        user_id = query.get("user_id", 0)
        query_embedding = self._prepare_query_embedding(query).reshape(1, -1)
        task_embedding = self._prepare_task_embedding(query).reshape(1, -1)

        if query_embedding.shape[1] != self.query_dim:
            if query_embedding.shape[1] > self.query_dim:
                query_embedding = query_embedding[:, : self.query_dim]
            else:
                pad = self.query_dim - query_embedding.shape[1]
                query_embedding = np.pad(query_embedding, ((0, 0), (0, pad)), mode="constant")

        if task_embedding.shape[1] != self.llm_dim:
            if task_embedding.shape[1] > self.llm_dim:
                task_embedding = task_embedding[:, : self.llm_dim]
            else:
                pad = self.llm_dim - task_embedding.shape[1]
                task_embedding = np.pad(task_embedding, ((0, 0), (0, pad)), mode="constant")
        user_feature = self._make_user_feature(user_id)

        query_embeddings = np.vstack([self.query_embedding_list, query_embedding])
        task_embeddings = np.vstack([self.task_embedding_list, task_embedding])

        new_query_idx = self.query_embedding_list.shape[0]
        edge_org_id = self.edge_org_id + [new_query_idx] * self.num_llms
        edge_des_id = self.edge_des_id + list(range(self.num_llms))

        edge_feature = np.concatenate(
            [self.effect_list, np.zeros(self.num_llms, dtype=np.float32)], axis=0
        )
        cost_list = np.concatenate([self.cost_list, np.zeros(self.num_llms, dtype=np.float32)], axis=0)
        label = np.concatenate(
            [self.label, np.zeros((self.num_llms, 1), dtype=np.float32)], axis=0
        )
        best_llm = (
            np.concatenate([self.best_llm_list, np.zeros(self.num_llms)], axis=0)
            if len(self.best_llm_list) == len(self.edge_org_id)
            else self.best_llm_list
        )

        total_edges = len(edge_org_id)
        mask_predict = torch.zeros(total_edges)
        predict_start = len(self.edge_org_id)
        mask_predict[predict_start:] = 1

        mask_train = torch.zeros(total_edges)
        mask_train[: len(self.mask_train)] = self.mask_train
        mask_val = torch.zeros(total_edges)
        mask_val[: len(self.mask_validate)] = self.mask_validate
        mask_test = mask_predict.clone()

        data = self.form_data.formulation(
            task_id=task_embeddings,
            query_feature=query_embeddings,
            llm_feature=self.llm_description_embedding,
            user_feature=user_feature,
            org_node=edge_org_id,
            des_node=edge_des_id,
            edge_feature=edge_feature,
            label=label,
            edge_mask=mask_predict,
            combined_edge=edge_feature,
            train_mask=mask_train,
            valide_mask=mask_val,
            test_mask=mask_test,
            llm_mapping=self.llm_mapping,
            best_llm=best_llm,
            cost_list=cost_list,
            config=self.gnn_config,
        )

        predicted_edges = self._predict_edges(data)
        scores = predicted_edges.reshape(-1, self.num_llms)
        model_idx = torch.argmax(scores, dim=1)[0].item()
        model_name = self.llm_names[model_idx] if model_idx < len(self.llm_names) else f"llm_{model_idx}"

        query_output = dict(query)
        query_output["model_name"] = model_name
        return query_output

    def route_batch(self, batch: Optional[Any] = None, task_name: Optional[str] = None) -> List[Dict[str, Any]]:
        if batch is None:
            return []

        items = batch if isinstance(batch, list) else [batch]
        outputs = []
        for item in items:
            if isinstance(item, dict):
                result = self.route_single(item)
            else:
                result = self.route_single({"query": str(item), "user_id": 0})
            if task_name and isinstance(result, dict):
                result["task_name"] = task_name
            outputs.append(result)
        return outputs
