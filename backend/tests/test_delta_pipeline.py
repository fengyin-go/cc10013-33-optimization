"""增量采集共享处理模块的回归测试。

运行：cd backend && .venv/bin/python -m unittest discover -s tests -v
"""
from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from app.main import app
from app.store import store

client = TestClient(app)

WATERMARK = "2026-09-03 12:40"


class DeltaPipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        store.reset()

    def scan(self, entry_id: int, start: str, end: str, **extra):
        values = {"窗口开始": start, "窗口结束": end, **extra}
        response = client.post(f"/api/delta/{entry_id}/scan", json={"values": values})
        self.assertEqual(response.status_code, 200)
        return response.json()

    def entry(self, entry_id: int) -> dict:
        return client.get(f"/api/delta/{entry_id}").json()

    def test_watermark_and_scan_position_are_separate(self) -> None:
        result = self.scan(3, "2026-09-03 12:40", "2026-09-03 12:55")
        self.assertEqual(result["entry"]["确认水位"], "2026-09-03 12:50")
        self.assertEqual(result["entry"]["扫描位置"], "2026-09-03 12:55")

        rewound = client.post("/api/delta/3/rewind").json()
        self.assertTrue(rewound["ok"])
        self.assertEqual(rewound["entry"]["扫描位置"], "2026-09-03 12:40")
        self.assertEqual(rewound["entry"]["确认水位"], "2026-09-03 12:50")

    def test_scan_position_never_overwrites_watermark(self) -> None:
        result = self.scan(
            3,
            "2026-09-03 11:45",
            "2026-09-03 12:00",
            扫描位置="2026-09-03 11:50",
        )
        self.assertEqual(result["entry"]["确认水位"], WATERMARK)
        self.assertEqual(result["entry"]["扫描位置"], "2026-09-03 11:50")

    def test_same_cursor_and_window_yields_one_batch(self) -> None:
        first = self.scan(3, "2026-09-03 12:40", "2026-09-03 12:55")
        second = self.scan(3, "2026-09-03 12:40", "2026-09-03 12:55")
        self.assertEqual(first["batch"]["id"], second["batch"]["id"])
        self.assertEqual(first["batch"]["窗口唯一键"], second["batch"]["窗口唯一键"])
        batches = client.get("/api/delta/batches?cursor_code=DELTA-331").json()
        self.assertEqual(batches["total"], 1)
        outcomes = {item["记录编号"]: item["结果"] for item in second["batch"]["处理结果"]}
        self.assertEqual(outcomes["R331-03"], "重复")
        self.assertEqual(second["batch"]["入账数"], 2)

    def test_dedup_window_does_not_double_book(self) -> None:
        result = self.scan(1, "2026-09-03 12:00", "2026-09-03 12:15")
        batch = result["batch"]
        self.assertEqual(batch["记录数"], 3)
        self.assertEqual(batch["入账数"], 3)
        self.assertEqual(batch["重复数"], 1)

        again = self.scan(1, "2026-09-03 12:00", "2026-09-03 12:15")
        self.assertEqual(again["batch"]["入账数"], 3)
        downstream = client.get("/api/delta/1/downstream").json()
        keys = [item["记录编号"] for item in downstream["记录"]]
        self.assertEqual(len(keys), len(set(keys)))

    def test_three_classes_split_late_at_and_after_watermark(self) -> None:
        result = self.scan(3, "2026-09-03 12:15", "2026-09-03 12:55")
        outcomes = {item["记录编号"]: item for item in result["batch"]["处理结果"]}
        self.assertEqual(outcomes["R331-01"]["判断"], "早于水位")
        self.assertEqual(outcomes["R331-01"]["结果"], "冲突隔离")
        self.assertEqual(outcomes["R331-02"]["判断"], "恰在水位")
        self.assertEqual(outcomes["R331-02"]["结果"], "重复")
        self.assertEqual(outcomes["R331-03"]["判断"], "晚于水位")
        self.assertEqual(outcomes["R331-03"]["结果"], "入账")
        self.assertEqual(result["entry"]["确认水位"], "2026-09-03 12:50")
        self.assertGreater(result["entry"]["确认水位"], WATERMARK)

    def test_late_record_conflict_goes_to_audit_queue(self) -> None:
        self.scan(3, "2026-09-03 12:15", "2026-09-03 12:55")
        audits = client.get("/api/delta/audit?status=待处理").json()["items"]
        kinds = {(row["类型"], row["失败窗口"]) for row in audits}
        self.assertIn(("水位冲突", "2026-09-03 12:15~2026-09-03 12:55"), kinds)
        conflict = next(row for row in audits if row["类型"] == "水位冲突")
        self.assertEqual(conflict["扫描位置"], "2026-09-03 12:55")

    def test_interrupted_scan_registers_failed_window(self) -> None:
        result = self.scan(
            3,
            "2026-09-03 12:55",
            "2026-09-03 13:10",
            中断=True,
        )
        batch = result["batch"]
        self.assertEqual(batch["终态"], "失败")
        self.assertEqual(batch["扫描位置"], "2026-09-03 13:10")
        self.assertEqual(result["entry"]["确认水位"], WATERMARK)
        audits = client.get("/api/delta/audit?status=待处理").json()["items"]
        failed = [row for row in audits if row["类型"] == "失败窗口"]
        self.assertIn("2026-09-03 12:55~2026-09-03 13:10", [row["失败窗口"] for row in failed])

        detail = client.get(f"/api/delta/batches/{batch['id']}").json()
        self.assertEqual(detail["扫描位置"], "2026-09-03 13:10")

    def test_retry_failed_window_keeps_watermark_and_batch(self) -> None:
        interrupted = self.scan(3, "2026-09-03 12:10", "2026-09-03 12:30", 中断=True)
        batch_id = interrupted["batch"]["id"]

        retried = client.post(f"/api/delta/batches/{batch_id}/retry").json()
        self.assertTrue(retried["ok"])
        self.assertEqual(retried["batch"]["id"], batch_id)
        self.assertEqual(retried["entry"]["确认水位"], WATERMARK)
        self.assertEqual(retried["entry"]["扫描位置"], "2026-09-03 12:30")
        outcomes = {item["记录编号"]: item["结果"] for item in retried["batch"]["处理结果"]}
        self.assertEqual(outcomes["R331-01"], "冲突隔离")
        keys = [
            item["窗口唯一键"]
            for item in client.get("/api/delta/batches?size=100").json()["items"]
            if item["游标编号"] == "DELTA-331"
        ]
        self.assertEqual(len(keys), len(set(keys)))

    def test_retry_seed_failed_window_advances_watermark_only_forward(self) -> None:
        before = self.entry(2)["确认水位"]
        retried = client.post("/api/delta/batches/1/retry").json()
        self.assertTrue(retried["ok"])
        self.assertGreater(retried["entry"]["确认水位"], before)
        self.assertLessEqual(retried["entry"]["确认水位"], "2026-09-03 10:05")
        self.assertEqual(
            client.get("/api/delta/audit?status=待处理").json()["total"], 0
        )

    def test_only_affected_window_changes(self) -> None:
        first = self.scan(3, "2026-09-03 12:40", "2026-09-03 12:55")
        second = self.scan(3, "2026-09-03 12:55", "2026-09-03 13:10")
        untouched = client.get(f"/api/delta/batches/{first['batch']['id']}").json()
        self.assertEqual(untouched["窗口唯一键"], first["batch"]["窗口唯一键"])
        self.assertEqual(untouched["记录数"], first["batch"]["记录数"])
        self.assertNotEqual(second["batch"]["窗口唯一键"], first["batch"]["窗口唯一键"])
        self.assertEqual(second["entry"]["确认水位"], "2026-09-03 12:50")

    def test_downstream_never_sees_regression_or_duplicates(self) -> None:
        self.scan(3, "2026-09-03 12:15", "2026-09-03 12:55")
        client.post("/api/delta/3/rewind")
        self.scan(3, "2026-09-03 12:15", "2026-09-03 12:55")

        snapshot = client.get("/api/delta/3/downstream").json()
        self.assertEqual(snapshot["确认水位"], "2026-09-03 12:50")
        records = snapshot["记录"]
        keys = [item["记录编号"] for item in records]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertNotIn("R331-01", keys)
        self.assertNotIn("R331-02", keys)

    def test_pause_and_resume_keep_watermark(self) -> None:
        paused = client.post("/api/delta/2/actions", json={"values": {"action": "暂停增量"}}).json()
        self.assertEqual(paused["entry"]["status"], "已暂停")
        watermark = paused["entry"]["确认水位"]
        resumed = client.post("/api/delta/2/actions", json={"values": {"action": "启动增量"}}).json()
        self.assertEqual(resumed["entry"]["status"], "运行中")
        self.assertEqual(resumed["entry"]["确认水位"], watermark)
        self.assertEqual(resumed["entry"]["扫描位置"], "2026-09-03 09:45")

    def test_scan_requires_running_cursor(self) -> None:
        response = client.post(
            "/api/delta/1/actions", json={"values": {"action": "暂停增量"}}
        ).json()
        self.assertTrue(response["ok"])
        blocked = client.post("/api/delta/1/scan", json={"values": {}}).json()
        self.assertFalse(blocked["ok"])
        self.assertIn("启动增量", blocked["message"])


if __name__ == "__main__":
    unittest.main()
