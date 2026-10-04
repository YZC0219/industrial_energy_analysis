"""Regenerate IEJ manuscript figures with units aligned to the revised target wording."""
from pathlib import Path
import pandas as pd
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "paper"
FIG = OUT / "figures"
plt.rcParams.update({"font.size": 8, "axes.titlesize": 9, "axes.labelsize": 8})

pred = pd.concat([
    pd.read_csv(ROOT / "output" / "ml_model_predictions.csv"),
    pd.read_csv(ROOT / "output" / "ml_deep_predictions.csv"),
], ignore_index=True)
pred["absolute_error"] = (pred.actual - pred.prediction).abs()
fold = pred.groupby(["model", "fold"], as_index=False).absolute_error.mean()
fig, ax = plt.subplots(figsize=(6.3, 3.0))
for name, group in fold.groupby("model"):
    ax.plot(group.fold, group.absolute_error, marker="o", linewidth=1.2, label=name)
ax.set(xlabel="Rolling test fold", ylabel="MAE (project-defined units)", title="Synthetic benchmark error by fold")
ax.grid(alpha=.25); ax.legend(frameon=False, ncol=2); fig.tight_layout()
fig.savefig(FIG / "fold_mae.png", dpi=220); plt.close(fig)

ab = pd.read_csv(OUT / "paper_ablation_by_fold.csv")
fig, ax = plt.subplots(figsize=(6.3, 3.0))
for name, group in ab.groupby("ablation", sort=False):
    ax.plot(group.fold, group.mae, marker="o", linewidth=1.1, label=name.replace("_", " "))
ax.set(xlabel="Rolling test fold", ylabel="MAE (project-defined units)", title="Synthetic LightGBM feature ablations")
ax.grid(alpha=.25); ax.legend(frameon=False, ncol=2, fontsize=6); fig.tight_layout()
fig.savefig(FIG / "ablation_mae.png", dpi=220); plt.close(fig)

shap = pd.read_csv(OUT / "paper_shap_fold1.csv")
columns = [column for column in shap.columns if column.startswith("shap::")]
imp = pd.Series({column.removeprefix("shap::"): shap[column].abs().mean() for column in columns}).nlargest(12).sort_values()
fig, ax = plt.subplots(figsize=(6.3, 3.3))
ax.barh(imp.index, imp.values, color="#3f78a8")
ax.set(xlabel="Mean absolute SHAP value (project-defined units)", title="TreeSHAP importance in first synthetic test fold")
ax.grid(axis="x", alpha=.2); fig.tight_layout()
fig.savefig(FIG / "shap_importance.png", dpi=220); plt.close(fig)
