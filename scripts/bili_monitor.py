#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
B 站博主视频监控爬虫（增强版）

功能：
- 监控 bloggers.txt 中的 UP 主，定时采集其视频元信息
- 视频信息（标题/时长/描述/发布时间/点赞/评论/收藏/投币/播放等）
  可存入本地 Excel，或 MongoDB（预留接口）
- 视频下载可选（--download 参数控制）
- 可单独指定下载某个视频（--video BVxxxx）

  用法：
  python bili_monitor.py                     # 监控模式：只采集信息存 Excel，不下载视频
  python bili_monitor.py --download          # 监控模式：采集信息 + 下载新视频
  python bili_monitor.py --video BVxxxx      # 单独采集某个视频信息
  python bili_monitor.py --video BVxxxx --download   # 单独采集并下载某个视频
  python bili_monitor.py --once              # 只检查一轮后退出
  python bili_monitor.py --batch 链接文件.txt             # 批量采集视频信息（不下载）
  python bili_monitor.py --batch 链接文件.txt --download  # 批量采集 + 下载视频
"""

import json
import os
import re
import sys
import time
import logging
import argparse
from pathlib import Path

import requests

try:
    import yt_dlp
except ImportError:
    print("缺少 yt-dlp，请先运行: pip install yt-dlp")
    sys.exit(1)

from bili_api import (
    WbiSigner, extract_uid, extract_bvid,
    get_up_info, get_recent_videos, get_video_detail,
    get_video_comments, get_comment_replies, parse_comment,
)
from storage import (ExcelStorage, MongoStorage, normalize_record,
                     COMMENT_COLUMNS, COMMENT_TITLES)

# 代码目录（scripts/，脚本本体所在位置，复用不改动）
BASE_DIR = Path(__file__).resolve().parent

# 数据目录：所有配置、输入、输出都放这里。
# 默认是当前工作目录下的「data」，可通过 --data-dir 覆盖。
DEFAULT_DATA_DIR = Path.cwd() / "data"

# 数据目录里需要用户填写的文件模板（首次运行自动生成）
_TEMPLATE_CONFIG = {
    "bloggers_file": "bloggers.txt",
    "download_dir": "downloads",
    "interval_seconds": 300,
    "quality": "best",
    "max_videos_per_check": 10,
    "cookie_file": "cookies.txt",
    "state_file": "downloaded.json",
    "request_interval_seconds": 2,
    "log_file": "monitor.log",
    "excel_enabled": True,
    "excel_file": "videos.xlsx",
    "comments_excel_file": "comments.xlsx",
    "mongodb": {
        "enabled": False,
        "uri": "mongodb://用户名:密码@主机:27017/数据库名?authSource=admin",
        "db": "bilibili",
        "collection": "videos",
    },
}
_TEMPLATE_BLOGGERS = "# 每行一个 B 站 UP 主主页链接（或纯 UID），以 # 开头的行为注释\n# 示例：https://space.bilibili.com/2\n"
_TEMPLATE_COOKIES = (
    "# 在此粘贴你的 B 站登录 Cookie（二选一格式）：\n"
    "# 1) 单行：SESSDATA=xxx; bili_jct=xxx; DedeUserID=xxx\n"
    "# 2) Netscape 导出格式（多行）\n"
    "# 单个视频详情/下载无需登录；拉 UP 主全部视频列表需填 SESSDATA。\n"
)


def ensure_data_dir(data_dir):
    """确保数据目录存在，并生成缺失的模板文件（config/cookies/bloggers）。"""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    for name, content in (
        ("config.json", json.dumps(_TEMPLATE_CONFIG, ensure_ascii=False, indent=2) + "\n"),
        ("cookies.txt", _TEMPLATE_COOKIES),
        ("bloggers.txt", _TEMPLATE_BLOGGERS),
    ):
        f = data_dir / name
        if not f.exists():
            f.write_text(content, encoding="utf-8")
    return data_dir


def load_config(path):
    p = Path(path)
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def setup_logger(log_file):
    logger = logging.getLogger("bili_monitor")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        logger.addHandler(sh)
        if log_file:
            fh = logging.FileHandler(log_file, encoding="utf-8")
            fh.setFormatter(fmt)
            logger.addHandler(fh)
    return logger


def sanitize_name(name):
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    name = name.strip().rstrip(".")
    return name if name else "unknown"


def parse_bloggers(file_path):
    bloggers = []
    if not os.path.exists(file_path):
        return bloggers
    with open(file_path, "r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            uid = extract_uid(line)
            if uid:
                bloggers.append({"uid": uid, "url": line})
    return bloggers


def load_cookies(cookie_file):
    """读取 Cookie 文件为 dict，兼容 Netscape 与 name=value; 两种格式。"""
    cookies = {}
    if not os.path.exists(cookie_file):
        return cookies
    with open(cookie_file, "r", encoding="utf-8-sig") as f:
        raw = f.read()
    if not raw.strip():
        return cookies
    if "\t" in raw:
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) >= 7:
                cookies[parts[5]] = parts[6]
        if cookies:
            return cookies
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for pair in line.split(";"):
            pair = pair.strip()
            if "=" in pair:
                k, v = pair.split("=", 1)
                k, v = k.strip(), v.strip()
                if k:
                    cookies[k] = v
    return cookies


def cookies_to_netscape(cookies, out_path, domain=".bilibili.com"):
    lines = ["# Netscape HTTP Cookie File", "# auto generated"]
    now = int(time.time())
    for k, v in cookies.items():
        lines.append(f"{domain}\tTRUE\t/\tFALSE\t{now + 3600*24*365}\t{k}\t{v}")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def load_state(state_file):
    if os.path.exists(state_file):
        with open(state_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state_file, state):
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def download_video(bvid, up_name, up_uid, download_dir, quality, cookies):
    """下载单个视频，返回 (ok, message)。"""
    video_url = f"https://www.bilibili.com/video/{bvid}"
    download_dir = Path(download_dir)
    folder = download_dir / sanitize_name(f"{up_name}_{up_uid}")
    folder.mkdir(parents=True, exist_ok=True)
    fmt = "bv*+ba/b" if quality == "best" else quality
    opts = {
        "outtmpl": str(folder / "%(title)s.%(ext)s"),
        "format": fmt,
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 5,
        "fragment_retries": 5,
        "socket_timeout": 30,
    }
    tmp_cookie = None
    if cookies:
        tmp_cookie = folder / ".cookies_tmp.txt"
        cookies_to_netscape(cookies, tmp_cookie)
        opts["cookiefile"] = str(tmp_cookie)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(video_url, download=True)
            return True, info.get("title", bvid)
    except Exception as e:
        return False, str(e)
    finally:
        if tmp_cookie:
            try:
                tmp_cookie.unlink(missing_ok=True)
            except Exception:
                pass


class StorageHub:
    """统一管理 Excel / MongoDB 两种存储。"""

    def __init__(self, config, base_dir):
        self.storages = []
        self.base_dir = base_dir
        # Excel
        excel_enabled = config.get("excel_enabled", True)
        if excel_enabled:
            excel_file = config.get("excel_file", "videos.xlsx")
            if not os.path.isabs(excel_file):
                excel_file = base_dir / excel_file
            self.excel = ExcelStorage(excel_file)
        else:
            self.excel = None
        # MongoDB
        mongo_cfg = config.get("mongodb", {})
        if mongo_cfg.get("enabled"):
            uri = mongo_cfg.get("uri", "mongodb://localhost:27017")
            self.mongo = MongoStorage(
                uri,
                db_name=mongo_cfg.get("db"),
                collection=mongo_cfg.get("collection", "videos"),
            )
        else:
            self.mongo = None

    def mongo_status(self):
        """返回 MongoDB 连接状态描述（用于启动日志）。"""
        if not self.mongo:
            return None
        if self.mongo.connect():
            return f"已连接: {self.mongo._db.name}.{self.mongo.collection}"
        return "连接失败（请检查连接串或网络）"

    def save(self, record):
        """保存到所有启用的存储，返回各存储是否新插入。"""
        result = {}
        if self.excel:
            try:
                result["excel"] = self.excel.save_record(record)
            except Exception as e:
                result["excel_error"] = str(e)
        if self.mongo:
            try:
                result["mongo"] = self.mongo.save_record(record)
            except Exception as e:
                result["mongo_error"] = str(e)
        return result

    def close(self):
        if self.mongo:
            self.mongo.close()


def process_video(bvid, headers, cookies, hub, config, logger, do_download=False):
    """采集单个视频详情 + 可选下载 + 存库。返回详情 dict 或 None。"""
    detail = get_video_detail(bvid, headers)
    if not detail:
        logger.error(f"获取视频详情失败: {bvid}（可能风控或视频不存在）")
        return None
    rec = normalize_record(detail)
    # 存储
    save_res = hub.save(rec)
    if save_res.get("excel"):
        logger.info(f"已记录到 Excel: [{rec['up_name']}] {rec['title']}")
    elif save_res.get("excel") is False:
        logger.info(f"已存在，跳过: {rec['title']}")
    if "excel_error" in save_res:
        logger.error(f"Excel 写入失败: {save_res['excel_error']}")
    if save_res.get("mongo"):
        logger.info("已写入 MongoDB")
    if "mongo_error" in save_res:
        logger.error(f"MongoDB 写入失败: {save_res['mongo_error']}")
    # 下载
    if do_download:
        ok, msg = download_video(
            bvid, rec["up_name"], rec["up_uid"],
            config.get("download_dir", "downloads"),
            config.get("quality", "best"),
            cookies,
        )
        if ok:
            logger.info(f"下载完成: {msg}")
        else:
            logger.error(f"下载失败 {bvid}: {msg}")
    return detail


def check_one_blogger(blogger, headers, signer, cookies, hub, config, logger, do_download):
    """检查单个博主，采集新视频信息（可选下载）。"""
    uid = blogger["uid"]
    up_info = get_up_info(uid, headers, signer)
    up_name = up_info["name"]
    videos = get_recent_videos(uid, headers, signer, limit=config.get("max_videos_per_check", 10))

    if not videos:
        logger.warning(f"[{up_name}] 未获取到视频列表（可能风控或 Cookie 失效）")
        return

    # 已记录的视频集合（用于跳过）
    state = load_state(config.get("state_file", "downloaded.json"))
    done = set(state.get(uid, {}).get("bvids", []))

    new_count = 0
    for v in videos:
        bvid = v["bvid"]
        if not bvid:
            continue
        if bvid in done:
            continue
        logger.info(f"[{up_name}] 发现新视频: {v['title']} ({bvid})")
        detail = process_video(bvid, headers, cookies, hub, config, logger, do_download)
        if detail:
            state.setdefault(uid, {"name": up_name, "bvids": []})
            state[uid]["bvids"].append(bvid)
            state[uid]["name"] = up_name
            new_count += 1
        time.sleep(config.get("request_interval_seconds", 1))

    if new_count:
        save_state(config.get("state_file", "downloaded.json"), state)
    else:
        logger.info(f"[{up_name}] 无新视频")


def build_headers(config, base_dir):
    """构建请求头，返回 (headers, cookies_dict)。"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://www.bilibili.com/",
        "Accept": "application/json, text/plain, */*",
    }
    cookies = {}
    cookie_file = config.get("cookie_file", "cookies.txt")
    cookie_path = base_dir / cookie_file if not os.path.isabs(cookie_file) else Path(cookie_file)
    if os.path.exists(cookie_path):
        cookies = load_cookies(cookie_path)
        if cookies:
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
    return headers, cookies


def parse_batch_file(file_path):
    """读取批量文件，每行一个视频链接或 BV 号，返回去重后的 bvid 列表。"""
    bvids = []
    if not os.path.exists(file_path):
        return bvids
    with open(file_path, "r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            bvid = extract_bvid(line)
            if bvid and bvid not in bvids:
                bvids.append(bvid)
    return bvids


def cmd_batch(args, headers, cookies, hub, config, logger):
    """批量采集视频（从文件读取链接列表），可选下载。"""
    batch_file = Path(args.batch)
    if not batch_file.exists():
        print(f"批量文件不存在: {batch_file}")
        sys.exit(1)

    bvids = parse_batch_file(batch_file)
    if not bvids:
        print(f"批量文件 '{batch_file}' 中没有解析到有效的 BV 号")
        sys.exit(1)

    print("=" * 60)
    print(f"批量采集：共 {len(bvids)} 个视频，下载视频: {'是' if args.download else '否'}")
    print("=" * 60)

    req_interval = config.get("request_interval_seconds", 2)
    ok_count = 0
    fail_count = 0
    for i, bvid in enumerate(bvids, 1):
        print(f"\n[{i}/{len(bvids)}] 处理 {bvid} ...")
        detail = process_video(bvid, headers, cookies, hub, config, logger,
                               do_download=args.download)
        if detail:
            ok_count += 1
        else:
            fail_count += 1
        if i < len(bvids):
            time.sleep(req_interval)

    print("\n" + "=" * 60)
    print(f"[完成] 成功 {ok_count} 个，失败 {fail_count} 个，共 {len(bvids)} 个")


def cmd_comments(args, headers, base_dir, config):
    """采集视频评论（含可选子评论），写入 Excel。"""
    bvid = extract_bvid(args.comments)
    if not bvid:
        print(f"无法从 '{args.comments}' 解析出 BV 号")
        sys.exit(1)

    # 评论接口需要 oid=aid，先通过 view 接口拿 aid
    detail = get_video_detail(bvid, headers)
    if not detail:
        print(f"获取视频详情失败: {bvid}（可能风控或视频不存在）")
        sys.exit(1)
    aid = detail["aid"]

    max_count = args.count if args.count else 200
    print(f"[采集] 视频评论: {detail['title'][:30]} ({bvid})")
    print(f"       总评论数 {detail['reply']}，含子评论: {args.sub_comments}，上限 {max_count} 条")

    comments_file = base_dir / config.get("comments_excel_file", "comments.xlsx")
    storage = ExcelStorage(comments_file, COMMENT_COLUMNS, COMMENT_TITLES,
                           "comment_id", "评论")

    total = 0
    saved = 0

    def save_comment(c, indent=""):
        nonlocal total, saved
        if total >= max_count:
            return False
        if not c.get("comment_id"):
            return True
        total += 1
        prefix = "  ↳ " if indent else ""
        print(f"[{total}] {prefix}{c.get('user_name', '')}: {c.get('content', '')[:50]}")
        if storage.save_record(c):
            saved += 1
        return True

    # 主评论分页：新版用 cursor.next 游标（兼容回退到旧 pn 分页）
    pn = 1
    ps = 20
    cursor = None
    stale_pages = 0
    while total < max_count:
        before = total
        result = get_video_comments(aid, headers, pn=pn, ps=ps, sort=2, cursor=cursor)
        replies = result.get("replies", [])
        if not replies:
            break
        for c in replies:
            if total >= max_count:
                break
            comment = parse_comment(c)
            if not save_comment(comment):
                break
            # 子评论（楼中楼）
            if args.sub_comments and comment.get("sub_comment_count"):
                spn = 1
                while total < max_count:
                    sub_res = get_comment_replies(aid, comment["comment_id"],
                                                  headers, pn=spn, ps=10)
                    subs = sub_res.get("replies", [])
                    if not subs:
                        break
                    for sc in subs:
                        if total >= max_count:
                            break
                        sub_comment = parse_comment(sc, parent_id=comment["comment_id"])
                        if not save_comment(sub_comment, indent=True):
                            break
                    if total >= max_count or len(subs) < 10:
                        break
                    spn += 1
                    time.sleep(0.5)
        added = total - before
        # 整页都是重复数据（说明回退到旧接口没有真正翻页），连续 2 页则停止，避免死循环
        stale_pages = stale_pages + 1 if added == 0 else 0
        if stale_pages >= 2:
            break
        if total >= max_count:
            break
        if result.get("is_end"):
            break
        # 优先用游标推进；回退到旧接口时游标为 None，按页码递增
        nxt = result.get("next")
        if result.get("is_end") or not nxt:
            break
        cursor = nxt
        pn += 1
        time.sleep(0.8)

    print("-" * 60)
    print(f"[完成] 共采集 {total} 条评论，新写入 {saved} 条 -> {config.get('comments_excel_file', 'comments.xlsx')}")


def main():
    parser = argparse.ArgumentParser(description="B 站博主视频监控（增强版）")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR),
                        help="数据目录（存放 config/cookies/bloggers 及所有输出，默认当前目录下的 data）")
    parser.add_argument("--config", default=None, help="配置文件路径（默认 <数据目录>/config.json）")
    parser.add_argument("--once", action="store_true", help="只执行一轮后退出")
    parser.add_argument("--download", action="store_true", help="下载视频（默认只采集信息）")
    parser.add_argument("--video", default=None, help="单独处理某个视频（BV号或视频链接）")
    parser.add_argument("--comments", default=None, help="采集视频评论（BV号或视频链接）")
    parser.add_argument("--sub-comments", action="store_true", help="采集子评论（楼中楼，配合 --comments）")
    parser.add_argument("--count", type=int, default=0, help="总共采集多少条评论（含子评论，默认 200）")
    parser.add_argument("--batch", default=None, help="批量采集视频（文件路径，每行一个视频链接或 BV 号）")
    args = parser.parse_args()

    # 数据目录：配置与所有输出都落在 data_dir，代码仍在 scripts/ 里复用
    data_dir = ensure_data_dir(Path(args.data_dir))
    config_path = Path(args.config) if args.config else data_dir / "config.json"
    if not config_path.exists():
        print(f"配置文件不存在: {config_path}")
        sys.exit(1)
    base_dir = config_path.parent
    config = load_config(config_path)

    logger = setup_logger(base_dir / config.get("log_file", "monitor.log"))

    # 路径
    bloggers_file = base_dir / config.get("bloggers_file", "bloggers.txt")
    download_dir = base_dir / config.get("download_dir", "downloads")
    state_file = base_dir / config.get("state_file", "downloaded.json")
    config["download_dir"] = str(download_dir)
    config["state_file"] = str(state_file)
    download_dir.mkdir(parents=True, exist_ok=True)

    headers, cookies = build_headers(config, base_dir)
    if cookies:
        logger.info(f"已加载 Cookie（{len(cookies)} 项，含 SESSDATA: {'SESSDATA' in cookies}）")
    else:
        logger.warning("未加载到 Cookie，可能无法获取视频列表（风控）")

    hub = StorageHub(config, base_dir)
    signer = WbiSigner(headers)

    # 存储状态
    if hub.excel:
        logger.info(f"Excel 存储: 已启用 ({config.get('excel_file', 'videos.xlsx')})")
    else:
        logger.info("Excel 存储: 已关闭")
    if hub.mongo:
        mongo_status = hub.mongo_status()
        logger.info(f"MongoDB 存储: {mongo_status}")
    else:
        logger.info("MongoDB 存储: 未启用")

    # ---- 采集评论 ----
    if args.comments:
        cmd_comments(args, headers, base_dir, config)
        return

    # ---- 批量采集视频 ----
    if args.batch:
        cmd_batch(args, headers, cookies, hub, config, logger)
        hub.close()
        return

    # ---- 单独处理某个视频 ----
    if args.video:
        bvid = extract_bvid(args.video)
        if not bvid:
            print(f"无法从 '{args.video}' 解析出 BV 号")
            sys.exit(1)
        logger.info(f"单独处理视频: {bvid}（下载: {args.download}）")
        process_video(bvid, headers, cookies, hub, config, logger, do_download=args.download)
        hub.close()
        print("处理完成")
        return

    # ---- 监控模式 ----
    interval = config.get("interval_seconds", 300)
    req_interval = config.get("request_interval_seconds", 3)
    logger.info("=" * 50)
    logger.info("B 站博主监控启动")
    logger.info(f"博主文件: {bloggers_file}")
    logger.info(f"下载目录: {download_dir}")
    logger.info(f"下载视频: {'是' if args.download else '否'}")
    logger.info(f"监控间隔: {interval} 秒")

    try:
        while True:
            bloggers = parse_bloggers(bloggers_file)
            if not bloggers:
                logger.warning("bloggers.txt 中没有有效的博主链接")
            else:
                logger.info(f"本次检查 {len(bloggers)} 个博主")
                for i, blogger in enumerate(bloggers):
                    try:
                        check_one_blogger(
                            blogger, headers, signer, cookies, hub, config, logger,
                            do_download=args.download,
                        )
                    except Exception as e:
                        logger.error(f"检查博主 {blogger.get('uid')} 出错: {e}")
                    if i < len(bloggers) - 1:
                        time.sleep(req_interval)
            if args.once:
                logger.info("单轮检查完成，退出")
                break
            logger.info(f"等待 {interval} 秒后进行下一轮检查...")
            time.sleep(interval)
    except KeyboardInterrupt:
        logger.info("已手动停止")
    finally:
        hub.close()


if __name__ == "__main__":
    main()
