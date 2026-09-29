"""C·K. 대행 협업 API — 대행 계약·통신 기록·인증 대행 필드 (§5.4 / S2-4 PR-1).

★ 이 파일의 몫: 실 HTTP로 등록→수정→낙관 잠금 409→목록 페이지 계약→권한·멱등을 본다.
  검증 규칙 전수(경계·검증 순서)는 이 파일이 실 API로, DB 불변식은
  tests/integration/test_collaboration_constraints.py가 맡는다.
"""

from __future__ import annotations

import itertools
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.time import today_kst
from app.main import app
from app.modules.identity.models import RoleCode
from tests.support.factories import (
    DEFAULT_PASSWORD,
    create_certification_instance,
    create_link_document,
    create_partner,
    create_requirement_template,
    create_sku,
    create_user,
)

pytestmark = [pytest.mark.group_c, pytest.mark.group_k]

LOGIN = "/api/v1/auth/login"
CONTRACTS = "/api/v1/agency-contracts"
COMM_LOGS = "/api/v1/comm-logs"
CERTIFICATIONS = "/api/v1/certifications"


def _client(email: str, *roles: RoleCode) -> Iterator[TestClient]:
    create_user(email, roles=roles)
    with TestClient(app) as client:
        response = client.post(LOGIN, json={"email": email, "password": DEFAULT_PASSWORD})
        assert response.status_code == 200, response.text
        yield client


@pytest.fixture
def cert() -> Iterator[TestClient]:
    yield from _client("cert-collab@example.com", RoleCode.CERT)


@pytest.fixture
def trader() -> Iterator[TestClient]:
    yield from _client("trade-collab@example.com", RoleCode.TRADE)


@pytest.fixture
def viewer() -> Iterator[TestClient]:
    yield from _client("viewer-collab@example.com", RoleCode.VIEWER)


def _agency(code: str = "AGY-001", name: str = "테스트 대행사") -> int:
    return create_partner(code, name_ko=name, types=("CERT_AGENCY",))


def _key(counter: list[int] = [0]) -> dict[str, str]:  # noqa: B006 — 테스트 내 단순 시퀀스
    counter[0] += 1
    return {"Idempotency-Key": f"collab-{counter[0]}"}


def _contract(client: TestClient, partner_id: int, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "partner_id": partner_id,
        "contract_no": "CT-001",
        "start_on": (today_kst() - timedelta(days=10)).isoformat(),
    }
    payload.update(overrides)
    response = client.post(CONTRACTS, json=payload, headers=_key())
    assert response.status_code == 201, response.text
    return response.json()


_SEQUENCE = itertools.count(1)


def _certification() -> int:
    """PREPARING 인증 1건 — 호출마다 요건명·SKU 코드가 달라 한 테스트가 여러 건을 만들 수 있다."""
    number = next(_SEQUENCE)
    template_id = create_requirement_template("US", name=f"요건-{number}")
    return create_certification_instance(
        template_id, "SKU", create_sku(f"SKU-C{number}"), status="PREPARING"
    )


def _log(client: TestClient, certification_id: int, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "subject_type": "CERTIFICATION",
        "subject_id": certification_id,
        "occurred_on": today_kst().isoformat(),
        "summary": "서류 보완 요청 메일 수신",
    }
    payload.update(overrides)
    response = client.post(COMM_LOGS, json=payload, headers=_key())
    assert response.status_code == 201, response.text
    return response.json()


def _errors(response: Any) -> dict[str, Any]:
    return response.json()["error"]["detail"]


# ═══ 대행 계약 ═══════════════════════════════════════════════════════════════


def test_registering_a_contract_returns_minor_units_and_the_computed_current_flag(
    cert: TestClient,
) -> None:
    """수수료는 최소단위로 응답하고(USD 12.34→1234) 유효 여부는 계산값이다"""
    body = _contract(cert, _agency(), fee="12.34", fee_currency="usd", scope_note="건당")
    assert body["fee_amount"] == 1234 and body["fee_currency"] == "USD"
    assert body["is_current"] is True
    assert body["partner_name"] == "테스트 대행사"
    assert body["version"] == 1


def test_a_zero_decimal_currency_keeps_whole_units(cert: TestClient) -> None:
    """KRW는 소수 자릿수 0 — 5000은 5000이다 (자릿수의 출처는 서버 통화표)"""
    assert _contract(cert, _agency(), fee="5000", fee_currency="KRW")["fee_amount"] == 5000


def test_a_partner_without_the_agency_type_cannot_hold_a_contract(cert: TestClient) -> None:
    """인증대행 유형이 아닌 거래처는 422 — 안내 문구가 유형 추가를 알려 준다"""
    supplier = create_partner("SUP-001", name_ko="공급사", types=("SUPPLIER",))
    response = cert.post(
        CONTRACTS,
        json={"partner_id": supplier, "contract_no": "X", "start_on": "2026-01-01"},
        headers=_key(),
    )
    assert response.status_code == 422
    assert "인증대행" in _errors(response)["partner_id"]


def test_an_unknown_partner_is_a_field_error_not_a_500(cert: TestClient) -> None:
    """없는 거래처 id는 필드 422다"""
    response = cert.post(
        CONTRACTS,
        json={"partner_id": 999999, "contract_no": "X", "start_on": "2026-01-01"},
        headers=_key(),
    )
    assert response.status_code == 422
    assert "partner_id" in _errors(response)


def test_a_duplicate_contract_number_for_the_same_agency_is_a_friendly_422(
    cert: TestClient,
) -> None:
    """같은 대행사의 같은 계약 번호는 DB 예외가 아니라 필드 안내 422다"""
    partner_id = _agency()
    _contract(cert, partner_id, contract_no="DUP")
    response = cert.post(
        CONTRACTS,
        json={"partner_id": partner_id, "contract_no": "DUP", "start_on": "2026-01-01"},
        headers=_key(),
    )
    assert response.status_code == 422
    assert "contract_no" in _errors(response)


def test_an_end_date_before_the_start_date_is_rejected(cert: TestClient) -> None:
    """종료일 < 시작일은 422 — 같은 날은 통과(경계)"""
    partner_id = _agency()
    bad = cert.post(
        CONTRACTS,
        json={
            "partner_id": partner_id,
            "contract_no": "A",
            "start_on": "2026-05-02",
            "end_on": "2026-05-01",
        },
        headers=_key(),
    )
    assert bad.status_code == 422 and "end_on" in _errors(bad)
    same = cert.post(
        CONTRACTS,
        json={
            "partner_id": partner_id,
            "contract_no": "B",
            "start_on": "2026-05-01",
            "end_on": "2026-05-01",
        },
        headers=_key(),
    )
    assert same.status_code == 201


@pytest.mark.parametrize(
    ("fee", "currency", "field"),
    [
        ("100", None, "fee"),  # 금액만
        (None, "USD", "fee"),  # 통화만
        ("1.234", "USD", "fee"),  # USD는 소수 2자리까지
        ("1.5", "KRW", "fee"),  # KRW는 소수 없음
        ("100", "ZZZ", "fee_currency"),  # 미등록 통화
    ],
)
def test_fee_and_currency_are_validated_as_a_pair(
    cert: TestClient, fee: str | None, currency: str | None, field: str
) -> None:
    """수수료는 통화와 한 쌍이고 자릿수·통화 목록의 출처는 서버다 — 위반은 필드 422"""
    payload: dict[str, Any] = {
        "partner_id": _agency(),
        "contract_no": "PAIR",
        "start_on": "2026-01-01",
    }
    if fee is not None:
        payload["fee"] = fee
    if currency is not None:
        payload["fee_currency"] = currency
    response = cert.post(CONTRACTS, json=payload, headers=_key())
    assert response.status_code == 422
    assert field in _errors(response)


def test_a_negative_fee_is_rejected(cert: TestClient) -> None:
    """음수 수수료는 스키마 422"""
    response = cert.post(
        CONTRACTS,
        json={
            "partner_id": _agency(),
            "contract_no": "NEG",
            "start_on": "2026-01-01",
            "fee": "-1",
            "fee_currency": "USD",
        },
        headers=_key(),
    )
    assert response.status_code == 422


def test_the_current_flag_follows_the_contract_period_boundaries(cert: TestClient) -> None:
    """is_current — 시작일·종료일 당일 포함, 시작 전·종료 후·미정 종료 (경계 전수)"""
    today = today_kst()
    partner_id = _agency()

    def flag(number: str, start_offset: int, end_offset: int | None) -> bool:
        body = _contract(
            cert,
            partner_id,
            contract_no=number,
            start_on=(today + timedelta(days=start_offset)).isoformat(),
            end_on=None if end_offset is None else (today + timedelta(days=end_offset)).isoformat(),
        )
        return bool(body["is_current"])

    assert flag("starts-today", 0, 30) is True
    assert flag("ends-today", -30, 0) is True
    assert flag("starts-tomorrow", 1, 30) is False
    assert flag("ended-yesterday", -30, -1) is False
    assert flag("open-ended", -30, None) is True


def test_a_blank_contract_number_is_a_field_error_not_a_duplicate_message(
    cert: TestClient,
) -> None:
    """공백뿐인 계약 번호는 "번호를 입력" 안내 — DB CHECK 위반이 "이미 등록된 번호"로 오진되지 않는다"""
    partner_id = _agency()
    created = _contract(cert, partner_id, contract_no="REAL")
    blank = cert.post(
        CONTRACTS,
        json={"partner_id": partner_id, "contract_no": "   ", "start_on": "2026-01-01"},
        headers=_key(),
    )
    assert blank.status_code == 422
    assert "입력" in _errors(blank)["contract_no"] and "이미" not in _errors(blank)["contract_no"]
    patched = cert.patch(f"{CONTRACTS}/{created['id']}", json={"version": 1, "contract_no": "  "})
    assert patched.status_code == 422 and "입력" in _errors(patched)["contract_no"]


def test_free_text_fields_have_a_length_ceiling(cert: TestClient) -> None:
    """범위·메모는 2000자까지 — 무제한 텍스트로 응답·로그가 부풀지 않게 한다"""
    partner_id = _agency()
    too_long = "가" * 2001
    for field in ("scope_note", "note"):
        response = cert.post(
            CONTRACTS,
            json={
                "partner_id": partner_id,
                "contract_no": f"LONG-{field}",
                "start_on": "2026-01-01",
                field: too_long,
            },
            headers=_key(),
        )
        assert response.status_code == 422, field


def test_patching_a_contract_needs_the_current_version(cert: TestClient) -> None:
    """낙관 잠금 — 오래된 version은 409, 성공하면 version이 오른다"""
    created = _contract(cert, _agency())
    stale = cert.patch(f"{CONTRACTS}/{created['id']}", json={"version": 99, "note": "x"})
    assert stale.status_code == 409
    ok = cert.patch(f"{CONTRACTS}/{created['id']}", json={"version": 1, "note": "갱신"})
    assert ok.status_code == 200
    assert ok.json()["note"] == "갱신" and ok.json()["version"] == 2


def test_the_agency_of_a_contract_cannot_be_changed(cert: TestClient) -> None:
    """partner_id는 수정 스키마에 없다 — 계약의 정체성(extra=forbid로 422)"""
    created = _contract(cert, _agency())
    response = cert.patch(
        f"{CONTRACTS}/{created['id']}", json={"version": 1, "partner_id": _agency("AGY-2", "다른")}
    )
    assert response.status_code == 422


def test_patch_leaves_unsent_fields_alone_and_rejects_explicit_null_for_required_ones(
    cert: TestClient,
) -> None:
    """exclude_unset — 안 보낸 필드 불변, 계약 번호·시작일의 명시 null은 422"""
    created = _contract(cert, _agency(), fee="10", fee_currency="USD", note="원본")
    ok = cert.patch(f"{CONTRACTS}/{created['id']}", json={"version": 1, "scope_note": "범위"})
    assert ok.json()["fee_amount"] == 1000 and ok.json()["note"] == "원본"
    for field in ("contract_no", "start_on"):
        bad = cert.patch(f"{CONTRACTS}/{created['id']}", json={"version": 2, field: None})
        assert bad.status_code == 422, field


def test_patching_the_period_is_checked_against_the_stored_other_end(cert: TestClient) -> None:
    """시작일만 보내도 저장된 종료일과 순서를 검증한다 — 한쪽만 바꿔 역전시킬 수 없다"""
    today = today_kst()
    created = _contract(
        cert,
        _agency(),
        start_on=(today - timedelta(days=5)).isoformat(),
        end_on=(today + timedelta(days=5)).isoformat(),
    )
    response = cert.patch(
        f"{CONTRACTS}/{created['id']}",
        json={"version": 1, "start_on": (today + timedelta(days=6)).isoformat()},
    )
    assert response.status_code == 422 and "end_on" in _errors(response)


def test_the_fee_can_be_cleared_as_a_pair(cert: TestClient) -> None:
    """수수료·통화를 함께 null로 보내면 미기재로 되돌린다"""
    created = _contract(cert, _agency(), fee="10", fee_currency="USD")
    cleared = cert.patch(
        f"{CONTRACTS}/{created['id']}", json={"version": 1, "fee": None, "fee_currency": None}
    )
    assert cleared.status_code == 200
    assert cleared.json()["fee_amount"] is None and cleared.json()["fee_currency"] is None


def test_the_contract_list_is_a_paged_envelope_and_filters_by_agency_and_currency_flag(
    cert: TestClient,
) -> None:
    """목록은 페이지 봉투(기본 50)이고 partner_id·current_only로 좁혀진다"""
    today = today_kst()
    first, second = _agency("AGY-A", "가 대행사"), _agency("AGY-B", "나 대행사")
    _contract(cert, first, contract_no="A-old", end_on=(today - timedelta(days=1)).isoformat())
    _contract(cert, first, contract_no="A-new")
    _contract(cert, second, contract_no="B-1")
    everything = cert.get(CONTRACTS)
    assert everything.status_code == 200
    page = everything.json()
    assert (page["total"], page["page"], page["size"]) == (3, 1, 50)
    only_first = cert.get(CONTRACTS, params={"partner_id": first}).json()
    assert only_first["total"] == 2
    current = cert.get(CONTRACTS, params={"current_only": "true"}).json()
    assert {item["contract_no"] for item in current["items"]} == {"A-new", "B-1"}


def test_current_only_is_inclusive_of_the_end_date_and_excludes_future_starts(
    cert: TestClient,
) -> None:
    """current_only — 종료일 당일은 포함, 내일 시작 계약은 제외(경계) — is_current와 같은 정의"""
    today = today_kst()
    partner_id = _agency()
    _contract(
        cert,
        partner_id,
        contract_no="ENDS-TODAY",
        start_on=(today - timedelta(days=5)).isoformat(),
        end_on=today.isoformat(),
    )
    _contract(
        cert,
        partner_id,
        contract_no="STARTS-TOMORROW",
        start_on=(today + timedelta(days=1)).isoformat(),
    )
    current = cert.get(CONTRACTS, params={"current_only": "true"}).json()
    assert {item["contract_no"] for item in current["items"]} == {"ENDS-TODAY"}


def test_the_contract_list_rejects_oversized_pages(cert: TestClient) -> None:
    """size 상한(200) 초과는 422 — 무페이지네이션 금지(§18.4)"""
    assert cert.get(CONTRACTS, params={"size": 201}).status_code == 422


def test_deleting_a_contract_hides_it_and_frees_the_number(cert: TestClient) -> None:
    """삭제는 soft delete — 조회 404, 같은 번호는 신규로 재등록된다"""
    partner_id = _agency()
    created = _contract(cert, partner_id, contract_no="REUSE")
    assert cert.delete(f"{CONTRACTS}/{created['id']}").status_code == 204
    assert cert.get(f"{CONTRACTS}/{created['id']}").status_code == 404
    again = _contract(cert, partner_id, contract_no="REUSE")
    assert again["id"] != created["id"]


def test_contract_registration_is_idempotent(cert: TestClient) -> None:
    """같은 키+같은 본문은 같은 응답을 재생하고(행 1개), 같은 키+다른 본문은 409"""
    partner_id = _agency()
    payload = {"partner_id": partner_id, "contract_no": "IDEM", "start_on": "2026-01-01"}
    headers = {"Idempotency-Key": "collab-contract-idem"}
    first = cert.post(CONTRACTS, json=payload, headers=headers)
    replay = cert.post(CONTRACTS, json=payload, headers=headers)
    assert (first.status_code, replay.status_code) == (201, 201)
    assert first.json() == replay.json()
    assert cert.get(CONTRACTS).json()["total"] == 1
    other = cert.post(CONTRACTS, json={**payload, "contract_no": "OTHER"}, headers=headers)
    assert other.status_code == 409


def test_contract_writes_are_denied_to_non_certification_roles(
    trader: TestClient, viewer: TestClient, cert: TestClient
) -> None:
    """편집=인증+관리자, 열람=전 역할 — 무역·조회는 쓰기 403, 읽기 200"""
    partner_id = _agency()
    created = _contract(cert, partner_id)
    for client in (trader, viewer):
        assert client.get(CONTRACTS).status_code == 200
        assert client.get(f"{CONTRACTS}/{created['id']}").status_code == 200
        post = client.post(
            CONTRACTS,
            json={"partner_id": partner_id, "contract_no": "Z", "start_on": "2026-01-01"},
            headers=_key(),
        )
        assert post.status_code == 403
        assert client.patch(f"{CONTRACTS}/{created['id']}", json={"version": 1}).status_code == 403
        assert client.delete(f"{CONTRACTS}/{created['id']}").status_code == 403


# ═══ 통신 기록 ═══════════════════════════════════════════════════════════════


def test_registering_a_comm_log_returns_labels_and_computed_flags(cert: TestClient) -> None:
    """등록 — 주제 표기(#id · 요건명)·계산 플래그·입력 시각·첨부 0"""
    certification_id = _certification()
    body = _log(
        cert,
        certification_id,
        next_action="샘플 발송",
        next_action_due=(today_kst() + timedelta(days=3)).isoformat(),
    )
    assert body["subject_label"] == f"#{certification_id} · 스냅샷"
    assert body["follow_up_open"] is True and body["follow_up_overdue"] is False
    assert body["attachment_count"] == 0
    assert body["created_at"].endswith("+00:00")  # UTC 저장 — 표시는 화면이 KST로
    assert body["version"] == 1


def test_a_log_without_a_next_action_has_no_follow_up(cert: TestClient) -> None:
    """다음 액션이 없으면 미완료도 도과도 아니다"""
    body = _log(cert, _certification())
    assert body["follow_up_open"] is False and body["follow_up_overdue"] is False


def test_an_overdue_follow_up_is_flagged(cert: TestClient) -> None:
    """기한이 어제인 미완료 다음 액션은 도과 — 오늘 기한은 도과가 아니다(경계)"""
    certification_id = _certification()
    today = today_kst()
    past = _log(
        cert,
        certification_id,
        occurred_on=(today - timedelta(days=5)).isoformat(),
        next_action="회신",
        next_action_due=(today - timedelta(days=1)).isoformat(),
    )
    due_today = _log(
        cert,
        certification_id,
        occurred_on=(today - timedelta(days=5)).isoformat(),
        next_action="회신",
        next_action_due=today.isoformat(),
    )
    assert past["follow_up_overdue"] is True
    assert due_today["follow_up_overdue"] is False and due_today["follow_up_open"] is True


def test_the_subject_must_exist(cert: TestClient) -> None:
    """없는 인증·삭제된 인증에는 기록할 수 없다 — 폴리모픽 실재 검증은 서비스 몫(FK 없음)"""
    response = cert.post(
        COMM_LOGS,
        json={
            "subject_type": "CERTIFICATION",
            "subject_id": 999999,
            "occurred_on": today_kst().isoformat(),
            "summary": "x",
        },
        headers=_key(),
    )
    assert response.status_code == 422 and "subject_id" in _errors(response)


def test_unknown_subject_types_are_rejected_at_the_schema(cert: TestClient) -> None:
    """소비분 밖 주제 유형(포워더·선적 등)은 스키마 422 — 열거는 소비 세션이 확장한다"""
    response = cert.post(
        COMM_LOGS,
        json={
            "subject_type": "SHIPMENT",
            "subject_id": 1,
            "occurred_on": today_kst().isoformat(),
            "summary": "x",
        },
        headers=_key(),
    )
    assert response.status_code == 422


def test_an_unknown_counterparty_is_a_field_error(cert: TestClient) -> None:
    """없는 상대 거래처는 필드 422 — 유형 제한은 없다(대행사 외 상대도 기록한다)"""
    certification_id = _certification()
    bad = cert.post(
        COMM_LOGS,
        json={
            "subject_type": "CERTIFICATION",
            "subject_id": certification_id,
            "partner_id": 999999,
            "occurred_on": today_kst().isoformat(),
            "summary": "x",
        },
        headers=_key(),
    )
    assert bad.status_code == 422 and "partner_id" in _errors(bad)
    supplier = create_partner("SUP-9", name_ko="공급사", types=("SUPPLIER",))
    ok = _log(cert, certification_id, partner_id=supplier)
    assert ok["partner_name"] == "공급사"


def test_dates_are_validated_against_the_conversation_and_today(cert: TestClient) -> None:
    """미래 오간 날·기한 < 오간 날·기한만 있고 액션 없음 — 각각 필드 422 (KST 오늘 기준)"""
    certification_id = _certification()
    tomorrow = (today_kst() + timedelta(days=1)).isoformat()
    today = today_kst().isoformat()
    yesterday = (today_kst() - timedelta(days=1)).isoformat()

    def attempt(**overrides: Any) -> Any:
        return cert.post(
            COMM_LOGS,
            json={
                "subject_type": "CERTIFICATION",
                "subject_id": certification_id,
                "occurred_on": today,
                "summary": "x",
                **overrides,
            },
            headers=_key(),
        )

    future = attempt(occurred_on=tomorrow)
    assert future.status_code == 422 and "occurred_on" in _errors(future)
    early_due = attempt(next_action="a", next_action_due=yesterday)
    assert early_due.status_code == 422 and "next_action_due" in _errors(early_due)
    orphan_due = attempt(next_action_due=today)
    assert orphan_due.status_code == 422 and "next_action" in _errors(orphan_due)
    assert attempt(next_action="a", next_action_due=today).status_code == 201


def test_a_blank_summary_is_rejected(cert: TestClient) -> None:
    """공백뿐인 요지는 422(스키마 min_length는 공백을 못 거르므로 서비스가 거른다)"""
    response = cert.post(
        COMM_LOGS,
        json={
            "subject_type": "CERTIFICATION",
            "subject_id": _certification(),
            "occurred_on": today_kst().isoformat(),
            "summary": "   ",
        },
        headers=_key(),
    )
    assert response.status_code == 422 and "summary" in _errors(response)


def test_completing_and_reopening_a_follow_up(cert: TestClient) -> None:
    """완료 처리=done_on 날짜(open 해제), null이면 재개 — version 필수"""
    log = _log(
        cert,
        _certification(),
        next_action="회신",
        next_action_due=(today_kst() + timedelta(days=2)).isoformat(),
    )
    done = cert.patch(
        f"{COMM_LOGS}/{log['id']}",
        json={"version": 1, "next_action_done_on": today_kst().isoformat()},
    )
    assert done.status_code == 200
    assert done.json()["follow_up_open"] is False and done.json()["version"] == 2
    reopened = cert.patch(
        f"{COMM_LOGS}/{log['id']}", json={"version": 2, "next_action_done_on": None}
    )
    assert reopened.json()["follow_up_open"] is True


def test_completion_cannot_be_in_the_future_or_before_the_conversation(cert: TestClient) -> None:
    """완료일은 오간 날 이상·오늘 이하 — 경계 위반은 필드 422"""
    today = today_kst()
    log = _log(
        cert,
        _certification(),
        occurred_on=(today - timedelta(days=3)).isoformat(),
        next_action="회신",
    )
    early = cert.patch(
        f"{COMM_LOGS}/{log['id']}",
        json={"version": 1, "next_action_done_on": (today - timedelta(days=4)).isoformat()},
    )
    assert early.status_code == 422 and "next_action_done_on" in _errors(early)
    future = cert.patch(
        f"{COMM_LOGS}/{log['id']}",
        json={"version": 1, "next_action_done_on": (today + timedelta(days=1)).isoformat()},
    )
    assert future.status_code == 422 and "next_action_done_on" in _errors(future)


def test_clearing_the_next_action_clears_its_dates(cert: TestClient) -> None:
    """다음 액션을 null로 지우면 기한·완료일도 함께 지워진다 — 주인 없는 날짜 금지"""
    log = _log(
        cert,
        _certification(),
        next_action="회신",
        next_action_due=(today_kst() + timedelta(days=2)).isoformat(),
    )
    cleared = cert.patch(f"{COMM_LOG_URL(log)}", json={"version": 1, "next_action": None})
    assert cleared.status_code == 200
    body = cleared.json()
    assert body["next_action"] is None and body["next_action_due"] is None
    assert body["follow_up_open"] is False


def COMM_LOG_URL(log: dict[str, Any]) -> str:
    return f"{COMM_LOGS}/{log['id']}"


def test_clearing_the_action_while_sending_a_new_due_date_is_rejected(cert: TestClient) -> None:
    """액션은 지우면서 기한을 새로 주는 모순 요청은 422"""
    log = _log(cert, _certification(), next_action="회신")
    response = cert.patch(
        COMM_LOG_URL(log),
        json={
            "version": 1,
            "next_action": None,
            "next_action_due": today_kst().isoformat(),
        },
    )
    assert response.status_code == 422


def test_comm_log_patch_is_optimistic_and_rejects_null_required_fields(cert: TestClient) -> None:
    """낙관 잠금 409 · 오간 날·요지의 명시 null은 422 · 수정으로 요지가 바뀐다"""
    log = _log(cert, _certification())
    assert cert.patch(COMM_LOG_URL(log), json={"version": 9, "summary": "y"}).status_code == 409
    for field in ("summary", "occurred_on"):
        assert cert.patch(COMM_LOG_URL(log), json={"version": 1, field: None}).status_code == 422
    ok = cert.patch(COMM_LOG_URL(log), json={"version": 1, "summary": "정정한 요지"})
    assert ok.json()["summary"] == "정정한 요지"


def test_the_comm_log_list_filters_orders_and_counts_attachments(cert: TestClient) -> None:
    """목록 — 주제 필터·open_only·최근 오간 날 순, 첨부 건수는 COMM_LOG 소유 문서의 활성 건수"""
    certification_id, other = _certification(), _certification()
    today = today_kst()
    older = _log(cert, certification_id, occurred_on=(today - timedelta(days=2)).isoformat())
    newer = _log(cert, certification_id, next_action="회신")
    _log(cert, other)
    create_link_document(
        older["id"], valid_until=None, owner_type="COMM_LOG", document_type="CERTIFICATE"
    )
    create_link_document(
        older["id"], valid_until=None, owner_type="COMM_LOG", document_type="CFS", tag="b"
    )
    listing = cert.get(
        COMM_LOGS, params={"subject_type": "CERTIFICATION", "subject_id": certification_id}
    )
    items = listing.json()["items"]
    assert [item["id"] for item in items] == [newer["id"], older["id"]]  # 최근 오간 날이 먼저
    assert items[1]["attachment_count"] == 2 and items[0]["attachment_count"] == 0
    open_only = cert.get(COMM_LOGS, params={"open_only": "true"}).json()
    assert [item["id"] for item in open_only["items"]] == [newer["id"]]


def test_soft_deleted_attachments_are_not_counted(cert: TestClient) -> None:
    """첨부 건수는 활성 문서만 센다 — 삭제된 첨부가 남으면 건수와 목록이 어긋난다"""
    from app.core.db.uow import unit_of_work
    from app.core.time import utcnow
    from app.modules.documents.models import Document

    log = _log(cert, _certification())
    create_link_document(log["id"], valid_until=None, owner_type="COMM_LOG", document_type="CFS")
    gone = create_link_document(
        log["id"], valid_until=None, owner_type="COMM_LOG", document_type="GMP", tag="gone"
    )
    with unit_of_work() as uow:
        row = uow.session.get(Document, gone)
        assert row is not None
        row.deleted_at = utcnow()
    assert cert.get(COMM_LOG_URL(log)).json()["attachment_count"] == 1


def test_deleting_a_comm_log_hides_it(cert: TestClient) -> None:
    """삭제는 soft delete — 상세 404·목록 제외"""
    certification_id = _certification()
    log = _log(cert, certification_id)
    assert cert.delete(COMM_LOG_URL(log)).status_code == 204
    assert cert.get(COMM_LOG_URL(log)).status_code == 404
    assert cert.get(COMM_LOGS, params={"subject_id": certification_id}).json()["total"] == 0


def test_comm_log_registration_is_idempotent(cert: TestClient) -> None:
    """같은 키+같은 본문 → 같은 응답(행 1개), 같은 키+다른 본문 → 409"""
    certification_id = _certification()
    payload = {
        "subject_type": "CERTIFICATION",
        "subject_id": certification_id,
        "occurred_on": today_kst().isoformat(),
        "summary": "멱등",
    }
    headers = {"Idempotency-Key": "collab-log-idem"}
    first = cert.post(COMM_LOGS, json=payload, headers=headers)
    replay = cert.post(COMM_LOGS, json=payload, headers=headers)
    assert first.json() == replay.json()
    assert cert.get(COMM_LOGS, params={"subject_id": certification_id}).json()["total"] == 1
    assert (
        cert.post(COMM_LOGS, json={**payload, "summary": "다름"}, headers=headers).status_code
        == 409
    )


def test_comm_log_writes_are_denied_to_non_certification_roles(
    trader: TestClient, viewer: TestClient, cert: TestClient
) -> None:
    """무역·조회는 통신 기록 읽기만 — 쓰기 403"""
    certification_id = _certification()
    log = _log(cert, certification_id)
    for client in (trader, viewer):
        assert client.get(COMM_LOGS).status_code == 200
        post = client.post(
            COMM_LOGS,
            json={
                "subject_type": "CERTIFICATION",
                "subject_id": certification_id,
                "occurred_on": today_kst().isoformat(),
                "summary": "x",
            },
            headers=_key(),
        )
        assert post.status_code == 403
        assert (
            client.patch(COMM_LOG_URL(log), json={"version": 1, "summary": "y"}).status_code == 403
        )
        assert client.delete(COMM_LOG_URL(log)).status_code == 403


def test_a_comm_log_can_own_a_document_through_the_documents_api(cert: TestClient) -> None:
    """문서 보관소 API로 통신 기록에 링크 첨부가 붙는다(소유 열거 COMM_LOG) — 없는 기록엔 422"""
    log = _log(cert, _certification())
    created = cert.post(
        "/api/v1/documents/links",
        json={
            "owner_type": "COMM_LOG",
            "owner_id": log["id"],
            "document_type": "CERTIFICATE",
            "url": "https://example.com/reply.pdf",
        },
        headers=_key(),
    )
    assert created.status_code == 201, created.text
    assert created.json()["owner_display"].startswith(f"통신 기록 #{log['id']}")
    assert cert.get(COMM_LOG_URL(log)).json()["attachment_count"] == 1
    missing = cert.post(
        "/api/v1/documents/links",
        json={
            "owner_type": "COMM_LOG",
            "owner_id": 999999,
            "document_type": "CERTIFICATE",
            "url": "https://example.com/x.pdf",
        },
        headers=_key(),
    )
    assert missing.status_code == 422 and "owner_id" in _errors(missing)


# ═══ 인증 인스턴스 — 대행 협업 필드 ═══════════════════════════════════════════


def _certification_url(certification_id: int) -> str:
    return f"{CERTIFICATIONS}/{certification_id}"


def _patch_cert(client: TestClient, certification_id: int, version: int, **fields: Any) -> Any:
    return client.patch(_certification_url(certification_id), json={"version": version, **fields})


def test_new_certifications_are_direct_and_internal_by_default(cert: TestClient) -> None:
    """기본값 — 직접 처리·사내 공·대행사 없음(기존 행도 동일하게 읽힌다)"""
    body = cert.get(_certification_url(_certification())).json()
    assert (body["handling_mode"], body["action_owner"]) == ("DIRECT", "INTERNAL")
    assert body["agency_partner_id"] is None and body["agency_partner_name"] is None
    assert body["action_owner_changed_on"] is None


def test_switching_to_agency_handling_records_the_agency_and_the_ball_date(
    cert: TestClient,
) -> None:
    """대행 전환 — 대행사명 표시·공이 넘어간 날은 서버가 오늘(KST)로 채운다"""
    certification_id = _certification()
    agency = _agency()
    response = _patch_cert(
        cert,
        certification_id,
        1,
        handling_mode="AGENCY",
        agency_partner_id=agency,
        action_owner="AGENCY",
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["handling_mode"] == "AGENCY" and body["agency_partner_name"] == "테스트 대행사"
    assert body["action_owner"] == "AGENCY"
    assert body["action_owner_changed_on"] == today_kst().isoformat()


def test_a_backdated_ball_date_wins_and_a_future_one_is_rejected(cert: TestClient) -> None:
    """소급 입력 — 명시한 날짜가 이기고 미래 날짜는 422 (§21 날짜 규율)"""
    certification_id = _certification()
    back = (today_kst() - timedelta(days=9)).isoformat()
    ok = _patch_cert(
        cert, certification_id, 1, action_owner="AUTHORITY", action_owner_changed_on=back
    )
    assert ok.json()["action_owner_changed_on"] == back
    future = _patch_cert(
        cert,
        certification_id,
        2,
        action_owner_changed_on=(today_kst() + timedelta(days=1)).isoformat(),
    )
    assert future.status_code == 422 and "action_owner_changed_on" in _errors(future)


def test_resending_the_same_owner_does_not_reset_the_ball_date(cert: TestClient) -> None:
    """화면이 폼 전체를 보내도(같은 주체) 공이 넘어간 날은 그대로다 — 정체 시계가 리셋되지 않는다"""
    certification_id = _certification()
    back = (today_kst() - timedelta(days=9)).isoformat()
    _patch_cert(cert, certification_id, 1, action_owner="AUTHORITY", action_owner_changed_on=back)
    again = _patch_cert(cert, certification_id, 2, action_owner="AUTHORITY", note="메모만")
    assert again.json()["action_owner_changed_on"] == back


def test_changing_the_owner_moves_the_ball_date_to_today(cert: TestClient) -> None:
    """주체가 실제로 바뀌면 날짜가 오늘로 옮겨진다"""
    certification_id = _certification()
    back = (today_kst() - timedelta(days=9)).isoformat()
    _patch_cert(cert, certification_id, 1, action_owner="AUTHORITY", action_owner_changed_on=back)
    moved = _patch_cert(cert, certification_id, 2, action_owner="INTERNAL")
    assert moved.json()["action_owner_changed_on"] == today_kst().isoformat()


def test_the_ball_date_can_be_cleared(cert: TestClient) -> None:
    """공이 넘어간 날을 null로 비우면 기록 해제(생성일로 읽는다)"""
    certification_id = _certification()
    _patch_cert(cert, certification_id, 1, action_owner="AUTHORITY")
    cleared = _patch_cert(cert, certification_id, 2, action_owner_changed_on=None)
    assert cleared.json()["action_owner_changed_on"] is None


@pytest.mark.parametrize(
    ("fields", "field"),
    [
        ({"handling_mode": "AGENCY"}, "agency_partner_id"),  # 대행인데 대행사 없음
        ({"agency_partner_id": "AGENCY"}, "agency_partner_id"),  # 직접인데 대행사 지정(아래서 치환)
        ({"action_owner": "AGENCY"}, "action_owner"),  # 직접 처리인데 공이 대행사
    ],
)
def test_inconsistent_agency_combinations_are_field_errors_not_500s(
    cert: TestClient, fields: dict[str, Any], field: str
) -> None:
    """DB CHECK 3조합은 서비스가 먼저 필드별 422로 안내한다(500·DB 예외 노출 금지)"""
    certification_id = _certification()
    payload = {
        key: (_agency() if value == "AGENCY" and key == "agency_partner_id" else value)
        for key, value in fields.items()
    }
    response = _patch_cert(cert, certification_id, 1, **payload)
    assert response.status_code == 422, response.text
    assert field in _errors(response)


def test_the_agency_must_be_a_certification_agency_partner(cert: TestClient) -> None:
    """대행사는 인증대행 유형 거래처 — 공급사를 지정하면 422"""
    supplier = create_partner("SUP-2", name_ko="공급사", types=("SUPPLIER",))
    response = _patch_cert(
        cert, _certification(), 1, handling_mode="AGENCY", agency_partner_id=supplier
    )
    assert response.status_code == 422 and "인증대행" in _errors(response)["agency_partner_id"]


def test_null_handling_mode_and_action_owner_are_schema_errors(cert: TestClient) -> None:
    """처리방식·액션 주체는 비울 수 없다 — 명시 null은 422, 모르는 값도 422"""
    certification_id = _certification()
    for field in ("handling_mode", "action_owner"):
        assert _patch_cert(cert, certification_id, 1, **{field: None}).status_code == 422
    assert _patch_cert(cert, certification_id, 1, handling_mode="OUTSOURCED").status_code == 422


def test_switching_back_to_direct_requires_clearing_the_agency_and_the_ball(
    cert: TestClient,
) -> None:
    """직접 처리 복귀 — 대행사 지정 해제·공 회수를 함께 보내야 성립한다(한 번에 일관된 상태로)"""
    certification_id = _certification()
    _patch_cert(
        cert,
        certification_id,
        1,
        handling_mode="AGENCY",
        agency_partner_id=_agency(),
        action_owner="AGENCY",
    )
    partial = _patch_cert(cert, certification_id, 2, handling_mode="DIRECT")
    assert partial.status_code == 422
    back = _patch_cert(
        cert,
        certification_id,
        2,
        handling_mode="DIRECT",
        agency_partner_id=None,
        action_owner="INTERNAL",
    )
    assert back.status_code == 200, back.text
    assert back.json()["agency_partner_name"] is None


def test_an_unrelated_edit_survives_a_later_loss_of_the_agency_type(cert: TestClient) -> None:
    """대행사 유형이 나중에 해제돼도 무관한 필드 편집·같은 대행사 재전송은 막히지 않는다"""
    certification_id = _certification()
    agency = _agency()
    _patch_cert(cert, certification_id, 1, handling_mode="AGENCY", agency_partner_id=agency)
    from sqlalchemy import update

    from app.core.db.uow import unit_of_work
    from app.core.time import utcnow
    from app.modules.partners.models import PartnerTypeLink

    with unit_of_work() as uow:
        uow.session.execute(
            update(PartnerTypeLink)
            .where(PartnerTypeLink.partner_id == agency)
            .values(deleted_at=utcnow())
        )
    same = _patch_cert(
        cert, certification_id, 2, handling_mode="AGENCY", agency_partner_id=agency, note="메모"
    )
    assert same.status_code == 200, same.text


def test_the_agency_fields_are_denied_to_non_certification_roles(
    trader: TestClient, cert: TestClient
) -> None:
    """대행 필드 편집도 인증+관리자만 — 무역은 403"""
    certification_id = _certification()
    response = _patch_cert(trader, certification_id, 1, action_owner="AUTHORITY")
    assert response.status_code == 403


def test_the_list_carries_agency_names_without_per_row_queries(cert: TestClient) -> None:
    """목록의 대행사명은 일괄 선로딩 — 행 1개와 12개의 질의 수가 같다 (N+1 방지, §18.4)"""
    from app.modules.certifications import service as certification_service
    from tests.support.sqlcount import count_statements

    agency = _agency()
    ids = [_certification() for _ in range(12)]
    for certification_id in ids:
        _patch_cert(cert, certification_id, 1, handling_mode="AGENCY", agency_partner_id=agency)

    def one() -> object:
        return certification_service.list_certifications(
            template_id=None, target_type=None, status=None, offset=0, limit=1
        )

    def many() -> object:
        return certification_service.list_certifications(
            template_id=None, target_type=None, status=None, offset=0, limit=50
        )

    single, bulk = count_statements(one), count_statements(many)
    assert single > 0  # 측정이 0을 세는 빈 검증 방지
    assert bulk == single
    rows = certification_service.list_certifications(
        template_id=None, target_type=None, status=None, offset=0, limit=50
    )[0]
    assert {row.agency_partner_name for row in rows} == {"테스트 대행사"}


# ═══ 스코어카드 ══════════════════════════════════════════════════════════════

SCORECARD = "/api/v1/agencies/scorecard"


def test_the_scorecard_is_readable_by_every_role_and_carries_the_definition_note(
    viewer: TestClient, trader: TestClient, cert: TestClient
) -> None:
    """전 역할 열람(원가·마진 아님) — 페이지 봉투 + 서버가 준 지표 정의 문구(note)"""
    agency = _agency()
    _contract(cert, agency, fee="1000", fee_currency="USD")
    for client in (viewer, trader, cert):
        response = client.get(SCORECARD)
        assert response.status_code == 200, response.text
        body = response.json()
        assert (body["total"], body["page"], body["size"]) == (1, 1, 50)
        assert "현재 지정된 대행사 기준" in body["note"]
        (item,) = body["items"]
        assert item["partner_id"] == agency and item["case_count"] == 0
        assert item["supplement_rate"] is None and item["lead_days_avg"] is None
        assert item["current_contract"]["fee_amount"] == 100000
        assert item["current_contract"]["fee_currency"] == "USD"


def test_the_scorecard_reflects_an_agency_assignment_immediately(cert: TestClient) -> None:
    """저장하지 않는 계산값 — 대행 지정 직후 담당 건수가 바로 오른다(배치 대기 없음)"""
    agency = _agency()
    certification_id = _certification()
    assert cert.get(SCORECARD).json()["items"][0]["case_count"] == 0
    response = _patch_cert(
        cert, certification_id, 1, handling_mode="AGENCY", agency_partner_id=agency
    )
    assert response.status_code == 200
    assert cert.get(SCORECARD).json()["items"][0]["case_count"] == 1


def test_the_scorecard_rejects_oversized_pages(viewer: TestClient) -> None:
    """size 상한(200) 초과는 422 — 무페이지네이션 금지(§18.4)"""
    assert viewer.get(SCORECARD, params={"size": 201}).status_code == 422
