# -*- coding: utf-8 -*-

import pandas as pd


def allocate_equal_weight(df: pd.DataFrame, risk_budget: float) -> pd.DataFrame:
    df = df.copy()
    n = len(df)
    if n <= 0:
        df["target_weight"] = []
        return df

    df["target_weight"] = (risk_budget / n)
    return df
