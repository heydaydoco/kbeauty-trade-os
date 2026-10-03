"""K·J. 오더 보드 벌크 순수 규칙 — 건별 멱등 키·처리 순서·대상 정규화·거부→결과 매핑 (S3-1 PR-15a / design-D D6 / ADR-0066)."""

from __future__ import annotations

import re
from typing import Any

import pytest
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm.exc import StaleDataError

from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError, ForbiddenError, NotFoundError
from app.modules.order_board import bulk
from app.modules.order_board.constants import (
    BULK_MAX_TARGETS,
    BulkAction,
    BulkOutcome,
    CardKind,
)

pytestmark = pytest.mark.group_k

T = bulk.Target


@pytest.mark.group_j
def test_item_keys_are_deterministic_64_hex_and_scoped_by_bulk_action_kind_and_id() -> None:
    """건별 키 = sha256(벌크 키|액션|종류|id) — 같은 입력이면 같은 키(재요청=재생), 하나라도 다르면 다른 키, 128자 한도 안(64 hex)"""
    key = bulk.item_key("k" * 500, BulkAction.CONFIRM_SO, CardKind.SO, 7)
    assert re.fullmatch(r"[0-9a-f]{64}", key)
    assert key == bulk.item_key("k" * 500, BulkAction.CONFIRM_SO, CardKind.SO, 7)
    variants = {
        bulk.item_key("other", BulkAction.CONFIRM_SO, CardKind.SO, 7),
        bulk.item_key("k" * 500, BulkAction.ASSIGN, CardKind.SO, 7),
        bulk.item_key("k" * 500, BulkAction.ASSIGN, CardKind.INTAKE, 7),
        bulk.item_key("k" * 500, BulkAction.CONFIRM_SO, CardKind.SO, 8),
    }
    assert key not in variants and len(variants) == 4


def test_processing_order_is_kind_rank_then_ascending_id() -> None:
    """처리 순서 = 인테이크(잠금 순서 (1)) → SO((5)), 같은 종류 안에서 id 오름차순 — 요청 순서와 무관"""
    targets = [
        T(CardKind.SO, 3, 1),
        T(CardKind.INTAKE, 9, 1),
        T(CardKind.SO, 1, 1),
        T(CardKind.INTAKE, 2, 1),
    ]
    ordered = bulk.processing_order(targets)
    assert [(t.kind, t.id) for t in ordered] == [
        (CardKind.INTAKE, 2),
        (CardKind.INTAKE, 9),
        (CardKind.SO, 1),
        (CardKind.SO, 3),
    ]


def test_normalize_merges_exact_duplicates_and_refuses_ambiguous_ones() -> None:
    """완전 중복은 하나로 · 같은 대상이 다른 version으로 두 번이면 422(모호 — fail-closed) · 같은 id라도 종류가 다르면 별개"""
    merged = bulk.normalize_targets(
        [T(CardKind.SO, 1, 2), T(CardKind.SO, 1, 2), T(CardKind.INTAKE, 1, 5)]
    )
    assert len(merged) == 2
    with pytest.raises(AppError) as caught:
        bulk.normalize_targets([T(CardKind.SO, 1, 2), T(CardKind.SO, 1, 3)])
    assert caught.value.code is ErrorCode.VALIDATION_INVALID_FIELD


def test_the_target_limit_counts_unique_targets_and_51_is_too_many() -> None:
    """상한은 중복 제거 후 50건 — 50건 통과·51건 422 `ORDER_BOARD.BULK.TOO_MANY`(중복 100개는 1건)"""
    assert (
        len(bulk.normalize_targets([T(CardKind.SO, n, 1) for n in range(BULK_MAX_TARGETS)])) == 50
    )
    assert len(bulk.normalize_targets([T(CardKind.SO, 1, 1)] * 100)) == 1
    with pytest.raises(AppError) as caught:
        bulk.normalize_targets([T(CardKind.SO, n, 1) for n in range(BULK_MAX_TARGETS + 1)])
    assert caught.value.code is ErrorCode.ORDER_BOARD_BULK_TOO_MANY
    assert caught.value.status_code == 422


class _Orig(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__(sqlstate)
        self.sqlstate = sqlstate


def _db_error(sqlstate: str) -> DBAPIError:
    return DBAPIError("SELECT 1", {}, _Orig(sqlstate))


@pytest.mark.parametrize(
    ("exc", "outcome", "code"),
    [
        (
            AppError(
                ErrorCode.TRADE_CHAIN_CONFIRM_GATE_BLOCKED,
                detail={
                    "blocked_gates": [
                        {
                            "gate_code": "CREDIT",
                            "line_id": None,
                            "line_no": None,
                            "level": "BLOCK",
                            "resolution": "APPROVAL",
                            "reason_code": "LIMIT_EXCEEDED",
                            "message_ko": "여신 한도 초과",
                            "basis": {"limit_amount": 1},
                            "basis_hash": "x" * 64,
                        }
                    ]
                },
            ),
            BulkOutcome.BLOCKED,
            "TRADE_CHAIN.CONFIRM.GATE_BLOCKED",
        ),
        (ForbiddenError(), BulkOutcome.FORBIDDEN, "COMMON.AUTH.FORBIDDEN"),
        (
            AppError(ErrorCode.CONCURRENCY_VERSION_CONFLICT),
            BulkOutcome.CONFLICT,
            "COMMON.CONCURRENCY.VERSION_CONFLICT",
        ),
        (
            AppError(ErrorCode.ORDER_INTAKE_STATE_NOT_PENDING),
            BulkOutcome.CONFLICT,
            "ORDER_INTAKE.STATE.NOT_PENDING",
        ),
        (NotFoundError(), BulkOutcome.FAILED, "COMMON.RESOURCE.NOT_FOUND"),
        (
            AppError(ErrorCode.ORDER_INTAKE_LINE_UNMAPPED_ITEMS),
            BulkOutcome.FAILED,
            "ORDER_INTAKE.LINE.UNMAPPED_ITEMS",
        ),
        (StaleDataError("x"), BulkOutcome.CONFLICT, "COMMON.CONCURRENCY.VERSION_CONFLICT"),
        (_db_error("55P03"), BulkOutcome.CONFLICT, "COMMON.CONCURRENCY.LOCK_BUSY"),
        (_db_error("40P01"), BulkOutcome.CONFLICT, "COMMON.CONCURRENCY.LOCK_BUSY"),
        (_db_error("57014"), BulkOutcome.FAILED, "COMMON.INTERNAL.UNEXPECTED"),
        (RuntimeError("x"), BulkOutcome.FAILED, "COMMON.INTERNAL.UNEXPECTED"),
    ],
    ids=[
        "gate-blocked",
        "forbidden",
        "version",
        "not-pending",
        "not-found",
        "unmapped",
        "stale-data",
        "lock-timeout",
        "deadlock",
        "statement-timeout",
        "unexpected",
    ],
)
def test_rejections_map_to_outcomes_by_code_and_status_only(
    exc: Exception, outcome: BulkOutcome, code: str
) -> None:
    """거부 → 건 결과는 코드·상태 매핑뿐(판정 재수행 없음) — 게이트 차단=BLOCKED(서버 항목에서 basis·해시는 빼고 옮김), 403=FORBIDDEN, 409·잠금 경합=CONFLICT, 그 밖=FAILED"""
    result = bulk.classify_exception(T(CardKind.SO, 1, 1), exc)
    assert result.outcome is outcome and result.code == code and result.message_ko
    if outcome is BulkOutcome.BLOCKED:
        (gate,) = result.blocked_gates
        assert set(gate) == set(bulk.BLOCKED_GATE_FIELDS)
        assert "basis" not in gate and "basis_hash" not in gate
        assert "개별" in result.message_ko
    else:
        assert result.blocked_gates == []


def test_the_report_counts_from_the_finished_results() -> None:
    """리포트 합계는 결과 목록에서 산출 — ok+skipped+fail=total, 6결과 전부 키가 있다"""
    results: list[Any] = [
        bulk.ItemResult(CardKind.SO, 1, BulkOutcome.OK, None, "ok"),
        bulk.ItemResult(CardKind.SO, 2, BulkOutcome.SKIPPED, None, "skip"),
        bulk.ItemResult(CardKind.SO, 3, BulkOutcome.BLOCKED, "X.Y.Z", "blocked"),
        bulk.ItemResult(CardKind.SO, 4, BulkOutcome.CONFLICT, "X.Y.Z", "conflict"),
    ]
    report = bulk.report(BulkAction.ASSIGN, results)
    assert (report["ok_count"], report["skipped_count"], report["fail_count"], report["total"]) == (
        1,
        1,
        2,
        4,
    )
    assert set(report["outcome_counts"]) == {o.value for o in BulkOutcome}
    assert report["outcome_counts"]["FORBIDDEN"] == 0
