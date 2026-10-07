# B 站 Web 端采集 API 参考

> 记录 B 站接口的 WBI 签名机制、端点、返回结构和实测踩过的坑。
> 对应脚本：`scripts/bilibili/`（bili_api.py / bili_monitor.py / storage.py）。

## 一、WBI 签名机制

B 站部分接口（尤其 `x/space/wbi/arc/search` 博主视频列表）需要 WBI 签名。

流程：
1. 请求 `https://api.bilibili.com/x/web-interface/nav`，从 `data.wbi_img.img_url` / `sub_url` 拿到 `img_key`、`sub_key`（URL 最后一段去掉扩展名）。
2. `mixin_key = get_mixin_key(img_key + sub_key)`：按固定置换表（MIXIN_KEY_ENC_TAB，64 项）取前 32 位。
3. 参数加 `wts`（当前秒级时间戳）→ 按 key 排序 → urlencode → `w_rid = md5(query + mixin_key)` → 加入参数。

**关键坑**：`nav` 接口**未登录时 code=-101，但依然返回 `wbi_img`**，所以不能只判断 `code==0` 才取密钥，要直接读 `data.wbi_img`。

## 二、接口端点

| 接口 | 用途 | 是否需 WBI 签名 |
|------|------|----------------|
| `GET /x/web-interface/nav` | 拿 WBI 密钥 | 否（仅需 Cookie） |
| `GET /x/space/wbi/acc/info?mid=<uid>` | UP 主信息（昵称） | **是**（旧 `space/acc/info` 已失效，作回退） |
| `GET /x/space/wbi/arc/search` | 博主视频列表（分页） | **是** |
| `GET /x/web-interface/view?bvid=<bvid>` | 单个视频详情 | **否**（最稳） |
| `GET /x/v2/reply/main` | 视频主评论（新·游标分页，`mode=2` 时间序） | **否** |
| `GET /x/v2/reply` | 视频主评论（旧·仅第一页热评，作回退） | **否** |
| `GET /x/v2/reply/reply` | 子评论/楼中楼（分页） | **否** |

### 视频详情接口（推荐优先用）

`GET https://api.bilibili.com/x/web-interface/view?bvid=xxx`，无需 WBI 签名，仅需 Cookie。
返回 `data`：

| 字段 | 说明 |
|------|------|
| `title` / `desc` | 标题 / 简介 |
| `duration` / `pubdate` | 时长（秒）/ 发布时间（时间戳） |
| `owner.name` / `owner.mid` | UP 主昵称 / UID |
| `stat.view` / `like` / `coin` / `favorite` / `danmaku` / `reply` / `share` | 播放/点赞/投币/收藏/弹幕/评论/分享 |
| `pic` | 封面 |
| `bvid` / `aid` / `cid` | 视频标识 |

### 博主视频列表接口（需 WBI 签名 + 登录 Cookie）

`GET /x/space/wbi/arc/search?mid=<uid>&ps=30&pn=1&order=pubdate` + WBI 签名参数。
返回 `data.list.vlist`：每项含 `bvid`、`title`、`created`（ctime）、`play`、`comment` 等。
`data.page.count` 为该 UP 主投稿总数，翻页直到累计数达到 count 即为全量。

**采集全量视频**：用 `fetch_all_bvids.py <uid或空间链接> [输出文件]` 自动翻页拿全部 BV 号，
再用 `bili_monitor.py --batch <输出文件>` 批量采详情。监控模式 `--once` 只取最新 N 个，拿不到全量。

### 评论接口（无需 WBI 签名）

> **⚠️ 2026-10 更新**：旧接口 `x/v2/reply` 已退化为「只返回第一页热门评论」（实测一个 676 条评论的视频只吐出 3 条主评论，翻页为空），新版评论区改走 **`x/v2/reply/main` 游标分页**。`bili_api.get_video_comments` 已改为优先新接口、失败回退旧接口，调用处请传 `cursor` 参数。

**主评论（新）**：`GET https://api.bilibili.com/x/v2/reply/main?type=1&oid=<aid>&next=<cursor>&mode=2`
- 需要 `oid` = 视频的 `aid`（先调 `view` 接口拿 aid），**不是 bvid**。
- `mode=2` 按时间序（能翻到全量），`mode=3` 按热度。
- 翻页：第一页 `next=0`，之后把上次返回的 `data.cursor.next` 作为 `next` 传入，直到 `data.cursor.is_end=true`。
- 返回 `data.replies`（评论数组）、`data.cursor.all_count`（总评论数）。
- **风控**：高频请求会返回 HTTP 412 / `code=-412`（风控页「出错啦」），属 IP 级限流，需退避数十秒~数分钟后再试。脚本已用 `safe_get_json` 内置指数退避。

**主评论（旧，回退用）**：`GET https://api.bilibili.com/x/v2/reply?type=1&oid=<aid>&pn=1&ps=20&sort=2`
- `sort=2` 按热度排序，`sort=0` 按时间。
- 返回 `data.replies`、`data.page.count`（总评论数）。新视频可能只返回第一页。

**子评论（楼中楼）**：`GET https://api.bilibili.com/x/v2/reply/reply?type=1&oid=<aid>&root=<主评论rpid>&pn=1&ps=10`
- `root` = 主评论的 `rpid`。
- 只对 `rcount > 0` 的主评论才需要调子评论接口。

**评论字段**（`replies` 数组每项）：
| 字段 | 说明 |
|------|------|
| `rpid` | 评论 ID |
| `member.uname` / `mid` | 用户昵称 / 用户 ID |
| `content.message` | 评论内容 |
| `like` | 点赞数 |
| `rcount` | 回复数（子评论数） |
| `root` | 父评论 ID（子评论才有，主评论为 0） |
| `ctime` | 评论时间（秒级时间戳） |

## 三、ID 解析

- UID：`space.bilibili.com/(\d+)` 或纯数字。
- BV 号：`(BV[0-9A-Za-z]{10})`。
- 视频链接：`https://www.bilibili.com/video/BVxxx`。

## 四、下载

用 `yt-dlp` 下载，格式 `bv*+ba/b`，merge 成 mp4。**yt-dlp 的 `cookiefile` 需要 Netscape 格式**，脚本内部把 `name=value` 格式的 Cookie 转成 Netscape 临时文件，用完即删。

## 五、实测踩过的坑

1. **列表接口风控**：`wbi/arc/search` 未登录 Cookie（缺 SESSDATA）时返回 -352 / 412，必须提供登录 Cookie。实测仅带匿名 `buvid3/buvid4`（来自 `x/frontend/finger/spi`）仍返回 `-352 风控校验失败`，**必须真实 SESSDATA**。
2. **免登录替代路径均不可靠**：官方动态接口 `x/polymer/web-dynamic/v1/feed/space` 对无动态的 UP 主返回空；RSSHub 公共实例（rsshub.app / rsshub.rssforever.com）常 502/503。要全量列表就得登录 Cookie。
3. **频繁请求触发 -799**：短时间内连续请求 `acc/info` 等接口会返回 `{"code":-799,"message":"请求过于频繁，请稍后再试"}`，属 IP 级限流，需退避等待（数十秒）后再试，并保持 `request_interval_seconds>=2`。
4. **`nav` 接口 code=-101 但仍返回密钥**：见上文 WBI 签名节。
5. **Cookie 格式**：用户从 DevTools 复制的 Cookie 是单行 `name=value;` 格式，不是 Netscape，load_cookies 需同时兼容两种。
6. **bloggers.txt 有 BOM**：读取需用 `utf-8-sig`。
7. **下载视频标题可能含非法字符**：文件夹名需 `re.sub(r'[\\/:*?"<>|]', "_", name)` 清洗。
8. **评论接口 oid 用 aid 不是 bvid**：`x/v2/reply` 的 `oid` 是视频 aid，需先调 `view` 接口取 aid；传 bvid 会返回空。
9. **子评论只对有 rcount 的主评论采**：主评论 `rcount > 0` 才调 `/reply/reply`，否则浪费请求。
10. **批量采集去重**：`--batch` 读文件按 bvid 去重（文件内 + 已入库），Excel 按 bvid 去重实现断点续传。

## 六、依赖

```
pip install yt-dlp requests openpyxl pymongo
```

- Python 3.10+，ffmpeg（音视频合并需要，yt-dlp 调用）。
