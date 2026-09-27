"""pytest 入口：先加载 test 包，让 test/__init__.py 的 env fallback 在收集任何用例前生效。

test/scripts、test/ai 等子目录没有 __init__.py，按目录顺序先收集到它们时，
不经过本文件就会在 import app 时直接构造 Settings() 而缺少必填 env（#143）。
"""
