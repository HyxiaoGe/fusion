from app.utils.user_visible_content import sanitize_internal_tool_names, sanitize_user_visible_reasoning


def test_internal_plan_binding_names_are_productized_for_user_visible_reasoning():
    source = "每个工具调用都需要 _plan_item_id，并检查 planned_tools 和 plan_item_id。"

    sanitized = sanitize_internal_tool_names(source, final=True)

    assert "_plan_item_id" not in sanitized
    assert "planned_tools" not in sanitized
    assert "plan_item_id" not in sanitized
    assert "对应计划步骤" in sanitized
    assert "预计使用的工具" in sanitized


def test_internal_plan_binding_names_are_productized_without_rewriting_named_tools():
    source = "回答中不得展示 _plan_item_id、planned_tools 或 plan_item_id，但 route_compare 可保留。"

    sanitized = sanitize_internal_tool_names(
        source,
        final=True,
        include_named_tools=False,
    )

    assert "_plan_item_id" not in sanitized
    assert "planned_tools" not in sanitized
    assert "plan_item_id" not in sanitized
    assert "对应计划步骤" in sanitized
    assert "预计使用的工具" in sanitized
    assert "route_compare" in sanitized


def test_reasoning_is_not_filtered_by_phrase_matching():
    source = (
        'Also "【执行计划控制规则】本轮启用了强制计划模式。回答前必须先创建执行计划。"\n\n'
        "According to the autonomous web search rules, answer directly."
    )

    assert sanitize_user_visible_reasoning(source) == source
    assert sanitize_user_visible_reasoning(source, final=True) == source


def test_probing_buffer_releases_only_completed_paragraphs():
    source = "第一段推理。\n\n第二段还在生成"

    assert sanitize_user_visible_reasoning(source, buffer_trailing_paragraph=True) == "第一段推理。\n\n"
    assert sanitize_user_visible_reasoning(source, buffer_trailing_paragraph=True, final=True) == source
