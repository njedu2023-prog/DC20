# -*- coding: utf-8 -*-

from pathlib import Path
import pandas as pd


def write_latest_signal(df: pd.DataFrame, out_path="docs/signals/top10_latest.csv") -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)
    return out_path
