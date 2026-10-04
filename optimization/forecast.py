"""Synthetic hourly load forecast with known production plans and weather forecasts.

Uses an explicit time split; never converts daily tce data into measured kW.
"""
import numpy as np
from sklearn.linear_model import Ridge


def demo(seed=42):
    rng = np.random.default_rng(seed)
    hours = np.arange(24 * 100)
    hour = hours % 24
    weekday = (hours // 24) % 7
    plan = np.where((hour >= 8) & (hour < 20) & (weekday < 5), 10., 3.)
    weather_forecast = 22 + 8 * np.sin(hours / (24 * 14))
    features = np.column_stack((plan, np.maximum(weather_forecast - 24, 0),
                               np.sin(2 * np.pi * hour / 24),
                               np.cos(2 * np.pi * hour / 24), weekday >= 5))
    load = 100 + 20 * plan + 5 * features[:, 1] + rng.normal(0, 5, len(hours))
    split = 24 * 83
    end_validation = 24 * 97
    model = Ridge(alpha=1.).fit(features[:split], load[:split])
    prediction = model.predict(features[split:end_validation])
    naive = load[split - 168:end_validation - 168]
    mae = lambda a: float(np.mean(np.abs(load[split:end_validation] - a)))
    # Refit after the validation period, forecast the final 72 withheld hours.
    model.fit(features[:end_validation], load[:end_validation])
    forecast = model.predict(features[end_validation:])
    return {"data_source": "synthetic", "seed": seed, "model": "Ridge",
            "train_hours": split, "validation_hours": end_validation - split,
            "forecast_horizon_hours": len(forecast), "mae_kw": mae(prediction),
            "weekly_naive_mae_kw": mae(naive), "forecast_kw": forecast.tolist(),
            "planned_production_units": plan[end_validation:].tolist(),
            "assumptions": "未来产量为预先已知计划，温度为模拟天气预报；不表示真实厂预测效果"}
