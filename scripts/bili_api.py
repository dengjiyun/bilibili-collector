#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B 站 API 封装：WBI 签名、UP 主信息、视频列表、视频详情。"""

import hashlib
import re
import time
import urllib.parse

import requests

# 风控退避：B 站在高频请求时会返回 HTTP 412 / code=-412，需要等待后重试
RISK_CODES = (-412, -509, -799)


def safe_get_json(url, params, headers, timeout=15, retries=3, backoff=20):
    """GET 并返回 JSON；遇到 412 风控时按指数退避重试。

    失败返回 None（调用方据此决定是否回退到备用接口）。
    """
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=timeout)
        except Exception:
            time.sleep(backoff)
            continue
        try:
            d = r.json()
        except Exception:
            # 非 JSON 基本就是风控页
            time.sleep(backoff * (attempt + 1))
            continue
        if isinstance(d, dict) and d.get("code") in RISK_CODES:
            time.sleep(backoff * (attempt + 1))
            continue
        return d
    return None

# WBI 签名置换表
MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
]

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.bilibili.com/",
    "Accept": "application/json, text/plain, */*",
}


def get_mixin_key(orig: str) -> str:
    return "".join(orig[i] for i in MIXIN_KEY_ENC_TAB)[:32]


def enc_wbi(params: dict, img_key: str, sub_key: str) -> dict:
    mixin_key = get_mixin_key(img_key + sub_key)
    params["wts"] = round(time.time())
    params = dict(sorted(params.items()))
    query = urllib.parse.urlencode(params)
    w_rid = hashlib.md5((query + mixin_key).encode()).hexdigest()
    params["w_rid"] = w_rid
    return params


class WbiSigner:
    """缓存 WBI 密钥。"""

    def __init__(self, headers):
        self.headers = headers
        self.img_key = None
        self.sub_key = None
        self._fetch_keys()

    def _fetch_keys(self):
        try:
            r = requests.get(
                "https://api.bilibili.com/x/web-interface/nav",
                headers=self.headers, timeout=15,
            )
            data = r.json()
            wbi_img = (data.get("data") or {}).get("wbi_img")
            if wbi_img:
                self.img_key = wbi_img["img_url"].rsplit("/", 1)[1].split(".")[0]
                self.sub_key = wbi_img["sub_url"].rsplit("/", 1)[1].split(".")[0]
        except Exception:
            pass

    def sign(self, params: dict) -> dict:
        if not self.img_key or not self.sub_key:
            self._fetch_keys()
        if self.img_key and self.sub_key:
            return enc_wbi(params, self.img_key, self.sub_key)
        return params


def extract_uid(text):
    """从链接或纯数字中提取 UID。"""
    m = re.search(r"space\.bilibili\.com/(\d+)", text)
    if m:
        return m.group(1)
    if text.isdigit():
        return text
    return None


def extract_bvid(text):
    """从视频链接或纯 BV 号中提取 bvid。"""
    m = re.search(r"(BV[0-9A-Za-z]{10})", text)
    if m:
        return m.group(1)
    return None


def get_up_info(uid, headers, signer=None):
    """获取 UP 主信息，返回 {uid, name}。

    优先用需要 WBI 签名 + 登录 Cookie 的 wbi/acc/info 接口（B 站已强制要求），
    失败时回退旧的 space/acc/info。两者都失败返回占位名 UP_<uid>。
    """
    for url, need_sign in (
        ("https://api.bilibili.com/x/space/wbi/acc/info", True),
        ("https://api.bilibili.com/x/space/acc/info", False),
    ):
        try:
            params = {"mid": uid}
            if need_sign and signer is not None:
                params = signer.sign(dict(params))
            r = requests.get(url, headers=headers, params=params, timeout=15)
            data = r.json()
            if data.get("code") == 0:
                d = data["data"]
                return {"uid": str(uid), "name": d.get("name", f"UP_{uid}")}
        except Exception:
            pass
    return {"uid": str(uid), "name": f"UP_{uid}"}


def get_recent_videos(uid, headers, signer, limit=30):
    """获取 UP 主最近的视频列表，返回 [{bvid, title, ctime, ...}]。"""
    url = "https://api.bilibili.com/x/space/wbi/arc/search"
    params = {"mid": uid, "ps": limit, "pn": 1, "order": "pubdate"}
    params = signer.sign(params)
    try:
        r = requests.get(url, headers=headers, params=params, timeout=15)
        data = r.json()
        if data.get("code") != 0:
            return []
        vlist = data.get("data", {}).get("list", {}).get("vlist", [])
        return [
            {
                "bvid": v.get("bvid"),
                "title": v.get("title"),
                "ctime": v.get("created"),
                "length": v.get("length", ""),
                "play": v.get("play", 0),
                "comment": v.get("comment", 0),
            }
            for v in vlist
        ]
    except Exception:
        return []


def get_video_detail(bvid, headers):
    """获取单个视频的完整详情（标题、时长、描述、发布时间、点赞/评论/收藏/投币/播放等）。

    返回 dict；失败返回 None。
    """
    url = "https://api.bilibili.com/x/web-interface/view"
    params = {"bvid": bvid}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=15)
        data = r.json()
        if data.get("code") != 0:
            return None
        d = data["data"]
        stat = d.get("stat", {})
        owner = d.get("owner", {})
        return {
            "bvid": bvid,
            "aid": d.get("aid"),
            "title": d.get("title", ""),
            "description": (d.get("desc") or "").strip(),
            "duration": d.get("duration", 0),           # 秒
            "pubdate": d.get("pubdate", 0),              # 时间戳
            "up_name": owner.get("name", ""),
            "up_uid": str(owner.get("mid", "")),
            "view": stat.get("view", 0),                 # 播放
            "like": stat.get("like", 0),                 # 点赞
            "coin": stat.get("coin", 0),                 # 投币
            "favorite": stat.get("favorite", 0),         # 收藏
            "danmaku": stat.get("danmaku", 0),           # 弹幕
            "reply": stat.get("reply", 0),               # 评论
            "share": stat.get("share", 0),               # 分享
            "pic": d.get("pic", ""),
            "cid": d.get("cid"),
            "url": f"https://www.bilibili.com/video/{bvid}",
            "crawl_time": int(time.time()),
        }
    except Exception:
        return None


def format_duration(seconds):
    """秒数转为 HH:MM:SS 或 MM:SS。"""
    if not seconds:
        return ""
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


# ---------------- 评论 ----------------

def get_video_comments(aid, headers, pn=1, ps=20, sort=2, cursor=None):
    """获取视频主评论（分页）。

    注意：旧接口 `x/v2/reply` 已逐步退化为「只返回第一页热门评论」，
    新版评论区改走 `x/v2/reply/main`，用 cursor.next 游标翻页。
    因此这里优先用新接口，失败再回退旧接口（兼容老视频）。
    返回 dict：{replies, total, next, is_end}
    """
    url = "https://api.bilibili.com/x/v2/reply/main"
    # cursor 为上次返回的 cursor.next（int）；None 时按页码换算：第 1 页传 0
    params = {"type": 1, "oid": aid, "next": 0 if cursor in (None, 0) else cursor, "mode": 2}
    data = safe_get_json(url, params, headers)
    if data is not None and data.get("code") == 0:
        d = data.get("data") or {}
        replies = d.get("replies") or []
        cur = d.get("cursor") or {}
        return {
            "replies": replies,
            "total": cur.get("all_count", 0),
            "next": cur.get("next"),
            "is_end": bool(cur.get("is_end")),
        }

    # 回退：旧分页接口
    url = "https://api.bilibili.com/x/v2/reply"
    params = {"type": 1, "oid": aid, "pn": pn, "ps": ps, "sort": sort}
    data = safe_get_json(url, params, headers)
    try:
        if data is not None and data.get("code") == 0:
            d = data.get("data") or {}
            return {
                "replies": d.get("replies") or [],
                "total": (d.get("page") or {}).get("count", 0),
                "next": pn + 1,
                "is_end": False,
            }
    except Exception:
        pass
    return {"replies": [], "total": 0, "next": None, "is_end": True}


def get_comment_replies(aid, root_rpid, headers, pn=1, ps=10):
    """获取某条主评论的子评论（楼中楼，分页）。

    返回 dict：{replies: [原始子评论...], total: 子评论总数}
    """
    url = "https://api.bilibili.com/x/v2/reply/reply"
    params = {"type": 1, "oid": aid, "root": root_rpid, "pn": pn, "ps": ps}
    data = safe_get_json(url, params, headers, retries=2)
    try:
        if data is not None and data.get("code") == 0:
            d = data.get("data") or {}
            return {
                "replies": d.get("replies") or [],
                "total": (d.get("page") or {}).get("count", 0),
            }
    except Exception:
        pass
    return {"replies": [], "total": 0}


def parse_comment(c, parent_id=""):
    """把 B 站评论对象解析成统一的 dict。"""
    if not c:
        return {}
    member = c.get("member") or {}
    content = c.get("content") or {}
    ctime = c.get("ctime", 0)
    return {
        "comment_id": str(c.get("rpid", "")),
        "parent_id": str(parent_id or c.get("root", "") or ""),
        "content": content.get("message", ""),
        "user_name": member.get("uname", ""),
        "user_id": str(c.get("mid", "")),
        "like_count": c.get("like", 0),
        "sub_comment_count": c.get("rcount", 0),
        "create_time": ctime,                        # 秒
        "create_time_text": _fmt_ts(ctime),
        "crawl_time": int(time.time()),
    }


def _fmt_ts(ts):
    """秒级时间戳 -> 'YYYY-MM-DD HH:MM:SS'。"""
    if not ts:
        return ""
    try:
        from datetime import datetime
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""
