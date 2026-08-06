"""애플리케이션 설정 — 임포트 시점에 검증하고, 부족하면 기동을 거부한다.

설계 근거: DESIGN.md §18.1(보안)·§18.2(환경 분리)·§18.4(에러 메시지 이원화)

이 모듈은 임포트되는 순간 `settings`를 만든다. 값이 잘못되면 서버가 포트를
열기 전에 죽는다 — 반쯤 뜬 상태로 요청을 받다가 첫 DB 접근에서 터지는 것보다
낫다("설정 누락"은 기동 실패로 드러나야 한다).
"""

from __future__ import annotations

from typing import Literal

from pydantic import SecretStr, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 개발용 예시 시크릿에 박아 두는 마커. 이 문자열이 든 값으로 운영 기동을 시도하면
# 거부한다 — .env.example이 그대로 운영에 복사되는 최빈 사고를 막는다.
DEV_SECRET_MARKER = "DEV_ONLY_DO_NOT_USE_IN_PROD"

# 런타임이 이 계정으로 DB에 붙으면 §17.5의 불변 강제가 통째로 무력화된다
# (소유자는 자기 권한을 되찾을 수 있다). 연결도 되고 기능도 다 도는 조용한 실패라
# 설정 단계에서 잡는다.
MIGRATION_ROLE_NAME = "kbos_owner"

SECRET_KEY_MIN_LENGTH = 32


class ConfigurationError(RuntimeError):
    """설정이 잘못되어 애플리케이션을 시작할 수 없다."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        case_sensitive=False,
        extra="ignore",
        # .env 파일을 읽지 않는다 — 값 주입 경로를 compose/CI의 환경변수 하나로
        # 고정해야 "어느 값이 실제로 먹었는지"를 추적할 수 있다.
        env_file=None,
    )

    # ── 실행 환경 ────────────────────────────────────────────────────────
    app_env: Literal["dev", "test", "prod"]
    app_name: str = "kbeauty-trade-os"
    app_version: str = "0.1.0"

    # ── 로깅 ─────────────────────────────────────────────────────────────
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    # 300ms를 넘는 쿼리를 느린 쿼리로 기록한다(§18.4).
    slow_query_ms: int = 300
    # 신뢰할 수 있는 프록시 뒤에 있을 때만 켠다. 켜면 외부가 보낸
    # X-Request-ID를 그대로 이어받는다(로그 위조 가능 → 기본 꺼짐).
    trust_incoming_request_id: bool = False

    # ── 보안 ─────────────────────────────────────────────────────────────
    secret_key: SecretStr

    # ── 파일 저장소 (§4.7 documents / §18.1 / ADR-0029) ──────────────────
    # 업로드 실물의 저장 루트. **정적 서빙 경로 밖**이어야 한다 — 접근은 권한
    # 검증 다운로드 엔드포인트 하나뿐이다. 컨테이너에서는 compose가 named
    # volume(/data/files)을 주입하고, 기본값은 CI·컨테이너 밖 pytest용 폴백이다.
    # S2-4 백업이 이 루트를 pg_dump와 세트로 통째 백업한다(§2 "files/ 세트").
    file_storage_root: str = "var/files"

    # ── DB 접속 (역할별로 분리 — ADR-0002) ───────────────────────────────
    # 런타임(kbos_app): 테이블을 만들 수 없다.
    database_url: SecretStr
    # 마이그레이션(kbos_owner): alembic 전용.
    migration_database_url: SecretStr

    # ── 테스트 전용 (dev 컨테이너에서 pytest를 돌릴 때만 쓰인다) ─────────
    test_database_url: SecretStr | None = None
    test_migration_database_url: SecretStr | None = None
    # 마이그레이션 왕복 검사(upgrade→downgrade→upgrade) 전용 빈 DB.
    migration_check_database_url: SecretStr | None = None

    # ── 검증 ─────────────────────────────────────────────────────────────

    @model_validator(mode="after")
    def _check_secret_key_length(self) -> Settings:
        if len(self.secret_key.get_secret_value()) < SECRET_KEY_MIN_LENGTH:
            raise ValueError(
                f"SECRET_KEY가 너무 짧습니다(최소 {SECRET_KEY_MIN_LENGTH}자). "
                ".env의 SECRET_KEY를 긴 임의 문자열로 바꾸세요."
            )
        return self

    @model_validator(mode="after")
    def _check_runtime_is_not_migration_role(self) -> Settings:
        if MIGRATION_ROLE_NAME in self.database_url.get_secret_value():
            raise ValueError(
                f"DATABASE_URL이 마이그레이션 계정({MIGRATION_ROLE_NAME})을 가리킵니다. "
                "런타임은 kbos_app으로 접속해야 원장·감사로그 불변이 DB 권한으로 강제됩니다"
                "(DESIGN.md §17.5). .env의 DATABASE_URL을 kbos_app으로 바꾸세요."
            )
        return self

    @model_validator(mode="after")
    def _check_prod_hardening(self) -> Settings:
        if self.app_env != "prod":
            return self

        leaked = [
            name
            for name, value in (
                ("SECRET_KEY", self.secret_key),
                ("DATABASE_URL", self.database_url),
                ("MIGRATION_DATABASE_URL", self.migration_database_url),
            )
            if DEV_SECRET_MARKER in value.get_secret_value()
        ]
        if leaked:
            raise ValueError(
                f"운영 환경에 개발용 예시 값이 들어 있습니다: {', '.join(leaked)}. "
                ".env.prod의 해당 값을 실제 운영 값으로 채우세요."
            )

        if self.log_level == "DEBUG":
            raise ValueError(
                "운영 환경에서는 LOG_LEVEL=DEBUG를 쓸 수 없습니다"
                "(민감정보 노출·로그 폭증). LOG_LEVEL을 INFO 이상으로 바꾸세요."
            )

        if any(
            (
                self.test_database_url,
                self.test_migration_database_url,
                self.migration_check_database_url,
            )
        ):
            raise ValueError(
                "운영 환경에 테스트용 DB 접속정보(TEST_*/MIGRATION_CHECK_*)가 설정돼 있습니다. "
                ".env.prod에서 해당 항목을 지우세요."
            )

        return self

    # ── 파생값 ───────────────────────────────────────────────────────────

    @property
    def is_prod(self) -> bool:
        return self.app_env == "prod"

    @property
    def is_dev(self) -> bool:
        return self.app_env == "dev"


def _describe_validation_error(exc: ValidationError) -> str:
    """pydantic의 영문 오류를 사용자(=운영자)가 읽을 한국어 안내로 바꾼다.

    §18.4 "에러 메시지 이원화 — 사용자=한국어+원인+조치". 원문 예외는 호출부에서
    `raise ... from exc`로 이어지므로 로그에는 상세가 그대로 남는다.
    """
    lines = ["설정이 올바르지 않아 애플리케이션을 시작할 수 없습니다.", ""]
    for err in exc.errors():
        # 모델 전체를 보는 검증(@model_validator)은 loc이 비어 있다.
        # 그 경우 메시지 본문이 이미 어느 항목인지 밝히므로 접두어를 붙이지 않는다.
        key = ".".join(str(p) for p in err["loc"]).upper()
        kind = err["type"]
        if kind == "missing":
            reason = "값이 없습니다."
            action = f".env 파일에 {key}를 추가하세요."
        elif kind.startswith("literal_error"):
            reason = f"허용되지 않는 값입니다. ({err.get('msg', '')})"
            action = f"{key}를 허용된 값 중 하나로 바꾸세요."
        elif kind == "value_error":
            reason = str(err.get("msg", "")).removeprefix("Value error, ")
            action = ""
        else:
            reason = str(err.get("msg", ""))
            action = f".env의 {key} 값을 확인하세요."
        detail = f"{reason}{(' → ' + action) if action else ''}"
        lines.append(f"  - {key}: {detail}" if key else f"  - {detail}")
    lines += [
        "",
        ".env.example에 필요한 항목이 모두 있습니다.",
        "PowerShell에서:  Copy-Item .env.example .env",
    ]
    return "\n".join(lines)


def load_settings() -> Settings:
    try:
        return Settings()  # type: ignore[call-arg]  # 값은 환경변수에서 온다
    except ValidationError as exc:
        raise ConfigurationError(_describe_validation_error(exc)) from exc


# 임포트 시점 인스턴스화 — 잘못된 설정으로는 프로세스가 뜨지 않는다.
settings = load_settings()
