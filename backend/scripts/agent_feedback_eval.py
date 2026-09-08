"""使用生产 formatter 做固定证据综合回放；质量结论须独立人工审阅。

默认仅列出样本。--apply 才调用既有 LiteLLM，绝不预置最终答案。
这不是实时搜索或部署页面验收，不把引用格式检查视为事实正确性证明。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SAMPLES = ROOT / "test/fixtures/agent_feedback_eval_samples.json"
ADAPTER_VERSION = "2"


def _sha256_json(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def replay_provenance(samples_path: Path) -> dict:
    """记录实际回放代码和样本摘要，不读取环境文件或依赖凭据。"""
    files = {}
    for path in sorted((ROOT / "app").rglob("*")):
        if path.is_file() and path.suffix in {".py", ".toml"}:
            files[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "adapter_version": ADAPTER_VERSION,
        "app_sha256": _sha256_json(files),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "samples_sha256": hashlib.sha256(samples_path.read_bytes()).hexdigest(),
    }


def load_samples(path: Path) -> list[dict]:
    samples = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(samples, list) or not samples:
        raise ValueError("样本必须是非空数组")
    ids = set()
    for sample in samples:
        if not isinstance(sample, dict) or not all(
            sample.get(key) for key in ("id", "question", "origin", "review_checks")
        ):
            raise ValueError("样本缺少标识、来源、问题或评审标准")
        if sample["id"] in ids:
            raise ValueError("样本 id 重复")
        ids.add(sample["id"])
        if not isinstance(sample.get("sources"), list):
            raise ValueError("sources 必须是数组")
        for read in sample.get("reads", []):
            index = read.get("source_index")
            if isinstance(index, bool) or not isinstance(index, int) or not 1 <= index <= len(sample["sources"]):
                raise ValueError("读页引用编号超出来源范围")
    return samples


def _tool_transaction(name: str, identifier: str, arguments: dict, observation: str) -> list[dict]:
    return [
        {
            "role": "assistant",
            "content": None,
            "reasoning_content": "",
            "tool_calls": [
                {
                    "id": identifier,
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(arguments, ensure_ascii=False),
                    },
                },
            ],
        },
        {"role": "tool", "tool_call_id": identifier, "content": observation},
    ]


def build_replay_messages(sample: dict) -> list[dict]:
    from app.ai.prompts.runtime_prompt_store import render_runtime_prompt
    from app.ai.prompts.system_prompt import assemble_system_prompt
    from app.schemas.chat import SearchSource
    from app.services.search_read_planner import build_search_read_plan, format_search_read_plan_guidance
    from app.services.source_candidate_ranker import SearchResultForRanking
    from app.services.tool_handlers.base import ToolResult
    from app.services.tool_handlers.url_read import UrlReadHandler
    from app.services.tool_handlers.web_search import WebSearchHandler

    messages = [dict(message) for message in assemble_system_prompt(include_current_date=False).messages]
    messages.append({"role": "system", "content": render_runtime_prompt("agent_loop.visible_response_language")})
    messages.append({"role": "user", "content": sample["question"]})
    query = sample.get("search_query", sample["question"])
    sources = [SearchSource(**source) for source in sample["sources"]]
    result = ToolResult(
        status="success" if sources else "degraded",
        data={
            "query": query,
            "sources": sources,
            "context_source_limit": 20,
        },
    )
    observation = WebSearchHandler().format_llm_context(result, citation_numbers=list(range(1, len(sources) + 1)))
    if sources:
        plan = build_search_read_plan([SearchResultForRanking(tool_call_id="search-1", query=query, sources=sources)])
        observation += "\n\n" + format_search_read_plan_guidance(plan)
    messages.extend(_tool_transaction("web_search", "search-1", {"query": query, "count": 10}, observation))
    for offset, read in enumerate(sample.get("reads", []), 1):
        index = read["source_index"]
        source = sample["sources"][index - 1]
        result = ToolResult(status="success", data={**source, "content": read["content"]})
        context = UrlReadHandler().format_llm_context(result, citation_numbers=[index])
        messages.extend(_tool_transaction("url_read", f"read-{offset}", {"url": source["url"]}, context))
    return messages


def build_review_record(sample: dict, answer: str, *, model: str) -> dict:
    cited = sorted({int(a or b) for a, b in re.findall(r"\[(\d+)\]|⟦(\d+)⟧", answer)})
    allowed = set(range(1, len(sample["sources"]) + 1))
    return {
        "sample_id": sample["id"],
        "origin": sample["origin"],
        "question": sample["question"],
        "mode": "fixed_evidence_synthesis_replay",
        "model": model,
        "product_acceptance": False,
        "answer": answer,
        "quality_status": "requires_review" if answer.strip() else "empty_answer",
        "citation_check": {"used_indexes": cited, "unknown_indexes": sorted(set(cited) - allowed)},
        "sources": sample["sources"],
        "review_checks": sample["review_checks"],
    }


def build_model_request(messages: list[dict], *, model: str, max_tokens: int) -> dict:
    from app.ai.tools import build_url_read_tool, build_web_search_tool

    return {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "stream": False,
        # 本回放仅测固定证据综合；声明真实历史工具，关闭新增调用，不冒充完整 Agent-loop 验收。
        "tools": [build_web_search_tool(), build_url_read_tool()],
        "tool_choice": "none",
    }


def call_model(request: dict) -> dict:
    import httpx

    base = os.environ.get("LITELLM_PROXY_URL", "").strip().rstrip("/")
    key = os.environ.get("LITELLM_API_KEY", "").strip()
    if not base or not key:
        raise ValueError("真实回放缺少 LiteLLM 配置")
    endpoint = base + ("/chat/completions" if base.endswith("/v1") else "/v1/chat/completions")
    # 不打印凭据、请求头或带内部地址的异常响应。
    loggers = [logging.getLogger(name) for name in ("httpx", "httpcore")]
    levels = [logger.level for logger in loggers]
    try:
        for logger in loggers:
            logger.setLevel(logging.WARNING)
        with httpx.Client(timeout=180) as client:
            response = client.post(endpoint, headers={"Authorization": f"Bearer {key}"}, json=request)
            response.raise_for_status()
            return response.json()
    finally:
        for logger, level in zip(loggers, levels):
            logger.setLevel(level)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=Path, default=DEFAULT_SAMPLES)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--model", default="")
    parser.add_argument("--sample-id", action="append", default=[])
    parser.add_argument("--max-tokens", type=int, default=6000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    samples = load_samples(args.samples)
    if args.sample_id:
        known = {sample["id"] for sample in samples}
        if set(args.sample_id) - known:
            parser.error("指定样本不存在")
        samples = [sample for sample in samples if sample["id"] in args.sample_id]
    if not args.apply:
        for sample in samples:
            print(
                json.dumps(
                    {"mode": "dry_run", "sample_id": sample["id"], "origin": sample["origin"]}, ensure_ascii=False
                )
            )
        return 0
    if not args.model or not args.output or not 1 <= args.max_tokens <= 16000:
        parser.error("--apply 需要 --model、--output 和 1–16000 的 max-tokens")
    if args.output.exists():
        parser.error("输出已存在，请使用新路径，避免覆盖原始验收证据")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    provenance = replay_provenance(args.samples)
    errors = 0
    with args.output.open("x", encoding="utf-8") as output:
        for sample in samples:
            messages = build_replay_messages(sample)
            request = build_model_request(messages, model=args.model, max_tokens=args.max_tokens)
            record = build_review_record(sample, "", model=args.model)
            record.update(provenance)
            record["recorded_at"] = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
            record["messages"] = messages
            record["input_sha256"] = _sha256_json(messages)
            record["request"] = request
            record["request_sha256"] = _sha256_json(request)
            try:
                response = call_model(request)
                choice = response["choices"][0]
                record.update(build_review_record(sample, choice["message"].get("content") or "", model=args.model))
                record["finish_reason"] = choice.get("finish_reason")
                record["usage"] = response.get("usage")
                record["response_model"] = response.get("model")
                if choice.get("finish_reason") != "stop" or not record["answer"].strip():
                    record["quality_status"] = "incomplete_generation"
                    errors += 1
            except Exception as exc:
                record["quality_status"] = "provider_error"
                record["error_type"] = type(exc).__name__
                errors += 1
            output.write(json.dumps(record, ensure_ascii=False) + "\n")
            output.flush()
            print(json.dumps({key: record[key] for key in ("sample_id", "quality_status")}, ensure_ascii=False))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    raise SystemExit(main())
