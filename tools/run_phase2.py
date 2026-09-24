"""Canonical phase-two runner for CI and Airflow; all artifacts are rebuilt and fingerprinted."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from ml.provenance import verify_manifest, write_manifest


def run(*args: str) -> None:
    subprocess.run([sys.executable, *args], cwd=ROOT, check=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--energy", default="output/clean_batch_energy.csv")
    parser.add_argument("--production", default="output/clean_batch_production.csv")
    parser.add_argument("--deep", action="store_true")
    parser.add_argument("--deep-only", action="store_true")
    args = parser.parse_args()
    if args.deep and args.deep_only:
        parser.error("--deep 与 --deep-only 不能同时使用")
    inputs = {"energy": args.energy, "production": args.production}
    artifacts = {"features": "output/ml_features.csv",
                 "predictions": "output/ml_model_predictions.csv",
                 "metrics": "output/ml_model_metrics.json"}
    manifest = "output/ml_provenance.json"

    if args.deep_only:
        verify_manifest(manifest, inputs, artifacts)
        run("tools/verify_ml_artifacts.py", "--energy", args.energy,
            "--production", args.production, "--features", artifacts["features"],
            "--predictions", artifacts["predictions"], "--metrics", artifacts["metrics"])
    else:
        run("-m", "ml.feature_pipeline", "--energy", args.energy,
            "--production", args.production, "--output", artifacts["features"])
        run("-m", "ml.model_benchmark", "--features", artifacts["features"],
            "--predictions", artifacts["predictions"], "--metrics", artifacts["metrics"])

    if args.deep or args.deep_only:
        deep_artifacts = {"deep_predictions": "output/ml_deep_predictions.csv",
                          "deep_metrics": "output/ml_deep_metrics.json"}
        run("-m", "ml.deep_benchmark", "--features", artifacts["features"],
            "--predictions", deep_artifacts["deep_predictions"],
            "--metrics", deep_artifacts["deep_metrics"])
        artifacts.update(deep_artifacts)

    write_manifest(manifest, inputs, artifacts)
    run("tools/verify_ml_artifacts.py", "--energy", args.energy,
        "--production", args.production, "--features", artifacts["features"],
        "--predictions", artifacts["predictions"], "--metrics", artifacts["metrics"],
        "--provenance", manifest)
    if args.deep or args.deep_only:
        run("tools/verify_deep_artifacts.py", "--baseline-predictions", artifacts["predictions"],
            "--deep-predictions", artifacts["deep_predictions"],
            "--metrics", artifacts["deep_metrics"])


if __name__ == "__main__":
    main()
