# 寻墨 InkSeek

> [!IMPORTANT]
> **项目状态：暂停开发 / 逻辑封存**
> 由于在 L2 阶段尝试接入 Anna's Archive (AA) 时遭遇高强度人机验证 (Cloudflare/hCaptcha)，考虑到单人维护的成本与反爬压力，决定暂时封存公开书源的自动接入逻辑。

用于自动化处理电子书导入与微信读书链路管理。

## 快速开始 / Quick Start

### 1. 环境准备

先安装 Python 依赖，再初始化 Playwright 浏览器环境：

```bash
pip install -r requirements.txt
playwright install chromium
```

如果本机没有 `playwright` 可执行命令，可以改用：

```bash
python3 -m playwright install chromium
```

### 2. 配置 `.env`

请在项目根目录新建一个 `.env` 文件，并填入以下内容：

```env
# Telegram Bot Token（从 @BotFather 获取）
TELEGRAM_BOT_TOKEN=your_bot_token_here

# 允许使用机器人的用户 ID（从 @userinfobot 获取，多个 ID 用逗号分隔）
ALLOWED_USER_IDS=12345678,87654321
```

来源说明：

- `TELEGRAM_BOT_TOKEN`：在 Telegram 搜索并私聊 `@BotFather`，发送 `/newbot` 创建机器人，按提示完成后获取 API Token。
- `ALLOWED_USER_IDS`：在 Telegram 私聊 `@userinfobot`，获取自己的数字 ID。这里使用白名单是出于安全考虑，避免未经授权的用户操作你的微信读书书架。

操作提醒：

- `.env` 文件放在项目根目录，与 [main.py](/Users/linrongjian/Desktop/ship_it/InkSeek/main.py) 同级。
- 不要将 `.env` 上传到公开仓库。当前仓库的 [.gitignore](/Users/linrongjian/Desktop/ship_it/InkSeek/.gitignore) 已忽略该文件。

### 3. 第一次使用：登录微信读书

第一次使用，或登录态失效时，必须先在终端执行：

```bash
python3 main.py login
```

执行后会弹出浏览器，请手动扫码登录。登录成功后，脚本会自动保存登录态到 `data/weread_state.json`。后续命令会复用这份登录态，通常无需重复扫码。

建议登录完成后顺手校验一次：

```bash
python3 main.py check
```

### 4. 使用方式

CLI 模式：

```bash
python3 main.py seek "书名"
```

Telegram 模式：

当前仓库的机器人入口位于 `src/` 目录，请先确认 `.env` 已配置完成，然后执行：

```bash
python3 src/bot_server.py
```

启动后，直接在 Telegram 中发送书名即可触发检索流程。

### 5. 常见问题 / Troubleshooting

- 如果遇到 `ERR_CONNECTION_CLOSED`，请先检查本机代理设置。
- 建议将 `weread.qq.com` 加入代理白名单，或临时关闭全局代理后再重试。
- 如果提示登录态失效、Session 不可用或无法进入微信读书首页，请重新执行 `python3 main.py login`。

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
