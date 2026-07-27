"""${message}

리비전 ID: ${up_revision}
직전 리비전: ${down_revision or "(없음 - 최초 리비전)"}
생성 시각(UTC): ${create_date}

체크리스트 (autogenerate 결과는 초안이다 — 사람이 전건 확인한다):
  □ 컬럼 rename이 drop+add로 생성되지 않았는가 (실데이터가 소실된다)
  □ CHECK 제약·부분 인덱스·server_default·트리거·GRANT는 자동 감지되지 않는다 — 손으로 썼는가
  □ 멱등 UNIQUE는 unique_active() 부분 인덱스인가 (§17.4)
  □ downgrade()가 실제로 되돌리는가 (CI가 upgrade→downgrade→upgrade 왕복을 검사한다)
  □ 되돌릴 수 없다면 파일 상단에 `# irreversible: <사유>`를 적고 ADR을 남겼는가
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: str | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
