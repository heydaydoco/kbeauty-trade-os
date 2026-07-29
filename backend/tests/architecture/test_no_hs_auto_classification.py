"""K. 보안·품질 — HS 자동판정 기능의 **부재**를 지킨다 (DESIGN.md §1 비범위 / WBS S1-1 DoD).

WBS S1-1의 DoD 한 줄은 "HS 자동판정 기능이 **없어야** 함"이다. 없는 기능은
사람이 지킬 수 없다 — 다음 세션이 "이 정도는 편의 기능"이라며 추천을 붙이고,
그 추천은 곧 사람이 확인하지 않은 세번이 되어 통관에서 터진다.

§1이 비범위로 못박은 것은 "관세율·요건·원산지의 자동 **판정**"이고, 허용한
것은 "계산·정리 지원"이다. 그 경계를 기계로 고정한다.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from sqlalchemy import BigInteger, Integer, Numeric, Text

from app.main import app
from app.modules.catalog.models import SkuHsCode

pytestmark = [pytest.mark.group_k, pytest.mark.meta]

APP_ROOT = Path(__file__).resolve().parents[2] / "app"

#: "판정·추천"을 뜻하는 이름 조각. 기록·조회 동사(create/list/get)는 여기 없다.
_JUDGEMENT_WORDS = (
    "classify",
    "classification",
    "predict",
    "suggest",
    "recommend",
    "infer",
    "determine",
    "auto",
)


def _mentions_hs(name: str) -> bool:
    lowered = name.lower()
    return "hs_" in lowered or lowered.startswith("hs") or lowered.endswith("hs")


def _offending_functions(tree: ast.AST, label: str) -> list[str]:
    return [
        f"{label}:{node.lineno} ({node.name})"
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and _mentions_hs(node.name)
        and any(word in node.name.lower() for word in _JUDGEMENT_WORDS)
    ]


def test_detector_catches_a_planted_classifier() -> None:
    """검사기가 실제로 구멍을 잡는다 (자기검사)

    ★ 이 검사가 없으면, 스캔이 조용히 고장 났을 때(예: 파일을 하나도 못 찾을 때)
      아래 테스트가 영원히 초록이 된다. 그건 규칙이 없는 것보다 나쁘다.
    """
    planted = ast.parse("def suggest_hs_code(sku):\n    return '3304990000'\n")

    assert _offending_functions(planted, "planted.py") == ["planted.py:1 (suggest_hs_code)"]


def test_no_hs_classification_function_exists() -> None:
    """app 어디에도 HS를 판정·추천하는 함수가 없다 (§1 비범위)"""
    files = sorted(APP_ROOT.rglob("*.py"))
    assert len(files) > 10, "스캔 경로가 잘못됐습니다 — 빈 목록으로 초록을 사지 않는다"

    offenders: list[str] = []
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        offenders.extend(_offending_functions(tree, str(path.relative_to(APP_ROOT.parent))))

    assert not offenders, (
        "HS를 판정·추천하는 함수: "
        + ", ".join(offenders)
        + " — §1이 비범위로 못박은 자동 판정입니다. 사람이 확인한 값을 기록만 하세요."
    )


def test_no_hs_classification_endpoint_is_exposed() -> None:
    """HS 판정·추천 엔드포인트가 노출되지 않는다"""
    paths = list(app.openapi()["paths"])
    assert any("hs-codes" in path for path in paths), (
        "HS 경로를 하나도 찾지 못했습니다 — 스캔이 공회전입니다"
    )

    offenders = [
        path
        for path in paths
        if "hs" in path.lower() and any(word in path.lower() for word in _JUDGEMENT_WORDS)
    ]
    assert not offenders, f"HS 판정·추천으로 보이는 경로: {offenders}"


def test_tariff_is_a_note_not_a_number() -> None:
    """세율은 숫자가 아니라 메모다 (§1 — 숫자가 되는 순간 계산에 쓰이기 시작한다)

    §4.1이 "세율 메모+근거링크"라고 쓴 그대로다. 숫자 컬럼으로 두면 화면 어딘가가
    그 값을 곱하기 시작하고, 그때부터 시스템이 관세를 "판정"하게 된다. 실제 세액은
    §10.1 비용 원장이 근거를 갖고 계산한다.
    """
    columns = SkuHsCode.__table__.columns
    assert isinstance(columns["tariff_note"].type, Text)

    #: 키·버전 컬럼을 뺀 나머지에 숫자 타입이 있으면 안 된다.
    allowed_numeric = {"id", "sku_id", "version", "created_by_id", "updated_by_id"}
    numeric = [
        column.name
        for column in columns
        if column.name not in allowed_numeric
        and isinstance(column.type, Numeric | Integer | BigInteger)
    ]
    assert not numeric, f"HS 테이블의 숫자 컬럼: {numeric} — 세율은 메모여야 합니다"
