from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from check_login import run_check
from login_weread import run_login
from seek_pipeline import run_seek
from upload_weread import run_upload
from utils import ensure_runtime_dirs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="寻墨统一命令入口")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("login", help="扫码登录并保存 Session")
    subparsers.add_parser("check", help="校验当前 Session 是否有效")
    seek_parser = subparsers.add_parser("seek", help="先扫书架查重，再给出站内候选；CLI 默认自动选择推荐值最高版本")
    seek_parser.add_argument("query", help="书名或检索关键词")

    upload_parser = subparsers.add_parser("upload", help="上传本地文件到微信读书")
    upload_parser.add_argument("path", help="待上传文件路径")

    return parser


def main() -> None:
    ensure_runtime_dirs()
    args = build_parser().parse_args()

    if args.command == "login":
        run_login()
        return

    if args.command == "check":
        run_check()
        return

    if args.command == "seek":
        run_seek(args.query)
        return

    run_upload(args.path)


if __name__ == "__main__":
    main()
