"""受管迁移与发布器能力探针的可达失败路径。"""

import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
REVISION = "b" * 64
IMAGE_ID = "sha256:" + "a" * 64
IMAGE = "crpi-77w10wlykpqilmmb.cn-shenzhen.personal.cr.aliyuncs.com/seanfield/fusion-api@" + IMAGE_ID


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), ROOT / "ops/deploy" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_reader_probe_executes_both_real_contracts_and_rejects_bad_consumer(monkeypatch):
    from app.ai.prompts.prompt_manager import prompt_manager

    module = load("prompt-hold-preflight")
    module.verify_engine_reader_profiles({"none", "jinja2"})
    monkeypatch.setattr(prompt_manager, "format_prompt", lambda *args, **kwargs: "未渲染的正文")
    with pytest.raises(ValueError, match="消费路径"):
        module.verify_engine_reader_profiles({"none", "jinja2"})


def test_bridge_cannot_be_proved_only_by_legacy_active(monkeypatch):
    from app.core import prompt_bundle

    validate = prompt_bundle.validate_published_bundle

    def legacy_only(bundle):
        if bundle.prompts[0].template_engine == "jinja2":
            raise ValueError("目标实际只有旧契约")
        return validate(bundle)

    monkeypatch.setattr(prompt_bundle, "validate_published_bundle", legacy_only)
    with pytest.raises(ValueError, match="旧契约"):
        load("prompt-hold-preflight").verify_engine_reader_profiles({"none", "jinja2"})


@pytest.fixture
def driver(monkeypatch):
    module = load("prompt-engine-transition")
    for key, value in {"GITHUB_ACTIONS": "true", "GITHUB_REF": "refs/heads/master", "GITHUB_ACTOR": "管理员"}.items():
        monkeypatch.setenv(key, value)
    row = {
        "Id": "worker-1",
        "Name": "/fusion-api",
        "Image": IMAGE_ID,
        "State": {"Status": "running", "Health": {"Status": "healthy"}},
        "Config": {
            "Image": IMAGE,
            "Env": [
                "DATABASE_URL=sqlite:///:memory:",
                "PROMPTHUB_SYNC_MODE=apply",
                "PROMPT_P0_BASELINE_ATTESTED=true",
                "PROMPTHUB_PROJECT_SLUG=fusion",
            ],
        },
    }
    calls = []

    def docker(*args, **kwargs):
        calls.append((args, kwargs))
        if args[0] == "ps":
            return "worker-1\n"
        if args[0] == "inspect":
            return json.dumps([row])
        if args[:2] == ("image", "inspect"):
            return json.dumps([{"Id": IMAGE_ID}])
        if "advance_prompt_engine_policy(**request)" in kwargs.get("input", ""):
            return json.dumps({"stage": "bridge", "idempotent": False})
        return "PROMPT_ENGINE_PROOF=" + json.dumps(
            {"source_kind": "prompthub_lkg", "revision": REVISION, "project_slug": "fusion"}
        )

    monkeypatch.setattr(module, "docker", docker)
    return module, row, calls


@pytest.mark.parametrize("apply", [True, False])
def test_transition_validates_workers_and_separate_digest_anchor_before_write(driver, apply):
    module, row, calls = driver
    result = module.transition(
        SimpleNamespace(stage="bridge", expected_revision=REVISION, reason="已独立验收", apply=apply)
    )
    assert result["status"] == ("applied" if apply else "validated")
    assert result["evidence"]["rollback_image"] == IMAGE
    probes = [call for call in calls if call[0][0] == "run"]
    assert len(probes) == 1 and IMAGE_ID in probes[0][0]
    assert probes[0][1]["env"]["DATABASE_URL"] == "sqlite:///:memory:"
    writes = [call for call in calls if "advance_prompt_engine_policy(**request)" in call[1].get("input", "")]
    assert len(writes) == int(apply)


def test_changed_worker_fails_before_policy_write(driver, monkeypatch):
    module, row, calls = driver
    original = module.inventory
    count = 0

    def changed():
        nonlocal count
        count += 1
        rows = copy.deepcopy(original())
        if count > 1:
            rows[0]["Id"] = "replaced-worker"
        return rows

    monkeypatch.setattr(module, "inventory", changed)
    with pytest.raises(ValueError, match="发生变化"):
        module.transition(SimpleNamespace(stage="bridge", expected_revision=REVISION, reason="已独立验收", apply=True))
    assert not any("advance_prompt_engine_policy(**request)" in call[1].get("input", "") for call in calls)


@pytest.mark.parametrize("failure", ["not_healthy", "tag", "outside_workflow"])
def test_unproven_inventory_or_coordination_never_changes_policy(driver, monkeypatch, failure):
    module, row, calls = driver
    if failure == "not_healthy":
        row["State"]["Health"]["Status"] = "starting"
    elif failure == "tag":
        row["Config"]["Image"] = "fusion-api:latest"
    else:
        monkeypatch.delenv("GITHUB_ACTIONS")
    with pytest.raises(ValueError):
        module.transition(SimpleNamespace(stage="bridge", expected_revision=REVISION, reason="验证", apply=True))
    assert not any("advance_prompt_engine_policy(**request)" in call[1].get("input", "") for call in calls)


def test_transition_and_all_deploys_share_non_cancelling_coordinator():
    import yaml

    documents = [
        yaml.safe_load((ROOT / ".github/workflows" / file).read_text())
        for file in ("deploy-dev.yml", "prompt-engine-transition.yml")
    ]
    assert all(item["concurrency"] == {"group": "fusion-dev", "cancel-in-progress": False} for item in documents)


def test_exited_old_worker_with_restart_policy_prevents_transition(driver, monkeypatch):
    module, row, calls = driver
    stopped = copy.deepcopy(row)
    stopped.update(
        {
            "Id": "stopped-old",
            "Name": "/old-api",
            "State": {"Status": "exited"},
            "HostConfig": {"RestartPolicy": {"Name": "always"}},
        }
    )
    original = module.docker

    def docker(*args, **kwargs):
        if args[0] == "inspect":
            return json.dumps([row, stopped])
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "docker", docker)
    with pytest.raises(ValueError, match="恢复"):
        module.transition(SimpleNamespace(stage="bridge", expected_revision=REVISION, reason="复核", apply=True))
    assert not any("advance_prompt_engine_policy(**request)" in call[1].get("input", "") for call in calls)


def test_api_without_docker_healthcheck_uses_actual_http_health(driver):
    module, row, calls = driver
    del row["State"]["Health"]
    assert (
        module.transition(SimpleNamespace(stage="bridge", expected_revision=REVISION, reason="复核", apply=False))[
            "status"
        ]
        == "validated"
    )
    assert any("http://127.0.0.1:8000/health" in " ".join(call[0]) for call in calls)


def test_failed_actual_http_health_prevents_policy_write(driver, monkeypatch):
    module, row, calls = driver
    original = module.docker

    def unhealthy(*args, **kwargs):
        if "http://127.0.0.1:8000/health" in " ".join(args):
            raise RuntimeError("健康探针失败")
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "docker", unhealthy)
    with pytest.raises(RuntimeError):
        module.transition(SimpleNamespace(stage="bridge", expected_revision=REVISION, reason="复核", apply=True))
    assert not any("advance_prompt_engine_policy(**request)" in call[1].get("input", "") for call in calls)
