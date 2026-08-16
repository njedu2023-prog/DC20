# -*- coding: utf-8 -*-

def to_jq_code(ts_code: str) -> str:
    """Convert 000001.SZ / 600000.SH -> 000001.XSHE / 600000.XSHG"""
    if not ts_code:
        return ""
    ts_code = ts_code.strip()
    if ts_code.endswith(".SZ"):
        return ts_code.replace(".SZ", ".XSHE")
    if ts_code.endswith(".SH"):
        return ts_code.replace(".SH", ".XSHG")
    return ts_code
