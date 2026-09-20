"""通过管理员 API 获取已留存观测的时间范围聚合，无需读取宿主日志。"""

import argparse
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="Fusion API 根地址")
    parser.add_argument("--from", dest="start", required=True, help="ISO 时间，含起点；无时区按 Asia/Shanghai")
    parser.add_argument("--to", dest="end", required=True, help="ISO 时间，不含终点")
    args = parser.parse_args()
    token = os.environ.get("FUSION_API_TOKEN")
    if not token:
        print("缺少 FUSION_API_TOKEN 管理员访问令牌", file=sys.stderr)
        return 2
    url = (
        args.base_url.rstrip("/")
        + "/api/admin/audit/product-answer-observations?"
        + urlencode({"from": args.start, "to": args.end})
    )
    request = Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urlopen(request, timeout=30) as response:
            result = json.load(response)
    except HTTPError as exc:
        print(f"观测查询失败：HTTP {exc.code}", file=sys.stderr)
        return 1
    except (URLError, ValueError) as exc:
        print(f"观测查询失败：{type(exc).__name__}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
