import csv
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gross_profit_report as report


class GrossProfitReportTest(unittest.TestCase):
    def test_detail_csv_matches_requested_finance_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            report.generate_report(
                [
                    {
                        "env": "cn",
                        "date": "2026-06-03",
                        "user_id": 1,
                        "username": "alice",
                        "channel_id": 7,
                        "channel_name": "azure",
                        "model_name": "gpt-5",
                        "prompt_tokens": 100,
                        "completion_tokens": 20,
                        "quota": Decimal("1000000"),
                        "base_cost_quota": Decimal("1250000"),
                        "revenue_usd": Decimal("1.000000"),
                        "cost_usd": Decimal("1.250000"),
                    }
                ],
                tmp,
                {},
            )

            with open(Path(tmp) / "detail.csv", encoding="utf-8-sig", newline="") as f:
                reader = csv.reader(f)
                header = next(reader)
                row = next(reader)

            self.assertEqual(
                header,
                [
                    "日期", "环境", "用户", "渠道ID", "渠道", "模型", "请求数",
                    "输入Tokens", "输出Tokens", "折扣倍率", "收入(USD)", "成本(USD)",
                    "毛利(USD)", "毛利率(%)",
                ],
            )
            self.assertEqual(
                row,
                [
                    "2026/6/3", "cn", "alice", "7", "azure", "gpt-5", "1",
                    "100", "20", "0.8", "1.000000", "1.250000", "-0.250000", "-25",
                ],
            )

    def test_detail_csv_marks_missing_ratio_instead_of_defaulting_to_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            report.generate_report(
                [
                    {
                        "env": "cn",
                        "date": "2026-06-03",
                        "user_id": 1,
                        "username": "alice",
                        "channel_id": 7,
                        "channel_name": "azure",
                        "model_name": "gpt-5",
                        "prompt_tokens": 100,
                        "completion_tokens": 20,
                        "quota": Decimal("1000000"),
                        "base_cost_quota": Decimal("1000000"),
                        "group_ratio_missing": True,
                        "revenue_usd": Decimal("2.000000"),
                        "cost_usd": Decimal("2.000000"),
                    }
                ],
                tmp,
                {},
            )

            with open(Path(tmp) / "detail.csv", encoding="utf-8-sig", newline="") as f:
                row = next(csv.DictReader(f))

            self.assertEqual(row["折扣倍率"], "缺失")


if __name__ == "__main__":
    unittest.main()
