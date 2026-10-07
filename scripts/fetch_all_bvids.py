#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分页拉取某 UP 主全部投稿视频的 BV 号，写入批量文件（每行一个 BV）。

用法: python fetch_all_bvids.py <uid_or_space_url> [输出文件] [--data-dir 数据目录]
"""
import sys
import time
from pathlib import Path

import requests

from bili_api import WbiSigner, extract_uid
from bili_monitor import load_config, build_headers, ensure_data_dir, DEFAULT_DATA_DIR

BASE_DIR = Path(__file__).resolve().parent


def fetch_all(uid, headers, signer, ps=30, interval=1.5):
    bvids = []
    pn = 1
    total = None
    while True:
        params = signer.sign({"mid": uid, "ps": ps, "pn": pn, "order": "pubdate"})
        r = requests.get(
            "https://api.bilibili.com/x/space/wbi/arc/search",
            headers=headers, params=params, timeout=15,
        )
        d = r.json()
        if d.get("code") != 0:
            print(f"[ERR] pn={pn} code={d.get('code')} msg={d.get('message')}")
            break
        data = d.get("data") or {}
        total = (data.get("page") or {}).get("count", 0)
        vlist = (data.get("list") or {}).get("vlist") or []
        if not vlist:
            break
        for v in vlist:
            bvid = v.get("bvid")
            if bvid and bvid not in bvids:
                bvids.append(bvid)
        print(f"  pn={pn} 本页 {len(vlist)} 个，累计 {len(bvids)}/{total}")
        if len(bvids) >= total or len(vlist) < ps:
            break
        pn += 1
        time.sleep(interval)
    return bvids, total


def main():
    args = [a for a in sys.argv[1:]]
    data_dir = DEFAULT_DATA_DIR
    if "--data-dir" in args:
        i = args.index("--data-dir")
        data_dir = Path(args[i + 1])
        del args[i:i + 2]
    if not args:
        print("用法: python fetch_all_bvids.py <uid_or_space_url> [输出文件] [--data-dir 数据目录]")
        sys.exit(1)
    uid = extract_uid(args[0])
    if not uid:
        print(f"无法解析 UID: {args[0]}")
        sys.exit(1)
    data_dir = ensure_data_dir(data_dir)
    out = Path(args[1]) if len(args) > 1 else data_dir / "all_videos.txt"

    config = load_config(data_dir / "config.json")
    headers, cookies = build_headers(config, data_dir)
    print(f"Cookie 项数: {len(cookies)}（SESSDATA: {'SESSDATA' in cookies}）")
    signer = WbiSigner(headers)

    bvids, total = fetch_all(uid, headers, signer)
    out.write_text("\n".join(bvids) + "\n", encoding="utf-8")
    print(f"[完成] 共 {len(bvids)} 个视频（接口报告总数 {total}）-> {out}")


if __name__ == "__main__":
    main()
