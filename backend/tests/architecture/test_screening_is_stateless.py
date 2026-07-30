"""K. 보안·품질 — 스크리닝 결과 영속화의 **부재**를 기계로 지킨다 (웹 세션 판정 D-③).

§4.3의 스크리닝은 계산값이고 저장하지 않는다(§5.3 매트릭스와 같은 원칙).
"없어야 하는 것"은 사람이 지킬 수 없다 — 다음 세션이 캐시·이력 테이블을
편의로 추가하는 순간 "그때 뭐라고 나왔었나"가 판정 기록처럼 읽히기 시작한다.
HS 자동판정 부재 검사(test_no_hs_auto_classification)와 같은 지위다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.registry import Base

pytestmark = pytest.mark.group_k

SCREENING_SOURCE = (
    Path(__file__).resolve().parents[2] / "app" / "modules" / "ingredients" / "screening.py"
)

#: 쓰기의 흔적들. 스크리닝 모듈에 하나라도 나타나면 "미저장" 계약이 깨진 것이다.
WRITE_MARKERS = (
    "session.add",
    "session.delete",
    "session.merge",
    "bulk_save",
    "insert(",
    "update(",
    "delete(",
    "outbox",
    "idempotency",
)


def test_screening_module_never_writes() -> None:
    """스크리닝 모듈에는 쓰기 경로가 없다 — SELECT만 있다"""
    source = SCREENING_SOURCE.read_text(encoding="utf-8")
    # 자기검사: 엉뚱한 파일을 읽고 통과하는 일이 없게, 있어야 할 표식부터 본다.
    assert "select(" in source, "스크리닝 모듈이 맞는지부터 의심하라 — select가 없다"
    assert "def screen_product" in source

    violations = [marker for marker in WRITE_MARKERS if marker in source]
    assert violations == [], (
        f"스크리닝 모듈에서 쓰기 흔적 발견: {violations}. "
        "스크리닝은 저장하지 않는다(§4.3·§5.3 원칙, 웹 세션 판정 D) — "
        "결과를 남기고 싶다면 웹 세션 판정을 먼저 받아라."
    )


def test_no_screening_table_exists() -> None:
    """스크리닝 이름이 붙은 테이블이 스키마에 없다 — 리포트는 데이터가 아니다"""
    suspicious = [
        name for name in Base.metadata.tables if "screening" in name or "스크리닝" in name
    ]
    assert suspicious == [], (
        f"스크리닝 결과를 담는 것으로 보이는 테이블 발견: {suspicious}. "
        "계산값은 저장하지 않는다(§5.3과 같은 원칙)."
    )
