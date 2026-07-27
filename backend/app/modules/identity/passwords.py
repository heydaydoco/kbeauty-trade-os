"""비밀번호 해시 (DESIGN.md §18.1 "비밀번호 argon2/bcrypt").

argon2-cffi의 기본값(argon2id)을 그대로 쓴다. 파라미터를 직접 튜닝하지 않는
이유는, 라이브러리 기본값이 시대에 맞춰 갱신되는데 우리가 박아 둔 숫자는
갱신되지 않기 때문이다. 대신 `needs_rehash()`로 기준이 올라갔을 때 로그인
시점에 조용히 재해시할 수 있는 통로를 열어 둔다.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()

#: 존재하지 않는 계정으로 로그인을 시도했을 때 검증하는 더미 해시.
#:
#: ★ 없으면 응답 시간이 계정 존재 여부를 알려 준다 — 없는 이메일은 해시 검증을
#:   건너뛰어 즉시 401이 오고, 있는 이메일은 argon2 계산(수십 ms) 뒤에 온다.
#:   그 차이만으로 유효한 계정 목록을 만들 수 있다(§18.1 사용자 열거 차단).
DUMMY_HASH = _hasher.hash("kbos-timing-equalizer")


def hash_password(raw: str) -> str:
    return _hasher.hash(raw)


def verify_password(hashed: str, raw: str) -> bool:
    """맞으면 True. 예외를 밖으로 흘리지 않는다 — 호출부의 분기가 단순해야 한다."""
    try:
        return _hasher.verify(hashed, raw)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(hashed: str) -> bool:
    """라이브러리 기본 파라미터가 올라가 재해시가 필요한지."""
    try:
        return _hasher.check_needs_rehash(hashed)
    except InvalidHashError:
        return True
