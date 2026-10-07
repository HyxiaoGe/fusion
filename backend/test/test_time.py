"""时间工具单元测试"""

import unittest
from datetime import datetime, timezone

from app.utils.time import as_china_time


class AsChinaTimeTests(unittest.TestCase):
    def test_converts_aware_time_to_beijing_offset(self):
        value = as_china_time(datetime(2026, 10, 6, 21, 41, tzinfo=timezone.utc))

        self.assertEqual(value.isoformat(), "2026-10-07T05:41:00+08:00")

    def test_rejects_naive_time(self):
        with self.assertRaises(ValueError):
            as_china_time(datetime(2026, 10, 6, 21, 41))


if __name__ == "__main__":
    unittest.main()
