# 寻墨 InkSeek

用于自动化处理电子书下载与微信读书导入流程。

## 项目结构

- OpenClaw 编排位于 `docker-compose.yml`
- 主要目录为 `src/`、`data/`、`config/`
- `.env.example` 预留了运行所需环境变量

## 运行方式

```bash
cp .env.example .env
# 按需填写 .env 中的环境变量

docker compose up -d
```

## 进度墙

- [已完成] 微信读书环境与登录 Session 自动化
- [进行中] 自动传书逻辑（最后调试阶段）
- [待开始] Z-Lib 书源接入
