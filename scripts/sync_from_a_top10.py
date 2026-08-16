#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import shutil
from pathlib import Path
from datetime import datetime

import requests

OWNER = os.getenv("A_TOP10_OWNER", "njedu2023-prog")
REPO = os.getenv("A_TOP10_REPO", "a-top10")
BRANCH = os.getenv("A_TOP10_BRANCH", "main")
DIR_PATH = os.getenv("A_TOP10_DIR", "outputs/learning")

PRED_DIR = Path("data/pred")
PRED_DIR.mkdir(parents=True, exist_ok=True)

PATTERN = re.compile(r"^pred_top10_(\d{8})\.csv$")


def gh_api_headers():
    headers = {"Accept": "application/vnd.github+json"}
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def list_dir_files():
    url = f"https://api.github.com/repos/{OWNER}/{REPO}/contents/{DIR_PATH}?ref={BRANCH}"
    r = requests.get(url, headers=gh_api_headers(), timeout=20)
    r.raise_for_status()
    return r.json()


def find_latest_pred(files):
    candidates = []
    for item in files:
        name = item.get("name", "")
        m = PATTERN.match(name)
        if not m:
            continue
        ymd = m.group(1)
        candidates.append((ymd, item))

    if not candidates:
        raise RuntimeError("在 a-top10/outputs/learning 未找到 pred_top10_YYYYMMDD.csv")

    candidates.sort(key=lambda x: x[0])
    return candidates[-1][0], candidates[-1][1]


def download_file(download_url: str, out_path: Path):
    r = requests.get(download_url, timeout=30)
    r.raise_for_status()
    out_path.write_bytes(r.content)


def main():
    files = list_dir_files()
    trade_date, item = find_latest_pred(files)

    download_url = item.get("download_url")
    if not download_url:
        raise RuntimeError("GitHub API 返回对象缺少 download_url")

    out_file = PRED_DIR / f"pred_top10_{trade_date}.csv"
    download_file(download_url, out_file)

    latest_file = PRED_DIR / "pred_top10_latest.csv"
    shutil.copyfile(out_file, latest_file)

    stamp = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[sync] ok trade_date={trade_date} -> {out_file} (UTC {stamp})")


if __name__ == "__main__":
    main()
