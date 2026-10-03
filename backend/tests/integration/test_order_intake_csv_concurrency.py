"""J. 오더 인테이크 CSV 입구 — 같은 파일 동시 업로드=1회 착지·같은 PO 다른 파일 동시=1건·교착 없음·파일 유니크 번역 (S3-1 PR-14a / ADR-0072 / design-D D2(f)).

핵심: 같은 파일(sha256)의 동시 업로드는 advisory lock으로 직렬화돼 **정확히 한 번만 착지**하고(나머지는 같은 키면 최초 결과 재생·다른 키면 409 `FILE.DUPLICATE`),
같은 PO를 다투는 서로 다른 파일은 한 쪽만 착지(나머지 전부 409 `DUPLICATE_BUYER_PO` — 500·교착 없음, 졌다고 해서 그 파일의 다른 PO가 새어 착지하지도 않는다).
"""

from __future__ import annotations

import io
from typing import Any

import pytest

from app.core.errors.exceptions import AppError
from app.modules.order_intake import csv_import
from app.modules.order_intake import service as intake_service
from app.modules.order_intake.models import IntakeSourceKind
from app.modules.order_intake.schemas import IntakeHeaderIn, IntakeLineIn
from tests.factories.intake import csv_bytes, csv_row, rows, scalar, trade_actor, world
from tests.factories.trade import unique
from tests.support.concurrency import Outcome, run_concurrently

pytestmark = pytest.mark.group_j

DUP_PO = "TRADE_DOCS.DOCUMENT.DUPLICATE_BUYER_PO"
FILE_DUP = "ORDER_INTAKE.FILE.DUPLICATE"


def _upload(actor: Any, content: bytes, *, key: str | None = None) -> tuple[int, dict[str, Any]]:
    return csv_import.import_csv(
        actor=actor,
        idempotency_key=key or unique("jcsv"),
        stream=io.BytesIO(content),
        filename="po.csv",
    )


def _codes(outcomes: list[Outcome]) -> list[str]:
    found: list[str] = []
    for outcome in outcomes:
        if outcome.ok:
            found.append("OK")
        else:
            assert isinstance(outcome.error, AppError), repr(
                outcome.error
            )  # 500·교착·IntegrityError 누출 없음
            found.append(str(outcome.error.code))
    return sorted(found)


def test_the_same_file_uploaded_by_eight_threads_lands_exactly_once() -> None:
    """같은 파일 8스레드 동시 업로드(스레드마다 다른 키) — 1건 성공·7건 409 FILE.DUPLICATE, 인테이크는 그룹 수(3)만큼 한 세트"""
    w = world(lines=3)
    c = w["codes"]
    content = csv_bytes(
        [csv_row(w, po=f"PO-{i}", code=c[i]) for i in range(3)] + [csv_row(w, po="PO-0", code=c[1])]
    )
    actor = trade_actor()
    outcomes = run_concurrently(lambda _i: _upload(actor, content), workers=8)
    assert _codes(outcomes) == ["OK", *[FILE_DUP] * 7]
    assert scalar("SELECT count(*) FROM order_intakes") == 3
    assert scalar("SELECT count(*) FROM order_intake_lines") == 4
    assert scalar("SELECT count(DISTINCT source_sha256) FROM order_intakes") == 1
    assert (
        scalar(
            "SELECT count(*) FROM events WHERE event_type = 'order_intakes.order_intake.created'"
        )
        == 3
    )


def test_the_same_file_with_the_same_key_replays_the_first_result_for_every_thread() -> None:
    """같은 파일·같은 키 8스레드 — 전부 최초 201 결과(같은 본문)를 받고 착지는 한 세트뿐이다"""
    w = world(lines=2)
    content = csv_bytes(
        [csv_row(w, po="PO-A", code=w["codes"][0]), csv_row(w, po="PO-B", code=w["codes"][1])]
    )
    actor = trade_actor()
    key = unique("same-key")
    outcomes = run_concurrently(lambda _i: _upload(actor, content, key=key), workers=8)
    assert all(o.ok for o in outcomes), [repr(o.error) for o in outcomes if not o.ok]
    bodies = [o.value[1] for o in outcomes]
    assert all(o.value[0] == 201 for o in outcomes) and all(b == bodies[0] for b in bodies)
    assert scalar("SELECT count(*) FROM order_intakes") == 2


def test_different_files_racing_for_the_same_po_land_exactly_one_file() -> None:
    """같은 PO를 다투는 서로 다른 파일 8스레드 — 파일 1개만 착지(공유 PO 1건+그 파일의 고유 PO 1건=2건), 나머지는 전부 409 DUPLICATE_BUYER_PO이고 그 파일의 고유 PO도 착지하지 않는다(파일 전체 원자)"""
    w = world(lines=1)
    code = w["codes"][0]

    def worker(i: int) -> tuple[int, dict[str, Any]]:
        content = csv_bytes(
            [csv_row(w, po=f"PO-OWN-{i}", code=code), csv_row(w, po="PO-RACE", code=code)]
        )
        return _upload(trade_actor(), content)

    outcomes = run_concurrently(worker, workers=8)
    assert _codes(outcomes) == ["OK", *[DUP_PO] * 7]
    assert scalar("SELECT count(*) FROM order_intakes") == 2
    assert scalar("SELECT count(*) FROM order_intakes WHERE buyer_po_no = 'PO-RACE'") == 1
    assert scalar("SELECT count(DISTINCT source_sha256) FROM order_intakes") == 1  # 한 파일의 두 PO


def test_files_listing_the_same_pos_in_opposite_orders_land_exactly_one_file() -> None:
    """스모크 — PO 두 개를 서로 반대 순서로 담은 파일 8개가 동시에 오면 정확히 한 파일만 착지한다(나머지 409). 교착 방지 자체의 결정적 시험은 아래 interleaved 시험이다"""
    w = world(lines=1)
    code = w["codes"][0]

    def worker(i: int) -> tuple[int, dict[str, Any]]:
        order = ("PO-1", "PO-2") if i % 2 == 0 else ("PO-2", "PO-1")
        content = csv_bytes([csv_row(w, po=po, code=code, qty=str(i + 1)) for po in order])
        return _upload(trade_actor(), content)

    outcomes = run_concurrently(worker, workers=8)
    assert _codes(outcomes) == ["OK", *[DUP_PO] * 7]
    assert scalar("SELECT count(*) FROM order_intakes") == 2


def test_two_files_interleaved_mid_landing_in_opposite_orders_do_not_deadlock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """교착 방지의 결정적 시험 — 두 파일(PO-1·PO-2 / PO-2·PO-1)이 **첫 PO를 착지한 직후 서로를 기다리게** 끼워 넣어도 착지가 `(거래처, PO키)` 정렬 순이라 교착(40P01)이 없다:
    두 트랜잭션이 같은 PO부터 잡으므로 뒤쪽은 앞쪽 커밋을 기다렸다가 409 DUPLICATE_BUYER_PO로 끝난다(정렬을 빼면 서로 다른 PO를 쥔 채 엇갈려 교착)"""
    import contextlib
    import threading

    w = world(lines=1)
    code = w["codes"][0]
    real = intake_service.register_intake
    barrier = threading.Barrier(2)
    local = threading.local()

    def interleaved(*args: Any, **kwargs: Any) -> Any:
        row = real(*args, **kwargs)
        if not getattr(local, "waited", False):
            local.waited = True
            # 상대가 첫 PO를 착지할 때까지 기다린다(상대가 내 잠금에 막혀 있으면 1초 뒤 진행 — lock_timeout 5s 안쪽에 넉넉한 여유).
            with contextlib.suppress(threading.BrokenBarrierError):
                barrier.wait(timeout=1)
        return row

    monkeypatch.setattr(csv_import.intake_service, "register_intake", interleaved)

    def worker(i: int) -> tuple[int, dict[str, Any]]:
        order = ("PO-1", "PO-2") if i == 0 else ("PO-2", "PO-1")
        content = csv_bytes([csv_row(w, po=po, code=code, qty=str(i + 1)) for po in order])
        return _upload(trade_actor(), content)

    outcomes = run_concurrently(worker, workers=2)
    assert _codes(outcomes) == sorted(["OK", DUP_PO])
    assert scalar("SELECT count(*) FROM order_intakes") == 2
    assert scalar("SELECT count(DISTINCT source_sha256) FROM order_intakes") == 1


def test_the_db_file_unique_is_translated_to_the_file_duplicate_code() -> None:
    """사전 조회를 빠져나간 경합의 최종 방어 — (sha256, 그룹 키) PENDING 부분 유니크 위반은 500이 아니라 같은 코드 409 FILE.DUPLICATE로 번역된다(제약명 분기)"""
    from app.core.db.uow import unit_of_work

    w = world(lines=1)
    sha = "a" * 64

    def land(po: str) -> None:
        with unit_of_work() as uow:
            intake_service.register_intake(
                uow.session,
                actor=trade_actor(),
                source_kind=IntakeSourceKind.CSV,
                buyer_partner_id=w["buyer"],
                header=IntakeHeaderIn(buyer_po_no=po, currency="USD", dest_market_code="US"),
                lines=[IntakeLineIn(buyer_item_code=w["codes"][0], quantity=1, unit_price="1.00")],
                extracted_snapshot={"kind": "CSV"},
                source_sha256=sha,
                source_group_key="same-group",
                original_filename="x.csv",
            )

    land("PO-FIRST")
    with pytest.raises(AppError) as caught:
        land("PO-SECOND")  # PO는 다르고 (sha, 그룹 키)만 같다 — 파일 유니크만 걸린다
    assert str(caught.value.code) == FILE_DUP
    assert rows("SELECT count(*) FROM order_intakes")[0][0] == 1
