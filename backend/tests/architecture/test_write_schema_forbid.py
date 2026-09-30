"""K. 쓰기 요청 스키마는 모르는 필드를 거부한다 — 래칫 (S3-1 ADR-0067).

`extra="forbid"`가 없으면 클라이언트가 보낸 `status`·`total_amount` 같은 필드가 **조용히
무시**된다. 상태·금액을 요청 본문으로 밀어 넣는 우회가 실패하지 않고 통과한 것처럼 보이는
것이 문제다(상태 대입 단일 통로 규율의 전제). 이 테스트는 app/modules 전체의 `*Request`
클래스를 AST로 전수 검사한다.

기존 모듈의 미적용분은 LEGACY_FORBID_EXEMPT에 이름으로 고정한다 — **목록은 줄어들기만
한다**(새 Request가 목록에 들어오면 실패, 이미 forbid가 된 이름이 남아 있어도 실패).
소급 전환은 계약 변경(프런트가 여분 필드를 보내는지 회귀 확인)이라 해당 모듈 세션이 한다.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.group_k

MODULES = Path(__file__).resolve().parents[2] / "app" / "modules"

#: "모듈.클래스" — 목록은 줄이기만 한다(추가 금지).
LEGACY_FORBID_EXEMPT: frozenset[str] = frozenset(
    {
        "catalog.BrandCreateRequest",
        "catalog.ItemProfileCreateRequest",
        "catalog.ProductCreateRequest",
        "catalog.SetComponentCreateRequest",
        "catalog.SkuCreateRequest",
        "catalog.SkuHsCodeCreateRequest",
        "catalog.SkuPriceCreateRequest",
        "documents.LinkDocumentCreateRequest",
        "documents.ProfileDocumentTypeAddRequest",
        "handover.HandoverRequest",
        "identity.AccountActiveRequest",
        "identity.LoginRequest",
        "identity.RoleAssignmentRequest",
        "ingredients.IngredientCreateRequest",
        "ingredients.IngredientRuleCreateRequest",
        "ingredients.ProductIngredientCreateRequest",
        "markets.MarketCreateRequest",
        "markets.MarketUpdateRequest",
        "materials.BomLineCreateRequest",
        "materials.LabelCreateRequest",
        "materials.MaterialCreateRequest",
        "partners.ItemCodeCreateRequest",
        "partners.PartnerCreateRequest",
        "partners.SignatoryCreateRequest",
        "requirements.ChecklistItemAddRequest",
        "requirements.ChecklistItemUpdateRequest",
        "requirements.PrerequisiteAddRequest",
        "requirements.ProfileRequirementTemplateAddRequest",
        "requirements.RequirementTemplateCreateRequest",
        "requirements.RequirementTemplateUpdateRequest",
        "requirements.TemplateActionRequest",
        "worklist.TaskCreateRequest",
    }
)


def _explicit_extra(node: ast.ClassDef) -> str | None:
    """클래스 본문의 model_config가 지정한 extra 값(없으면 None)."""
    for stmt in node.body:
        target = None
        value = None
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            target, value = stmt.targets[0], stmt.value
        elif isinstance(stmt, ast.AnnAssign):
            target, value = stmt.target, stmt.value
        if isinstance(target, ast.Name) and target.id == "model_config" and value is not None:
            for kw in ast.walk(value):
                if (
                    isinstance(kw, ast.keyword)
                    and kw.arg == "extra"
                    and isinstance(kw.value, ast.Constant)
                ):
                    return str(kw.value.value)
    return None


def _is_forbid(node: ast.ClassDef, forbid_names: set[str]) -> bool:
    # 본문의 명시가 상속보다 앞선다 — 자식이 extra="allow"로 덮어쓰면 forbid가 아니다.
    explicit = _explicit_extra(node)
    if explicit is not None:
        return explicit == "forbid"
    return any(isinstance(b, ast.Name) and b.id in forbid_names for b in node.bases)


def _scan() -> tuple[set[str], set[str]]:
    """(전체 Request 클래스, 그중 forbid인 것) — 이름은 '모듈.클래스'."""
    everything: set[str] = set()
    forbidden: set[str] = set()
    for path in sorted(MODULES.glob("*/schemas.py")):
        module = path.parent.name
        tree = ast.parse(path.read_text(encoding="utf-8"))
        forbid_names: set[str] = set()
        classes = [n for n in tree.body if isinstance(n, ast.ClassDef)]
        for node in classes:  # 소스 순서 — 부모가 자식보다 앞에 온다
            if _is_forbid(node, forbid_names):
                forbid_names.add(node.name)
            if node.name.endswith("Request"):
                everything.add(f"{module}.{node.name}")
                if node.name in forbid_names:
                    forbidden.add(f"{module}.{node.name}")
    return everything, forbidden


def test_scan_is_not_vacuous() -> None:
    """검사할 쓰기 스키마가 실제로 있다"""
    everything, forbidden = _scan()
    assert len(everything) >= 40 and len(forbidden) >= 10


def test_every_write_schema_forbids_unknown_fields_except_the_legacy_list() -> None:
    """예외 목록 밖의 모든 *Request는 extra='forbid'다"""
    everything, forbidden = _scan()
    missing = everything - forbidden - LEGACY_FORBID_EXEMPT
    assert not missing, (
        f"extra='forbid'가 없는 쓰기 스키마: {sorted(missing)}\n"
        "model_config = ConfigDict(extra='forbid')를 붙이세요 — 새 스키마를 예외 목록에 "
        "추가하는 것은 금지입니다."
    )


def test_legacy_list_only_shrinks() -> None:
    """예외 목록에 없어진 클래스·이미 forbid가 된 클래스가 남아 있지 않다"""
    everything, forbidden = _scan()
    assert everything >= LEGACY_FORBID_EXEMPT, "목록에 더는 없는 클래스가 있다"
    assert not (LEGACY_FORBID_EXEMPT & forbidden), "이미 forbid인 클래스는 목록에서 지운다"


def test_child_that_overrides_extra_is_not_forbid() -> None:
    """부모가 forbid여도 자식이 extra='allow'로 덮어쓰면 forbid로 세지 않는다"""
    tree = ast.parse(
        "class Base(BaseModel):\n    model_config = ConfigDict(extra='forbid')\n"
        "class Child(Base):\n    model_config = ConfigDict(extra='allow')\n"
        "class Plain(Base):\n    x: int\n"
    )
    base, child, plain = (n for n in tree.body if isinstance(n, ast.ClassDef))
    assert _is_forbid(base, set())
    assert not _is_forbid(child, {"Base"})
    assert _is_forbid(plain, {"Base"})
