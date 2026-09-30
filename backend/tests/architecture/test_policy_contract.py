"""K. 정책 저장소 계약 — 레지스트리 키 == DB CHECK 키, 시드 금지, 끔 스위치 금지, 리터럴 키 실재 (ADR-0065)."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from sqlalchemy import text

from app.core.db.session import owner_engine
from app.modules.policies.registry import POLICY_REGISTRY

pytestmark = pytest.mark.group_k

APP = Path(__file__).resolve().parents[2] / "app"


def _check_keys() -> set[str]:
    with owner_engine.connect() as conn:
        definition = conn.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
                " WHERE conname = 'ck_policy_settings_policy_key_closed'"
            )
        ).scalar_one()
    return set(re.findall(r"'([a-z][a-z0-9_]*)'", definition))


def test_registry_and_check_constraint_keys_are_identical() -> None:
    """레지스트리 키 집합과 DB CHECK 키 집합이 1:1이다(공회전 방지: 비어 있지 않다)"""
    assert POLICY_REGISTRY
    assert _check_keys() == set(POLICY_REGISTRY)


def test_no_credit_off_switch_is_registered() -> None:
    """여신은 항상 켜진 승인 게이트다 — 끄는 정책 키를 등재할 수 없다(§2 우회 통로 금지)"""
    forbidden = re.compile(r"credit.*(enabled|off|skip|disable|bypass)", re.IGNORECASE)
    assert [k for k in POLICY_REGISTRY if forbidden.search(k)] == []


def test_registry_defaults_are_strict_values_within_their_own_range() -> None:
    """미설정 기본값이 각 키의 허용 값 안에 있고(자기모순 금지), 모드의 기본은 가장 엄격한 BLOCK이다"""
    for key, spec in POLICY_REGISTRY.items():
        if spec.kind == "MODE":
            assert spec.unset_value in spec.allowed, key
        else:
            assert spec.minimum <= int(spec.unset_value) <= spec.maximum, key
    assert POLICY_REGISTRY["pi_advance_gate_mode"].unset_value == "BLOCK"
    assert POLICY_REGISTRY["price_deviation_tolerance_bp"].unset_value == 0


def _literal_policy_keys(source: str) -> set[str]:
    """소스에서 get_policy(…, "키") 리터럴 키를 모은다(모듈 상수 참조도 해석)"""
    tree = ast.parse(source)
    constants = {
        target.id: node.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    seen: set[str] = set()
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and getattr(node.func, "id", getattr(node.func, "attr", "")) == "get_policy"
            and len(node.args) >= 2
        ):
            continue
        arg = node.args[1]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            seen.add(arg.value)
        elif isinstance(arg, ast.Name) and arg.id in constants:
            seen.add(constants[arg.id])
    return seen


def test_the_literal_key_scan_is_not_idle() -> None:
    """공회전 방지 — 스캔이 리터럴·상수 참조 호출을 실제로 알아본다(소비 코드는 PR-9+에서 처음 등장)"""
    sample = 'K = "typo_key"\nget_policy(s, "a_key")\nsvc.get_policy(s, K)\nget_policy(s, var)\n'
    assert _literal_policy_keys(sample) == {"a_key", "typo_key"}


def test_every_get_policy_literal_key_is_registered() -> None:
    """코드가 get_policy(…, "키")로 읽는 리터럴 키는 전부 레지스트리에 있다(오타가 조용히 404 되지 않게)"""
    seen: set[str] = set()
    for path in APP.rglob("*.py"):
        seen |= _literal_policy_keys(path.read_text(encoding="utf-8"))
    assert seen <= set(POLICY_REGISTRY), (
        f"등록되지 않은 정책 키: {sorted(seen - set(POLICY_REGISTRY))}"
    )


def test_value_shape_check_matches_the_registry() -> None:
    """policy_value_shape CHECK가 레지스트리의 허용 값·범위와 같다(어긋나면 저장 시 500)"""
    with owner_engine.connect() as conn:
        definition = conn.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
                " WHERE conname = 'ck_policy_settings_policy_value_shape'"
            )
        ).scalar_one()
    for key, spec in POLICY_REGISTRY.items():
        assert f"'{key}'" in definition, key
        if spec.kind == "MODE":
            for mode in spec.allowed:
                assert f"'{mode}'" in definition, (key, mode)
        else:
            assert str(spec.minimum) in definition and str(spec.maximum) in definition, key
    modes = set(re.findall(r"'([A-Z_]+)'", definition))
    allowed = {m for spec in POLICY_REGISTRY.values() for m in spec.allowed}
    assert modes == allowed, "CHECK에만 있는 모드 값"
