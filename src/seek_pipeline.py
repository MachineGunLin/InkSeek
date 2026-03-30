from __future__ import annotations

from annas_bridge import run_seek as run_public_seek
from utils import ensure_runtime_dirs, fail, log_info, log_success
from weread_search import try_add_best_match


def run_seek(query: str) -> None:
    ensure_runtime_dirs()
    normalized_query = query.strip()
    if not normalized_query:
        fail("检索关键词不能为空")

    log_info("正在翻阅微信读书...")
    weread_result = try_add_best_match(normalized_query)
    if weread_result.found:
        log_success(
            f"站内已有，已为您挑选好评率最高（{weread_result.rating:.1f}%）的版本入库。"
        )
        return

    log_info("站内无果，正在启动公开书源寻墨...")
    run_public_seek(normalized_query)
