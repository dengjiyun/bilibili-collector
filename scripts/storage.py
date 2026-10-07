#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""存储层：Excel 存储 + MongoDB 预留接口。

视频信息字段（统一 schema）：
bvid, aid, title, description, duration(秒), pubdate(时间戳),
up_name, up_uid, view, like, coin, favorite, danmaku, reply, share,
pic, url, crawl_time
"""

import os
import time
from datetime import datetime
from pathlib import Path


# 统一的列定义（顺序即 Excel 列顺序）
COLUMNS = [
    "bvid", "aid", "title", "description", "duration", "duration_text",
    "pubdate", "pubdate_text", "up_name", "up_uid",
    "view", "like", "coin", "favorite", "danmaku", "reply", "share",
    "pic", "url", "crawl_time",
]

COLUMN_TITLES = {
    "bvid": "BV号", "aid": "AV号", "title": "标题", "description": "描述",
    "duration": "时长(秒)", "duration_text": "时长",
    "pubdate": "发布时间戳", "pubdate_text": "发布时间",
    "up_name": "UP主", "up_uid": "UP主UID",
    "view": "播放", "like": "点赞", "coin": "投币", "favorite": "收藏",
    "danmaku": "弹幕", "reply": "评论", "share": "分享",
    "pic": "封面", "url": "链接", "crawl_time": "采集时间",
}

# ---------------- 评论表列定义 ----------------
COMMENT_COLUMNS = [
    "comment_id", "parent_id", "content", "user_name", "user_id",
    "like_count", "sub_comment_count", "create_time", "create_time_text",
    "crawl_time",
]

COMMENT_TITLES = {
    "comment_id": "评论ID", "parent_id": "父评论ID", "content": "评论内容",
    "user_name": "用户昵称", "user_id": "用户ID", "like_count": "点赞数",
    "sub_comment_count": "回复数", "create_time": "评论时间戳",
    "create_time_text": "评论时间", "crawl_time": "采集时间",
}


def normalize_record(detail: dict) -> dict:
    """把视频详情标准化为存储记录，补齐派生字段。"""
    rec = {k: detail.get(k, "") for k in COLUMNS if k not in ("duration_text", "pubdate_text")}
    dur = detail.get("duration", 0)
    rec["duration"] = dur
    rec["duration_text"] = _fmt_duration(dur)
    pub = detail.get("pubdate", 0)
    rec["pubdate"] = pub
    rec["pubdate_text"] = _fmt_ts(pub)
    return rec


def _fmt_duration(seconds):
    if not seconds:
        return ""
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _fmt_ts(ts):
    if not ts:
        return ""
    return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S")


# ---------------- Excel 存储 ----------------

class ExcelStorage:
    """把记录写入 Excel（xlsx），按主键去重。

    默认用于视频表；传 columns/titles/pk_column 可复用为评论表。
    """

    def __init__(self, file_path, columns=None, titles=None,
                 pk_column="bvid", sheet_title="视频信息"):
        self.file_path = str(Path(file_path))
        self.columns = columns or COLUMNS
        self.titles = titles or COLUMN_TITLES
        self.pk_column = pk_column
        self.sheet_title = sheet_title
        self._pks = None          # 主键缓存，避免每写一行都重读整个文件
        self._ensure_header()

    def _ensure_header(self):
        if os.path.exists(self.file_path):
            return
        try:
            from openpyxl import Workbook
            wb = Workbook()
            ws = wb.active
            ws.title = self.sheet_title
            ws.append([self.titles.get(c, c) for c in self.columns])
            wb.save(self.file_path)
        except ImportError:
            raise RuntimeError("缺少 openpyxl，请运行: pip install openpyxl")

    def _load_existing_pks(self):
        """读取已有主键集合（结果缓存，避免重复解析整表）。"""
        if self._pks is not None:
            return self._pks
        existing = set()
        if not os.path.exists(self.file_path):
            self._pks = existing
            return existing
        try:
            from openpyxl import load_workbook
            wb = load_workbook(self.file_path, read_only=True)
            ws = wb.active
            header = None
            for row in ws.iter_rows(min_row=1, max_row=1, values_only=True):
                header = row
                break
            wb.close()
            if not header:
                self._pks = existing
                return existing
            pk_title = self.titles.get(self.pk_column, self.pk_column)
            pk_idx = None
            for i, h in enumerate(header):
                if h == pk_title:
                    pk_idx = i
                    break
            if pk_idx is None:
                self._pks = existing
                return existing
            wb2 = load_workbook(self.file_path, read_only=True)
            ws2 = wb2.active
            for row in ws2.iter_rows(min_row=2, values_only=True):
                if row and len(row) > pk_idx and row[pk_idx]:
                    existing.add(str(row[pk_idx]))
            wb2.close()
        except Exception:
            pass
        self._pks = existing
        return existing

    def save_record(self, record: dict) -> bool:
        """保存一条记录，主键已存在则跳过并返回 False。"""
        pk = str(record.get(self.pk_column, "") or "")
        existing = self._load_existing_pks()
        if pk and pk in existing:
            return False
        try:
            from openpyxl import load_workbook
            wb = load_workbook(self.file_path)
            ws = wb.active
            row = [record.get(c, "") for c in self.columns]
            ws.append(row)
            wb.save(self.file_path)
            if pk:
                existing.add(pk)
            return True
        except Exception as e:
            raise RuntimeError(f"写入 Excel 失败: {e}")


# ---------------- MongoDB 存储 ----------------

class MongoStorage:
    """MongoDB 存储。

    连接串示例：
    - 普通远程：mongodb://user:pass@host:27017/mydb?authSource=admin
    - Atlas：    mongodb+srv://user:pass@cluster.mongodb.net/mydb?retryWrites=true&w=majority
    """

    def __init__(self, uri, db_name=None, collection="videos"):
        self.uri = uri
        self.db_name = db_name
        self.collection = collection
        self._client = None
        self._coll = None
        self.connected = False

    def connect(self):
        """建立连接，成功返回 True，失败返回 False（不抛异常）。"""
        try:
            from pymongo import MongoClient
        except ImportError:
            raise RuntimeError("缺少 pymongo，请运行: pip install pymongo")
        try:
            self._client = MongoClient(self.uri, serverSelectionTimeoutMS=8000)
            # 触发一次真实连接检测
            self._client.admin.command("ping")
            # 确定数据库名：优先显式传入，否则从连接串解析，最后用默认
            db_name = self.db_name or self._client.get_default_database()
            if db_name is None:
                # 从 uri 中解析数据库名
                db_name = self._extract_db_from_uri(self.uri) or "bilibili"
            self._db = self._client[db_name]
            self._coll = self._db[self.collection]
            self.connected = True
            return True
        except Exception as e:
            self._client = None
            self.connected = False
            return False

    @staticmethod
    def _extract_db_from_uri(uri):
        """从连接串中解析数据库名（/ 后面的部分，去掉 ? 参数）。"""
        try:
            path = uri.split("://", 1)[1]
            # 去掉认证部分（@ 之前）
            if "@" in path:
                path = path.split("@", 1)[1]
            # 取第一个 / 之后、? 之前
            rest = path.split("/", 1)
            if len(rest) > 1:
                db = rest[1].split("?", 1)[0].strip()
                if db:
                    return db
        except Exception:
            pass
        return None

    def save_record(self, record: dict) -> bool:
        """upsert 一条记录（按 bvid 去重）。连接失败时抛出 RuntimeError。"""
        if not self.connected:
            if not self.connect():
                raise RuntimeError("MongoDB 连接失败，请检查连接串或网络")
        bvid = record.get("bvid", "")
        self._coll.update_one({"bvid": bvid}, {"$set": record}, upsert=True)
        return True

    def close(self):
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
            self.connected = False
