# B 站视频采集（bilibili-collector）

一个 B 站（Bilibili）视频数据采集脚本集，基于 `requests` + 登录 Cookie + WBI 签名，下载视频用 `yt-dlp`。可作为 [WorkBuddy](https://www.workbuddy.cn) 的 skill 使用，也可独立运行。

## 四大功能

| # | 功能 | 说明 |
| --- | --- | --- |
| ① | **单个视频** | 采集标题/简介/时长/发布时间/播放·点赞·投币·收藏·弹幕·评论·分享数/封面，可选下载视频 |
| ② | **视频评论** | 采集主评论，可选子评论（楼中楼），可控制条数 |
| ③ | **某 UP 主全部视频** | 翻页拉取某博主所有投稿视频及其信息 |
| ④ | **批量采集** | 从文件读取一批视频链接/BV 号，批量采集（可选下载） |

## 特性

- 登录 Cookie + WBI 签名，稳定应对 B 站风控
- 评论走新版 `/x/v2/reply/main` 游标分页（游客态可翻全量）
- 内置风控退避重试（`-412` / `-509` / `-799`）
- 数据落 Excel（`openpyxl`），可选 MongoDB 存储
- 脚本复用、数据分离：脚本固定在本仓库，数据落调用者当前目录

## 快速开始

```bash
# 1. 装依赖
pip install requests openpyxl yt-dlp

# 2. 在目标目录配置 Cookie（拉 UP 主列表必需，单视频/评论/下载无需登录）
mkdir -p data && echo "<你的 B 站 Cookie>" > data/cookies.txt

# 3. 运行（SCRIPTS 指向本仓库的 scripts 目录）
python scripts/bili_monitor.py --video BV17VhU62EGP                # 单视频详情
python scripts/bili_monitor.py --video BV17VhU62EGP --download     # 详情 + 下载
python scripts/bili_monitor.py --comments BV1aSh76DEra --sub-comments --count 100  # 评论
python scripts/fetch_all_bvids.py "https://space.bilibili.com/<UID>/"  # 某 UP 主全量 BV
```

> 完整用法、参数速查、Cookie 获取方式、MongoDB 配置见 [scripts/README.md](scripts/README.md)。

## 目录结构

```
bilibili-collector/
├── scripts/
│   ├── bili_monitor.py     # 主脚本（入口）
│   ├── bili_api.py         # B 站 API 封装（WBI 签名、视频详情、评论）
│   ├── storage.py          # 存储层（Excel + MongoDB）
│   └── fetch_all_bvids.py  # 分页拉某 UP 主全部 BV 号
├── references/api.md       # 接口参考
└── data/                   # 数据目录（运行时生成，已 gitignore）
```

## 免责声明

仅供个人学习研究使用，请遵守 B 站用户协议与相关法律法规，控制请求频率，勿用于商业用途或侵犯他人权益。
