"""汇总搜索候选信息；由模型按证据缺口决定后续搜索和阅读。"""

from __future__ import annotations

from app.services.source_candidate_ranker import (
    SearchResultForRanking,
    SourceSelectionPlan,
    format_source_selection_guidance,
    rank_search_sources,
)


def build_search_read_plan(search_results: list[SearchResultForRanking]) -> SourceSelectionPlan:
    """保留跨搜索去重和供应商原始顺序。"""
    return rank_search_sources(search_results)


def format_search_read_plan_guidance(plan: SourceSelectionPlan) -> str:
    """生成候选信息，不规定阅读数量和优先级。"""
    return format_source_selection_guidance(plan)
