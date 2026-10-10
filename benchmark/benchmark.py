import pandas as pd
import numpy as np
import time
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data" / "vizag_accidents.csv"

print("=" * 50)
print("TrueTraffic AI — Acceleration Benchmark")
print("pandas vs NVIDIA RAPIDS cuDF")
print("=" * 50)

# Load dataset
df_pandas = pd.read_csv(DATA)
print(f"\nDataset size: {len(df_pandas):,} records\n")

# --- PANDAS BENCHMARK ---
print("Running with pandas...")
start = time.time()

for _ in range(5):
    risk_scores = df_pandas.groupby("road_name").agg(
        avg_risk=("risk_score", "mean"),
        total_accidents=("accident_id", "count"),
        fatal_count=("fatalities", "sum"),
        peak_hour=("hour", lambda x: x.mode()[0])
    ).reset_index()

    risk_scores["danger_level"] = pd.cut(
        risk_scores["avg_risk"],
        bins=[0, 40, 65, 80, 100],
        labels=["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    )

pandas_time = (time.time() - start) / 5
print(f"pandas average time: {pandas_time:.3f} seconds")

# --- cuDF BENCHMARK ---
try:
    import cudf # type: ignore
    print("\nRunning with NVIDIA RAPIDS cuDF...")
    df_cudf = cudf.read_csv(DATA)

    start = time.time()
    for _ in range(5):
        risk_scores_gpu = df_cudf.groupby("road_name").agg(
            {"risk_score": "mean",
             "accident_id": "count",
             "fatalities": "sum"}
        ).reset_index()

    cudf_time = (time.time() - start) / 5
    print(f"cuDF average time:   {cudf_time:.3f} seconds")

    speedup = pandas_time / cudf_time
    print(f"\n🚀 RAPIDS is {speedup:.1f}x FASTER than pandas!")
    print(f"Time saved per query: {pandas_time - cudf_time:.3f} seconds")

except ImportError:
    print("\ncuDF not available (requires an NVIDIA GPU, e.g. a Colab T4 runtime).")
    print("Only the measured pandas result above is reported - no GPU numbers are estimated.")

print("\nRisk scores by road:")
print(risk_scores[["road_name", "avg_risk",
      "total_accidents"]].to_string(index=False))