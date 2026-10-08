"""天气产品工具。工具名单独放在包入口，供审计、脱敏等模块引用而不加载供应商实现。"""

WEATHER_FORECAST_TOOL_NAME = "weather_forecast"
WEATHER_TOOL_NAMES = frozenset({WEATHER_FORECAST_TOOL_NAME})
