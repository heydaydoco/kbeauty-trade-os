"""F. 오더 인테이크 CSV 입구 — 실상한 대형 파일 실측(PO 200 × 라인 200 = 40,000라인) (S3-1 PR-14a 검토 A2①).

상시 스위트에 넣기엔 무거워(수십 초) **환경 변수 `KBOS_HEAVY=1`일 때만** 돈다. 측정값(시간·질의 수)은 PROGRESS 14a에 기록한다 —
상한 값(`MAX_GROUPS`·`MAX_GROUP_LINES`)은 바꾸지 않는다. 실행: `KBOS_HEAVY=1 pytest tests/e2e/test_order_intake_csv_heavy.py -s`.
"""

from __future__ import annotations

import io
import os
import time
from typing import Any

import pytest
from sqlalchemy import event

from app.core.db.session import engine
from app.modules.order_intake import csv_import, csv_template
from tests.factories.intake import csv_bytes, csv_row, scalar, trade_actor, world

pytestmark = [
    pytest.mark.group_f,
    pytest.mark.skipif(os.environ.get("KBOS_HEAVY") != "1", reason="KBOS_HEAVY=1일 때만(실측용)"),
]


def test_the_real_maximum_file_lands_and_reports_time_and_query_count() -> None:
    """PO 200 × 라인 200(40,000라인, 실상한) 파일 1개 — 한 트랜잭션으로 전부 착지하고 소요 시간·질의 수를 출력한다"""
    assert (csv_template.MAX_GROUPS, csv_template.MAX_GROUP_LINES) == (200, 200)
    w = world(lines=1)
    rows = [
        csv_row(w, po=f"PO-{g:03d}", code=f"C-{line:03d}")
        for g in range(csv_template.MAX_GROUPS)
        for line in range(csv_template.MAX_GROUP_LINES)
    ]
    content = csv_bytes(rows)
    queries = {"n": 0}

    def count(*_: Any) -> None:
        queries["n"] += 1

    event.listen(engine, "before_cursor_execute", count)
    started = time.perf_counter()
    try:
        status, body = csv_import.import_csv(
            actor=trade_actor(),
            idempotency_key="heavy",
            stream=io.BytesIO(content),
            filename="max.csv",
        )
    finally:
        elapsed = time.perf_counter() - started
        event.remove(engine, "before_cursor_execute", count)
    print(
        f"\n[PR-14a 실측] 파일 {len(content) / 1024 / 1024:.1f}MiB · 40,000라인 · {elapsed:.1f}s · 질의 {queries['n']}건"
    )
    assert status == 201 and body["group_count"] == 200 and body["line_count"] == 40_000
    assert scalar("SELECT count(*) FROM order_intake_lines") == 40_000
