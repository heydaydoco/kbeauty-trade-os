"""K. 보안·품질 — 설정 fail-fast·prod 가드 (DESIGN.md §18.1).

★ Settings는 실제 os.environ을 읽는다. 테스트 컨테이너에는 이미 APP_ENV·
  SECRET_KEY·DATABASE_URL 등이 설정돼 있으므로, 각 테스트는 monkeypatch로
  관련 환경변수를 전부 지운 뒤 필요한 것만 다시 넣어 통제한다.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings

pytestmark = pytest.mark.group_k

#: Settings가 읽는 모든 환경변수 — 테스트 전에 초기화한다.
SETTINGS_ENV_VARS = [
    "APP_ENV",
    "APP_NAME",
    "APP_VERSION",
    "LOG_LEVEL",
    "SLOW_QUERY_MS",
    "TRUST_INCOMING_REQUEST_ID",
    "SECRET_KEY",
    "DATABASE_URL",
    "MIGRATION_DATABASE_URL",
    "TEST_DATABASE_URL",
    "TEST_MIGRATION_DATABASE_URL",
    "MIGRATION_CHECK_DATABASE_URL",
]

BASE_ENV = {
    "APP_ENV": "dev",
    "SECRET_KEY": "x" * 40,
    "DATABASE_URL": "postgresql+psycopg://kbos_app:pw12345678@db:5432/kbos_dev",
    "MIGRATION_DATABASE_URL": "postgresql+psycopg://kbos_owner:pw12345678@db:5432/kbos_dev",
}

PROD_ENV = {
    "APP_ENV": "prod",
    "SECRET_KEY": "realprodsecret_" + "a" * 30,
    "DATABASE_URL": "postgresql+psycopg://kbos_app:realpw123456@prod-db:5432/kbos",
    "MIGRATION_DATABASE_URL": "postgresql+psycopg://kbos_owner:realpw123456@prod-db:5432/kbos",
    "LOG_LEVEL": "INFO",
}


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for var in SETTINGS_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


def _load(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> Settings:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings()  # type: ignore[call-arg]


def test_valid_settings_load(clean_env: pytest.MonkeyPatch) -> None:
    """온전한 설정은 정상적으로 로드된다"""
    settings = _load(clean_env, BASE_ENV)
    assert settings.app_env == "dev"


def test_missing_secret_key_fails(clean_env: pytest.MonkeyPatch) -> None:
    """SECRET_KEY가 없으면 로드에 실패한다"""
    env = {k: v for k, v in BASE_ENV.items() if k != "SECRET_KEY"}
    with pytest.raises(Exception):  # noqa: B017 - ValidationError 계열
        _load(clean_env, env)


def test_short_secret_key_fails(clean_env: pytest.MonkeyPatch) -> None:
    """32자 미만 SECRET_KEY는 거부된다"""
    with pytest.raises(ValueError, match="너무 짧"):
        _load(clean_env, {**BASE_ENV, "SECRET_KEY": "short"})


def test_runtime_pointing_at_owner_role_fails(clean_env: pytest.MonkeyPatch) -> None:
    """런타임 DSN이 kbos_owner를 가리키면 거부된다 (§17.5 무력화 방지)"""
    with pytest.raises(ValueError, match="kbos_app"):
        _load(
            clean_env,
            {
                **BASE_ENV,
                "DATABASE_URL": "postgresql+psycopg://kbos_owner:pw12345678@db:5432/kbos_dev",
            },
        )


def test_prod_with_dev_marker_secret_fails(clean_env: pytest.MonkeyPatch) -> None:
    """운영에 개발용 예시 시크릿이 들어 있으면 거부된다"""
    with pytest.raises(ValueError, match="개발용 예시"):
        _load(clean_env, {**PROD_ENV, "SECRET_KEY": "DEV_ONLY_DO_NOT_USE_IN_PROD_" + "x" * 20})


def test_prod_with_debug_log_fails(clean_env: pytest.MonkeyPatch) -> None:
    """운영 + LOG_LEVEL=DEBUG는 거부된다"""
    with pytest.raises(ValueError, match="DEBUG"):
        _load(clean_env, {**PROD_ENV, "LOG_LEVEL": "DEBUG"})


def test_prod_with_leftover_test_dsn_fails(clean_env: pytest.MonkeyPatch) -> None:
    """운영에 테스트용 DB 접속정보가 남아 있으면 거부된다"""
    with pytest.raises(ValueError, match="테스트"):
        _load(
            clean_env,
            {**PROD_ENV, "TEST_DATABASE_URL": "postgresql+psycopg://kbos_app:x@db:5432/kbos_test"},
        )


def test_prod_valid_config_loads(clean_env: pytest.MonkeyPatch) -> None:
    """운영용 정상 설정은 로드된다 (가드가 정상 값까지 막지 않는다)"""
    settings = _load(clean_env, PROD_ENV)
    assert settings.is_prod


def test_configuration_error_message_is_korean(clean_env: pytest.MonkeyPatch) -> None:
    """load_settings의 실패 안내는 한국어로 조치를 담는다"""
    from pydantic import ValidationError

    from app.core.config import _describe_validation_error

    clean_env.setenv("APP_ENV", "dev")  # SECRET_KEY 등은 비운 상태
    try:
        Settings()  # type: ignore[call-arg]
    except ValidationError as exc:
        message = _describe_validation_error(exc)
        assert "Copy-Item" in message
        assert "SECRET_KEY" in message.upper()
    else:
        pytest.fail("필수 값이 빠졌는데 예외가 나지 않았다")
