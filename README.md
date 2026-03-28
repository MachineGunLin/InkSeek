# 寻墨 InkSeek

> “寻墨”起步于一个简单的烦恼：有些书不该消失在搜索框的一次失灵里。让阅读重回自由，就从拆掉这堵搬书的墙开始。

一句话：寻墨不搞虚的，就是为了终结“下书、搬书”的摩擦力。

## 备忘

- 入口：Telegram 发书名。
- 动作：脚本去 Z-Lib 找书并下载 `epub`。
- 收口：Playwright 自动上传到微信读书私有文档。
- 目标：把“手动来回倒书”这件事彻底删掉。

## 今天落地

- OpenClaw 基础编排已放在 `docker-compose.yml`。
- 端口避让完成：Gateway `8082`，Bridge `3001`。
- 目录已建：`src/`、`data/`、`config/`。
- `.env.example` 已预留关键变量。

## 怎么跑

```bash
cp .env.example .env
# 填写 TG_TOKEN / ZLIB_COOKIE / WEREAD_COOKIE / TAVILY_API_KEY

docker compose up -d
```

## 进度墙

- [已完成] 项目基础设施与环境配置。
- [已完成] 微信读书登录 Session 持久化 (极简稳健版)。
- [修复中] 纠正微信读书入口 URL，解决 404 报错。
- [待开始] TG Bot 收到书名后触发任务。
- [待开始] Z-Lib 检索与 EPUB 下载稳定化。
- [待开始] 失败重试与任务状态回执。
