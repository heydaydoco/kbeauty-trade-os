"""유효기간 만료 스윕 — 견적·PI (S3-1 ADR-0056 / design-B B7 / design-integrated §2.6 · X-18).

잡 `document-expiry-sweep`(daily@06:10 KST)의 본체다. **이 잡이 만드는 전이는 정확히 두 엣지뿐이다**:
(QT, ISSUED→EXPIRED)·(PI, ISSUED→EXPIRED). 4금 논증(ADR-0056 필수 문단):
  ① 발주 확정 — PO·SO를 만들거나 바꾸지 않는다(만료는 "사용 불능 쪽" 이동이며 약정 진입이 아니다).
  ② 법적 판정 — 사람이 입력해 동결한 `valid_until`과 달력의 산술 비교일 뿐이다.
  ③ 대외 발송 — 아무것도 보내지 않는다(아웃박스 이벤트는 내부 라우팅).
  ④ 장부 확정 — QT·PI는 원장·분개·채권을 만들지 않는 영업 문서이고 만료는 CONTENT를 바꾸지 않는다.
사람 통제 유지: 만료는 종결이지만 문서는 삭제되지 않고 재발행 경로(복제·새 PI)가 사람 손에 있다. 알림·번호·SO·PO 모듈을
임포트하지 않는다(아키텍처 테스트가 고정).

■ **후보 = 미입금 발행(ISSUED) + `valid_until < 기준일`(당일 KST 24:00까지 유효) + 삭제 아님**. 일부입금·입금완료 PI와 CONVERTED QT는
  상태로 이미 비대상이고, **살아 있는 후속(QT←PI·SO, PI←SO)이 있으면 제외한다**(후속이 부모를 붙잡는다 — 입금이 계속 들어와야 하므로
  시스템이 닫지 않는다).
■ **건별 독립 트랜잭션**(§17.6): 후보 id 수집(TX1) → 건마다 새 TX에서 행 `FOR UPDATE` → 상태·조건 **재확인**(사람 취소·입금·후속
  생성과 직렬화 — 이미 바뀌었으면 skipped) → `record_transition(EXPIRED, automatic=True, actor=None)`. 실패 건은 그 건만 롤백·로그(scrub)·집계하고
  나머지를 계속 처리하며, 마지막에 호출자(스케줄러)가 실패 건이 있으면 잡을 FAILED로 올린다. 멱등(재실행 변화 0).
■ PI를 먼저 처리한 뒤 QT를 처리한다 — 후속이 죽은 QT가 같은 실행에서 만료되도록(후보는 PI 처리 뒤에 다시 수집한다).
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select

from app.core.db.uow import unit_of_work
from app.core.logging import get_logger
from app.core.logging.redaction import scrub_text
from app.core.time import today_kst
from app.modules.proforma_invoices.models import ProformaInvoice
from app.modules.quotations.models import Quotation
from app.modules.trade_docs.chain import has_live_children
from app.modules.trade_docs.constants import DocKind
from app.modules.trade_docs.expiry import EXPIRY_CANDIDATE_STATUS, is_lapsed
from app.modules.trade_docs.locking import lock_document
from app.modules.trade_docs.transition import record_transition

logger = get_logger(__name__)

#: 스윕이 만드는 (전표, from, to)의 전부 — 아키텍처 테스트가 이 집합과 실제 결과를 대사한다.
SWEEP_EDGES: frozenset[tuple[DocKind, str, str]] = frozenset(
    {
        (DocKind.QUOTATION, "ISSUED", "EXPIRED"),
        (DocKind.PROFORMA_INVOICE, "ISSUED", "EXPIRED"),
    }
)

_TARGETS: tuple[tuple[DocKind, type[Quotation] | type[ProformaInvoice]], ...] = (
    (
        DocKind.PROFORMA_INVOICE,
        ProformaInvoice,
    ),  # PI 먼저 — 후속이 죽은 QT가 같은 실행에서 만료된다
    (DocKind.QUOTATION, Quotation),
)


def _candidate_ids(model: type[Quotation] | type[ProformaInvoice], today: date) -> list[int]:
    with unit_of_work() as uow:
        return list(
            uow.session.execute(
                select(model.id)
                .where(
                    model.status == EXPIRY_CANDIDATE_STATUS,
                    model.deleted_at.is_(None),
                    model.valid_until < today,
                )
                .order_by(model.id)
            ).scalars()
        )


def _expire_one(
    kind: DocKind, model: type[Quotation] | type[ProformaInvoice], doc_id: int, today: date
) -> bool:
    """한 건 = 한 트랜잭션. 만료시켰으면 True, 잠근 뒤 재확인에서 대상이 아니게 됐으면 False(skipped)."""
    with unit_of_work() as uow:
        session = uow.session
        row = lock_document(
            session, model, doc_id
        )  # 잠금 순서: 후속 생성·입금·취소와 같은 행 잠금으로 직렬화
        if row.status != EXPIRY_CANDIDATE_STATUS or not is_lapsed(
            kind, row.status, row.valid_until, today
        ):
            return False
        if has_live_children(session, kind, row.id):
            return False  # 후속이 부모를 붙잡는다(X-18) — 시스템이 닫지 않는다
        record_transition(
            session,
            row,
            "EXPIRED",
            actor_user_id=None,
            reason=f"자동: 유효기간 경과 (valid_until={row.valid_until}, 기준일={today})",
            automatic=True,
        )
        return True


def sweep_expired_documents(*, base_date: date | None = None) -> dict[str, int]:
    """QT·PI 만료 스윕 — `{expired_qt, expired_pi, skipped, failed}`. 기준일 기본은 KST 오늘."""
    today = base_date or today_kst()
    counts = {"expired_qt": 0, "expired_pi": 0, "skipped": 0, "failed": 0}
    for kind, model in _TARGETS:
        for doc_id in _candidate_ids(model, today):
            try:
                expired = _expire_one(kind, model, doc_id, today)
            except Exception as error:
                counts["failed"] += 1
                logger.error(
                    "document_expiry_sweep_failed",
                    doc_kind=kind.value,
                    entity_id=doc_id,
                    error=scrub_text(f"{type(error).__name__}: {error}")[:500],
                )
                continue
            if not expired:
                counts["skipped"] += 1
            elif kind is DocKind.QUOTATION:
                counts["expired_qt"] += 1
            else:
                counts["expired_pi"] += 1
    return counts
