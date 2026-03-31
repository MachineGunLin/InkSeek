# 寻墨 InkSeek

用于自动化处理电子书导入与微信读书链路管理。

## 快速开始

```bash
python3 main.py login
python3 main.py check
python3 main.py seek "科学怪人"
python3 main.py upload <path-to-file>
python3 src/bot_server.py
```

## 指令一览

| 指令 | 说明 |
| --- | --- |
| `python3 main.py login` | 打开微信读书登录页并保存 Session |
| `python3 main.py check` | 校验当前 Session 是否可用 |
| `python3 main.py seek "书名"` | 先扫书架查重，再取站内候选；CLI 模式默认自动选择推荐值最高版本，未命中时再走公开书源并执行封面检查 |
| `python3 main.py upload <path>` | 上传文件到微信读书，成功后自动归档到 `data/archive/` |
| `python3 src/bot_server.py` | 启动 Telegram 遥控器，接收授权用户发来的书名 |

## 目录结构

- `src/`：脚本实现
- `data/`：运行时文件目录
- `data/downloads/`：待处理文件目录
- `data/archive/`：上传完成后的归档目录
- `config/`：配置目录

## 运行配置

- 在根目录准备 `.env`
- Telegram 遥控器需要 `TELEGRAM_BOT_TOKEN` 与 `ALLOWED_USER_ID`
- 微信读书上传仍依赖已有登录 Session

## 进度墙

- [已完成] 微信读书全链路自动化。
- [已完成] 项目架构脱敏与标准化。
- [已完成] Telegram 遥控器部署。
- [已完成] 接入公开书源 (Anna's Archive .gl 镜像)，增加搜索区域限定与标题相似度校验，提升检索精度。
