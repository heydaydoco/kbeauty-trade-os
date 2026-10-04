"""s32_milestones_customs

리비전 ID: 281da4794717
직전 리비전: acd34f28c11e
생성 시각(UTC): 2026-10-04 17:53:15.928657+00:00

S3-2 PR-4a 마일스톤·롤오버·통보·통관 스키마 — M15 (ADR-0074·0080·0083·0085 / design-integrated §2.1 (e)~(i)·§2.2·§2.12·§9 R-01·R-16·R-18·R-26):
  ① item_profile_milestone_types — 품목군 마일스톤 세트(선적 저장형 8종만 CHECK, (품목군, 종류) 부분 유니크 — 이름은 63자 상한 때문에
     `…_profile_type_active`로 직접 지음). 쓰기 경로는 PR-4c(ADMIN 전용) — 표는 여기서 먼저 선다(계획서 PR-4c 행 "표는 M15에 이미 있음").
  ② customs_records — 통관 기록(R-16: M14 → M15 이동). 선적:통관 = 1:N, (신고 구분, 신고번호) 부분 유니크, **수리일 = 유일 원천**(X-02).
     CHECK kind_valid·accept_after_declare·declaration_no_shape(ASCII `^[A-Z0-9][A-Z0-9/-]*$`)·note_clean(보이는 글자 1개 이상·탭·LF·CR만
     허용)·date_range(2000~2999). 세율·HS 열 없음.
  ③ milestones — 소유자(선적 또는 OEM PO 정확히 하나) × 저장형 종류 1행, 계획/실적 이중값(날짜형 DATE / 시각형 TIMESTAMPTZ+tz).
     CHECK one_owner·type_valid(**파생 3종 거부**)·owner_type_scope(OEM 4종 ⇔ po_id)·date_shape·datetime_shape·tz_iff_instant·tz_format·
     value_range(날짜 2000~2999·시각 [2000-01-01Z, 3000-01-01Z))·**customs_actual_from_records**(신고수리 실적 열 NULL 강제 — X-02).
     부분 유니크 (shipment_id, 종류)·(po_id, 종류).
  ④ milestone_changes — 변경 이력(**IMMUTABLE** — revoke_mutations). CHECK change_kind_valid·reason_required(PLAN_CHANGED·ACTUAL_CORRECTED)·
     reason_clean(1~500자·보이는 글자 1개 이상·제어문자 0)·value_pairs·kind_values·changed(무변경 이력 금지).
  ⑤ milestone_change_notices — 통보 연결(**IMMUTABLE**), UNIQUE (change_id, comm_log_id).
  ⑥ comm_logs 주제 CHECK 재정의 — `subject_type IN ('CERTIFICATION', 'SHIPMENT')`(기존 표 변경, 수기 drop→create — 함정 ①·⑪).
  ⑦ comm_logs 요지 CHECK 재정의(보이는 글자 1개 이상 — 범용 주제는 유니코드 공백, SHIPMENT는 서식·채움 글자까지 빈 글자) +
     선적 통보 오간 날 범위 CHECK 신설(SHIPMENT만 2000~2999). 적대 검토 반영 ⑤·⑥(미병합 리비전이라 같은 파일 수정).

체크리스트:
  ■ 신규 5표 + 기존 표 CHECK 재정의 2건·신설 1건(comm_logs — 신설은 SHIPMENT 행만 대상이라 기존 데이터 무접촉). shipments·SO·PO 스키마 무변경.
  ■ CHECK는 create_table 안에 op.f() 최종 이름(alembic check가 CHECK 정의를 못 보므로 정의문 시험
    tests/integration/test_milestone_constraints.py가 pg_get_constraintdef로 고정). 식별자 63자 이내(시험이 실측).
  ■ 멱등·중복 UNIQUE는 부분 인덱스(WHERE deleted_at IS NULL — §17.4). IMMUTABLE 2표는 soft delete 열이 없어 일반 UNIQUE.
  ■ **시드 0** — 전부 사람이 화면·API로 만드는 업무 기록이다(함정 ⑩).
  ■ milestone_changes·milestone_change_notices는 REVOKE UPDATE, DELETE, TRUNCATE를 손으로 부른다(table_policy.IMMUTABLE_TABLES와 짝).
  ■ **downgrade 가드(M14 선례)**: 신규 5표에 행이 1건이라도 있거나(soft delete 포함) SHIPMENT 주제 통신 기록이 있으면 DDL 전에 RuntimeError —
    통관·일정·롤오버 이력·통보 기록을 조용히 지우거나, comm_logs 주제 CHECK 원복이 SHIPMENT 행 때문에 반쯤 실패하는 길을 막는다.
    되돌리려면 데이터 처리 방침(보존 반출 등)을 먼저 정한다.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.db.table_policy import revoke_mutations

revision: str = "281da4794717"
down_revision: str | None = "acd34f28c11e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COMM_SUBJECT_CHECK = "ck_comm_logs_subject_type_valid"
_COMM_SUBJECT_OLD = "subject_type IN ('CERTIFICATION')"
_COMM_SUBJECT_NEW = "subject_type IN ('CERTIFICATION', 'SHIPMENT')"
# ── 적대 검토 반영(⑤·⑥) — 이 리비전은 미병합이라 같은 파일을 고쳤다 ─────────────────────────────────────────────
#: '보이는 글자 1개 이상' 판정 집합(PG ARE 괄호식 내용) — btrim은 U+0020만 잘라 U+3000·U+00A0·U+2003만의 값이 통과하던 구멍.
#: app/core/db/constraints.py의 SPACE_CHAR_CLASS·BLANK_CHAR_CLASS와 같은 문자열이다(마이그레이션은 앱 상수를 임포트하지 않는다 —
#: 이력 고정. 대사는 tests/integration/test_milestone_constraints.py).
_SPACE_POINTS: tuple[int | tuple[int, int], ...] = (
    0x0085,
    0x00A0,
    0x1680,
    (0x2000, 0x200A),
    0x2028,
    0x2029,
    0x202F,
    0x205F,
    0x3000,
)
_INVISIBLE_POINTS: tuple[int | tuple[int, int], ...] = (
    0x180E,
    (0x200B, 0x200D),
    0x2060,
    0xFEFF,
    0x115F,
    0x1160,
    0x3164,
    0xFFA0,
)


def _char_class(points: tuple[int | tuple[int, int], ...]) -> str:
    """PG ARE 괄호식 내용(`\\uXXXX`·`\\uXXXX-\\uYYYY`) — 정의문에 보이지 않는 글자를 직접 넣지 않는다."""
    return "".join(
        f"\\u{p[0]:04x}-\\u{p[1]:04x}" if isinstance(p, tuple) else f"\\u{p:04x}" for p in points
    )


_SPACE = "\\s" + _char_class(_SPACE_POINTS)
_BLANK = _SPACE + _char_class(_INVISIBLE_POINTS)
#: 업무 날짜 범위(2000~2999 — 휴일 연도 규약과 같다). 달력 끝 값의 파생 산술 OverflowError(응답 500)를 입구에서 막는다.
_DATE_RANGE = "({col} IS NULL OR {col} BETWEEN DATE '2000-01-01' AND DATE '2999-12-31')"
_INSTANT_RANGE = (
    "({col} IS NULL OR ({col} >= TIMESTAMPTZ '2000-01-01 00:00:00+00'"
    " AND {col} < TIMESTAMPTZ '3000-01-01 00:00:00+00'))"
)
_COMM_SUMMARY_CHECK = "ck_comm_logs_summary_not_blank"
_COMM_SUMMARY_OLD = "length(btrim(summary)) > 0"
#: 범용 주제는 유니코드 공백만 빈 글자로(기존 행 = 파이썬 strip 통과분이라 호환), 선적 통보는 서식·채움 글자까지 빈 글자로.
_COMM_SUMMARY_NEW = (
    f"summary ~ '[^{_SPACE}]' AND (subject_type <> 'SHIPMENT' OR summary ~ '[^{_BLANK}]')"
)
_COMM_OCCURRED_CHECK = "ck_comm_logs_shipment_occurred_on_range"
#: 선적 통보의 오간 날 = 업무 날짜 범위(범용 주제 행은 대상 밖 — 기존 데이터 무접촉).
_COMM_OCCURRED = (
    "subject_type <> 'SHIPMENT' OR occurred_on BETWEEN DATE '2000-01-01' AND DATE '2999-12-31'"
)
#: downgrade 가드가 세는 표(행이 있으면 손실 — soft delete 행 포함).
_GUARDED_TABLES = (
    "customs_records",
    "milestones",
    "milestone_changes",
    "milestone_change_notices",
    "item_profile_milestone_types",
)


def upgrade() -> None:
    op.create_table(
        "item_profile_milestone_types",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("profile_id", sa.BigInteger(), nullable=False),
        sa.Column("milestone_type", sa.String(length=24), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_id", sa.BigInteger(), nullable=True),
        sa.Column("updated_by_id", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(
            "milestone_type IN ('BL_ISSUED', 'CARGO_CLOSING', 'CUSTOMS_CLEARED', 'DOC_CUTOFF',"
            " 'ETA', 'ETD', 'IMPORT_TAX_DUE', 'PSI')",
            name=op.f("ck_item_profile_milestone_types_type_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name=op.f("fk_item_profile_milestone_types_created_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["item_profiles.id"],
            name=op.f("fk_item_profile_milestone_types_profile_id_item_profiles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"],
            ["users.id"],
            name=op.f("fk_item_profile_milestone_types_updated_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_item_profile_milestone_types")),
    )
    op.create_index(
        "uq_item_profile_milestone_types_profile_type_active",
        "item_profile_milestone_types",
        ["profile_id", "milestone_type"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_table(
        "customs_records",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("shipment_id", sa.BigInteger(), nullable=False),
        sa.Column("declaration_kind", sa.String(length=8), nullable=False),
        sa.Column("declaration_no", sa.String(length=40), nullable=False),
        sa.Column("declared_on", sa.Date(), nullable=False),
        sa.Column("accepted_on", sa.Date(), nullable=True),
        sa.Column("customs_broker_partner_id", sa.BigInteger(), nullable=True),
        sa.Column("note", sa.String(length=1000), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_by_id", sa.BigInteger(), nullable=True),
        sa.Column("updated_by_id", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(
            r"declaration_no ~ '^[A-Z0-9][A-Z0-9/-]*$'",
            name=op.f("ck_customs_records_declaration_no_shape"),
        ),
        sa.CheckConstraint(
            "declaration_kind IN ('EXPORT', 'IMPORT')", name=op.f("ck_customs_records_kind_valid")
        ),
        sa.CheckConstraint(
            f"note IS NULL OR (note ~ '[^{_BLANK}]'"
            " AND translate(note, chr(9) || chr(10) || chr(13), '') !~ '[[:cntrl:]]')",
            name=op.f("ck_customs_records_note_clean"),
        ),
        sa.CheckConstraint(
            _DATE_RANGE.format(col="declared_on") + " AND " + _DATE_RANGE.format(col="accepted_on"),
            name=op.f("ck_customs_records_date_range"),
        ),
        sa.CheckConstraint(
            "accepted_on IS NULL OR accepted_on >= declared_on",
            name=op.f("ck_customs_records_accept_after_declare"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name=op.f("fk_customs_records_created_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["customs_broker_partner_id"],
            ["partners.id"],
            name=op.f("fk_customs_records_customs_broker_partner_id_partners"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["shipment_id"],
            ["shipments.id"],
            name=op.f("fk_customs_records_shipment_id_shipments"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"],
            ["users.id"],
            name=op.f("fk_customs_records_updated_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_customs_records")),
    )
    op.create_index(
        "ix_customs_records_customs_broker_partner_id",
        "customs_records",
        ["customs_broker_partner_id"],
        unique=False,
    )
    op.create_index(
        "ix_customs_records_shipment_id_live",
        "customs_records",
        ["shipment_id"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "uq_customs_records_declaration_kind_declaration_no_active",
        "customs_records",
        ["declaration_kind", "declaration_no"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_table(
        "milestones",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("shipment_id", sa.BigInteger(), nullable=True),
        sa.Column("po_id", sa.BigInteger(), nullable=True),
        sa.Column("milestone_type", sa.String(length=24), nullable=False),
        sa.Column("planned_on", sa.Date(), nullable=True),
        sa.Column("actual_on", sa.Date(), nullable=True),
        sa.Column("planned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actual_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tz", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_by_id", sa.BigInteger(), nullable=True),
        sa.Column("updated_by_id", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(
            "(milestone_type IN ('FILLING', 'OUTGOING_INSPECTION', 'PACKING', 'RAW_MATERIAL_READY'))"
            " = (po_id IS NOT NULL)",
            name=op.f("ck_milestones_owner_type_scope"),
        ),
        sa.CheckConstraint(
            "milestone_type <> 'CUSTOMS_CLEARED' OR (actual_on IS NULL AND actual_at IS NULL)",
            name=op.f("ck_milestones_customs_actual_from_records"),
        ),
        sa.CheckConstraint(
            "milestone_type IN ('BL_ISSUED', 'CARGO_CLOSING', 'CUSTOMS_CLEARED', 'DOC_CUTOFF', 'ETA',"
            " 'ETD', 'FILLING', 'IMPORT_TAX_DUE', 'OUTGOING_INSPECTION', 'PACKING', 'PSI',"
            " 'RAW_MATERIAL_READY')",
            name=op.f("ck_milestones_type_valid"),
        ),
        sa.CheckConstraint(
            "milestone_type IN ('CARGO_CLOSING', 'DOC_CUTOFF')"
            " OR (planned_at IS NULL AND actual_at IS NULL AND tz IS NULL)",
            name=op.f("ck_milestones_date_shape"),
        ),
        sa.CheckConstraint(
            "milestone_type NOT IN ('CARGO_CLOSING', 'DOC_CUTOFF')"
            " OR (planned_on IS NULL AND actual_on IS NULL)",
            name=op.f("ck_milestones_datetime_shape"),
        ),
        sa.CheckConstraint(
            "tz IS NULL OR tz ~ '^[A-Za-z0-9_+/-]{1,64}$'", name=op.f("ck_milestones_tz_format")
        ),
        sa.CheckConstraint(
            " AND ".join(
                [_DATE_RANGE.format(col=c) for c in ("planned_on", "actual_on")]
                + [_INSTANT_RANGE.format(col=c) for c in ("planned_at", "actual_at")]
            ),
            name=op.f("ck_milestones_value_range"),
        ),
        sa.CheckConstraint(
            "(shipment_id IS NULL) <> (po_id IS NULL)", name=op.f("ck_milestones_one_owner")
        ),
        sa.CheckConstraint(
            "(tz IS NULL) = (planned_at IS NULL AND actual_at IS NULL)",
            name=op.f("ck_milestones_tz_iff_instant"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name=op.f("fk_milestones_created_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["po_id"],
            ["purchase_orders.id"],
            name=op.f("fk_milestones_po_id_purchase_orders"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["shipment_id"],
            ["shipments.id"],
            name=op.f("fk_milestones_shipment_id_shipments"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"],
            ["users.id"],
            name=op.f("fk_milestones_updated_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_milestones")),
    )
    op.create_index(
        "uq_milestones_po_id_milestone_type_active",
        "milestones",
        ["po_id", "milestone_type"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "uq_milestones_shipment_id_milestone_type_active",
        "milestones",
        ["shipment_id", "milestone_type"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_table(
        "milestone_changes",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("milestone_id", sa.BigInteger(), nullable=False),
        sa.Column("change_kind", sa.String(length=20), nullable=False),
        sa.Column("old_on", sa.Date(), nullable=True),
        sa.Column("new_on", sa.Date(), nullable=True),
        sa.Column("old_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("new_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("old_tz", sa.String(length=64), nullable=True),
        sa.Column("new_tz", sa.String(length=64), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "((change_kind IN ('PLAN_SET', 'ACTUAL_RECORDED')) = (old_on IS NULL AND old_at IS NULL))"
            " AND (change_kind = 'ACTUAL_CORRECTED' OR new_on IS NOT NULL OR new_at IS NOT NULL)",
            name=op.f("ck_milestone_changes_kind_values"),
        ),
        sa.CheckConstraint(
            "change_kind IN ('ACTUAL_CORRECTED', 'ACTUAL_RECORDED', 'PLAN_CHANGED', 'PLAN_SET')",
            name=op.f("ck_milestone_changes_change_kind_valid"),
        ),
        sa.CheckConstraint(
            "change_kind NOT IN ('ACTUAL_CORRECTED', 'PLAN_CHANGED') OR reason IS NOT NULL",
            name=op.f("ck_milestone_changes_reason_required"),
        ),
        sa.CheckConstraint(
            f"reason IS NULL OR (char_length(reason) BETWEEN 1 AND 500 AND reason ~ '[^{_BLANK}]'"
            " AND reason !~ '[[:cntrl:]]')",
            name=op.f("ck_milestone_changes_reason_clean"),
        ),
        sa.CheckConstraint(
            "(old_on IS NULL OR old_at IS NULL) AND (new_on IS NULL OR new_at IS NULL)"
            " AND (old_at IS NULL) = (old_tz IS NULL) AND (new_at IS NULL) = (new_tz IS NULL)",
            name=op.f("ck_milestone_changes_value_pairs"),
        ),
        sa.CheckConstraint(
            "(old_on, old_at, old_tz) IS DISTINCT FROM (new_on, new_at, new_tz)",
            name=op.f("ck_milestone_changes_changed"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name=op.f("fk_milestone_changes_actor_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["milestone_id"],
            ["milestones.id"],
            name=op.f("fk_milestone_changes_milestone_id_milestones"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_milestone_changes")),
    )
    op.create_index(
        "ix_milestone_changes_actor_user_id", "milestone_changes", ["actor_user_id"], unique=False
    )
    op.create_index(
        "ix_milestone_changes_milestone_id_id",
        "milestone_changes",
        ["milestone_id", sa.literal_column("id DESC")],
        unique=False,
    )
    revoke_mutations(op, "milestone_changes")
    op.create_table(
        "milestone_change_notices",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("change_id", sa.BigInteger(), nullable=False),
        sa.Column("comm_log_id", sa.BigInteger(), nullable=False),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name=op.f("fk_milestone_change_notices_actor_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["change_id"],
            ["milestone_changes.id"],
            name=op.f("fk_milestone_change_notices_change_id_milestone_changes"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["comm_log_id"],
            ["comm_logs.id"],
            name=op.f("fk_milestone_change_notices_comm_log_id_comm_logs"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_milestone_change_notices")),
        sa.UniqueConstraint(
            "change_id", "comm_log_id", name="uq_milestone_change_notices_change_id_comm_log_id"
        ),
    )
    op.create_index(
        "ix_milestone_change_notices_actor_user_id",
        "milestone_change_notices",
        ["actor_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_milestone_change_notices_comm_log_id",
        "milestone_change_notices",
        ["comm_log_id"],
        unique=False,
    )
    revoke_mutations(op, "milestone_change_notices")
    # ⑥ 기존 표 변경 — comm_logs 주제 CHECK 재정의(autogenerate 미감지 — 함정 ①, op.f() 필수 — 함정 ⑪)
    op.drop_constraint(op.f(_COMM_SUBJECT_CHECK), "comm_logs", type_="check")
    op.create_check_constraint(op.f(_COMM_SUBJECT_CHECK), "comm_logs", _COMM_SUBJECT_NEW)
    # ⑦ comm_logs 요지 CHECK 재정의(보이는 글자 1개 이상) + 선적 통보 오간 날 범위 CHECK 신설(적대 검토 반영 ⑤·⑥)
    op.drop_constraint(op.f(_COMM_SUMMARY_CHECK), "comm_logs", type_="check")
    op.create_check_constraint(op.f(_COMM_SUMMARY_CHECK), "comm_logs", _COMM_SUMMARY_NEW)
    op.create_check_constraint(op.f(_COMM_OCCURRED_CHECK), "comm_logs", _COMM_OCCURRED)


def refuse_lossy_downgrade(bind: sa.engine.Connection) -> None:
    """신규 5표의 행(삭제 포함) 또는 SHIPMENT 주제 통신 기록이 있으면 RuntimeError — DDL 전에 부른다(되돌릴 수 없는 손실 방지)."""
    counts = {
        table: int(bind.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one())
        for table in _GUARDED_TABLES
    }
    counts["comm_logs(SHIPMENT)"] = int(
        bind.execute(
            sa.text("SELECT count(*) FROM comm_logs WHERE subject_type = 'SHIPMENT'")
        ).scalar_one()
    )
    found = {name: count for name, count in counts.items() if count}
    if found:
        detail = ", ".join(f"{name} {count}건" for name, count in found.items())
        raise RuntimeError(
            f"{detail}이 있어 M15를 내릴 수 없습니다 — 통관·마일스톤·롤오버 이력·통보 기록이 사라집니다. "
            "데이터 처리 방침(보존 반출 등)을 먼저 정한 뒤 내리세요."
        )


def downgrade() -> None:
    refuse_lossy_downgrade(op.get_bind())
    # IF EXISTS — 적대 검토 반영 전 M15(이 CHECK 없음)를 올린 로컬 DB도 내릴 수 있게(같은 리비전 파일 수정의 뒷정리)
    op.execute(f"ALTER TABLE comm_logs DROP CONSTRAINT IF EXISTS {_COMM_OCCURRED_CHECK}")
    op.drop_constraint(op.f(_COMM_SUMMARY_CHECK), "comm_logs", type_="check")
    op.create_check_constraint(op.f(_COMM_SUMMARY_CHECK), "comm_logs", _COMM_SUMMARY_OLD)
    op.drop_constraint(op.f(_COMM_SUBJECT_CHECK), "comm_logs", type_="check")
    op.create_check_constraint(op.f(_COMM_SUBJECT_CHECK), "comm_logs", _COMM_SUBJECT_OLD)
    op.drop_index("ix_milestone_change_notices_comm_log_id", table_name="milestone_change_notices")
    op.drop_index(
        "ix_milestone_change_notices_actor_user_id", table_name="milestone_change_notices"
    )
    op.drop_table("milestone_change_notices")
    op.drop_index("ix_milestone_changes_milestone_id_id", table_name="milestone_changes")
    op.drop_index("ix_milestone_changes_actor_user_id", table_name="milestone_changes")
    op.drop_table("milestone_changes")
    op.drop_index(
        "uq_milestones_shipment_id_milestone_type_active",
        table_name="milestones",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_index(
        "uq_milestones_po_id_milestone_type_active",
        table_name="milestones",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_table("milestones")
    op.drop_index(
        "uq_customs_records_declaration_kind_declaration_no_active",
        table_name="customs_records",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_index(
        "ix_customs_records_shipment_id_live",
        table_name="customs_records",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_index("ix_customs_records_customs_broker_partner_id", table_name="customs_records")
    op.drop_table("customs_records")
    op.drop_index(
        "uq_item_profile_milestone_types_profile_type_active",
        table_name="item_profile_milestone_types",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_table("item_profile_milestone_types")
