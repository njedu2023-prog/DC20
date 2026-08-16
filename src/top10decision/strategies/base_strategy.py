# -*- coding: utf-8 -*-

import pandas as pd


class Strategy:
    name: str = "base"

    def run(self, df: pd.DataFrame) -> pd.DataFrame:
        raise NotImplementedError
