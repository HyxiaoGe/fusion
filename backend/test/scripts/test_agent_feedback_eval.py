"""固定证据回放必须使用实际反馈，不能把检测器通过冒充答案正确。"""

import json

import pytest

from scripts import agent_feedback_eval as evaluator


def sample():
    return {
        "id": "ownership",
        "origin": "constructed",
        "question": "甲项目为何投放鱼苗？",
        "search_query": "独立检索词",
        "sources": [
            {"title": "甲项目简报", "url": "https://www.example.org/a", "description": "甲项目本次投放5万尾。"},
            {"title": "乙项目简报", "url": "https://www.example.org/b", "description": "乙公司累计投放382万尾。"},
        ],
        "reads": [],
        "review_checks": ["评审专用秘密标记：不得把乙的数据归给甲"],
    }


def test_model_input_contains_actual_observation_without_review_answers():
    messages = evaluator.build_replay_messages(sample())
    text = json.dumps(messages, ensure_ascii=False)
    assert "评审专用秘密标记" not in text
    tools = [item for item in messages if item["role"] == "tool"]
    assert len(tools) == 1
    assert "独立检索词" in tools[0]["content"]
    assert "www.example.org/a" in tools[0]["content"]
    assert "[1]" in tools[0]["content"] and "[2]" in tools[0]["content"]


def test_wrong_entity_with_valid_citation_never_becomes_automatic_quality_pass():
    report = evaluator.build_review_record(sample(), "甲项目累计投放382万尾。[2]", model="test-model")
    assert report["citation_check"]["unknown_indexes"] == []
    assert report["quality_status"] == "requires_review"
    assert report["product_acceptance"] is False
    assert report["answer"] == "甲项目累计投放382万尾。[2]"
    assert report["review_checks"] == sample()["review_checks"]


def test_unknown_citation_and_empty_answer_are_reported():
    report = evaluator.build_review_record(sample(), "情况见资料[9]。", model="test-model")
    assert report["citation_check"]["unknown_indexes"] == [9]
    assert evaluator.build_review_record(sample(), "", model="test-model")["quality_status"] == "empty_answer"


def test_url_read_keeps_search_global_source_number():
    value = sample()
    value["reads"] = [{"source_index": 2, "content": "乙公司累计投放382万尾，统计口径仅为本公司。"}]
    messages = evaluator.build_replay_messages(value)
    assert messages[-1]["role"] == "tool"
    assert "[2]" in messages[-1]["content"]
    assert "乙公司累计" in messages[-1]["content"]


def test_duplicate_sample_id_fails_before_execution(tmp_path):
    path = tmp_path / "fixtures.json"
    path.write_text(json.dumps([sample(), sample()]), encoding="utf-8")
    with pytest.raises(ValueError, match="重复"):
        evaluator.load_samples(path)


def test_dry_run_does_not_call_provider(tmp_path, capsys, monkeypatch):
    path = tmp_path / "fixtures.json"
    path.write_text(json.dumps([sample()]), encoding="utf-8")

    def forbidden(*args, **kwargs):
        pytest.fail("默认预览不应访问模型")

    monkeypatch.setattr(evaluator, "call_model", forbidden)
    assert evaluator.main(["--samples", str(path)]) == 0
    assert '"mode": "dry_run"' in capsys.readouterr().out


def test_replay_tool_history_explicitly_marks_absent_reasoning():
    messages = evaluator.build_replay_messages(sample())
    history = [message for message in messages if message.get("tool_calls")]
    assert history
    assert all(message.get("reasoning_content") == "" for message in history)


def test_synthesis_transport_declares_tools_but_does_not_request_new_search(monkeypatch):
    import httpx

    captured = []

    def reply(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "已核实[1]"}, "finish_reason": "stop"}]})

    client = httpx.Client(transport=httpx.MockTransport(reply))
    monkeypatch.setenv("LITELLM_PROXY_URL", "http://model.test/v1")
    monkeypatch.setenv("LITELLM_API_KEY", "test-only")
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client)
    evaluator.call_model(
        evaluator.build_model_request(evaluator.build_replay_messages(sample()), model="test", max_tokens=100)
    )
    assert captured[0]["tool_choice"] == "none"
    assert {tool["function"]["name"] for tool in captured[0]["tools"]} == {"web_search", "url_read"}


def test_replay_record_contains_exact_request_and_provider_identity(tmp_path, monkeypatch):
    path = tmp_path / "fixtures.json"
    output = tmp_path / "result.jsonl"
    path.write_text(json.dumps([sample()]), encoding="utf-8")
    captured = []

    def model_reply(*args, **kwargs):
        captured.append((args, kwargs))
        return {
            "model": "provider-resolved-model",
            "choices": [{"message": {"content": "甲项目本次5万尾[1]"}, "finish_reason": "stop"}],
        }

    monkeypatch.setattr(evaluator, "call_model", model_reply)
    assert (
        evaluator.main(
            [
                "--samples",
                str(path),
                "--apply",
                "--model",
                "requested-alias",
                "--max-tokens",
                "123",
                "--output",
                str(output),
            ]
        )
        == 0
    )
    record = json.loads(output.read_text())
    request = record["request"]
    assert captured == [((request,), {})]
    assert request["max_tokens"] == 123
    assert request["tool_choice"] == "none" and request["stream"] is False
    assert record["response_model"] == "provider-resolved-model"
    assert record["adapter_version"]
    for key in ("request_sha256", "app_sha256", "script_sha256", "samples_sha256"):
        assert len(record[key]) == 64
    assert "Authorization" not in request and "headers" not in request


def test_transport_does_not_log_internal_endpoint_or_key(monkeypatch, caplog):
    import httpx

    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})))
    monkeypatch.setenv("LITELLM_PROXY_URL", "http://private-provider.test/v1")
    monkeypatch.setenv("LITELLM_API_KEY", "credential-test-only")
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client)
    with caplog.at_level("INFO"):
        evaluator.call_model(
            evaluator.build_model_request(evaluator.build_replay_messages(sample()), model="test", max_tokens=100)
        )
    assert "private-provider.test" not in caplog.text
    assert "credential-test-only" not in caplog.text
