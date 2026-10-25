"""模型上下线：登记 → 预检 → 发布 / 下线，一条命令完成。

Fusion 的模型目录就是 LiteLLM 代理里 Fusion key 白名单允许的模型：
- add：登记到代理（不进白名单，用户看不到）后用 master key 跑预检；预检失败自动删除。
- publish：加进白名单并推进目录代次，Fusion 立即可见。
- retire：代码里仍写死该模型则拒绝；否则移出白名单、删除代理模型，输出备份。
- set-capabilities：开关已登记模型的能力位（如 thinkingSwitchable），只改元数据里的
  capabilities，回读确认后推进目录代次。
- set-context-window：设置已登记模型的输入窗口（max_input_tokens）。窗口未知时上下文
  管理整轮跳过，既不裁剪也不显示用量；回读确认后推进目录代次。
- set-pricing：改已登记模型的单价（输入/输出/缓存命中，可选 200k 以上分档），USD/1M tokens。
  同时写 litellm_params（代理计费用）和 model_info（Fusion 目录读取用）；未传的可选项保持
  原值不清除。缓存价缺失时命中缓存的输入按全价计，所以有缓存折扣的模型都应填。

在 dev 上运行（master key 只经环境变量传入，不落盘不打印）：
    cd ~/project/litellm-proxy && set -a && . ./.env && set +a
    docker exec -i -e LITELLM_MASTER_KEY fusion-api python -m scripts.model_onboard <command> ...
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Protocol

import httpx

from scripts.check_litellm_candidate_preflight import Candidate, run_preflight, serialize_report

MASTER_KEY_ENV = "LITELLM_MASTER_KEY"
SOURCE = "fusion-model-onboard-v1"
# thinkingSwitchable：传 thinking=disabled 能真正关掉推理（上线前须实测推理 token 归零）。
CAPABILITY_KEYS = (
    "vision",
    "functionCalling",
    "deepThinking",
    "thinkingSwitchable",
    "webSearch",
    "fileSupport",
    "imageGen",
)
APP_ROOT = Path(__file__).resolve().parents[1] / "app"


class OnboardError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


class HttpClient(Protocol):
    def get(self, url: str, **kwargs: Any) -> Any: ...

    def post(self, url: str, **kwargs: Any) -> Any: ...

    def patch(self, url: str, **kwargs: Any) -> Any: ...


class ProxyAdmin:
    """LiteLLM 代理管理接口；只操作 Fusion 自己的模型与 key。"""

    def __init__(self, client: HttpClient, base_url: str, master_key: str, fusion_key: str) -> None:
        self.client = client
        self.base_url = base_url.rstrip("/")
        self.master_key = master_key
        self.fusion_key = fusion_key

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.master_key}"}

    def _json(self, response: Any, code: str) -> Any:
        if response.status_code >= 400:
            raise OnboardError(code, f"HTTP {response.status_code}")
        return response.json()

    def _get(self, path: str, code: str, **params: Any) -> Any:
        response = self.client.get(f"{self.base_url}{path}", headers=self._headers(), params=params, timeout=20.0)
        return self._json(response, code)

    def _post(self, path: str, payload: Mapping[str, Any], code: str) -> Any:
        response = self.client.post(f"{self.base_url}{path}", headers=self._headers(), json=dict(payload), timeout=20.0)
        return self._json(response, code)

    def model_entries(self) -> list[dict[str, Any]]:
        return self._get("/model/info", "model_info_failed").get("data") or []

    def find_model(self, model_id: str) -> dict[str, Any] | None:
        for entry in self.model_entries():
            if entry.get("model_name") == model_id and (entry.get("model_info") or {}).get("db_model"):
                return entry
        return None

    def key_models(self) -> list[str]:
        info = self._get("/key/info", "key_info_failed", key=self.fusion_key).get("info") or {}
        models = info.get("models")
        if not isinstance(models, list):
            raise OnboardError("key_info_failed", "models 缺失")
        return [str(item) for item in models]

    def set_key_models(self, models: list[str]) -> None:
        self._post("/key/update", {"key": self.fusion_key, "models": models}, "key_update_failed")

    def create_model(self, payload: Mapping[str, Any]) -> None:
        self._post("/model/new", payload, "model_new_failed")

    def delete_model(self, model_uuid: str) -> None:
        self._post("/model/delete", {"id": model_uuid}, "model_delete_failed")

    def update_model_metadata(self, model_uuid: str, metadata: Mapping[str, Any]) -> None:
        self.update_model_info(model_uuid, {"metadata": dict(metadata)})

    def update_model_info(self, model_uuid: str, model_info: Mapping[str, Any]) -> None:
        # PATCH 只合并传入字段；实测 litellm_params、定价与上下文窗口保持不变。
        self.update_model(model_uuid, {"model_info": dict(model_info)})

    def update_model(self, model_uuid: str, payload: Mapping[str, Any]) -> None:
        """PATCH /model/{id}/update：litellm_params 与 model_info 都按字段合并，不传的保持原值。"""
        response = self.client.patch(
            f"{self.base_url}/model/{model_uuid}/update",
            headers=self._headers(),
            json=dict(payload),
            timeout=20.0,
        )
        self._json(response, "model_update_failed")


def _usd_per_token(per_million: float) -> float:
    return round(per_million / 1_000_000, 15)


# 命令行单价参数（USD/1M tokens）→ 代理计费字段（USD/token）与元数据里的可读价目键。
PRICING_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("input_price", "input_cost_per_token", "input"),
    ("output_price", "output_cost_per_token", "output"),
    ("cache_price", "cache_read_input_token_cost", "cache_read_input"),
    ("input_price_above_200k", "input_cost_per_token_above_200k_tokens", "input_above_200k"),
    ("output_price_above_200k", "output_cost_per_token_above_200k_tokens", "output_above_200k"),
)


def pricing_from_args(args: argparse.Namespace) -> tuple[dict[str, float], dict[str, Any]]:
    """返回 (计费字段, 元数据价目)。只含命令行实际传入的单价，未传的不出现。"""
    costs: dict[str, float] = {}
    pricing: dict[str, Any] = {"unit": "USD/1M tokens"}
    for arg_name, cost_key, pricing_key in PRICING_FIELDS:
        value = getattr(args, arg_name, None)
        if value is None:
            continue
        if value < 0:
            raise OnboardError("invalid_price", f"{arg_name}={value}")
        costs[cost_key] = _usd_per_token(value)
        pricing[pricing_key] = value
    return costs, pricing


def build_model_payload(args: argparse.Namespace) -> dict[str, Any]:
    capabilities = {key: key in set(args.capabilities) for key in CAPABILITY_KEYS}
    costs, pricing = pricing_from_args(args)
    return {
        "model_name": args.model_id,
        "litellm_params": {
            "model": args.upstream,
            "api_base": args.api_base,
            # 只登记环境变量引用，供应商密钥由代理进程自己解析。
            "api_key": f"os.environ/{args.api_key_env}",
            **costs,
        },
        "model_info": {
            "max_input_tokens": args.context_window,
            **costs,
            "metadata": {
                "source": SOURCE,
                "display_name": args.display_name or args.model_id,
                "provider_key": args.provider_key,
                "provider_display": args.provider_display,
                "capabilities": capabilities,
                "pricing": pricing,
            },
        },
    }


def preflight_candidate(entry: Mapping[str, Any]) -> Candidate:
    metadata = (entry.get("model_info") or {}).get("metadata") or {}
    return Candidate(
        model_id=entry["model_name"],
        litellm_model=(entry.get("litellm_params") or {}).get("model", ""),
        # 已登记到代理，直接按别名调用，不需要候选路由。
        preflight_model=entry["model_name"],
        preflight_route={"status": "ready"},
        capabilities=dict(metadata.get("capabilities") or {}),
        pricing=dict(metadata.get("pricing") or {}),
        provider_key=str(metadata.get("provider_key") or ""),
    )


def find_code_references(model_id: str, root: Path = APP_ROOT) -> list[str]:
    """代码里以字符串字面量写死的模型别名；下线前必须先切走。"""
    needles = (f'"{model_id}"', f"'{model_id}'")
    hits = []
    for path in sorted(root.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if any(needle in line for needle in needles):
                hits.append(f"{path.relative_to(root.parent)}:{lineno}")
    return hits


def bump_catalog_generation() -> str:
    from app.ai import litellm_catalog

    return litellm_catalog.bump_generation()


def cmd_add(admin: ProxyAdmin, args: argparse.Namespace, *, client: HttpClient = httpx) -> dict[str, Any]:
    if admin.find_model(args.model_id):
        raise OnboardError("model_exists", args.model_id)
    admin.create_model(build_model_payload(args))
    entry = admin.find_model(args.model_id)
    if entry is None:
        raise OnboardError("model_readback_failed", args.model_id)
    report = run_preflight(
        candidate=preflight_candidate(entry),
        base_url=admin.base_url,
        api_key=admin.master_key,
        apply=True,
        client=client,
        timeout_seconds=args.timeout_seconds,
    )
    serialized = serialize_report(report, context={})
    checks = serialized["candidate_checks_by_model"][args.model_id]
    # 用例全过还不够：usage/计费缺失会让成本统计失真，同样不准上线。
    healthy = report.healthy and all(checks.values())
    result = {
        "status": "registered",
        "model_id": args.model_id,
        "healthy": healthy,
        "checks": checks,
        "cases": serialized["cases"],
        "quality_issues": serialized["quality_issues"],
    }
    if not healthy:
        admin.delete_model(entry["model_info"]["id"])
        result["status"] = "preflight_failed_rolled_back"
    return result


def cmd_publish(admin: ProxyAdmin, args: argparse.Namespace) -> dict[str, Any]:
    if admin.find_model(args.model_id) is None:
        raise OnboardError("model_missing", args.model_id)
    models = admin.key_models()
    if args.model_id not in models:
        admin.set_key_models([*models, args.model_id])
    if args.model_id not in admin.key_models():
        raise OnboardError("key_readback_failed", args.model_id)
    return {"status": "published", "model_id": args.model_id, "catalog_generation": bump_catalog_generation()}


def cmd_retire(admin: ProxyAdmin, args: argparse.Namespace, *, code_root: Path = APP_ROOT) -> dict[str, Any]:
    references = find_code_references(args.model_id, code_root)
    if references:
        raise OnboardError("model_referenced_in_code", ", ".join(references))
    entry = admin.find_model(args.model_id)
    models = admin.key_models()
    if args.model_id in models:
        admin.set_key_models([item for item in models if item != args.model_id])
    if entry is not None:
        admin.delete_model(entry["model_info"]["id"])
    return {
        "status": "retired",
        "model_id": args.model_id,
        "catalog_generation": bump_catalog_generation(),
        # 供应商密钥只以 os.environ/ 引用登记，备份里不含明文。
        "backup": entry,
    }


def cmd_set_capabilities(admin: ProxyAdmin, args: argparse.Namespace) -> dict[str, Any]:
    entry = admin.find_model(args.model_id)
    if entry is None:
        raise OnboardError("model_missing", args.model_id)
    metadata = dict((entry.get("model_info") or {}).get("metadata") or {})
    before = dict(metadata.get("capabilities") or {})
    after = {**before, **{key: True for key in args.enable}, **{key: False for key in args.disable}}
    admin.update_model_metadata(entry["model_info"]["id"], {**metadata, "capabilities": after})
    readback = admin.find_model(args.model_id)
    readback_capabilities = ((readback or {}).get("model_info") or {}).get("metadata", {}).get("capabilities")
    if readback_capabilities != after:
        raise OnboardError("model_update_readback_failed", args.model_id)
    return {
        "status": "capabilities_updated",
        "model_id": args.model_id,
        "before": before,
        "after": after,
        "catalog_generation": bump_catalog_generation(),
    }


def cmd_set_context_window(admin: ProxyAdmin, args: argparse.Namespace) -> dict[str, Any]:
    if args.tokens <= 0:
        raise OnboardError("invalid_context_window", str(args.tokens))
    entry = admin.find_model(args.model_id)
    if entry is None:
        raise OnboardError("model_missing", args.model_id)
    info = entry.get("model_info") or {}
    before = info.get("max_input_tokens")
    admin.update_model_info(info["id"], {"max_input_tokens": args.tokens})
    readback = (admin.find_model(args.model_id) or {}).get("model_info") or {}
    if readback.get("max_input_tokens") != args.tokens or readback.get("metadata") != info.get("metadata"):
        raise OnboardError("model_update_readback_failed", args.model_id)
    return {
        "status": "context_window_updated",
        "model_id": args.model_id,
        "before": before,
        "after": args.tokens,
        "catalog_generation": bump_catalog_generation(),
    }


def cmd_set_pricing(admin: ProxyAdmin, args: argparse.Namespace) -> dict[str, Any]:
    costs, pricing = pricing_from_args(args)
    entry = admin.find_model(args.model_id)
    if entry is None:
        raise OnboardError("model_missing", args.model_id)
    info = entry.get("model_info") or {}
    params = entry.get("litellm_params") or {}
    metadata = dict(info.get("metadata") or {})
    before = {key: params.get(key) for _arg, key, _pk in PRICING_FIELDS}
    # 价目元数据按键合并：没传的档位保留旧值，和计费字段的合并语义一致。
    merged_pricing = {**dict(metadata.get("pricing") or {}), **pricing}
    # litellm_params 整体回传（路由、api_base、os.environ/ 密钥引用原样带上）：无论代理按字段合并
    # 还是整体替换，路由都不会被改坏；回读再核对一次。
    admin.update_model(
        info["id"],
        {
            "litellm_params": {**params, **costs},
            "model_info": {**costs, "metadata": {**metadata, "pricing": merged_pricing}},
        },
    )
    readback = admin.find_model(args.model_id) or {}
    readback_params = readback.get("litellm_params") or {}
    readback_info = readback.get("model_info") or {}
    unchanged = ("model", "api_base", "api_key")
    if (
        any(readback_params.get(key) != costs[key] for key in costs)
        or any(readback_params.get(key) != params.get(key) for key in unchanged)
        or readback_info.get("max_input_tokens") != info.get("max_input_tokens")
        or (readback_info.get("metadata") or {}).get("capabilities") != metadata.get("capabilities")
    ):
        raise OnboardError("model_update_readback_failed", args.model_id)
    return {
        "status": "pricing_updated",
        "model_id": args.model_id,
        "before": before,
        "after": {key: readback_params.get(key) for _arg, key, _pk in PRICING_FIELDS},
        "pricing": merged_pricing,
        "catalog_generation": bump_catalog_generation(),
    }


def cmd_list(admin: ProxyAdmin, _args: argparse.Namespace) -> dict[str, Any]:
    entries = admin.model_entries()
    published = set(admin.key_models())
    rows = []
    for entry in entries:
        info = entry.get("model_info") or {}
        if not info.get("db_model"):
            continue
        rows.append(
            {
                "model_id": entry.get("model_name"),
                "upstream": (entry.get("litellm_params") or {}).get("model"),
                "published": entry.get("model_name") in published,
                "max_input_tokens": info.get("max_input_tokens"),
            }
        )
    return {"models": sorted(rows, key=lambda row: str(row["model_id"]))}


def _add_pricing_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--input-price", type=float, required=True, help="USD / 1M tokens")
    parser.add_argument("--output-price", type=float, required=True, help="USD / 1M tokens")
    parser.add_argument("--cache-price", type=float, help="缓存命中输入，USD / 1M tokens；不传则按全价计")
    parser.add_argument("--input-price-above-200k", type=float, help="单次输入超 200k 时整次输入单价")
    parser.add_argument("--output-price-above-200k", type=float, help="单次输入超 200k 时整次输出单价")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="登记到代理并预检（不对用户可见）")
    add.add_argument("model_id")
    add.add_argument("--upstream", required=True, help="LiteLLM 底层模型，如 openai/mimo-v2.6-flash")
    add.add_argument("--api-base", required=True)
    add.add_argument("--api-key-env", required=True, help="代理进程里供应商密钥的环境变量名")
    _add_pricing_arguments(add)
    add.add_argument("--context-window", type=int, required=True)
    add.add_argument("--provider-key", required=True)
    add.add_argument("--provider-display", required=True)
    add.add_argument("--display-name")
    add.add_argument("--capabilities", nargs="*", default=[], choices=CAPABILITY_KEYS)
    add.add_argument("--timeout-seconds", type=float, default=60.0)

    for name, help_text in (("publish", "加入 Fusion 白名单，用户可见"), ("retire", "下线并输出备份")):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("model_id")
    sub.add_parser("list", help="列出代理里登记的模型及发布状态")

    set_caps = sub.add_parser("set-capabilities", help="开关已登记模型的能力位")
    set_caps.add_argument("model_id")
    set_caps.add_argument("--enable", nargs="*", default=[], choices=CAPABILITY_KEYS)
    set_caps.add_argument("--disable", nargs="*", default=[], choices=CAPABILITY_KEYS)

    set_window = sub.add_parser("set-context-window", help="设置已登记模型的输入窗口")
    set_window.add_argument("model_id")
    set_window.add_argument("tokens", type=int, help="max_input_tokens，按服务商文档的最大输入")

    set_pricing = sub.add_parser("set-pricing", help="改已登记模型的单价（USD / 1M tokens）")
    set_pricing.add_argument("model_id")
    _add_pricing_arguments(set_pricing)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    master_key = os.environ.get(MASTER_KEY_ENV, "")
    fusion_key = os.environ.get("LITELLM_API_KEY", "")
    if not master_key or not fusion_key:
        print(json.dumps({"status": "error", "code": "missing_credentials"}))
        return 2
    base_url = os.environ.get("LITELLM_PROXY_URL", "http://litellm-proxy:4000")
    admin = ProxyAdmin(httpx, base_url, master_key, fusion_key)
    handlers = {
        "add": cmd_add,
        "publish": cmd_publish,
        "retire": cmd_retire,
        "list": cmd_list,
        "set-capabilities": cmd_set_capabilities,
        "set-context-window": cmd_set_context_window,
        "set-pricing": cmd_set_pricing,
    }
    try:
        result = handlers[args.command](admin, args)
    except OnboardError as exc:
        print(json.dumps({"status": "error", "code": exc.code, "detail": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=1, default=str))
    return 0 if result.get("status") != "preflight_failed_rolled_back" else 1


if __name__ == "__main__":
    sys.exit(main())
