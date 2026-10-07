---
name: bilibili-collector
description: 采集 B 站（Bilibili）视频数据，四大功能：① 采集单个视频信息并下载该视频；② 采集视频评论（含楼中楼）；③ 采集某 UP 主（博主）全部视频及信息；④ 批量采集一批视频。当用户需要抓取 B 站视频详情、下载视频、采集评论、监控或拉取 UP 主投稿、批量采集视频时使用。使用前第一步先让用户在 data/cookies.txt 配置好 B 站登录 Cookie（SESSDATA）；脚本复用本 skill 的 scripts/，数据落调用者当前目录的 data/。
agent_created: true
---

# B 站视频采集

采集 B 站视频元信息、评论并下载，基于 `requests` + 登录 Cookie + WBI 签名，下载用 `yt-dlp`。

## 能做什么（四大功能）

| 功能 | 说明 | 命令 |
| --- | --- | --- |
| **① 单个视频** | 采集标题/简介/时长/发布时间/播放·点赞·投币·收藏·弹幕·评论·分享数/封面，可选下载视频 | `--video` |
| **② 视频评论** | 采集主评论，可选子评论（楼中楼），可控制条数 | `--comments` |
| **③ 某 UP 主全部视频** | 翻页拉取某博主所有投稿视频及其信息 | `fetch_all_bvids.py` + `--batch` |
| **④ 批量采集** | 从文件读取一批视频链接/BV 号，批量采集（可选下载） | `--batch` |

## 核心规则：脚本复用，数据分离

- **脚本永不复制**：所有 `.py` 固定在本 skill 的 `scripts/` 下，脚本用 `__file__` 定位自身，从任意目录直接调用即可。
- **数据落调用者当前目录**：运行时所在目录会自动生成 `data/`，放配置（`config.json`/`cookies.txt`/`bloggers.txt`）和全部产出（`videos.xlsx`/`comments.xlsx`/`downloads/`/`monitor.log`）。

## 使用步骤

**第一步：先配置 Cookie（必做）**

先在目标目录的 `data/cookies.txt` 里填好 B 站登录 Cookie。获取方式：浏览器登录 `https://www.bilibili.com` → `F12` → `Network` → 刷新页面 → 点任意 `api.bilibili.com` 请求 → `Headers` 里复制 `Cookie` 整段，粘贴为一行 `name=value; name=value; ...`。关键字段 `SESSDATA`（登录态）。

> 若 `data/cookies.txt` 不存在，可先运行一次脚本自动生成模板文件再填。

第二步：装依赖（一次性）：`pip install requests openpyxl yt-dlp`（下载视频另需 ffmpeg）。

第三步：按需填 `data/bloggers.txt`（每行一个 UP 主链接，仅功能③需要）。

第四步：在目标目录运行。`<SKILL_DIR>` 换成本 skill 的实际安装目录：

```bash
SKILL_SCRIPTS="<SKILL_DIR>/scripts"   # 本 skill 的 scripts 目录

# ① 单个视频：详情（view 接口，无需签名）
python "$SKILL_SCRIPTS/bili_monitor.py" --video "BV号或链接"
python "$SKILL_SCRIPTS/bili_monitor.py" --video "BV号" --download     # 详情 + 下载

# ② 视频评论
python "$SKILL_SCRIPTS/bili_monitor.py" --comments "BV号"              # 主评论
python "$SKILL_SCRIPTS/bili_monitor.py" --comments "BV号" --sub-comments --count 100  # 含楼中楼

# ④ 批量采集（文件每行一个链接/BV，支持 # 注释、自动去重）
python "$SKILL_SCRIPTS/bili_monitor.py" --batch "链接文件.txt"
python "$SKILL_SCRIPTS/bili_monitor.py" --batch "链接文件.txt" --download

# ③ 某 UP 主全部视频（两段式）
python "$SKILL_SCRIPTS/fetch_all_bvids.py" "https://space.bilibili.com/<UID>/"  # 抓全量 BV -> data/all_videos.txt
python "$SKILL_SCRIPTS/bili_monitor.py" --batch data/all_videos.txt             # 批量采详情
```

> 另支持 `--once`（只跑一轮，配合 `bloggers.txt` 做监控）。

### 参数速查

`--video` 单视频；`--comments` 评论；`--sub-comments` 楼中楼；`--count` 评论条数（默认 200）；`--batch` 批量文件；`--download` 下载；`--once` 只跑一轮；`--data-dir` 指定数据目录（默认 `./data`）。

## 数据目录（`data/`）

| 文件 | 用途 |
| --- | --- |
| `config.json` | 配置，已给默认值 |
| `cookies.txt` | B 站 Cookie（拉 UP 主列表需 `SESSDATA`） |
| `bloggers.txt` | 每行一个 UP 主链接（功能③用） |

产出：`videos.xlsx`（视频信息）、`comments.xlsx`（评论）、`downloads/<UP主_UID>/`（视频）、`monitor.log`。

## 关键坑

- **列表接口需 WBI 签名 + 登录 Cookie**（`x/space/wbi/arc/search`），否则 -352/412。
- **`nav` 接口 code=-101 仍返回密钥**：拿 WBI 密钥直接读 `data.wbi_img`，别只判 code==0。
- **详情优先用 `view` 接口**：`x/web-interface/view?bvid=xxx` 无需签名，最稳。
- **评论 `oid` 用 `aid`**：先调 `view` 拿 aid；子评论只采 `rcount>0` 的主评论。
- **评论接口已迁移到游标分页**：旧 `x/v2/reply` 已退化为「只返回第一页热评」（676 条评论的视频只吐 3 条），新版改走 `x/v2/reply/main`（`mode=2` 时间序 + `cursor.next` 游标翻页）。`get_video_comments` 已自动优先新接口、失败回退旧接口。
- **评论采集要用游客态**：`x/v2/reply/main` 在登录态下 `cursor.is_end` 会异常返回 `True`（实则还有内容）导致提前终止；不带登录 Cookie 的游客态反而能正常翻全量。翻页请以 `cursor.next` 是否为 0 判断，不要依赖 `is_end`。
- **评论接口会触发 -412 风控**：高频请求返回 HTTP 412 / `code=-412`（风控页「出错啦」），属 IP 级限流，需退避数十秒~数分钟。脚本已用 `safe_get_json` 内置指数退避重试。
- **Cookie 留占位值会导致请求失败**：要么删掉、要么填真实 Cookie。
- **UP 主信息接口已迁移**：旧 `x/space/acc/info` 失效，需用 WBI 签名的 `x/space/wbi/acc/info`（`get_up_info(uid, headers, signer)` 已自动签名并回退；不传 signer 会退化为占位名 `UP_<uid>`）。
- **评论表没有「所属视频」列**：`COMMENT_COLUMNS` 只按 `comment_id` 去重，多个视频的评论会混进同一个 `comments.xlsx` 且无法区分。采集不同视频的评论时，建议新建一份只改 `comments_excel_file` 的配置（如 `config_<bvid>.json`）后用 `--config` 指定，或改用独立 `--data-dir`。

接口细节读 `references/api.md`。

## 注意

- Cookie 含登录态勿外泄，失效后重导。
- 仅个人学习研究，遵守 B 站协议，控制请求频率（`config.json` 的 `request_interval_seconds`）。
