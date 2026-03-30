# 寻墨 InkSeek

用于自动化处理电子书导入与微信读书链路管理。

## 快速开始

```bash
cp .env.example .env
python3 main.py login
python3 main.py check
python3 main.py seek "Frankenstein"
python3 main.py upload <path-to-file>
```

## 指令一览

| 指令 | 说明 |
| --- | --- |
| `python3 main.py login` | 打开微信读书登录页并保存 Session |
| `python3 main.py check` | 校验当前 Session 是否可用 |
| `python3 main.py seek "书名"` | 从公开书源检索 EPUB，下载后自动上传并归档 |
| `python3 main.py upload <path>` | 上传文件到微信读书，成功后自动归档到 `data/archive/` |

## 目录结构

- `src/`：脚本实现
- `data/`：运行时文件目录
- `data/downloads/`：待处理文件目录
- `data/archive/`：上传完成后的归档目录
- `config/`：配置目录

## 进度墙

- [已完成] 微信读书全链路自动化。
- [已完成] 项目架构脱敏与标准化。
- [进行中] 接入公开书源桥接（替代 TG 方案）。
