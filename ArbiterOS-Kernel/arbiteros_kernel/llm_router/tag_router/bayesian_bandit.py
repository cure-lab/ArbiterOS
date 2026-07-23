"""
Bayesian Logistic Bandit: Thompson Sampling over per-model logistic regression.

Usage:
    bandit = BayesianLogisticBandit(feature_names, candidate_models, ...)
    bandit.fit_all(tagged_data)           # fit once from database
    model, info = bandit.select(tags)     # Thompson sample
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)


class BayesianLogisticBandit:
    """Per-model Bayesian logistic regression with Laplace-approximated posterior.

    fit_all(db: list) —批量拟合所有数据
    select(features) — Thompson采样选择最优模型
    """

    def __init__(
        self,
        feature_names: list[str],
        candidate_models: list[str],
        prior_var: float = 1.0,
        cost_weight: float = 0.3,
        model_costs: Optional[dict[str, float]] = None,
    ):
        self.feature_names = list(feature_names)
        self.candidate_models = list(candidate_models)
        self.prior_var = float(prior_var)
        self.cost_weight = float(cost_weight)
        self.model_costs = model_costs or {}
        self.dim = len(feature_names) + 1  # +1 intercept

        # Initialized as prior N(0, prior_var * I) for each model
        self.posteriors: dict[str, dict[str, np.ndarray]] = {}
        for m in self.candidate_models:
            self.posteriors[m] = {
                "mu": np.zeros(self.dim),
                "Sigma": np.eye(self.dim) * self.prior_var,
            }

    # ------------------------------------------------------------------
    # Batch fit
    # ------------------------------------------------------------------

    def fit_all(self, database: list[dict]) -> None:
        """
        从数据库批量拟合所有模型的后验。

        database: list of {model: str, tags: dict, success: bool}
        tags 的 key 来自 feature_names
        """
        # Group by model
        model_data: dict[str, list] = {m: [] for m in self.candidate_models}
        for row in database:
            m = row.get("model", "")
            if m not in model_data:
                continue
            x = self._featurize(row.get("tags", {}))
            y = 1.0 if row.get("success", False) else 0.0
            model_data[m].append((x, y))

        for model_id, rows in model_data.items():
            if not rows:
                self.posteriors[model_id] = {
                    "mu": np.zeros(self.dim),
                    "Sigma": np.eye(self.dim) * self.prior_var,
                }
                continue

            X = np.array([np.append(x, 1.0) for x, _ in rows])
            y = np.array([yy for _, yy in rows])

            self.posteriors[model_id] = self._laplace_approx(X, y)

        logger.info(
            f"[Bandit] fit_all done: { {m: len(d) for m, d in model_data.items()} }"
        )

    def _laplace_approx(self, X: np.ndarray, y: np.ndarray) -> dict[str, np.ndarray]:
        """MAP estimate + Hessian → posterior N(mu, Sigma)."""

        def neg_log_post(w):
            z = X @ w
            nll = np.sum(np.log1p(np.exp(z)) - y * z)
            prior = 0.5 * np.dot(w, w) / self.prior_var
            return nll + prior

        def grad(w):
            z = X @ w
            p = 1.0 / (1.0 + np.exp(-z))
            return X.T @ (p - y) + w / self.prior_var

        from scipy.optimize import minimize

        w0 = np.zeros(self.dim)
        res = minimize(neg_log_post, w0, jac=grad, method="L-BFGS-B",
                       options={"maxiter": 100, "ftol": 1e-10})
        w_map = res.x

        z = X @ w_map
        p = 1.0 / (1.0 + np.exp(-z))
        D = np.diag(p * (1.0 - p))
        H = X.T @ D @ X + np.eye(self.dim) / self.prior_var

        try:
            Sigma = np.linalg.inv(H)
        except np.linalg.LinAlgError:
            Sigma = np.linalg.inv(H + np.eye(self.dim) * 1e-6)

        Sigma = (Sigma + Sigma.T) / 2
        min_eig = np.min(np.linalg.eigvalsh(Sigma))
        if min_eig < 1e-10:
            Sigma += np.eye(self.dim) * (1e-10 - min_eig)

        return {"mu": w_map, "Sigma": Sigma}

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def select(
        self,
        features: dict[str, Any],
        candidate_models: Optional[list[str]] = None,
    ) -> tuple[str, dict[str, Any]]:
        """Thompson-sample the best model."""
        models = candidate_models or self.candidate_models
        x = self._featurize(features)

        costs = {}
        for m in models:
            costs[m] = self.model_costs.get(m, 0.0)
        max_cost = max(costs.values()) if costs else 1.0
        if max_cost <= 0:
            max_cost = 1.0

        scores, draws = {}, {}
        for m in models:
            p_success = self._sample_and_predict(m, x)
            cost_penalty = self.cost_weight * (costs[m] / max_cost)
            scores[m] = float(p_success - cost_penalty)
            draws[m] = float(p_success)

        selected = max(scores, key=scores.get)
        return selected, {
            "scores": {m: round(s, 4) for m, s in scores.items()},
            "predicted_success": {m: round(d, 4) for m, d in draws.items()},
            "costs": costs,
        }

    def _sample_and_predict(self, model_id: str, x: np.ndarray) -> float:
        post = self.posteriors.get(model_id)
        if post is None:
            w = np.random.randn(self.dim) * np.sqrt(self.prior_var)
        else:
            w = np.random.multivariate_normal(post["mu"], post["Sigma"])
        x_aug = np.append(x, 1.0)
        return float(1.0 / (1.0 + np.exp(-np.dot(w, x_aug))))

    # ------------------------------------------------------------------
    # Feature encoding
    # ------------------------------------------------------------------

    def _featurize(self, features: dict[str, Any]) -> np.ndarray:
        vals = [1.0 if features.get(name) is True else 0.0 for name in self.feature_names]
        return np.array(vals, dtype=np.float64)

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def get_stats_summary(self) -> dict[str, Any]:
        summary = {}
        for model_id in self.candidate_models:
            post = self.posteriors.get(model_id)
            if post is None:
                summary[model_id] = {"w_mean": None}
                continue
            summary[model_id] = {
                "w_mean": [round(float(v), 4) for v in post["mu"]],
                "w_std": [round(float(np.sqrt(post["Sigma"][i, i])), 4) for i in range(self.dim)],
            }
        return summary
