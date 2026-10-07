# B 站视频采集 - 使用说明

采集 B 站视频元信息、评论并下载，基于 `requests` + 登录 Cookie + WBI 签名，下载用 `yt-dlp`。

## 能做什么（四大功能）

| # | 功能 | 说明 | 命令 |
| --- | --- | --- | --- |
| ① | **单个视频** | 采集标题/简介/时长/发布时间/播放·点赞·投币·收藏·弹幕·评论·分享数/封面，可选下载视频 | `--video` |
| ② | **视频评论** | 采集主评论，可选子评论（楼中楼），可控制条数 | `--comments` |
| ③ | **某 UP 主全部视频** | 翻页拉取某博主所有投稿视频及其信息 | `fetch_all_bvids.py` + `--batch` |
| ④ | **批量采集** | 从文件读取一批视频链接/BV 号，批量采集（可选下载） | `--batch` |

## 核心规则：脚本复用，数据分离

- **脚本永不复制**：所有 `.py` 固定在本 skill 的 `scripts/` 下，脚本用 `__file__` 定位自身，从任意目录直接调用即可。
- **数据落调用者当前目录**：运行时所在目录会自动生成 `data/`，放配置和全部产出。

`data/` 目录内容：

| 文件 | 说明 |
| --- | --- |
| `config.json` | 配置，已给默认值 |
| `cookies.txt` | B 站 Cookie（拉 UP 主列表需 `SESSDATA`，你填） |
| `bloggers.txt` | 每行一个 UP 主链接（功能③用，你填） |
| `videos.xlsx` | 视频信息表（自动生成） |
| `comments.xlsx` | 评论表（自动生成） |
| `downloads/<UP主_UID>/` | 下载的视频（自动生成） |
| `monitor.log` | 日志（自动生成） |

## 环境要求

- Python 3.10+
- ffmpeg（下载视频合并音视频需要）
- 依赖：`requests`、`openpyxl`、`yt-dlp`（MongoDB 另需 `pymongo`）

```bash
pip install requests openpyxl yt-dlp
```

## 快速开始

1. **先配置 Cookie（必做）**：在 `data/cookies.txt` 填好 B 站登录 Cookie（获取方法见下「获取登录 Cookie」）。关键字段 `SESSDATA`。
2. **装依赖**：`pip install requests openpyxl yt-dlp`（下载视频另需 ffmpeg）。
3. **填博主列表**（仅功能③需要）：`data/bloggers.txt` 每行一个 UP 主链接。
4. **运行**：在任意目录执行。`<SKILL_DIR>` 换成本 skill 的实际安装目录：

```bash
SKILL_SCRIPTS="<SKILL_DIR>/scripts"   # 本 skill 的 scripts 目录

# ① 单个视频
python "$SKILL_SCRIPTS/bili_monitor.py" --video BV17VhU62EGP            # 详情
python "$SKILL_SCRIPTS/bili_monitor.py" --video BV17VhU62EGP --download # 详情 + 下载
python "$SKILL_SCRIPTS/bili_monitor.py" --video https://www.bilibili.com/video/BV17VhU62EGP  # 传链接也行

# ② 视频评论
python "$SKILL_SCRIPTS/bili_monitor.py" --comments BV1aSh76DEra                                  # 主评论
python "$SKILL_SCRIPTS/bili_monitor.py" --comments BV1aSh76DEra --sub-comments --count 100      # 含楼中楼，共100条

# ④ 批量采集（文件每行一个链接/BV，支持 # 注释、自动去重）
python "$SKILL_SCRIPTS/bili_monitor.py" --batch 链接文件.txt
python "$SKILL_SCRIPTS/bili_monitor.py" --batch 链接文件.txt --download

# ③ 某 UP 主全部视频（两段式）
python "$SKILL_SCRIPTS/fetch_all_bvids.py" "https://space.bilibili.com/674669330/"  # 抓全量 BV -> data/all_videos.txt
python "$SKILL_SCRIPTS/bili_monitor.py" --batch data/all_videos.txt                 # 批量采详情
```

> 嫌路径长可在项目里设环境变量或别名指向脚本目录，仍不复制脚本。

### 参数速查

| 参数 | 说明 |
| --- | --- |
| `--video` | 单个视频（BV 号或链接） |
| `--comments` | 采集评论（BV 号或链接） |
| `--sub-comments` | 配合 `--comments`，采楼中楼 |
| `--count` | 评论总条数（含子评论，默认 200） |
| `--batch` | 批量采集（文件每行一个链接/BV） |
| `--download` | 下载视频（默认只采集信息） |
| `--once` | 只跑一轮（配合 `bloggers.txt` 做监控） |
| `--data-dir` | 指定数据目录（默认 `./data`） |

## 获取登录 Cookie

B 站对未登录的 UP 主视频列表请求会风控（`-352`/`412`）。**单视频详情/下载、评论无需登录**；拉 UP 主视频列表（功能③）必须填 `SESSDATA`。

两种格式任选其一，粘贴到 `data/cookies.txt`：

**方式 A（推荐）：DevTools 复制**
1. 浏览器登录 B 站 → `F12` → `Network` 标签
2. 刷新页面，点任意 `api.bilibili.com` 请求 → `Headers` → `Cookie`，复制整段
3. 粘贴为一行 `name=value; name=value; ...`

**方式 B：插件导出（Netscape 格式）**
1. 装 Chrome 插件「Get cookies.txt LOCALLY」
2. B 站页面 → 插件 → Export → Netscape 格式 → 存为 `cookies.txt`

> Cookie 含登录态，勿外泄；约一周过期，失效后重导。

## 配置说明（config.json）

| 字段 | 说明 | 默认值 |
| --- | --- | --- |
| `bloggers_file` | 博主链接文件名 | `bloggers.txt` |
| `download_dir` | 视频下载目录 | `downloads` |
| `interval_seconds` | 监控间隔（秒） | `300` |
| `quality` | 画质，`best`=最高 | `best` |
| `max_videos_per_check` | 每博主每次检查的视频数 | `10` |
| `request_interval_seconds` | 视频间请求间隔（秒） | `2` |
| `excel_enabled` | 是否存 Excel | `true` |
| `excel_file` | 视频信息 Excel 文件名 | `videos.xlsx` |
| `comments_excel_file` | 评论 Excel 文件名 | `comments.xlsx` |
| `mongodb.enabled` | 是否启用 MongoDB | `false` |
| `mongodb.uri` | MongoDB 连接串 | — |
| `mongodb.db` | 数据库名（可不填，自动解析） | `bilibili` |
| `mongodb.collection` | 集合名 | `videos` |

### 配置 MongoDB

```json
{
  "mongodb": {
    "enabled": true,
    "uri": "mongodb://用户名:密码@服务器IP:27017/数据库名?authSource=admin",
    "db": "bilibili",
    "collection": "videos"
  }
}
```

- 连接串：`mongodb://用户名:密码@主机IP:端口/数据库名?authSource=admin`；Atlas 用 `mongodb+srv://...`
- 密码含特殊字符需 URL 编码：`@`→`%40`、`:`→`%3A`、`/`→`%2F`、`#`→`%23`
- 远程 MongoDB 需开放防火墙端口，并把本机 IP 加入白名单（Atlas 必须）

启动日志会显示连接状态：`MongoDB 存储: 已连接: bilibili.videos` 或 `连接失败`。

## 数据字段

视频表（`videos.xlsx`，按 `bvid` 去重）：`bvid`/`aid`、`title`/`description`、`duration`/`duration_text`、`pubdate`/`pubdate_text`、`up_name`/`up_uid`、`view`/`like`/`coin`/`favorite`/`danmaku`/`reply`/`share`、`pic`/`url`/`crawl_time`。

评论表（`comments.xlsx`，按 `comment_id` 去重）：`comment_id`/`parent_id`、`content`、`user_name`/`user_id`、`like_count`、`sub_comment_count`、`create_time`/`create_time_text`。

## 目录结构

```
bilibili-collector/
├── scripts/                # 代码（复用，不复制）
│   ├── bili_monitor.py     # 主脚本（入口）
│   ├── bili_api.py         # B 站 API 封装（WBI 签名、视频详情、评论）
│   ├── storage.py          # 存储层（Excel + MongoDB）
│   └── fetch_all_bvids.py  # 分页拉某 UP 主全部 BV 号
├── references/api.md       # 接口参考
└── data/                   # 数据目录（配置 + 产出）
    ├── config.json
    ├── cookies.txt
    ├── bloggers.txt
    ├── videos.xlsx / comments.xlsx   # 自动生成
    ├── downloaded.json               # 已处理记录（自动生成）
    └── downloads/<UP主_UID>/xxx.mp4
```

## 常见问题

1. **风控/412/未获取到列表**：Cookie 缺失或过期，重新导出；单视频详情可无 Cookie 直接用 `--video`。
2. **MongoDB 连接失败**：检查连接串格式、密码特殊字符转义、白名单/防火墙。
3. **下载失败**：网络问题或画质过高，可改 `quality` 为 `best[height<=1080]`。
4. **只采集不下载**：不加 `--download` 即可（默认行为）。
5. **改监控频率**：改 `interval_seconds`。
