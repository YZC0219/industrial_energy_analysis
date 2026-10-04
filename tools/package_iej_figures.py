from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
root=Path(__file__).resolve().parents[1]
figs=[
 ('Figure_1_synthetic_fold_MAE.png',root/'output/paper/figures/fold_mae.png'),
 ('Figure_2_synthetic_ablation_MAE.png',root/'output/paper/figures/ablation_mae.png'),
 ('Figure_3_synthetic_SHAP.png',root/'output/paper/figures/shap_importance.png'),
 ('Figure_4_UCI_fold_MAE.png',root/'output/paper/figures/uci_steel/uci_fold_mae.png'),
 ('Figure_5_UCI_SHAP.png',root/'output/paper/figures/uci_steel/uci_shap_importance.png'),
]
out=root/'IEJ_separate_figure_files.zip'
with ZipFile(out,'w',ZIP_DEFLATED) as z:
 for name,path in figs:z.write(path,arcname=name)
print(out)
