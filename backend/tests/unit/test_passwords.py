"""K. 보안·품질 — 비밀번호 해시 유틸 (§18.1 / ADR-0031 조건 10b).

커버리지 게이트 도입 실측에서 identity/passwords.py의 미커버 4문(38-41행)이
전부 needs_rehash()였다 — 라이브러리 기본 파라미터 상향 시 로그인 시점
재해시를 위해 열어 둔 통로인데 아직 호출처가 없어 어떤 테스트도 지나가지
않았다. 판정 조건 10b(보안 경로 미커버 → 동일 PR 케이스 추가)로 닫는다.
재해시 통로가 실제로 결선될 때(로그인 서비스) 이 케이스가 그 전제를 지킨다.
"""

from __future__ import annotations

import pytest

from app.modules.identity.passwords import hash_password, needs_rehash, verify_password

pytestmark = pytest.mark.group_k


def test_a_fresh_hash_does_not_need_rehash() -> None:
    """현행 기본 파라미터로 만든 해시는 재해시 대상이 아니다"""
    assert needs_rehash(hash_password("kbos-rehash-check-1234")) is False


def test_an_invalid_hash_needs_rehash() -> None:
    """형식이 깨진 해시는 재해시 대상이다 (fail-closed — 모르는 것은 갱신 쪽)"""
    assert needs_rehash("not-a-valid-argon2-hash") is True


def test_verify_round_trip_still_holds() -> None:
    """해시→검증 왕복 자기검사 — 위 두 케이스의 전제(해시가 유효하다) 고정"""
    hashed = hash_password("kbos-rehash-check-1234")
    assert verify_password(hashed, "kbos-rehash-check-1234") is True
    assert verify_password(hashed, "wrong-password") is False
