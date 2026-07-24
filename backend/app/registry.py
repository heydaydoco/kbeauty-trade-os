"""ORM 모델 등록소 — alembic autogenerate가 볼 수 있게 전부 여기서 임포트한다.

모델 모듈을 만들고 여기에 추가하지 않으면, autogenerate는 그 테이블을
"없어야 할 테이블"로 보고 DROP TABLE 마이그레이션을 만들어 낸다.
"""

from __future__ import annotations

from app.core.db.base import Base

# S0-2부터 여기에 모델 모듈 임포트를 추가한다. 예:
#   from app.modules.identity import models as identity_models  # noqa: F401

__all__ = ["Base"]
