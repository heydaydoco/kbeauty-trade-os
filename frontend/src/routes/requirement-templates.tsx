// 요건 템플릿 편집기 (DESIGN.md §5.1·§5.5 / §14 ⑭ / ADR-0033·0034 / S2-1 PR-2).
//
// ★ 확정은 사람 1클릭이다 — 근거 2필드(근거링크·최종확인일) 없는 확정은
//   서버가 422로 거부하고, 그 사유가 그대로 표시된다(GC-C8). 확정 상태의
//   편집은 409다 — "초안 전환" 후 수정하고 다시 확정한다(조건 11).
// ★ 편집(등록·수정·확정·전환·체크리스트·선행요건)은 인증+관리자다(판정 ⑤ —
//   무역·물류·조회는 열람). 화면 게이트는 표시일 뿐 실제 차단은 서버(§18.1).
// ★ 드롭다운(시장·문서종류·선행 템플릿·통화)은 전부 ?size=200 우회다 —
//   50건 표시 한계 재발 지점(계획서 §3-4 명시).

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ListPager } from "../components/list-pager";
import { ListState } from "../components/list-state";
import { apiDelete, apiFetch } from "../lib/api";
import { appliesToLabel, orEmpty, templateStatusLabel } from "../lib/labels";
import { formatMoney, useCurrencies } from "../lib/money";
import { usePagedList, usePagedQuery } from "../lib/paging";
import { hasRole, useSession } from "../lib/session";
import { fieldMessage } from "./brands";
import type { DocumentTypeRow } from "./documents";
import { DOCUMENT_TYPES_QUERY_KEY } from "./documents";
import type { Market } from "./markets";
import { MARKETS_QUERY_KEY, MARKETS_SELECT_PATH } from "./markets";

export interface RequirementTemplate {
  id: number;
  market_id: number;
  market_code: string;
  market_name: string;
  name: string;
  applies_to: string;
  requirement_type: string;
  validity_months: number | null;
  renewal_cycle_months: number | null;
  renewal_lead_days: number | null;
  estimated_cost_amount: number | null;
  estimated_cost_currency: string | null;
  source_url: string | null;
  last_verified_on: string | null;
  status: string;
  note: string | null;
  version: number;
}

interface ChecklistItem {
  id: number;
  template_id: number;
  seq: number;
  item_name: string;
  document_type_id: number | null;
  document_type_code: string | null;
  document_type_name: string | null;
  is_required: boolean;
  note: string | null;
  version: number;
}

interface Prerequisite {
  id: number;
  template_id: number;
  prerequisite_template_id: number;
  prerequisite_name: string;
  prerequisite_status: string;
  version: number;
}

export const REQUIREMENT_TEMPLATES_QUERY_KEY = ["requirement-templates"] as const;

/** 드롭다운 소비용 — ?size=200 우회(50건 표시 한계 재발 지점). */
export const REQUIREMENT_TEMPLATES_SELECT_PATH = "/v1/requirement-templates?size=200";

const APPLIES_TO_OPTIONS = ["PRODUCT", "SKU", "FACILITY", "COMPANY", "INGREDIENT"] as const;

/** 폼 상태 — 서버 표기 그대로의 문자열. 숫자·날짜 변환은 제출 시점에 한다. */
interface TemplateForm {
  market_id: string;
  name: string;
  applies_to: string;
  requirement_type: string;
  validity_months: string;
  renewal_cycle_months: string;
  renewal_lead_days: string;
  estimated_cost: string;
  estimated_cost_currency: string;
  source_url: string;
  last_verified_on: string;
  status: string;
  note: string;
}

const EMPTY_FORM: TemplateForm = {
  market_id: "",
  name: "",
  applies_to: "PRODUCT",
  requirement_type: "",
  validity_months: "",
  renewal_cycle_months: "",
  renewal_lead_days: "",
  estimated_cost: "",
  estimated_cost_currency: "",
  source_url: "",
  last_verified_on: "",
  status: "DRAFT",
  note: "",
};

function formOf(row: RequirementTemplate): TemplateForm {
  return {
    market_id: String(row.market_id),
    name: row.name,
    applies_to: row.applies_to,
    requirement_type: row.requirement_type,
    validity_months: row.validity_months === null ? "" : String(row.validity_months),
    renewal_cycle_months:
      row.renewal_cycle_months === null ? "" : String(row.renewal_cycle_months),
    renewal_lead_days: row.renewal_lead_days === null ? "" : String(row.renewal_lead_days),
    // 수정 폼의 금액은 최소단위가 아니라 사람 표기다 — 저장값(최소단위)을 그대로
    // 넣으면 KRW 밖 통화에서 자릿수가 틀어진다. 통화표 없이 역환산할 수 없으므로
    // 수정 시 금액은 비워 두고 다시 입력받는다(값은 표에 보인다).
    estimated_cost: "",
    estimated_cost_currency: row.estimated_cost_currency ?? "",
    source_url: row.source_url ?? "",
    last_verified_on: row.last_verified_on ?? "",
    status: row.status,
    note: row.note ?? "",
  };
}

function numberOrUndefined(value: string): number | undefined {
  return value.trim() === "" ? undefined : Number(value);
}

function orUndefined(value: string): string | undefined {
  return value.trim() === "" ? undefined : value.trim();
}

/** 한 템플릿의 체크리스트 편집 패널 (§5.1 — S2-2 참조 복사의 원천). */
function ChecklistEditor({
  template,
  canEdit,
}: {
  template: RequirementTemplate;
  canEdit: boolean;
}) {
  const client = useQueryClient();
  const [form, setForm] = useState({ seq: "", item_name: "", document_type: "", required: true });

  const listKey = ["requirement-templates", template.id, "checklist"] as const;
  const checklist = usePagedQuery<ChecklistItem>(
    listKey,
    `/v1/requirement-templates/${template.id}/checklist`,
  );
  const types = usePagedQuery<DocumentTypeRow>(
    DOCUMENT_TYPES_QUERY_KEY,
    "/v1/document-types?size=200",
    canEdit,
  );

  const add = useMutation({
    mutationFn: () =>
      apiFetch<ChecklistItem>(`/v1/requirement-templates/${template.id}/checklist`, {
        method: "POST",
        body: {
          seq: Number(form.seq),
          item_name: form.item_name,
          document_type: orUndefined(form.document_type),
          is_required: form.required,
        },
      }),
    onSuccess: () => {
      setForm({ seq: "", item_name: "", document_type: "", required: true });
      void client.invalidateQueries({ queryKey: listKey });
    },
  });

  const remove = useMutation({
    mutationFn: (itemId: number) =>
      apiDelete(`/v1/requirement-templates/${template.id}/checklist/${itemId}`),
    onSuccess: () => void client.invalidateQueries({ queryKey: listKey }),
  });

  const message = fieldMessage(add.error) ?? fieldMessage(remove.error);

  return (
    <div className="mt-4 rounded-lg border border-gray-200 p-4">
      <h3 className="font-semibold">체크리스트</h3>
      <p className="mt-1 text-sm text-gray-500">
        이 요건을 준비할 때 밟는 항목입니다. 서류가 필요한 항목은 문서 종류를 연결하세요 — 인증
        진행(다음 단계)이 이 목록을 태스크로 복사합니다.
      </p>
      <ListState
        isPending={checklist.isPending}
        error={checklist.error}
        isEmpty={checklist.data?.items.length === 0}
        emptyHint={
          canEdit ? "항목이 없습니다. 아래에서 순번과 이름을 넣어 추가하세요." : "항목이 없습니다."
        }
      >
        <table className="mt-3 w-full text-sm">
          <thead className="bg-gray-50 text-left">
            <tr>
              <th className="cell-nowrap px-3 py-2 num">순번</th>
              <th className="px-3 py-2">항목</th>
              <th className="cell-nowrap px-3 py-2">문서 종류</th>
              <th className="cell-nowrap px-3 py-2">필수</th>
              {canEdit && <th className="px-3 py-2" />}
            </tr>
          </thead>
          <tbody>
            {checklist.data?.items.map((item) => (
              <tr key={item.id} className="border-t border-gray-100">
                <td className="cell-nowrap px-3 py-2 num">{item.seq}</td>
                <td className="px-3 py-2">{item.item_name}</td>
                <td className="cell-nowrap px-3 py-2">{orEmpty(item.document_type_name)}</td>
                <td className="cell-nowrap px-3 py-2">{item.is_required ? "필수" : "선택"}</td>
                {canEdit && (
                  <td className="cell-nowrap px-3 py-2">
                    <button
                      type="button"
                      onClick={() => remove.mutate(item.id)}
                      disabled={remove.isPending}
                      className="rounded border border-gray-300 px-2 py-1 text-xs"
                    >
                      제거
                    </button>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </ListState>

      {canEdit && (
        <form
          className="mt-3 flex flex-wrap items-end gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            add.mutate();
          }}
        >
          <label className="flex flex-col gap-1 text-sm">
            <span className="cell-nowrap text-gray-600">순번</span>
            <input
              name="seq"
              required
              type="number"
              min={1}
              value={form.seq}
              onChange={(event) => setForm((prev) => ({ ...prev, seq: event.target.value }))}
              className="w-20 rounded border border-gray-300 px-3 py-2"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="cell-nowrap text-gray-600">항목 이름</span>
            <input
              name="item_name"
              required
              maxLength={200}
              value={form.item_name}
              onChange={(event) => setForm((prev) => ({ ...prev, item_name: event.target.value }))}
              className="rounded border border-gray-300 px-3 py-2"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="cell-nowrap text-gray-600">문서 종류 (선택)</span>
            <select
              name="document_type"
              value={form.document_type}
              onChange={(event) =>
                setForm((prev) => ({ ...prev, document_type: event.target.value }))
              }
              className="rounded border border-gray-300 px-3 py-2"
            >
              <option value="">없음 (행정 절차)</option>
              {types.data?.items.map((type) => (
                <option key={type.id} value={type.code}>
                  {type.name_ko}
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-2 pb-2 text-sm">
            <input
              name="is_required"
              type="checkbox"
              checked={form.required}
              onChange={(event) => setForm((prev) => ({ ...prev, required: event.target.checked }))}
            />
            <span className="cell-nowrap">필수 항목</span>
          </label>
          <button
            type="submit"
            disabled={add.isPending}
            className="rounded bg-gray-900 px-4 py-2 text-sm text-white disabled:opacity-50"
          >
            항목 추가
          </button>
          {message && (
            <p role="alert" className="w-full text-sm text-signal-red">
              {message}
            </p>
          )}
        </form>
      )}
    </div>
  );
}

/** 한 템플릿의 선행요건 편집 패널 (조건 1 — 자기참조·순환은 서버가 422). */
function PrerequisiteEditor({
  template,
  canEdit,
}: {
  template: RequirementTemplate;
  canEdit: boolean;
}) {
  const client = useQueryClient();
  const [prerequisiteId, setPrerequisiteId] = useState("");

  const listKey = ["requirement-templates", template.id, "prerequisites"] as const;
  const prerequisites = usePagedQuery<Prerequisite>(
    listKey,
    `/v1/requirement-templates/${template.id}/prerequisites`,
  );
  const candidates = usePagedQuery<RequirementTemplate>(
    REQUIREMENT_TEMPLATES_QUERY_KEY,
    REQUIREMENT_TEMPLATES_SELECT_PATH,
    canEdit,
  );

  const add = useMutation({
    mutationFn: () =>
      apiFetch<Prerequisite>(`/v1/requirement-templates/${template.id}/prerequisites`, {
        method: "POST",
        body: { prerequisite_template_id: Number(prerequisiteId) },
      }),
    onSuccess: () => {
      setPrerequisiteId("");
      void client.invalidateQueries({ queryKey: listKey });
    },
  });

  const remove = useMutation({
    mutationFn: (linkId: number) =>
      apiDelete(`/v1/requirement-templates/${template.id}/prerequisites/${linkId}`),
    onSuccess: () => void client.invalidateQueries({ queryKey: listKey }),
  });

  const message = fieldMessage(add.error) ?? fieldMessage(remove.error);

  return (
    <div className="mt-4 rounded-lg border border-gray-200 p-4">
      <h3 className="font-semibold">선행요건</h3>
      <p className="mt-1 text-sm text-gray-500">
        이 요건보다 먼저 끝나야 하는 요건입니다(예: 중국 비안 앞의 경내책임자 지정). 서로가
        서로를 기다리는 순환은 등록되지 않습니다.
      </p>
      <ListState
        isPending={prerequisites.isPending}
        error={prerequisites.error}
        isEmpty={prerequisites.data?.items.length === 0}
        emptyHint={canEdit ? "선행요건이 없습니다. 아래에서 추가하세요." : "선행요건이 없습니다."}
      >
        <ul className="mt-3 flex flex-wrap gap-2">
          {prerequisites.data?.items.map((entry) => (
            <li
              key={entry.id}
              className="flex items-center gap-2 rounded-full border border-gray-300 px-3 py-1 text-sm"
            >
              <span className="cell-nowrap">
                {entry.prerequisite_name} ({templateStatusLabel(entry.prerequisite_status)})
              </span>
              {canEdit && (
                <button
                  type="button"
                  onClick={() => remove.mutate(entry.id)}
                  disabled={remove.isPending}
                  aria-label={`${entry.prerequisite_name} 해제`}
                  className="text-gray-500 disabled:opacity-50"
                >
                  ×
                </button>
              )}
            </li>
          ))}
        </ul>
      </ListState>

      {canEdit && (
        <form
          className="mt-3 flex flex-wrap items-end gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            add.mutate();
          }}
        >
          <label className="flex flex-col gap-1 text-sm">
            <span className="cell-nowrap text-gray-600">선행 템플릿</span>
            <select
              name="prerequisite_template_id"
              required
              value={prerequisiteId}
              onChange={(event) => setPrerequisiteId(event.target.value)}
              className="rounded border border-gray-300 px-3 py-2"
            >
              <option value="">선택</option>
              {candidates.data?.items
                .filter((row) => row.id !== template.id)
                .map((row) => (
                  <option key={row.id} value={row.id}>
                    [{row.market_code}] {row.name}
                  </option>
                ))}
            </select>
          </label>
          <button
            type="submit"
            disabled={add.isPending}
            className="rounded bg-gray-900 px-4 py-2 text-sm text-white disabled:opacity-50"
          >
            선행요건 추가
          </button>
          {message && (
            <p role="alert" className="w-full text-sm text-signal-red">
              {message}
            </p>
          )}
        </form>
      )}
    </div>
  );
}

export function RequirementTemplatesPage() {
  const { me } = useSession();
  const client = useQueryClient();
  const canEdit = hasRole(me, "CERT");

  const [form, setForm] = useState<TemplateForm>(EMPTY_FORM);
  // 수정 모드 — 대상 행의 id·version을 기억한다(폼은 위 form 하나를 공유).
  const [editing, setEditing] = useState<{ id: number; version: number } | null>(null);
  // 선택은 행 객체로 보관한다 — 목록 쪽 이동만으로 패널이 닫히지 않게(품목군 선례).
  const [selected, setSelected] = useState<RequirementTemplate | null>(null);
  const [marketFilter, setMarketFilter] = useState("");

  const listPath =
    marketFilter === ""
      ? "/v1/requirement-templates"
      : `/v1/requirement-templates?market_id=${marketFilter}`;
  const list = usePagedList<RequirementTemplate>(
    [...REQUIREMENT_TEMPLATES_QUERY_KEY, { market: marketFilter }],
    listPath,
  );
  const markets = usePagedQuery<Market>(MARKETS_QUERY_KEY, MARKETS_SELECT_PATH);
  const currencies = useCurrencies();

  const save = useMutation({
    mutationFn: () => {
      const content = {
        name: form.name,
        applies_to: form.applies_to,
        requirement_type: form.requirement_type,
        validity_months: numberOrUndefined(form.validity_months),
        renewal_cycle_months: numberOrUndefined(form.renewal_cycle_months),
        renewal_lead_days: numberOrUndefined(form.renewal_lead_days),
        // 사람 표기 그대로 — 최소단위 환산은 서버가 한다(단가 입력과 같은 규약).
        estimated_cost: orUndefined(form.estimated_cost),
        estimated_cost_currency: orUndefined(form.estimated_cost_currency),
        source_url: orUndefined(form.source_url),
        last_verified_on: orUndefined(form.last_verified_on),
        note: orUndefined(form.note),
      };
      return editing === null
        ? apiFetch<RequirementTemplate>("/v1/requirement-templates", {
            method: "POST",
            body: { ...content, market_id: Number(form.market_id) },
          })
        : apiFetch<RequirementTemplate>(`/v1/requirement-templates/${editing.id}`, {
            method: "PATCH",
            body: { ...content, version: editing.version, status: form.status },
          });
    },
    onSuccess: () => {
      setForm(EMPTY_FORM);
      setEditing(null);
      void client.invalidateQueries({ queryKey: REQUIREMENT_TEMPLATES_QUERY_KEY });
    },
  });

  const confirm = useMutation({
    mutationFn: (row: RequirementTemplate) =>
      apiFetch<RequirementTemplate>(`/v1/requirement-templates/${row.id}/confirm`, {
        method: "POST",
        body: { version: row.version },
      }),
    onSuccess: () =>
      void client.invalidateQueries({ queryKey: REQUIREMENT_TEMPLATES_QUERY_KEY }),
  });

  const revert = useMutation({
    mutationFn: (row: RequirementTemplate) =>
      apiFetch<RequirementTemplate>(`/v1/requirement-templates/${row.id}/revert-to-draft`, {
        method: "POST",
        body: { version: row.version },
      }),
    onSuccess: () =>
      void client.invalidateQueries({ queryKey: REQUIREMENT_TEMPLATES_QUERY_KEY }),
  });

  const saveMessage = fieldMessage(save.error);
  const actionMessage = fieldMessage(confirm.error) ?? fieldMessage(revert.error);

  return (
    <section>
      <header>
        <h1 className="text-2xl font-bold">요건 템플릿</h1>
        <p className="mt-1 text-sm text-gray-500">
          시장별 인증·규제 요건의 정의 대장입니다. 근거링크와 최종확인일이 없는 템플릿은 확정할 수
          없습니다. 확정된 템플릿을 고치려면 초안 전환 후 수정하고 다시 확정하세요.
        </p>
      </header>

      {canEdit && (
        <form
          className="mt-6 rounded-lg border border-gray-200 p-4"
          onSubmit={(event) => {
            event.preventDefault();
            save.mutate();
          }}
        >
          <h2 className="font-semibold">{editing === null ? "새 템플릿 등록" : "템플릿 수정"}</h2>
          <div className="mt-3 flex flex-wrap items-end gap-3">
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">시장</span>
              <select
                name="market_id"
                required
                disabled={editing !== null}
                value={form.market_id}
                onChange={(event) =>
                  setForm((prev) => ({ ...prev, market_id: event.target.value }))
                }
                className="rounded border border-gray-300 px-3 py-2 disabled:opacity-60"
              >
                <option value="">선택</option>
                {markets.data?.items.map((market) => (
                  <option key={market.id} value={market.id}>
                    [{market.code}] {market.name_ko}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">요건 이름</span>
              <input
                name="name"
                required
                maxLength={200}
                value={form.name}
                onChange={(event) => setForm((prev) => ({ ...prev, name: event.target.value }))}
                className="w-64 rounded border border-gray-300 px-3 py-2"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">적용단위</span>
              <select
                name="applies_to"
                value={form.applies_to}
                onChange={(event) =>
                  setForm((prev) => ({ ...prev, applies_to: event.target.value }))
                }
                className="rounded border border-gray-300 px-3 py-2"
              >
                {APPLIES_TO_OPTIONS.map((code) => (
                  <option key={code} value={code}>
                    {appliesToLabel(code)}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">유형 코드</span>
              <input
                name="requirement_type"
                required
                maxLength={40}
                placeholder="REGISTRATION"
                value={form.requirement_type}
                onChange={(event) =>
                  setForm((prev) => ({ ...prev, requirement_type: event.target.value }))
                }
                className="w-44 rounded border border-gray-300 px-3 py-2"
              />
            </label>
            {editing !== null && (
              <label className="flex flex-col gap-1 text-sm">
                <span className="cell-nowrap text-gray-600">상태</span>
                <select
                  name="status"
                  value={form.status}
                  onChange={(event) =>
                    setForm((prev) => ({ ...prev, status: event.target.value }))
                  }
                  className="rounded border border-gray-300 px-3 py-2"
                >
                  <option value="DRAFT">초안</option>
                  <option value="RETIRED">폐기</option>
                </select>
              </label>
            )}
          </div>
          <div className="mt-3 flex flex-wrap items-end gap-3">
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">유효기간(개월)</span>
              <input
                name="validity_months"
                type="number"
                min={1}
                value={form.validity_months}
                onChange={(event) =>
                  setForm((prev) => ({ ...prev, validity_months: event.target.value }))
                }
                className="w-28 rounded border border-gray-300 px-3 py-2"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">갱신주기(개월)</span>
              <input
                name="renewal_cycle_months"
                type="number"
                min={1}
                value={form.renewal_cycle_months}
                onChange={(event) =>
                  setForm((prev) => ({ ...prev, renewal_cycle_months: event.target.value }))
                }
                className="w-28 rounded border border-gray-300 px-3 py-2"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">갱신 리드타임(일)</span>
              <input
                name="renewal_lead_days"
                type="number"
                min={1}
                value={form.renewal_lead_days}
                onChange={(event) =>
                  setForm((prev) => ({ ...prev, renewal_lead_days: event.target.value }))
                }
                className="w-32 rounded border border-gray-300 px-3 py-2"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">예상 비용</span>
              <input
                name="estimated_cost"
                inputMode="decimal"
                placeholder="1500000"
                value={form.estimated_cost}
                onChange={(event) =>
                  setForm((prev) => ({ ...prev, estimated_cost: event.target.value }))
                }
                className="w-32 rounded border border-gray-300 px-3 py-2"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">통화</span>
              <select
                name="estimated_cost_currency"
                value={form.estimated_cost_currency}
                onChange={(event) =>
                  setForm((prev) => ({ ...prev, estimated_cost_currency: event.target.value }))
                }
                className="rounded border border-gray-300 px-3 py-2"
              >
                <option value="">없음</option>
                {currencies.data?.items.map((currency) => (
                  <option key={currency.code} value={currency.code}>
                    {currency.code}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="mt-3 flex flex-wrap items-end gap-3">
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">근거링크</span>
              <input
                name="source_url"
                type="url"
                maxLength={500}
                placeholder="https://…"
                value={form.source_url}
                onChange={(event) =>
                  setForm((prev) => ({ ...prev, source_url: event.target.value }))
                }
                className="w-80 rounded border border-gray-300 px-3 py-2"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">최종확인일</span>
              <input
                name="last_verified_on"
                type="date"
                value={form.last_verified_on}
                onChange={(event) =>
                  setForm((prev) => ({ ...prev, last_verified_on: event.target.value }))
                }
                className="rounded border border-gray-300 px-3 py-2"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">메모</span>
              <input
                name="note"
                value={form.note}
                onChange={(event) => setForm((prev) => ({ ...prev, note: event.target.value }))}
                className="w-72 rounded border border-gray-300 px-3 py-2"
              />
            </label>
            <button
              type="submit"
              disabled={save.isPending}
              className="rounded bg-gray-900 px-4 py-2 text-sm text-white disabled:opacity-50"
            >
              {save.isPending ? "저장 중…" : editing === null ? "등록" : "저장"}
            </button>
            {editing !== null && (
              <button
                type="button"
                onClick={() => {
                  setEditing(null);
                  setForm(EMPTY_FORM);
                }}
                className="rounded border border-gray-300 px-4 py-2 text-sm"
              >
                취소
              </button>
            )}
          </div>
          {saveMessage && (
            <p role="alert" className="mt-3 text-sm text-signal-red">
              {saveMessage}
            </p>
          )}
        </form>
      )}

      <div className="mt-6 flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-2 text-sm">
          <span className="cell-nowrap text-gray-600">시장 필터</span>
          <select
            value={marketFilter}
            onChange={(event) => setMarketFilter(event.target.value)}
            className="rounded border border-gray-300 px-3 py-2"
          >
            <option value="">전체</option>
            {markets.data?.items.map((market) => (
              <option key={market.id} value={market.id}>
                [{market.code}] {market.name_ko}
              </option>
            ))}
          </select>
        </label>
        <ListPager data={list.data} page={list.page} onPageChange={list.setPage} />
      </div>

      <div className="mt-3 overflow-x-auto rounded-lg border border-gray-200">
        <ListState
          isPending={list.isPending}
          error={list.error}
          isEmpty={list.data?.items.length === 0}
          emptyHint={
            canEdit
              ? "등록된 요건 템플릿이 없습니다. 위에서 시장과 이름을 넣어 등록하세요."
              : "등록된 요건 템플릿이 없습니다."
          }
        >
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left">
              <tr>
                <th className="cell-nowrap px-4 py-2">시장</th>
                <th className="px-4 py-2">요건 이름</th>
                <th className="cell-nowrap px-4 py-2">적용단위</th>
                <th className="cell-nowrap px-4 py-2">유형</th>
                <th className="cell-nowrap px-4 py-2 num">유효(개월)</th>
                <th className="cell-nowrap px-4 py-2 num">예상 비용</th>
                <th className="px-4 py-2">근거</th>
                <th className="cell-nowrap px-4 py-2">상태</th>
                <th className="px-4 py-2" />
              </tr>
            </thead>
            <tbody>
              {list.data?.items.map((row) => (
                <tr key={row.id} className="border-t border-gray-100">
                  <td className="cell-nowrap px-4 py-2">
                    [{row.market_code}] {row.market_name}
                  </td>
                  <td className="px-4 py-2">{row.name}</td>
                  <td className="cell-nowrap px-4 py-2">{appliesToLabel(row.applies_to)}</td>
                  <td className="cell-nowrap px-4 py-2">{row.requirement_type}</td>
                  <td className="cell-nowrap px-4 py-2 num">{orEmpty(row.validity_months)}</td>
                  <td className="cell-nowrap px-4 py-2 num">
                    {/* ★ 통화표가 아직 없으면 값을 만들지 않는다 — formatMoney는
                        모르는 통화(빈 통화표 포함)에 예외를 던지고, 렌더 중
                        예외는 화면 백지가 된다. ListState 가드는 children
                        평가를 못 막는다(함정 ⑦ — 부채 #16 동형, sku-detail 선례). */}
                    {row.estimated_cost_amount === null || row.estimated_cost_currency === null
                      ? orEmpty(null)
                      : currencies.data === undefined
                        ? orEmpty(null)
                        : formatMoney(
                            row.estimated_cost_amount,
                            row.estimated_cost_currency,
                            currencies.data.items,
                          )}
                  </td>
                  <td className="px-4 py-2">
                    {row.source_url === null ? (
                      orEmpty(null)
                    ) : (
                      <a
                        href={row.source_url}
                        target="_blank"
                        rel="noreferrer"
                        className="text-gray-700 underline"
                      >
                        근거
                      </a>
                    )}
                    {row.last_verified_on !== null && (
                      <span className="cell-nowrap ml-2 text-gray-500">
                        확인 {row.last_verified_on}
                      </span>
                    )}
                  </td>
                  <td className="cell-nowrap px-4 py-2">{templateStatusLabel(row.status)}</td>
                  <td className="cell-nowrap px-4 py-2">
                    <button
                      type="button"
                      onClick={() =>
                        setSelected((prev) => (prev?.id === row.id ? null : row))
                      }
                      className="text-sm text-gray-700 underline"
                    >
                      {selected?.id === row.id ? "상세 닫기" : "상세"}
                    </button>
                    {canEdit && row.status !== "CONFIRMED" && (
                      <>
                        <button
                          type="button"
                          onClick={() => {
                            setEditing({ id: row.id, version: row.version });
                            setForm(formOf(row));
                          }}
                          className="ml-2 rounded border border-gray-300 px-2 py-1 text-xs"
                        >
                          수정
                        </button>
                        <button
                          type="button"
                          disabled={confirm.isPending}
                          onClick={() => confirm.mutate(row)}
                          className="ml-2 rounded bg-gray-900 px-2 py-1 text-xs text-white disabled:opacity-50"
                        >
                          확정
                        </button>
                      </>
                    )}
                    {canEdit && row.status === "CONFIRMED" && (
                      <button
                        type="button"
                        disabled={revert.isPending}
                        onClick={() => revert.mutate(row)}
                        className="ml-2 rounded border border-gray-300 px-2 py-1 text-xs"
                      >
                        초안 전환
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </ListState>
      </div>

      {actionMessage && (
        <p role="alert" className="mt-3 text-sm text-signal-red">
          {actionMessage}
        </p>
      )}

      {selected && (
        <>
          <ChecklistEditor
            template={selected}
            canEdit={canEdit && selected.status !== "CONFIRMED"}
          />
          <PrerequisiteEditor
            template={selected}
            canEdit={canEdit && selected.status !== "CONFIRMED"}
          />
        </>
      )}
    </section>
  );
}
