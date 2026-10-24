"""首页「今日灵感」按产品功能出题的目录。

灵感题目展示的是 Fusion 能做什么，所以按功能列出方向，每天按目录出题。
接入新能力时在这里加一项，首页就会出现对应的题。

requires 对应权益接口里的功能开关（见 guest_quota.build_entitlements）：用户没有该功能时
前端不展示这类题，比如游客看不到生图、地图、天气题。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InspirationCapability:
    key: str
    label: str
    guidance: str
    count: int
    ttl_hours: int
    requires: str | None = None


INSPIRATION_CAPABILITIES: tuple[InspirationCapability, ...] = (
    InspirationCapability(
        key="image",
        label="生成图片",
        guidance="请 AI 画一张图：插画、海报、头像、壁纸、产品图等，写清画面内容和风格",
        count=6,
        ttl_hours=7 * 24,
        requires="image_generation",
    ),
    InspirationCapability(
        key="weather",
        label="天气",
        guidance="问某个中国城市今天、明天或这周的天气，或者据天气决定穿衣、出行、带不带伞",
        count=4,
        ttl_hours=30,
        requires="mcp_tools",
    ),
    InspirationCapability(
        key="map",
        label="地图与出行",
        guidance="问中国城市里的路线怎么走、某地附近有什么好吃好玩的、一日游怎么安排",
        count=5,
        ttl_hours=7 * 24,
        requires="map",
    ),
    InspirationCapability(
        key="news",
        label="联网查新闻",
        guidance="让 AI 联网查新闻：围绕当下的热点问来龙去脉、最新进展或影响",
        count=5,
        ttl_hours=30,
    ),
    InspirationCapability(
        key="document",
        label="生成文档",
        guidance="让 AI 写一份完整文档：旅行攻略、学习计划、周报、活动方案、产品说明等",
        count=4,
        ttl_hours=7 * 24,
    ),
    InspirationCapability(
        key="research",
        label="调研分析",
        guidance="需要查多方资料再对比分析的问题：选购对比、行业现状、方案优劣，可以围绕热点里的产品、公司或行业",
        count=4,
        ttl_hours=7 * 24,
    ),
    InspirationCapability(
        key="writing",
        label="写作与润色",
        guidance="写文案、改写、润色、翻译一段具体的话，问题里直接带上要处理的原文",
        count=4,
        ttl_hours=7 * 24,
    ),
    InspirationCapability(
        key="knowledge",
        label="知识问答",
        guidance="生活、健康、科学、历史等常识性问题",
        count=4,
        ttl_hours=7 * 24,
    ),
    InspirationCapability(
        key="code",
        label="编程",
        guidance="写一段具体的代码、解释某个技术概念或排查常见报错",
        count=3,
        ttl_hours=7 * 24,
    ),
)

CAPABILITY_BY_KEY = {capability.key: capability for capability in INSPIRATION_CAPABILITIES}
