// 대행사 화면 — 스코어카드(계산값)·대행 계약 (DESIGN.md §5.4 / S2-4 PR-1).
//
// ★ 스코어카드는 계산값 화면이라 캐시를 믿지 않는다 — 다른 화면에서 대행사를 지정·전이한 뒤 돌아오면
//   옛 숫자가 남지 않게 마운트마다 다시 가져온다(FRESH_EVERY_TIME, 매트릭스·보드와 같은 이유).
// ★ 지표 정의(귀속·소요일·보완율·비용)는 서버가 준 note 문구를 그대로 보인다 — 화면에 복사본이 없다.
// ★ 수수료는 합산·환산하지 않는다 — 계약에 적힌 통화·금액 그대로다. 표시 셀은 통화표 도착을 확인하고
//   formatMoney를 부른다(주의 인계 ⑦ — ListState 가드는 children 평가를 못 막는다).
// ★ 계약 편집은 인증+관리자다(화면 게이트는 표시일 뿐 — 실제 차단은 서버 §18.1). 계약 유효 여부(현행·종료·
//   예정)는 서버 계산값이다 — 화면이 날짜를 다시 비교하지 않는다.

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ListPager } from "../components/list-pager";
import { ListState } from "../components/list-state";
import { apiDelete, apiFetch } from "../lib/api";
import { orEmpty } from "../lib/labels";
import { formatMoney, minorUnitsOf, toDecimalInput, useCurrencies } from "../lib/money";
import type { Currency } from "../lib/money";
import { FRESH_EVERY_TIME, usePagedList, usePagedQuery } from "../lib/paging";
import type { Page } from "../lib/paging";
import { hasRole, useSession } from "../lib/session";
import { fieldMessage } from "./brands";
import type { Partner } from "./partners";
import { PARTNERS_SELECT_PATH } from "./partners";

export interface CurrentContract {
  id: number;
  contract_no: string;
  start_on: string;
  end_on: string | null;
  fee_amount: number | null;
  fee_currency: string | null;
  scope_note: string | null;
}

export interface AgencyScore {
  partner_id: number;
  partner_code: string;
  partner_name: string;
  case_count: number;
  submitted_count: number;
  approved_count: number;
  supplemented_count: number;
  supplement_rate: number | null;
  lead_sample_count: number;
  lead_days_avg: number | null;
  lead_days_median: number | null;
  current_contract: CurrentContract | null;
}

interface ScorecardPage extends Page<AgencyScore> {
  note: string;
}

export interface Contract {
  id: number;
  partner_id: number;
  partner_name: string;
  contract_no: string;
  scope_note: string | null;
  start_on: string;
  end_on: string | null;
  fee_amount: number | null;
  fee_currency: string | null;
  note: string | null;
  version: number;
  is_current: boolean;
}

export const SCORECARD_QUERY_KEY = ["agencies", "scorecard"] as const;
export const CONTRACTS_QUERY_KEY = ["agency-contracts"] as const;

/** 수수료 셀 — 통화표가 도착한 뒤에만 서식을 만든다(없으면 미기재 표시). */
function feeText(
  amount: number | null,
  currency: string | null,
  currencies: readonly Currency[] | undefined,
): string {
  if (amount === null || currency === null) return orEmpty(null);
  if (currencies === undefined) return "…";
  try {
    return formatMoney(amount, currency, currencies);
  } catch {
    // 통화표에 없는 코드 — 화면을 죽이지 않고 원값을 그대로 보인다(조용히 0자리로 떨어뜨리지 않는다).
    return `${amount} ${currency}`;
  }
}

function rateText(score: AgencyScore): string {
  if (score.supplement_rate === null) return orEmpty(null);
  // 비율 → 백분율(소수 1자리)이며 금액 환산이 아니다. 1000 단위로 반올림해 표기한다 — 통화 환산
  // 산술을 막는 가드(money.test.ts)의 패턴을 이 무관한 계산이 건드리지 않게 한다.
  const percent = Math.round(score.supplement_rate * 1000) / 10;
  return `${percent.toFixed(1)}% (${score.supplemented_count}/${score.submitted_count})`;
}

function leadText(score: AgencyScore): string {
  if (score.lead_days_avg === null || score.lead_days_median === null) return orEmpty(null);
  return `평균 ${score.lead_days_avg}일 · 중앙 ${score.lead_days_median}일 (${score.lead_sample_count}건)`;
}

/** 계약 유효 여부 배지 — 서버 계산값(is_current)과 종료·예정 구분(날짜 비교가 아닌 표시 분류). */
function ContractBadge({ contract }: { contract: Contract }) {
  if (contract.is_current) {
    return (
      <span className="cell-nowrap rounded bg-green-100 px-1.5 py-0.5 text-xs text-green-900">
        현행
      </span>
    );
  }
  return (
    <span className="cell-nowrap rounded bg-gray-100 px-1.5 py-0.5 text-xs text-gray-700">
      현행 아님
    </span>
  );
}

const BLANK_FORM = {
  partner_id: "",
  contract_no: "",
  start_on: "",
  end_on: "",
  fee: "",
  fee_currency: "",
  scope_note: "",
  note: "",
};

function orUndefined(value: string): string | undefined {
  return value.trim() === "" ? undefined : value.trim();
}

export function AgenciesPage() {
  const { me } = useSession();
  const client = useQueryClient();
  const canEdit = hasRole(me, "CERT");
  const currencies = useCurrencies();

  const scorecard = usePagedList<AgencyScore, ScorecardPage>(
    SCORECARD_QUERY_KEY,
    "/v1/agencies/scorecard",
    true,
    FRESH_EVERY_TIME,
  );
  const contracts = usePagedList<Contract>(CONTRACTS_QUERY_KEY, "/v1/agency-contracts");
  const partners = usePagedQuery<Partner>(["partners", "agency-options"], PARTNERS_SELECT_PATH, canEdit);
  const agencyChoices = (partners.data?.items ?? []).filter((partner) =>
    partner.type_codes.includes("CERT_AGENCY"),
  );

  const [form, setForm] = useState({ ...BLANK_FORM });
  const [editing, setEditing] = useState<Contract | null>(null);
  const [editForm, setEditForm] = useState({ ...BLANK_FORM });

  const invalidate = () => {
    void client.invalidateQueries({ queryKey: CONTRACTS_QUERY_KEY });
    void client.invalidateQueries({ queryKey: ["agencies"] });
  };

  const register = useMutation({
    mutationFn: () =>
      apiFetch<Contract>("/v1/agency-contracts", {
        method: "POST",
        body: {
          partner_id: Number(form.partner_id),
          contract_no: form.contract_no,
          start_on: form.start_on,
          end_on: orUndefined(form.end_on),
          fee: orUndefined(form.fee),
          fee_currency: orUndefined(form.fee_currency),
          scope_note: orUndefined(form.scope_note),
          note: orUndefined(form.note),
        },
      }),
    onSuccess: () => {
      setForm({ ...BLANK_FORM });
      invalidate();
    },
  });

  const update = useMutation({
    mutationFn: (contract: Contract) =>
      apiFetch<Contract>(`/v1/agency-contracts/${contract.id}`, {
        method: "PATCH",
        body: {
          version: contract.version,
          contract_no: editForm.contract_no,
          start_on: editForm.start_on,
          end_on: orUndefined(editForm.end_on) ?? null,
          // 수수료·통화는 한 쌍으로 함께 보낸다 — 비우면 함께 null(미기재로 되돌림).
          fee: orUndefined(editForm.fee) ?? null,
          fee_currency: orUndefined(editForm.fee_currency) ?? null,
          scope_note: orUndefined(editForm.scope_note) ?? null,
          note: orUndefined(editForm.note) ?? null,
        },
      }),
    onSuccess: () => {
      setEditing(null);
      invalidate();
    },
  });

  const remove = useMutation({
    mutationFn: (contract: Contract) => apiDelete(`/v1/agency-contracts/${contract.id}`),
    onSuccess: () => {
      setEditing(null);
      invalidate();
    },
  });

  const startEdit = (contract: Contract) => {
    // 최소단위 정수를 입력칸 표기로 되돌린다 — 통화표가 아직이면 수수료 편집은 열지 않는다.
    let fee = "";
    if (contract.fee_amount !== null && contract.fee_currency !== null) {
      if (currencies.data === undefined) return;
      fee = toDecimalInput(
        contract.fee_amount,
        minorUnitsOf(contract.fee_currency, currencies.data.items),
      );
    }
    setEditing(contract);
    setEditForm({
      partner_id: String(contract.partner_id),
      contract_no: contract.contract_no,
      start_on: contract.start_on,
      end_on: contract.end_on ?? "",
      fee,
      fee_currency: contract.fee_currency ?? "",
      scope_note: contract.scope_note ?? "",
      note: contract.note ?? "",
    });
  };

  const registerMessage = fieldMessage(register.error);
  const updateMessage = fieldMessage(update.error) ?? fieldMessage(remove.error);
  const currencyItems = currencies.data?.items;

  const contractFields = (
    values: typeof BLANK_FORM,
    set: (next: Partial<typeof BLANK_FORM>) => void,
    withPartner: boolean,
  ) => (
    <>
      {withPartner && (
        <label className="flex flex-col gap-1 text-sm">
          <span className="cell-nowrap text-gray-600">대행사</span>
          <select
            name="partner_id"
            required
            value={values.partner_id}
            onChange={(event) => set({ partner_id: event.target.value })}
            className="rounded border border-gray-300 px-3 py-2"
          >
            <option value="">선택</option>
            {agencyChoices.map((partner) => (
              <option key={partner.id} value={partner.id}>
                {partner.name_ko}
              </option>
            ))}
          </select>
        </label>
      )}
      <label className="flex flex-col gap-1 text-sm">
        <span className="cell-nowrap text-gray-600">계약 번호</span>
        <input
          name="contract_no"
          required
          maxLength={60}
          value={values.contract_no}
          onChange={(event) => set({ contract_no: event.target.value })}
          className="w-40 rounded border border-gray-300 px-3 py-2"
        />
      </label>
      <label className="flex flex-col gap-1 text-sm">
        <span className="cell-nowrap text-gray-600">시작일</span>
        <input
          name="start_on"
          type="date"
          required
          value={values.start_on}
          onChange={(event) => set({ start_on: event.target.value })}
          className="rounded border border-gray-300 px-3 py-2"
        />
      </label>
      <label className="flex flex-col gap-1 text-sm">
        <span className="cell-nowrap text-gray-600">종료일 (기간 미정이면 비움)</span>
        <input
          name="end_on"
          type="date"
          value={values.end_on}
          onChange={(event) => set({ end_on: event.target.value })}
          className="rounded border border-gray-300 px-3 py-2"
        />
      </label>
      <label className="flex flex-col gap-1 text-sm">
        <span className="cell-nowrap text-gray-600">수수료</span>
        <input
          name="fee"
          inputMode="decimal"
          value={values.fee}
          onChange={(event) => set({ fee: event.target.value })}
          className="w-32 rounded border border-gray-300 px-3 py-2"
        />
      </label>
      <label className="flex flex-col gap-1 text-sm">
        <span className="cell-nowrap text-gray-600">통화</span>
        <select
          name="fee_currency"
          value={values.fee_currency}
          onChange={(event) => set({ fee_currency: event.target.value })}
          className="rounded border border-gray-300 px-3 py-2"
        >
          <option value="">선택</option>
          {currencyItems?.map((currency) => (
            <option key={currency.code} value={currency.code}>
              {currency.code}
            </option>
          ))}
        </select>
      </label>
      <label className="flex flex-col gap-1 text-sm">
        <span className="cell-nowrap text-gray-600">범위·수수료 기준</span>
        <input
          name="scope_note"
          value={values.scope_note}
          onChange={(event) => set({ scope_note: event.target.value })}
          className="w-64 rounded border border-gray-300 px-3 py-2"
        />
      </label>
    </>
  );

  return (
    <section>
      <header>
        <h1 className="text-2xl font-bold">대행사</h1>
        <p className="mt-1 text-sm text-gray-500">
          인증 대행사별 실적(계산값)과 대행 계약 대장입니다. 인증 상세에서 처리방식을 대행으로 바꾸고
          대행사를 지정하면 이 표에 집계됩니다.
        </p>
      </header>

      <h2 className="mt-6 text-lg font-semibold">스코어카드</h2>
      {scorecard.data && (
        <p className="mt-1 text-sm text-gray-500" data-testid="scorecard-note">
          {scorecard.data.note}
        </p>
      )}
      <div className="mt-3">
        <ListPager data={scorecard.data} page={scorecard.page} onPageChange={scorecard.setPage} />
      </div>
      <div className="mt-2 overflow-x-auto rounded-lg border border-gray-200">
        <ListState
          isPending={scorecard.isPending}
          error={scorecard.error}
          isEmpty={scorecard.data?.items.length === 0}
          emptyHint="집계할 대행사가 없습니다. 거래처에서 유형에 '인증대행'을 추가하면 나타납니다."
        >
          <table className="w-full text-sm" aria-label="대행사 스코어카드">
            <thead className="bg-gray-50 text-left">
              <tr>
                <th className="cell-nowrap px-4 py-2">대행사</th>
                <th className="cell-nowrap px-4 py-2 text-center">담당 건수</th>
                <th className="cell-nowrap px-4 py-2 text-center">승인</th>
                <th className="cell-nowrap px-4 py-2">소요일</th>
                <th className="cell-nowrap px-4 py-2">보완율</th>
                <th className="px-4 py-2">현행 계약(비용)</th>
              </tr>
            </thead>
            <tbody>
              {scorecard.data?.items.map((score) => (
                <tr key={score.partner_id} className="border-t border-gray-100">
                  <td className="px-4 py-2">{score.partner_name}</td>
                  <td className="cell-nowrap px-4 py-2 text-center">{score.case_count}</td>
                  <td className="cell-nowrap px-4 py-2 text-center">{score.approved_count}</td>
                  <td className="cell-nowrap px-4 py-2">{leadText(score)}</td>
                  <td className="cell-nowrap px-4 py-2">{rateText(score)}</td>
                  <td className="px-4 py-2">
                    {score.current_contract === null ? (
                      orEmpty(null)
                    ) : (
                      <>
                        <span className="cell-nowrap">{score.current_contract.contract_no}</span>{" "}
                        <span className="cell-nowrap">
                          {feeText(
                            score.current_contract.fee_amount,
                            score.current_contract.fee_currency,
                            currencyItems,
                          )}
                        </span>
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </ListState>
      </div>

      <h2 className="mt-8 text-lg font-semibold">대행 계약</h2>
      {canEdit && (
        <form
          className="mt-3 rounded-lg border border-gray-200 p-4"
          aria-label="대행 계약 등록"
          onSubmit={(event) => {
            event.preventDefault();
            register.mutate();
          }}
        >
          <div className="flex flex-wrap items-end gap-3">
            {contractFields(form, (next) => setForm((prev) => ({ ...prev, ...next })), true)}
            <button
              type="submit"
              disabled={register.isPending}
              className="rounded bg-gray-900 px-4 py-2 text-sm text-white disabled:opacity-50"
            >
              등록
            </button>
          </div>
          {registerMessage && (
            <p role="alert" className="mt-3 text-sm text-signal-red">
              {registerMessage}
            </p>
          )}
        </form>
      )}

      <div className="mt-3">
        <ListPager data={contracts.data} page={contracts.page} onPageChange={contracts.setPage} />
      </div>
      <div className="mt-2 overflow-x-auto rounded-lg border border-gray-200">
        <ListState
          isPending={contracts.isPending}
          error={contracts.error}
          isEmpty={contracts.data?.items.length === 0}
          emptyHint={canEdit ? "등록된 계약이 없습니다. 위에서 등록하세요." : "등록된 계약이 없습니다."}
        >
          <table className="w-full text-sm" aria-label="대행 계약 목록">
            <thead className="bg-gray-50 text-left">
              <tr>
                <th className="cell-nowrap px-4 py-2">대행사</th>
                <th className="cell-nowrap px-4 py-2">계약 번호</th>
                <th className="cell-nowrap px-4 py-2">기간</th>
                <th className="cell-nowrap px-4 py-2">수수료</th>
                <th className="px-4 py-2">범위·수수료 기준</th>
                <th className="px-4 py-2" />
              </tr>
            </thead>
            <tbody>
              {contracts.data?.items.map((contract) => (
                <tr key={contract.id} className="border-t border-gray-100">
                  <td className="px-4 py-2">{contract.partner_name}</td>
                  <td className="cell-nowrap px-4 py-2">
                    {contract.contract_no} <ContractBadge contract={contract} />
                  </td>
                  <td className="cell-nowrap px-4 py-2">
                    {contract.start_on} ~ {contract.end_on ?? "기간 미정"}
                  </td>
                  <td className="cell-nowrap px-4 py-2">
                    {feeText(contract.fee_amount, contract.fee_currency, currencyItems)}
                  </td>
                  <td className="px-4 py-2">{orEmpty(contract.scope_note)}</td>
                  <td className="cell-nowrap px-4 py-2">
                    {canEdit && (
                      <button
                        type="button"
                        onClick={() => startEdit(contract)}
                        className="text-sm text-gray-700 underline"
                      >
                        수정
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </ListState>
      </div>

      {editing && (
        <form
          className="mt-4 rounded-lg border border-gray-300 p-4"
          aria-label="대행 계약 수정"
          onSubmit={(event) => {
            event.preventDefault();
            update.mutate(editing);
          }}
        >
          <div className="flex flex-wrap items-center gap-3">
            <h3 className="font-semibold">
              계약 수정 — {editing.partner_name} · {editing.contract_no}
            </h3>
            <button
              type="button"
              onClick={() => setEditing(null)}
              className="ml-auto text-sm text-gray-500 underline"
            >
              닫기
            </button>
          </div>
          <p className="mt-1 text-sm text-gray-500">
            대행사는 바꿀 수 없습니다 — 다른 대행사는 새 계약으로 등록하세요.
          </p>
          <div className="mt-3 flex flex-wrap items-end gap-3">
            {contractFields(editForm, (next) => setEditForm((prev) => ({ ...prev, ...next })), false)}
            <button
              type="submit"
              disabled={update.isPending}
              className="rounded bg-gray-900 px-4 py-2 text-sm text-white disabled:opacity-50"
            >
              저장
            </button>
            <button
              type="button"
              disabled={remove.isPending}
              onClick={() => {
                if (window.confirm("이 계약을 삭제할까요? 같은 번호로 다시 등록할 수 있습니다.")) {
                  remove.mutate(editing);
                }
              }}
              className="rounded border border-signal-red px-4 py-2 text-sm text-signal-red disabled:opacity-50"
            >
              삭제
            </button>
          </div>
          {updateMessage && (
            <p role="alert" className="mt-3 text-sm text-signal-red">
              {updateMessage}
            </p>
          )}
        </form>
      )}
    </section>
  );
}
