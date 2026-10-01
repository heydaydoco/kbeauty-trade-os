"""K. 모듈 계층 임포트 방향 — 단일 DAG (S3-1 ADR-0052 / design-integrated §2.8 · X-20·X-48).

    L0  trade_docs                                  : 상수·상태 기계·통로·FIELD_POLICY·잠금·사슬 레지스트리·잔량·검산
    L1  quotations·proforma_invoices·bank_accounts·sales_orders·purchase_orders : 모델·스키마·CRUD·라인 편집만
    L2  trade_chain (+ credit·payments·order_*)     : 모든 전이 오케스트레이션·수렴·잠금
        (PR-10a: payments=순수 원장 — trade_chain→payments 정방향, payments→trade_chain 금지[L2_NO_CHAIN])
    허용 간선: L1→L0 / L2→L1·L0. 금지: L0→L1·L2 / L1→L2 / L1→다른 L1 / 플랫폼 공용(정책·승인 등)→전표.
    PR-9a: approvals는 도메인 무임포트 플랫폼(TargetSpec 레지스트리), credit은 L2(→ sales_orders·approvals·partners 허용,
    → trade_chain 금지 — "credit·payments·order_intake→trade_chain 금지", design-integrated §2.8).

계층을 어기는 임포트는 조용히 순환을 만든다(L0가 L1 모델을 요구하는 순간 설계가 무너진다) — 그래서 기계로 고정한다.
S3 신규 모듈이 늘면 각 PR이 아래 표에 자기 모듈을 등록한다(미등록 전표 모듈은 test_every_s3_module_is_registered가 실패).
"""

from __future__ import annotations

import pytest

from tests.support.astscan import app_sources, imported_modules, module_of, parse_source

pytestmark = pytest.mark.group_k

L0 = {"trade_docs"}
L1 = {"quotations", "proforma_invoices", "bank_accounts", "sales_orders", "purchase_orders"}
L2 = {"trade_chain", "credit", "payments"}
S3_DOMAIN = L0 | L1 | L2
#: 전표 도메인과 무관해야 하는 S3 공용 모듈(전표를 임포트하면 안 된다).
S3_PLATFORM = {"policies", "approvals", "gates"}

#: L2 안에서도 오케스트레이터(trade_chain)를 거꾸로 임포트하면 안 되는 모듈(trade_chain이 이들을 부른다).
L2_NO_CHAIN = {"credit", "payments", "order_intake"}

#: 모듈 트리 밖에서 전표 모듈을 임포트해도 되는 파일(등록·배선 지점) — 이 밖의 임포트는 계층 위반이다.
ALLOWED_OUTSIDE_IMPORTERS = {
    "registry.py",  # 모델 등록소
    "api/router.py",  # 라우터 부착 + TargetSpec 등록 배선(credit.spec — approvals는 도메인을 모른다)
    "cli.py",  # 운영 명령(검산 수동 실행)
    "modules/handover/targets.py",  # 담당 이관 등록(handover→도메인 방향)
    "modules/platform/scheduler.py",  # 잡 레지스트리(검산 잡)
}


def _violations(sources: dict[str, object]) -> list[str]:
    out: list[str] = []
    for rel, tree in sources.items():
        owner = module_of(rel)
        used = imported_modules(tree)  # type: ignore[arg-type]
        if owner in L0 and used & (L1 | L2):
            out.append(f"{rel}: L0가 L1·L2를 임포트 {sorted(used & (L1 | L2))}")
        if owner in L1 and used & L2:
            out.append(f"{rel}: L1이 L2를 임포트 {sorted(used & L2)}")
        if owner in L1 and used & (L1 - {owner}):
            out.append(f"{rel}: L1이 다른 L1을 임포트 {sorted(used & (L1 - {owner}))}")
        if owner in L2_NO_CHAIN and "trade_chain" in used:
            out.append(f"{rel}: 평가·입금·인테이크 모듈이 trade_chain을 임포트(역방향)")
        if owner in S3_PLATFORM and used & S3_DOMAIN:
            out.append(f"{rel}: 공용 모듈이 전표 도메인을 임포트 {sorted(used & S3_DOMAIN)}")
        if (
            owner not in S3_DOMAIN | S3_PLATFORM
            and used & S3_DOMAIN
            and rel not in ALLOWED_OUTSIDE_IMPORTERS
        ):
            out.append(f"{rel}: 허용 목록 밖에서 전표 모듈을 임포트 {sorted(used & S3_DOMAIN)}")
    return out


def test_the_scan_is_not_vacuous() -> None:
    """스캔이 실제 임포트를 본다 — 견적이 커널을, 사슬이 견적을 임포트하고 그것이 허용 방향이다"""
    sources = app_sources()
    assert "trade_docs" in imported_modules(sources["modules/quotations/service.py"])
    assert {"quotations", "trade_docs"} <= imported_modules(
        sources["modules/trade_chain/lifecycle.py"]
    )
    assert any(module_of(rel) == "trade_docs" for rel in sources)


def test_the_layer_dag_holds_for_the_whole_app() -> None:
    """L0→L1·L2 금지 / L1→L2·다른 L1 금지 / 공용→전표 금지 / 허용 목록 밖 외부 임포트 금지"""
    assert _violations(app_sources()) == []


def test_allowed_outside_importers_really_exist_and_import_s3_modules() -> None:
    """허용 목록에 죽은 항목이 없다 — 각 파일이 실제로 전표 모듈을 임포트한다(목록이 낡아 구멍이 되지 않게)"""
    sources = app_sources()
    for rel in ALLOWED_OUTSIDE_IMPORTERS:
        assert rel in sources, rel
        assert imported_modules(sources[rel]) & S3_DOMAIN, rel


def test_every_s3_domain_module_directory_is_registered_in_a_layer() -> None:
    """전표 계열 모듈 디렉터리(trade_docs·quotations·trade_chain)는 계층 표에 있다 — 새 전표 모듈은 표에 등록해야 한다"""
    present = {module_of(rel) for rel in app_sources()} - {None}
    known_s3 = {"trade_docs", "quotations", "trade_chain", "proforma_invoices", "sales_orders", "purchase_orders",
                "bank_accounts", "credit", "payments", "order_intake", "order_board", "approvals", "gates"}  # fmt: skip
    assert (present & known_s3) <= (S3_DOMAIN | S3_PLATFORM), (
        f"계층 표에 없는 S3 모듈: {sorted((present & known_s3) - S3_DOMAIN)} — tests/architecture/"
        "test_import_direction.py의 L0·L1·L2 집합에 등록하세요."
    )


def test_approvals_knows_no_domain_and_credit_is_the_only_spec_registrar() -> None:
    """approvals는 어떤 도메인 모듈도 임포트하지 않고(TargetSpec은 소비 모듈이 등록한다), credit이 approvals를 임포트하는 것이 정방향이다"""
    sources = app_sources()
    owned = {rel: tree for rel, tree in sources.items() if module_of(rel) == "approvals"}
    assert len(owned) >= 6
    for rel, tree in owned.items():
        used = imported_modules(tree)
        assert not used & (S3_DOMAIN | {"partners", "sales_orders", "payments", "gates"}), (
            rel,
            used,
        )
    assert "approvals" in imported_modules(sources["modules/credit/spec.py"])


def test_the_checker_flags_synthetic_violations() -> None:
    """자기검사 — 계층을 어기는 가짜 소스를 넣으면 6종 위반(입금→trade_chain 역방향 포함)이 전부 잡히고 정방향(trade_chain→payments)은 통과한다"""
    fake = {
        "modules/credit/x.py": parse_source("from app.modules.trade_chain import lifecycle\n"),
        "modules/trade_docs/x.py": parse_source("from app.modules.quotations import models\n"),
        "modules/quotations/x.py": parse_source("from app.modules.trade_chain import lifecycle\n"),
        "modules/policies/x.py": parse_source("import app.modules.trade_docs.constants\n"),
        "modules/certifications/x.py": parse_source(
            "from app.modules.quotations.models import Quotation\n"
        ),
        "modules/payments/x.py": parse_source(
            "from app.modules.trade_chain import payment_status\n"
        ),
        "modules/quotations/ok.py": parse_source("from app.modules.trade_docs import machine\n"),
        "modules/trade_chain/ok2.py": parse_source("from app.modules.payments import service\n"),
        "modules/trade_chain/ok.py": parse_source("from app.modules.quotations import service\n"),
    }
    found = _violations(fake)
    assert len(found) == 6 and not any(
        "ok" in line.split(":")[0].rsplit("/", 1)[-1] for line in found
    )
