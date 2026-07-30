"""ORM 모델 등록소 — alembic autogenerate가 볼 수 있게 전부 여기서 임포트한다.

모델 모듈을 만들고 여기에 추가하지 않으면, autogenerate는 그 테이블을
"없어야 할 테이블"로 보고 DROP TABLE 마이그레이션을 만들어 낸다.
(누락은 tests/architecture/test_conventions.py가 잡는다)
"""

from __future__ import annotations

from app.core.db.base import Base
from app.modules.audit import models as audit_models
from app.modules.catalog import models as catalog_models
from app.modules.idempotency import models as idempotency_models
from app.modules.identity import models as identity_models
from app.modules.ingredients import models as ingredients_models
from app.modules.numbering import models as numbering_models
from app.modules.outbox import models as outbox_models
from app.modules.platform import models as platform_models
from app.modules.worklist import models as worklist_models

__all__ = [
    "Base",
    "audit_models",
    "catalog_models",
    "idempotency_models",
    "identity_models",
    "ingredients_models",
    "numbering_models",
    "outbox_models",
    "platform_models",
    "worklist_models",
]
