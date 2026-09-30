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
    monkeypatch: Any, *, fk_column: str = "qt_id", table_name: str = "scratch_successors"
) -> Iterator[FakeSuccessors]:
    """QT의 후속(PI·SO 대역) 테이블을 임시로 만들고 사슬 레지스트리에 끼운다.

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
                DocKind.QUOTATION, table_name, fk_column, confirmed_column="confirmed_at"
            ),
        ),
    )
    try:
        yield FakeSuccessors(table_name, fk_column)
    finally:
        Base.metadata.remove(table)
        with owner_engine.begin() as connection:
            connection.execute(sa.text(f'DROP TABLE IF EXISTS public."{table_name}"'))
