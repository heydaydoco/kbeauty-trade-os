"""전표(S3-1) 테스트 팩토리 — 역할별 사용자·바이어·판가 SKU·견적 요청 본문 (design-integrated G-06).

PR-5a가 골격을 만들고 각 전표 PR이 확장한다(PI·SO·PO·승인 결재선 등). 생성 지점을 여기 모아 필수 열이 하나
늘 때 전 테스트가 깨지는 일을 막는다. **테스트 데이터는 전부 익명 합성**이다(실 거래처·단가 금지).
"""

from __future__ import annotations

import itertools
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.db.uow import unit_of_work
from app.core.time import today_kst
from app.main import app
from app.modules.catalog.models import SkuPrice
from app.modules.identity.models import RoleCode
from app.modules.partners.models import CustomerItemCode, Partner
from tests.support.factories import (
    DEFAULT_PASSWORD,
    create_market,
    create_partner,
    create_sku,
    create_user,
)

_counter = itertools.count(1)


def unique(prefix: str) -> str:
    """테스트 안에서 겹치지 않는 자연키(거래처 코드·SKU 코드·멱등 키 등) — 고정 코드값 함정 회피."""
    return f"{prefix}-{next(_counter):04d}"


def idem() -> dict[str, str]:
    """Idempotency-Key 헤더 — 요청마다 새 키(더블클릭 시나리오는 같은 값을 재사용한다)."""
    return {"Idempotency-Key": unique("idem")}


@contextmanager
def logged_in(*roles: RoleCode, email: str | None = None) -> Iterator[TestClient]:
    """역할을 가진 사용자로 로그인한 클라이언트."""
    address = email or f"{unique('user')}@example.com"
    create_user(address, roles=roles)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/auth/login", json={"email": address, "password": DEFAULT_PASSWORD}
        )
        assert response.is_success, response.text
        yield client


def create_buyer(
    *,
    code: str | None = None,
    name_ko: str = "합성 바이어",
    name_en: str | None = "Acme Trading Inc.",
    address_en: str | None = "1 Main St, Los Angeles, US",
) -> int:
    """BUYER 유형 거래처 — 영문명·주소를 채워 QT 서류 표기 스냅샷 원천을 갖춘다."""
    partner_id = create_partner(code or unique("BUY"), name_ko=name_ko, types=("BUYER",))
    with unit_of_work() as uow:
        partner = uow.session.get(Partner, partner_id)
        assert partner is not None
        partner.name_en, partner.address_en = name_en, address_en
    return partner_id


def create_priced_sku(
    code: str | None = None,
    *,
    amount: int = 1000,
    currency: str = "USD",
    price_type: str = "SALES",
    effective_from: date = date(2020, 1, 1),
    status: str = "ACTIVE",
) -> int:
    """SKU와 발효 중인 판가 1행(정수 최소단위)."""
    sku_id = create_sku(code or unique("SKU"), status=status)
    set_price(
        sku_id, amount, currency=currency, price_type=price_type, effective_from=effective_from
    )
    return sku_id


def set_price(
    sku_id: int,
    amount: int,
    *,
    currency: str = "USD",
    price_type: str = "SALES",
    effective_from: date = date(2020, 1, 1),
) -> None:
    with unit_of_work() as uow:
        uow.session.add(
            SkuPrice(
                sku_id=sku_id,
                price_type=price_type,
                currency=currency,
                amount=amount,
                effective_from=effective_from,
            )
        )


def map_buyer_item_code(partner_id: int, sku_id: int, code: str) -> None:
    with unit_of_work() as uow:
        uow.session.add(
            CustomerItemCode(partner_id=partner_id, sku_id=sku_id, buyer_item_code=code)
        )


def user_id_of(email: str) -> int:
    from app.modules.identity.models import User

    with unit_of_work() as uow:
        return int(uow.session.execute(select(User.id).where(User.email == email)).scalar_one())


PAYMENT_TT_ADVANCE_30: dict[str, Any] = {
    "payment_type": "TT_ADVANCE",
    "advance_pct": "30",
    "balance_anchor": "ETD_DATE",
    "balance_days": -7,
}
INCOTERM_FOB_BUSAN: dict[str, Any] = {"code": "FOB", "place": "Busan", "year": 2020}


def quotation_payload(buyer_partner_id: int, **overrides: Any) -> dict[str, Any]:
    """견적 작성 요청 본문 — 기본은 발행 가능한 완결 초안(라인 없음). 필요한 필드만 덮어쓴다."""
    create_market("US")
    body: dict[str, Any] = {
        "buyer_partner_id": buyer_partner_id,
        "dest_market_code": "US",
        "currency": "USD",
        "fx_rate": "1350.5",
        "valid_until": (today_kst() + timedelta(days=30)).isoformat(),
        "payment_terms": PAYMENT_TT_ADVANCE_30,
        "incoterm": INCOTERM_FOB_BUSAN,
    }
    body.update(overrides)
    return body


def create_quotation_via_api(
    client: TestClient, buyer_partner_id: int, sku_ids: list[int] | None = None, **overrides: Any
) -> dict[str, Any]:
    """API로 견적 초안을 만든다(라인은 SKU당 수량 10). 응답 본문을 돌려준다."""
    lines = overrides.pop("lines", [{"sku_id": sku, "quantity": 10} for sku in (sku_ids or [])])
    response = client.post(
        "/api/v1/quotations",
        json=quotation_payload(buyer_partner_id, lines=lines, **overrides),
        headers=idem(),
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def issue_via_api(client: TestClient, qt: dict[str, Any]) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/quotations/{qt['id']}/issue", json={"version": qt["version"]}, headers=idem()
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


# ── 후속 전표 대역(PI·SO가 아직 없는 PR-5a 시점의 사슬 시험용) ─────────────────────────────


class FakeSuccessors:
    """`CHILD_LINKS`에 등록되는 임시 후속 테이블 — 후속 생존·수주전환(QT) 시나리오를 만든다."""

    def __init__(self, table_name: str, fk_column: str) -> None:
        self.table_name = table_name
        self.fk_column = fk_column

    def add(
        self,
        parent_id: int,
        *,
        status: str = "ISSUED",
        confirmed: bool = False,
        number: str | None = None,
    ) -> int:
        from sqlalchemy import text

        from app.core.db.session import owner_engine

        with owner_engine.begin() as connection:
            return int(
                connection.execute(
                    text(
                        f'INSERT INTO public."{self.table_name}" (doc_number, {self.fk_column}, status,'
                        " confirmed_at) VALUES (:n, :p, :s, CASE WHEN :c THEN now() END) RETURNING id"
                    ),
                    {"n": number or unique("SC"), "p": parent_id, "s": status, "c": confirmed},
                ).scalar_one()
            )

    def set_status(self, row_id: int, status: str) -> None:
        from sqlalchemy import text

        from app.core.db.session import owner_engine

        with owner_engine.begin() as connection:
            connection.execute(
                text(f'UPDATE public."{self.table_name}" SET status = :s WHERE id = :i'),
                {"s": status, "i": row_id},
            )

    def confirm(self, row_id: int) -> None:
        from sqlalchemy import text

        from app.core.db.session import owner_engine

        with owner_engine.begin() as connection:
            connection.execute(
                text(f'UPDATE public."{self.table_name}" SET confirmed_at = now() WHERE id = :i'),
                {"i": row_id},
            )


@contextmanager
def fake_successors(
    monkeypatch: Any,
    *,
    fk_column: str = "qt_id",
    table_name: str = "scratch_successors",
    parent: Any = None,
) -> Iterator[FakeSuccessors]:
    """부모(기본 QT — PR-6a부터 PI도 가능)의 후속(SO 대역 등) 테이블을 임시로 만들고 사슬 레지스트리에 끼운다.

    메타데이터에 잠깐 등록했다가 반드시 빼고(다른 테스트의 alembic drift 검사 보호) 테이블을 지운다.
    """
    import sqlalchemy as sa

    from app.core.db.base import Base
    from app.core.db.session import owner_engine
    from app.modules.trade_docs import chain
    from app.modules.trade_docs.constants import DocKind

    with owner_engine.begin() as connection:
        connection.execute(sa.text(f'DROP TABLE IF EXISTS public."{table_name}"'))
        connection.execute(
            sa.text(
                f'CREATE TABLE public."{table_name}" (id BIGSERIAL PRIMARY KEY, doc_number TEXT NOT NULL,'
                f" {fk_column} BIGINT, status TEXT NOT NULL DEFAULT 'ISSUED',"
                " deleted_at TIMESTAMPTZ, confirmed_at TIMESTAMPTZ)"
            )
        )
        connection.execute(
            sa.text(f'GRANT SELECT, INSERT, UPDATE, DELETE ON public."{table_name}" TO kbos_app')
        )
    table = sa.Table(
        table_name,
        Base.metadata,
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("doc_number", sa.Text),
        sa.Column(fk_column, sa.BigInteger),
        sa.Column("status", sa.Text),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
    )
    monkeypatch.setattr(
        chain,
        "CHILD_LINKS",
        (
            chain.ChildLink(
                parent or DocKind.QUOTATION, table_name, fk_column, confirmed_column="confirmed_at"
            ),
        ),
    )
    try:
        yield FakeSuccessors(table_name, fk_column)
    finally:
        Base.metadata.remove(table)
        with owner_engine.begin() as connection:
            connection.execute(sa.text(f'DROP TABLE IF EXISTS public."{table_name}"'))


# ── DB 직행 견적(서비스를 거치지 않고 임의 상태를 만든다 — 전이표·수렴 시험용) ─────────────────


_FROZEN_VALUES: dict[str, Any] = {
    "payment_type": "TT_ADVANCE",
    "advance_pct_bp": 3000,
    "balance_anchor": "ETD_DATE",
    "balance_days": -7,
    "incoterm_code": "FOB",
    "incoterm_place": "Busan",
    "incoterm_year": 2020,
    "fx_rate": 1350,
    "fx_rate_date": date(2026, 9, 1),
    "buyer_address": "1 Main St",
}


def raw_quotation(
    status: str = "DRAFT",
    *,
    buyer_partner_id: int | None = None,
    assignee_id: int | None = None,
    valid_until: date | None = None,
    doc_date: date = date(2026, 9, 1),
    with_history: bool = True,
    complete: bool = True,
) -> int:
    """지정 상태의 견적을 SQL로 직접 넣는다(채번은 전용 시퀀스 대신 임시 번호). 이력에 탄생 행 1개를 남긴다.

    기본은 **발행 가능한 완결 값**(결제조건·Incoterms·환율·주소·유효기간)을 모든 상태에 채운다 — 초안도 발행 전이를
    시험할 수 있게. 발행·전환 상태(ISSUED·CONVERTED)는 동결 시각도 채운다(CHECK).
    """
    from sqlalchemy import text

    from app.core.db.session import owner_engine

    buyer = buyer_partner_id or create_buyer()
    owner = assignee_id or create_user(f"{unique('raw')}@example.com", roles=(RoleCode.TRADE,))
    create_market("US")
    frozen = complete
    values: dict[str, Any] = {
        "doc_number": f"QT-2026-{next(_counter) + 5000:04d}",
        "doc_date": doc_date,
        "status": status,
        "currency": "USD",
        "assignee_id": owner,
        "buyer_partner_id": buyer,
        "buyer_name": "Raw Buyer",
        "dest_market_code": "US",
        "valid_until": valid_until or (date(2099, 1, 1) if complete else None),
        "frozen_at": "2026-09-02T00:00:00+00:00" if status in ("ISSUED", "CONVERTED") else None,
        **(_FROZEN_VALUES if frozen else {}),
    }
    columns = list(values)
    sql = (
        f"INSERT INTO quotations ({', '.join(columns)}) "
        f"VALUES ({', '.join(':' + c for c in columns)}) RETURNING id"
    )
    with owner_engine.begin() as connection:
        qt_id = int(connection.execute(text(sql), values).scalar_one())
        if with_history:
            connection.execute(
                text(
                    "INSERT INTO quotation_status_log (quotation_id, from_status, to_status, actor_user_id,"
                    " automatic) VALUES (:q, NULL, 'DRAFT', :a, false)"
                ),
                {"q": qt_id, "a": owner},
            )
    return qt_id


# ── PI(PR-6a) — 은행 계좌·발행된 견적·참조 생성 요청 본문·DB 직행 PI ───────────────────────────


def create_bank_account(
    currency: str = "USD", *, label: str | None = None, account_no: str | None = None
) -> int:
    """활성 은행 계좌 1건(서비스를 거치지 않고 ORM으로 — 마이그레이션 시드 금지 규칙과 별개로 테스트 데이터일 뿐)."""
    from app.modules.bank_accounts.models import BankAccount

    with unit_of_work() as uow:
        row = BankAccount(
            label=label or unique("BANK"),
            currency=currency,
            beneficiary_name="Kbeauty Trading Co., Ltd.",
            beneficiary_address="1 Gangnam-daero, Seoul, KR",
            bank_name="Synthetic Bank",
            bank_address="2 Teheran-ro, Seoul, KR",
            account_no=account_no or f"110-{next(_counter):06d}-01",
            swift_code="SYNTKRSE",
        )
        uow.session.add(row)
        uow.session.flush()
        return int(row.id)


def issued_quotation(
    client: TestClient,
    buyer_partner_id: int | None = None,
    sku_ids: list[int] | None = None,
    *,
    quantity: int = 10,
    **overrides: Any,
) -> dict[str, Any]:
    """발행 상태 견적(라인 SKU당 수량 `quantity`). 응답 본문(ISSUED)을 돌려준다."""
    buyer = buyer_partner_id or create_buyer()
    skus = sku_ids or [create_priced_sku(amount=1000)]
    lines = [{"sku_id": sku, "quantity": quantity} for sku in skus]
    qt = create_quotation_via_api(client, buyer, lines=lines, **overrides)
    return issue_via_api(client, qt)


def pi_payload(
    qt: dict[str, Any],
    bank_account_id: int,
    *,
    valid_days: int = 30,
    **overrides: Any,
) -> dict[str, Any]:
    """PI 참조 생성 요청 본문 — 원천 QT 값을 다시 받는 필드는 **없다**(version·유효기간·계좌만)."""
    body: dict[str, Any] = {
        "version": qt["version"],
        "valid_until": (today_kst() + timedelta(days=valid_days)).isoformat(),
        "bank_account_id": bank_account_id,
    }
    body.update(overrides)
    return body


def create_pi_via_api(
    client: TestClient,
    qt: dict[str, Any],
    bank_account_id: int | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    """API로 PI를 만든다(생성=발행). 응답 본문(ISSUED)을 돌려준다. 계좌를 안 주면 QT 통화 계좌를 만든다."""
    bank = bank_account_id or create_bank_account(qt["currency"])
    response = client.post(
        f"/api/v1/quotations/{qt['id']}/proforma-invoices",
        json=pi_payload(qt, bank, **overrides),
        headers=idem(),
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def raw_pi(
    qt_id: int,
    status: str = "ISSUED",
    *,
    valid_until: date | None = None,
    doc_date: date = date(2026, 9, 1),
    assignee_id: int | None = None,
    with_history: bool = True,
    total_amount: int = 0,
) -> int:
    """지정 상태의 PI를 SQL로 직접 넣는다(라인 없음 — 전이표·스윕·수렴 시험용). 이력에 탄생 행(ISSUED) 1개를 남긴다.

    원천 QT의 통화·바이어를 그대로 쓴다(복합 FK·바이어 일치). 은행 계좌는 새로 만든다.
    """
    from sqlalchemy import text

    from app.core.db.session import owner_engine

    with owner_engine.connect() as connection:
        qt = connection.execute(
            text(
                "SELECT currency, buyer_partner_id, buyer_name, dest_market_code FROM quotations"
                " WHERE id = :i"
            ),
            {"i": qt_id},
        ).one()
    bank = create_bank_account(qt[0])
    owner = assignee_id or create_user(f"{unique('rawpi')}@example.com", roles=(RoleCode.TRADE,))
    values: dict[str, Any] = {
        "doc_number": f"PI-2026-{next(_counter) + 7000:04d}",
        "doc_date": doc_date,
        "status": status,
        "currency": qt[0],
        "assignee_id": owner,
        "buyer_partner_id": qt[1],
        "buyer_name": qt[2],
        "dest_market_code": qt[3],
        "qt_id": qt_id,
        "valid_until": valid_until or date(2099, 1, 1),
        "bank_account_id": bank,
        "bank_beneficiary_name": "Kbeauty Trading Co., Ltd.",
        "bank_beneficiary_address": "1 Gangnam-daero, Seoul, KR",
        "bank_name": "Synthetic Bank",
        "bank_address": "2 Teheran-ro, Seoul, KR",
        "bank_account_no": "110-000000-01",
        "bank_swift_code": "SYNTKRSE",
        "total_amount": total_amount,
        **_FROZEN_VALUES,
    }
    columns = list(values)
    sql = (
        f"INSERT INTO proforma_invoices ({', '.join(columns)}) "
        f"VALUES ({', '.join(':' + c for c in columns)}) RETURNING id"
    )
    with owner_engine.begin() as connection:
        pi_id = int(connection.execute(text(sql), values).scalar_one())
        if with_history:
            connection.execute(
                text(
                    "INSERT INTO proforma_invoice_status_log (proforma_invoice_id, from_status,"
                    " to_status, actor_user_id, automatic) VALUES (:p, NULL, 'ISSUED', :a, false)"
                ),
                {"p": pi_id, "a": owner},
            )
    return pi_id


# ── SO(PR-7a) — 참조 생성 요청 본문·API 생성·직접(인테이크형) 수주·DB 직행 SO ───────────────────


def so_payload(source: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    """QT/PI → SO 참조 생성 요청 본문 — 원천 값을 다시 받는 필드는 **없다**(원천 version만). 나머지는 선택 필드."""
    body: dict[str, Any] = {"version": source["version"]}
    body.update(overrides)
    return body


def create_so_from_qt_via_api(
    client: TestClient, qt: dict[str, Any], **overrides: Any
) -> dict[str, Any]:
    """API로 QT에서 수주를 직접 만든다(접수 상태). 응답 본문을 돌려준다."""
    response = client.post(
        f"/api/v1/quotations/{qt['id']}/sales-orders",
        json=so_payload(qt, **overrides),
        headers=idem(),
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def create_so_from_pi_via_api(
    client: TestClient, pi: dict[str, Any], **overrides: Any
) -> dict[str, Any]:
    """API로 PI에서 수주를 만든다(접수 상태)."""
    response = client.post(
        f"/api/v1/proforma-invoices/{pi['id']}/sales-orders",
        json=so_payload(pi, **overrides),
        headers=idem(),
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def issued_pi_chain(
    client: TestClient,
    *,
    quantity: int = 10,
    sku_ids: list[int] | None = None,
    buyer_partner_id: int | None = None,
    amount: int = 1000,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """(발행된 QT, 그 QT 전량으로 만든 PI) — QT→PI→SO 관통 시험의 출발점."""
    skus = sku_ids or [create_priced_sku(amount=amount)]
    qt = issued_quotation(client, buyer_partner_id, skus, quantity=quantity)
    pi = create_pi_via_api(client, qt)
    return qt, pi


def create_direct_so(
    buyer_partner_id: int,
    sku_ids: list[int] | None = None,
    *,
    quantity: int = 5,
    buyer_po_no: str | None = None,
    currency: str = "USD",
    assignee_id: int | None = None,
    price_amount: int = 1000,
) -> dict[str, Any]:
    """직접(인테이크형) 수주 — `create_received_sales_order` 단일 착지를 서비스로 직접 부른다(참조 없음 — 결제조건·Incoterms·환율은 비어 있다).

    라인 단가는 바이어 PO 단가(`BUYER_PO`)로 복사한다(인테이크 확정 PR-13이 같은 모양으로 채운다).
    """
    from app.modules.identity.service import AuthenticatedUser
    from app.modules.sales_orders import service as sos
    from app.modules.trade_docs.snapshot import LineSnapshot

    create_market("US")
    skus = sku_ids or [create_priced_sku(amount=price_amount, currency=currency)]
    owner = assignee_id or create_user(f"{unique('dso')}@example.com", roles=(RoleCode.TRADE,))
    actor = AuthenticatedUser(
        id=owner,
        email="direct@example.com",
        display_name="직접 수주 작성자",
        roles=frozenset({RoleCode.TRADE}),
        session_id=0,
    )
    from app.modules.catalog.models import Sku

    with unit_of_work() as uow:
        lines = []
        for sku_id in skus:
            sku = uow.session.get(Sku, sku_id)
            assert sku is not None
            lines.append(
                sos.NewSoLine(
                    LineSnapshot(
                        sku_id=sku.id,
                        sku_code=sku.sku_code,
                        sku_name_ko=sku.name_ko,
                        sku_name_en=sku.name_en,
                        sku_kind=sku.kind,
                        buyer_item_code=None,
                        unit_price_amount=price_amount,
                        list_price_amount=price_amount,
                        price_basis="BUYER_PO",
                        is_free=False,
                        price_reason=None,
                    ),
                    quantity,
                )
            )
        row = sos.create_received_sales_order(
            uow.session,
            actor=actor,
            draft=sos.SalesOrderDraft(
                buyer_partner_id=buyer_partner_id,
                currency=currency,
                dest_market_code="US",
                lines=lines,
                buyer_po_no=buyer_po_no,
                assignee_id=owner,
            ),
        )
        body = sos.detail_body(uow.session, row)
    return body


#: 원시 SQL로 "확정된" SO를 만드는 픽스처가 함께 채워야 하는 증적(M10 CHECK) — `SET status='CONFIRMED', confirmed_at=now()` 뒤에 붙인다.
CONFIRMED_EVIDENCE = {"credit_verdict": "NOT_MANAGED", "pi_gate_verdict": "NOT_APPLICABLE"}
CONFIRMED_EVIDENCE_SQL = "credit_verdict = 'NOT_MANAGED', pi_gate_verdict = 'NOT_APPLICABLE'"


def raw_so(
    status: str = "RECEIVED",
    *,
    buyer_partner_id: int | None = None,
    buyer_po_no: str | None = None,
    buyer_po_no_key: str | None = None,
    qt_id: int | None = None,
    pi_id: int | None = None,
    doc_date: date = date(2026, 9, 1),
    assignee_id: int | None = None,
    with_history: bool = True,
    confirmed: bool | None = None,
    complete: bool = True,
) -> int:
    """지정 상태의 SO를 SQL로 직접 넣는다(라인 없음 — 전이표·역순 취소·제약 시험용). 이력에 탄생 행(RECEIVED) 1개를 남긴다.

    `confirmed`를 안 주면 확정 시각은 상태가 요구하는 대로(CONFIRMED·후반=채움, RECEIVED=NULL, ON_HOLD·CANCELLED=NULL).
    `confirmed=True`로 ON_HOLD를 만들면 "확정 뒤 보류"가 된다. 통화·바이어는 원천 QT/PI를 따른다(복합 FK).
    """
    from sqlalchemy import text

    from app.core.db.session import owner_engine

    buyer = buyer_partner_id or create_buyer()
    currency = "USD"
    market = "US"
    create_market("US")
    with owner_engine.connect() as connection:
        if pi_id is not None:
            row = connection.execute(
                text(
                    "SELECT currency, buyer_partner_id, dest_market_code, qt_id FROM proforma_invoices"
                    " WHERE id = :i"
                ),
                {"i": pi_id},
            ).one()
            currency, buyer, market, qt_id = row[0], row[1], row[2], row[3]
        elif qt_id is not None:
            row = connection.execute(
                text(
                    "SELECT currency, buyer_partner_id, dest_market_code FROM quotations WHERE id = :i"
                ),
                {"i": qt_id},
            ).one()
            currency, buyer, market = row[0], row[1], row[2]
    owner = assignee_id or create_user(f"{unique('rawso')}@example.com", roles=(RoleCode.TRADE,))
    if confirmed is None:
        confirmed = status in (
            "CONFIRMED",
            "PARTIALLY_ALLOCATED",
            "ALLOCATED",
            "IN_SHIPMENT",
            "COMPLETED",
        )
    values: dict[str, Any] = {
        "doc_number": f"SO-2026-{next(_counter) + 9000:04d}",
        "doc_date": doc_date,
        "status": status,
        "currency": currency,
        "assignee_id": owner,
        "buyer_partner_id": buyer,
        "buyer_name": "Raw Buyer",
        "dest_market_code": market,
        "qt_id": qt_id,
        "pi_id": pi_id,
        "buyer_po_no": buyer_po_no,
        "buyer_po_no_key": buyer_po_no_key,
        "confirmed_at": "2026-09-02T00:00:00+00:00" if confirmed else None,
        # M10 — 확정 증적 3열은 확정 시각과 함께만 존재한다(DB CHECK). 원시 SQL 픽스처는 "여신 미관리·PI 비활성" 증적을 단다.
        "credit_verdict": CONFIRMED_EVIDENCE["credit_verdict"] if confirmed else None,
        "pi_gate_verdict": CONFIRMED_EVIDENCE["pi_gate_verdict"] if confirmed else None,
        **(_FROZEN_VALUES if complete else {}),
    }
    values.pop("buyer_address", None)
    columns = list(values)
    sql = (
        f"INSERT INTO sales_orders ({', '.join(columns)}) "
        f"VALUES ({', '.join(':' + c for c in columns)}) RETURNING id"
    )
    with owner_engine.begin() as connection:
        so_id = int(connection.execute(text(sql), values).scalar_one())
        if with_history:
            connection.execute(
                text(
                    "INSERT INTO sales_order_status_log (sales_order_id, from_status, to_status,"
                    " actor_user_id, automatic) VALUES (:s, NULL, 'RECEIVED', :a, false)"
                ),
                {"s": so_id, "a": owner},
            )
    return so_id


# ── 구매 발주서(PO, PR-8a) ────────────────────────────────────────────────────

#: 원가 마스킹 테스트가 쓰는 센티널 — 다른 값과 겹치지 않는 자릿수의 원가(USD 센트 단위 정수). 어떤 채널에도 **0회**여야 한다.
SENTINEL_UNIT_COST = 7654321
SENTINEL_UNIT_COST_TEXT = "76543.21"


def create_supplier(
    *,
    code: str | None = None,
    types: tuple[str, ...] = ("SUPPLIER",),
    name_en: str | None = "Synthetic Supply Co.",
    name_ko: str = "합성 공급사",
) -> int:
    """공급사(SUPPLIER)·OEM 유형 거래처 — 영문명을 채워 PO 공급사 표기 스냅샷 원천을 갖춘다."""
    partner_id = create_partner(code or unique("SUP"), name_ko=name_ko, types=types)
    with unit_of_work() as uow:
        partner = uow.session.get(Partner, partner_id)
        assert partner is not None
        partner.name_en = name_en
    return partner_id


def create_purchase_priced_sku(
    code: str | None = None,
    *,
    amount: int = 500,
    currency: str = "USD",
    effective_from: date = date(2020, 1, 1),
    status: str = "ACTIVE",
) -> int:
    """SKU와 발효 중인 **매입가**(PURCHASE) 1행(정수 최소단위)."""
    return create_priced_sku(
        code,
        amount=amount,
        currency=currency,
        price_type="PURCHASE",
        effective_from=effective_from,
        status=status,
    )


PAYMENT_TT_DEFERRED_RECEIPT: dict[str, Any] = {
    "payment_type": "TT_DEFERRED",
    "balance_anchor": "RECEIPT_DATE",
    "balance_days": 30,
}
INCOTERM_EXW_SEOUL: dict[str, Any] = {"code": "EXW", "place": "Seoul", "year": 2020}


def po_payload(
    supplier_partner_id: int, sku_ids: list[int] | None = None, **overrides: Any
) -> dict[str, Any]:
    """PO 생성·미리보기 요청 본문 — 기본은 발행 가능한 완결 본문(라인은 SKU당 수량 10, 단가 생략=마스터 매입가)."""
    lines = overrides.pop("lines", [{"sku_id": sku, "quantity": 10} for sku in (sku_ids or [])])
    body: dict[str, Any] = {
        "supplier_partner_id": supplier_partner_id,
        "currency": "USD",
        "fx_rate": "1350.5",
        "payment_terms": PAYMENT_TT_DEFERRED_RECEIPT,
        "incoterm": INCOTERM_EXW_SEOUL,
        "lines": lines,
    }
    body.update(overrides)
    return body


def create_po_via_api(
    client: TestClient,
    supplier_partner_id: int | None = None,
    sku_ids: list[int] | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    """API로 PO를 만든다(=발행). 공급사·SKU를 안 주면 합성 1건을 만든다. 응답 본문을 돌려준다."""
    supplier = supplier_partner_id or create_supplier()
    skus = sku_ids if sku_ids is not None else [create_purchase_priced_sku()]
    response = client.post(
        "/api/v1/purchase-orders", json=po_payload(supplier, skus, **overrides), headers=idem()
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def raw_po(
    status: str = "ISSUED",
    *,
    supplier_partner_id: int | None = None,
    doc_date: date = date(2026, 9, 1),
    assignee_id: int | None = None,
    with_history: bool = True,
    oc_received_on: date | None = None,
    oc_reference: str | None = None,
    po_kind: str = "PURCHASE",
    copied_from_id: int | None = None,
) -> int:
    """지정 상태의 PO를 SQL로 직접 넣는다(라인 없음 — 전이표·제약 시험용). 이력에 탄생 행(ISSUED) 1개를 남긴다.

    OC 열은 상태가 요구하는 대로(ISSUED=NULL·공급사 확인 이후=OC 일자 채움)다. 호출자가 `oc_received_on`을 주면 그 값이 우선한다.
    """
    from sqlalchemy import text

    from app.core.db.session import owner_engine

    supplier = supplier_partner_id or create_supplier()
    owner = assignee_id or create_user(f"{unique('rawpo')}@example.com", roles=(RoleCode.TRADE,))
    needs_oc = status in ("SUPPLIER_CONFIRMED", "PARTIALLY_RECEIVED", "FULLY_RECEIVED", "CLOSED")
    values: dict[str, Any] = {
        "doc_number": f"PO-2026-{next(_counter) + 9000:04d}",
        "doc_date": doc_date,
        "status": status,
        "currency": "USD",
        "assignee_id": owner,
        "supplier_partner_id": supplier,
        "supplier_name": "Raw Supplier",
        "po_kind": po_kind,
        "copied_from_id": copied_from_id,
        "oc_received_on": oc_received_on or (doc_date if needs_oc else None),
        "oc_reference": oc_reference,
        "payment_type": "TT_DEFERRED",
        "balance_anchor": "RECEIPT_DATE",
        "balance_days": 30,
        "incoterm_code": "EXW",
        "incoterm_place": "Seoul",
        "incoterm_year": 2020,
        "fx_rate": 1350,
        "fx_rate_date": doc_date,
    }
    columns = list(values)
    sql = (
        f"INSERT INTO purchase_orders ({', '.join(columns)}) "
        f"VALUES ({', '.join(':' + c for c in columns)}) RETURNING id"
    )
    with owner_engine.begin() as connection:
        po_id = int(connection.execute(text(sql), values).scalar_one())
        if with_history:
            connection.execute(
                text(
                    "INSERT INTO purchase_order_status_log (purchase_order_id, from_status, to_status,"
                    " actor_user_id, automatic) VALUES (:p, NULL, 'ISSUED', :a, false)"
                ),
                {"p": po_id, "a": owner},
            )
    return po_id
