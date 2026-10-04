"""K. 보안·품질 — 대행 협업 스키마의 불변식을 DB가 강제한다 (§5.4·§17.4·§17.5 / S2-4 PR-1).

★ 서비스를 거치지 않고 ORM으로 직접 INSERT/UPDATE한다 — 모델이 선언한 CHECK·부분 유니크가
  DB에 실재하는지를 실측한다. 특히 **기존 테이블(certifications·documents)에 붙는 CHECK는
  autogenerate가 마이그레이션 초안에 넣어 주지 않으므로(함정 ①)** 수기 반영 누락의 최종
  검출이 이 실측이다.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.core.db.uow import unit_of_work
from app.core.time import utcnow
from app.modules.certifications.models import ACTION_OWNERS, HANDLING_MODES, Certification
from app.modules.collaboration.models import COMM_SUBJECT_TYPES, AgencyContract, CommLog
from app.modules.documents.models import DOCUMENT_OWNER_TYPES, Document, DocumentType
from tests.support.factories import (
    create_certification_instance,
    create_partner,
    create_requirement_template,
    create_sku,
)

pytestmark = pytest.mark.group_k


def _agency() -> int:
    return create_partner("AGY-001", name_ko="테스트 대행사", types=("CERT_AGENCY",))


def _insert_contract(**overrides: Any) -> int:
    columns: dict[str, Any] = {
        "partner_id": overrides.pop("partner_id", None) or _agency(),
        "contract_no": "CT-001",
        "start_on": date(2026, 1, 1),
    }
    columns.update(overrides)
    with unit_of_work() as uow:
        row = AgencyContract(**columns)
        uow.session.add(row)
        uow.session.flush()
        return row.id


def _insert_comm_log(**overrides: Any) -> int:
    columns: dict[str, Any] = {
        "subject_type": "CERTIFICATION",
        "subject_id": 1,  # 폴리모픽 — FK가 없으므로 실재 검증은 서비스 몫이다
        "occurred_on": date(2026, 9, 1),
        "summary": "요지",
    }
    columns.update(overrides)
    with unit_of_work() as uow:
        row = CommLog(**columns)
        uow.session.add(row)
        uow.session.flush()
        return row.id


# ── 대행 계약 ──────────────────────────────────────────────────────────────


def test_a_contract_ending_before_it_starts_is_rejected() -> None:
    """종료일이 시작일보다 빠른 계약은 DB가 거부한다 (기간 순서 §17.5)"""
    with pytest.raises(IntegrityError) as exc:
        _insert_contract(start_on=date(2026, 5, 1), end_on=date(2026, 4, 30))
    assert "period_order" in str(exc.value)


def test_an_open_ended_contract_and_a_one_day_contract_are_accepted() -> None:
    """종료일 없음(기간 미정)과 시작=종료(하루 계약)는 통과한다 — 경계"""
    partner_id = _agency()
    assert _insert_contract(partner_id=partner_id, contract_no="OPEN", end_on=None) > 0
    assert (
        _insert_contract(
            partner_id=partner_id,
            contract_no="ONE",
            start_on=date(2026, 5, 1),
            end_on=date(2026, 5, 1),
        )
        > 0
    )


def test_a_blank_contract_number_is_rejected() -> None:
    """공백뿐인 계약 번호는 DB가 거부한다"""
    with pytest.raises(IntegrityError) as exc:
        _insert_contract(contract_no="   ")
    assert "contract_no_not_blank" in str(exc.value)


def test_a_fee_needs_its_currency_and_vice_versa() -> None:
    """수수료 금액과 통화는 한 쌍이다 — 한쪽만 있으면 거부 (금액 규약 ADR-0003 ④)"""
    partner_id = _agency()
    with pytest.raises(IntegrityError) as amount_only:
        _insert_contract(partner_id=partner_id, fee_amount=1000)
    assert "fee_pair" in str(amount_only.value)
    with pytest.raises(IntegrityError) as currency_only:
        _insert_contract(partner_id=partner_id, contract_no="CT-2", fee_currency="USD")
    assert "fee_pair" in str(currency_only.value)


def test_a_negative_fee_and_a_lowercase_currency_are_rejected() -> None:
    """음수 수수료·소문자 통화는 DB가 거부한다"""
    partner_id = _agency()
    with pytest.raises(IntegrityError) as negative:
        _insert_contract(partner_id=partner_id, fee_amount=-1, fee_currency="USD")
    assert "fee_amount_nonnegative" in str(negative.value)
    with pytest.raises(IntegrityError) as lowercase:
        _insert_contract(
            partner_id=partner_id, contract_no="CT-2", fee_amount=1, fee_currency="usd"
        )
    assert "fee_currency_uppercase" in str(lowercase.value)


def test_a_zero_fee_is_accepted() -> None:
    """수수료 0은 통과한다(무료 대행 — 경계). 미기재(NULL)와 다른 사실이다"""
    assert _insert_contract(fee_amount=0, fee_currency="KRW") > 0


def test_the_same_contract_number_for_one_agency_is_blocked_but_not_after_delete() -> None:
    """같은 대행사의 활성 계약 번호는 유일하다 — soft delete 뒤 재사용은 신규 (§17.4)"""
    partner_id = _agency()
    first = _insert_contract(partner_id=partner_id, contract_no="DUP")
    with pytest.raises(IntegrityError) as exc:
        _insert_contract(partner_id=partner_id, contract_no="DUP")
    assert "uq_agency_contracts_partner_id_contract_no_active" in str(exc.value)
    with unit_of_work() as uow:
        row = uow.session.get(AgencyContract, first)
        assert row is not None
        row.deleted_at = utcnow()
    assert _insert_contract(partner_id=partner_id, contract_no="DUP") > first


def test_the_same_contract_number_for_two_agencies_is_allowed() -> None:
    """계약 번호 유일성은 대행사별이다 — 서로 다른 대행사의 같은 번호는 통과"""
    first = _insert_contract(partner_id=_agency(), contract_no="SAME")
    other = create_partner("AGY-002", name_ko="다른 대행사", types=("CERT_AGENCY",))
    assert _insert_contract(partner_id=other, contract_no="SAME") > first


# ── 통신 기록 ──────────────────────────────────────────────────────────────


def test_the_subject_enumeration_is_locked_to_the_consumed_value() -> None:
    """주제 유형은 소비분만 — CERTIFICATION + SHIPMENT(S3-2 PR-4a 선적 통보 — ADR-0083). 알 수 없는 값은 DB가 거부한다 (ADR-0028)"""
    assert COMM_SUBJECT_TYPES == ("CERTIFICATION", "SHIPMENT")
    with pytest.raises(IntegrityError) as exc:
        _insert_comm_log(subject_type="FORWARDER")
    assert "subject_type_valid" in str(exc.value)
    # DB는 SHIPMENT를 허용한다(M15 CHECK 재정의)
    assert _insert_comm_log(subject_type="SHIPMENT") > 0


def test_the_generic_api_subjects_stay_certification_only() -> None:
    """R-05 — 범용 `/comm-logs`가 다루는 주제는 CERTIFICATION뿐이다(스키마 Literal·목록 기본 조건·id 접근·문서 첨부가 같은 상수를 본다).
    SHIPMENT 통보 기록은 선적 전용 통로만 만든다 — DB 허용(위 시험)과 범용 API 거부(e2e)를 둘로 나눠 고정한다"""
    from app.modules.collaboration.models import GENERIC_COMM_SUBJECT_TYPES
    from app.modules.collaboration.schemas import SubjectType

    assert GENERIC_COMM_SUBJECT_TYPES == ("CERTIFICATION",)
    assert set(SubjectType.__args__) == set(GENERIC_COMM_SUBJECT_TYPES)  # type: ignore[attr-defined]
    assert set(GENERIC_COMM_SUBJECT_TYPES) < set(COMM_SUBJECT_TYPES)


def test_a_blank_summary_is_rejected() -> None:
    """공백뿐인 요지는 DB가 거부한다"""
    with pytest.raises(IntegrityError) as exc:
        _insert_comm_log(summary="  ")
    assert "summary_not_blank" in str(exc.value)


def test_follow_up_dates_without_an_action_are_rejected() -> None:
    """다음 액션 없이 기한·완료일만 있으면 거부 — 주인 없는 날짜 금지"""
    with pytest.raises(IntegrityError) as due:
        _insert_comm_log(next_action_due=date(2026, 9, 10))
    assert "follow_up_requires_action" in str(due.value)
    with pytest.raises(IntegrityError) as done:
        _insert_comm_log(next_action_done_on=date(2026, 9, 10))
    assert "follow_up_requires_action" in str(done.value)


def test_follow_up_dates_cannot_precede_the_conversation() -> None:
    """기한·완료일은 오간 날보다 빠를 수 없다 — 당일은 통과(경계)"""
    with pytest.raises(IntegrityError) as due:
        _insert_comm_log(next_action="회신", next_action_due=date(2026, 8, 31))
    assert "due_after_occurred" in str(due.value)
    with pytest.raises(IntegrityError) as done:
        _insert_comm_log(next_action="회신", next_action_done_on=date(2026, 8, 31))
    assert "done_after_occurred" in str(done.value)
    assert (
        _insert_comm_log(
            next_action="회신",
            next_action_due=date(2026, 9, 1),
            next_action_done_on=date(2026, 9, 1),
        )
        > 0
    )


def test_a_blank_next_action_is_rejected() -> None:
    """공백뿐인 다음 액션은 거부 — NULL(없음)과 구분한다"""
    with pytest.raises(IntegrityError) as exc:
        _insert_comm_log(next_action=" ")
    assert "next_action_not_blank" in str(exc.value)


# ── certifications — 대행 협업 컬럼 (수기 CHECK 4건) ─────────────────────────


def _cert() -> int:
    template_id = create_requirement_template("US")
    return create_certification_instance(template_id, "SKU", create_sku(), status="NOT_STARTED")


def _update_cert(cert_id: int, **values: Any) -> None:
    with unit_of_work() as uow:
        row = uow.session.get(Certification, cert_id)
        assert row is not None
        for key, value in values.items():
            setattr(row, key, value)
        uow.session.flush()


def test_new_columns_default_to_direct_and_internal() -> None:
    """기존·신규 행의 기본값은 직접 처리·사내 공이다 (server_default)"""
    cert_id = _cert()
    with unit_of_work() as uow:
        row = uow.session.get(Certification, cert_id)
        assert row is not None
        assert (row.handling_mode, row.action_owner) == ("DIRECT", "INTERNAL")
        assert row.agency_partner_id is None and row.action_owner_changed_on is None


def test_the_enumerations_match_the_documented_values() -> None:
    """열거 정의가 문서(ADR-0048)와 같다 — 값 추가는 마이그레이션 사건이다"""
    assert HANDLING_MODES == ("DIRECT", "AGENCY")
    assert ACTION_OWNERS == ("INTERNAL", "AGENCY", "AUTHORITY")


def test_unknown_handling_mode_and_action_owner_are_rejected() -> None:
    """알 수 없는 처리방식·액션 주체는 DB가 거부한다"""
    cert_id = _cert()
    with pytest.raises(IntegrityError) as mode:
        _update_cert(cert_id, handling_mode="OTHER")
    assert "handling_mode_valid" in str(mode.value)
    with pytest.raises(IntegrityError) as owner:
        _update_cert(cert_id, action_owner="CUSTOMER")
    assert "action_owner_valid" in str(owner.value)


def test_agency_handling_requires_an_agency_and_direct_forbids_one() -> None:
    """(처리방식=대행) ⇔ (대행사 지정) — 양방향으로 DB가 거부한다"""
    cert_id = _cert()
    agency = _agency()
    with pytest.raises(IntegrityError) as missing:
        _update_cert(cert_id, handling_mode="AGENCY")
    assert "handling_agency_pair" in str(missing.value)
    with pytest.raises(IntegrityError) as extra:
        _update_cert(cert_id, agency_partner_id=agency)
    assert "handling_agency_pair" in str(extra.value)
    _update_cert(cert_id, handling_mode="AGENCY", agency_partner_id=agency)


def test_the_ball_cannot_be_with_an_agency_unless_the_certification_is_agency_handled() -> None:
    """공이 대행사에 있으려면 대행 처리여야 한다 — 직접 처리+대행사 공은 거부"""
    cert_id = _cert()
    with pytest.raises(IntegrityError) as exc:
        _update_cert(cert_id, action_owner="AGENCY")
    assert "agency_owner_requires_agency" in str(exc.value)
    _update_cert(cert_id, action_owner="AUTHORITY")  # 기관 공은 직접 처리에서도 성립


def test_an_agency_partner_cannot_be_hard_deleted_while_referenced() -> None:
    """대행사 거래처를 참조하는 인증이 있으면 FK RESTRICT — 하드 삭제 불가"""
    cert_id = _cert()
    agency = _agency()
    _update_cert(cert_id, handling_mode="AGENCY", agency_partner_id=agency)
    with pytest.raises(IntegrityError) as exc, unit_of_work() as uow:
        uow.session.execute(
            text("DELETE FROM partner_type_links WHERE partner_id = :p"), {"p": agency}
        )
        uow.session.execute(text("DELETE FROM partners WHERE id = :p"), {"p": agency})
    assert "fk_certifications_agency_partner_id_partners" in str(exc.value)


# ── documents 소유 열거 — COMM_LOG ─────────────────────────────────────────


def _seed_type_id() -> int:
    with unit_of_work() as uow:
        return uow.session.execute(
            select(DocumentType.id).where(
                DocumentType.code == "CERTIFICATE", DocumentType.deleted_at.is_(None)
            )
        ).scalar_one()


def test_a_comm_log_owned_document_is_accepted_by_the_database() -> None:
    """documents.owner_type에 COMM_LOG가 실재한다 — 통신 기록 첨부의 전제"""
    with unit_of_work() as uow:
        row = Document(
            owner_type="COMM_LOG",
            owner_id=1,
            document_type_id=_seed_type_id(),
            storage_kind="LINK",
            url="https://example.com/x.pdf",
        )
        uow.session.add(row)
        uow.session.flush()
        assert row.id > 0
    assert "COMM_LOG" in DOCUMENT_OWNER_TYPES


def test_documents_owner_check_definition_names_every_model_value() -> None:
    """owner_type CHECK 정의문에 모델 열거 전건이 실재한다 (마이그레이션 1df4a398d38a)"""
    with unit_of_work() as uow:
        definition = uow.session.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'ck_documents_owner_type_valid'"
            )
        ).scalar_one()
    for code in DOCUMENT_OWNER_TYPES:
        assert f"'{code}'" in definition
