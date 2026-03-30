from __future__ import annotations

from annas_bridge import run_seek as run_public_seek
from utils import ensure_runtime_dirs, fail, log_info, log_success
from weread_search import (
    STATE_DUPLICATE_FOUND,
    STATE_NOT_FOUND,
    STATE_WAITING_FOR_SELECTION,
    WeReadCandidate,
    WeReadSeekPreparation,
    add_candidate_to_shelf,
    log_candidate_preview,
    prepare_seek_selection,
    select_highest_rated,
)


def prepare_seek_request(query: str) -> WeReadSeekPreparation:
    ensure_runtime_dirs()
    normalized_query = query.strip()
    if not normalized_query:
        fail("检索关键词不能为空")

    log_info("正在翻阅微信读书...")
    preparation = prepare_seek_selection(normalized_query, limit=3)
    if preparation.state == STATE_DUPLICATE_FOUND and preparation.duplicate is not None:
        log_success(f"书架已存在《{preparation.duplicate.title}》，无需重复入库。")
        return preparation

    if preparation.state == STATE_WAITING_FOR_SELECTION:
        log_candidate_preview(preparation.candidates)
        return preparation

    log_info("站内无果，正在启动公开书源寻墨...")
    return preparation


def format_candidate_options(candidates: list[WeReadCandidate]) -> str:
    lines = ["站内检索到以下版本，请回复数字选择："]
    for index, candidate in enumerate(candidates, start=1):
        translator = candidate.translator or "未标注"
        lines.append(
            f"{index}. {candidate.title} | 译者：{translator} | 推荐值：{candidate.rating:.1f}%"
        )
    lines.append("60 秒未回复时，我会自动选择推荐值最高的版本。")
    return "\n".join(lines)


def execute_selection(preparation: WeReadSeekPreparation, *, selection_index: int | None = None) -> str:
    if preparation.state != STATE_WAITING_FOR_SELECTION or not preparation.candidates:
        fail("当前没有可执行的候选版本")

    if selection_index is None:
        candidate = select_highest_rated(preparation.candidates)
        source = "已自动选择推荐值最高的版本"
    else:
        if selection_index < 0 or selection_index >= len(preparation.candidates):
            fail("选书序号超出范围")
        candidate = preparation.candidates[selection_index]
        source = "已根据您的选择完成入库"

    add_candidate_to_shelf(candidate)
    return f"{source}：《{candidate.title}》"


def run_public_fallback(query: str) -> None:
    run_public_seek(query)


def run_seek(query: str) -> None:
    preparation = prepare_seek_request(query)

    if preparation.state == STATE_DUPLICATE_FOUND:
        return

    if preparation.state == STATE_WAITING_FOR_SELECTION:
        result = execute_selection(preparation, selection_index=None)
        log_success(result)
        return

    if preparation.state != STATE_NOT_FOUND:
        fail("未知的选书状态")

    run_public_fallback(preparation.query)
