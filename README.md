# 寻墨 InkSeek

> [!IMPORTANT]
> **项目状态：暂停开发 / 逻辑封存**
> 由于在 L2 阶段尝试接入 Anna's Archive (AA) 时遭遇高强度人机验证 (Cloudflare/hCaptcha)，考虑到单人维护的成本与反爬压力，决定暂时封存公开书源的自动接入逻辑。

用于自动化处理电子书导入与微信读书链路管理。

## 快速开始

```bash
python3 main.py login
python3 main.py check
python3 main.py seek "科学怪人"
python3 main.py upload <path-to-file>
python3 src/bot_server.py
```

## 已完成核心功能

- **L1 微信读书自动化**：实现站内精准搜索、书架查重及高分版本自动入库。
- **长效会话管理**：重构了基于状态机的 Session 持久化逻辑，支持 WeRead 登录态的自动恢复与校验。
- **AA 详情页解析引擎**：实现了对 Anna's Archive MD5 详情页的结构化解析（提取 Cloudflare/IPFS 镜像及文件大小），作为技术储备留存。
- **Telegram 遥控器**：支持远程发送书名触发检索逻辑。

## 遇到的挑战 (Roadblocks)

- **hCaptcha 验证壁垒**：Anna's Archive .gl 域名及相关下载镜像（如 momot.rs）已开启强力防护。目前的“人工辅助模式”虽可跑通，但无法实现 100% 的无人值守全自动闭环。
- **维护成本**：因影子书库域名及反爬策略跳变频繁，维护该链路的开发成本已超出项目初衷。

## 指令一览

| 指令 | 说明 |
| --- | --- |
| `python3 main.py login` | 打开微信读书登录页并保存 Session |
| `python3 main.py check` | 校验当前 Session 是否可用 |
| `python3 main.py seek "书名"` | 先扫书架查重，再取站内候选；CLI 模式支持自动选择与结果比对 |
| `python3 main.py upload <path>` | 上传文件到微信读书，成功后自动归档到 `data/archive/` |
| `python3 src/bot_server.py` | 启动 Telegram 遥控器，接收授权用户发来的书名 |

## 目录结构

- `src/`：脚本实现 (包含已封存的 `annas_bridge.py`)
- `data/`：运行时文件目录
- `data/downloads/`：待处理文件目录
- `data/archive/`：上传完成后的归档目录
- `config/`：配置目录

## 进度墙 (已封存)

- [已完成] 微信读书全链路自动化。
- [已完成] 项目架构脱敏与标准化。
- [已完成] Telegram 遥控器部署。
- [已完成] 接入公开书源桥接 (Anna's Archive) 详情页解析 logic。
- [已暂停] 应对 hCaptcha 的全自动绕过。
