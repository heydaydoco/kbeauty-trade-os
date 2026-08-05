// 거래처 목록·등록 (DESIGN.md §4.6 / ADR-0026).
//
// ★ 여신한도는 마스킹 비대상이다(마스킹 원장 2번 — 웹 세션 판정 2026-08-05).
//   그래도 표시 셀은 통화표 도착을 확인하고 formatMoney를 부른다 — ListState
//   가드는 children 평가를 못 막는다(주의 인계 ⑦, product-detail과 같은 패턴).

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ListState } from "../components/list-state";
import { apiFetch } from "../lib/api";
import { orEmpty, partnerTypeLabel } from "../lib/labels";
import { formatMoney, useCurrencies } from "../lib/money";
import { usePagedQuery } from "../lib/paging";
import { hasRole, useSession } from "../lib/session";
import { fieldMessage } from "./brands";

export interface Partner {
  id: number;
  partner_code: string;
  name_ko: string;
  type_codes: string[];
  credit_limit_amount: number | null;
  credit_limit_currency: string | null;
  dg_capable: boolean | null;
  strengths: string | null;
  weaknesses: string | null;
  note: string | null;
}

export const PARTNERS_QUERY_KEY = ["partners"] as const;

/** 드롭다운·매핑 소비용 — 통화 선례의 size=200 우회(50건 표시 한계 관찰 항목). */
export const PARTNERS_SELECT_PATH = "/v1/partners?size=200";

/** §4.6 열거 10종. 값은 서버 코드 그대로, 라벨은 화면이 붙인다. */
const PARTNER_TYPE_OPTIONS = [
  "SUPPLIER",
  "OEM",
  "BUYER",
  "RP",
  "IOR",
  "DOMESTIC_RP",
  "FORWARDER",
  "CUSTOMS_BROKER",
  "THREE_PL",
  "CERT_AGENCY",
] as const;

/** 여신 통화 선택지 — 자릿수 출처(서버 통화표)와 같은 목록을 쓴다. */
export function PartnersPage() {
  const { me } = useSession();
  const client = useQueryClient();
  const canRegister = hasRole(me, "TRADE");

  const [form, setForm] = useState({
    partner_code: "",
    name_ko: "",
    credit_limit: "",
    credit_limit_currency: "",
    dg_capable: "",
    strengths: "",
    weaknesses: "",
  });
  const [types, setTypes] = useState<string[]>([]);

  const list = usePagedQuery<Partner>(PARTNERS_QUERY_KEY, "/v1/partners");
  const currencies = useCurrencies();

  const register = useMutation({
    mutationFn: () =>
      apiFetch<Partner>("/v1/partners", {
        method: "POST",
        body: {
          partner_code: form.partner_code,
          name_ko: form.name_ko,
          type_codes: types,
          credit_limit: form.credit_limit === "" ? undefined : form.credit_limit,
          credit_limit_currency:
            form.credit_limit_currency === "" ? undefined : form.credit_limit_currency,
          // "미확인"은 보내지 않는다 — false(불가 확인)와 구분돼야 §7.7의
          // 소싱 제외가 보수적으로 선다.
          dg_capable: form.dg_capable === "" ? undefined : form.dg_capable === "true",
          strengths: form.strengths === "" ? undefined : form.strengths,
          weaknesses: form.weaknesses === "" ? undefined : form.weaknesses,
        },
      }),
    onSuccess: () => {
      setForm({
        partner_code: "",
        name_ko: "",
        credit_limit: "",
        credit_limit_currency: "",
        dg_capable: "",
        strengths: "",
        weaknesses: "",
      });
      setTypes([]);
      void client.invalidateQueries({ queryKey: PARTNERS_QUERY_KEY });
    },
  });

  const message = fieldMessage(register.error);

  const input = (key: "partner_code" | "name_ko" | "credit_limit" | "strengths" | "weaknesses") => ({
    value: form[key],
    onChange: (event: React.ChangeEvent<HTMLInputElement>) =>
      setForm((previous) => ({ ...previous, [key]: event.target.value })),
    className: "rounded border border-gray-300 px-3 py-2",
  });

  function toggleType(code: string, checked: boolean) {
    setTypes((previous) => (checked ? [...previous, code] : previous.filter((c) => c !== code)));
  }

  return (
    <section>
      <header className="flex items-baseline justify-between">
        <div>
          <h1 className="text-2xl font-bold">거래처</h1>
          <p className="mt-1 text-sm text-gray-500">
            공급사·OEM·바이어·포워더 등 모든 상대를 한 대장에 둡니다. 한 거래처가 여러 유형을
            가질 수 있습니다. SKU의 제조사(OEM)·자재의 기본공급사(공급사)가 이 대장을 참조합니다.
          </p>
        </div>
        <a
          href="/api/v1/partners/export.csv"
          className="cell-nowrap text-sm text-gray-700 underline"
        >
          CSV 내보내기
        </a>
      </header>

      {canRegister && (
        <form
          className="mt-6 rounded-lg border border-gray-200 p-4"
          onSubmit={(event) => {
            event.preventDefault();
            register.mutate();
          }}
        >
          <div className="flex flex-wrap items-end gap-3">
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">거래처코드</span>
              <input name="partner_code" required maxLength={40} {...input("partner_code")} />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">거래처명</span>
              <input name="name_ko" required maxLength={200} {...input("name_ko")} />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">여신한도 (선택)</span>
              <input name="credit_limit" inputMode="decimal" {...input("credit_limit")} />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">여신통화</span>
              <select
                name="credit_limit_currency"
                value={form.credit_limit_currency}
                onChange={(event) =>
                  setForm((previous) => ({
                    ...previous,
                    credit_limit_currency: event.target.value,
                  }))
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
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">DG 취급</span>
              <select
                name="dg_capable"
                value={form.dg_capable}
                onChange={(event) =>
                  setForm((previous) => ({ ...previous, dg_capable: event.target.value }))
                }
                className="rounded border border-gray-300 px-3 py-2"
              >
                <option value="">미확인</option>
                <option value="true">가능</option>
                <option value="false">불가</option>
              </select>
            </label>
            <button
              type="submit"
              disabled={register.isPending}
              className="rounded bg-gray-900 px-4 py-2 text-sm text-white disabled:opacity-50"
            >
              {register.isPending ? "등록 중…" : "등록"}
            </button>
          </div>

          <fieldset className="mt-3">
            <legend className="text-sm text-gray-600">유형 (1개 이상)</legend>
            <div className="mt-2 flex flex-wrap gap-3">
              {PARTNER_TYPE_OPTIONS.map((code) => (
                <label key={code} className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    name={`type_${code}`}
                    checked={types.includes(code)}
                    onChange={(event) => toggleType(code, event.target.checked)}
                  />
                  <span className="cell-nowrap text-gray-600">{partnerTypeLabel(code)}</span>
                </label>
              ))}
            </div>
          </fieldset>

          <div className="mt-3 flex flex-wrap items-end gap-3">
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">강점 (선택)</span>
              <input name="strengths" {...input("strengths")} />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">약점 (선택)</span>
              <input name="weaknesses" {...input("weaknesses")} />
            </label>
          </div>

          {message && (
            <p role="alert" className="mt-3 text-sm text-signal-red">
              {message}
            </p>
          )}
        </form>
      )}

      <p className="mt-6 text-sm text-gray-500">전체 {list.data?.total ?? 0}건</p>

      <div className="mt-3 overflow-x-auto rounded-lg border border-gray-200">
        <ListState
          isPending={list.isPending}
          error={list.error}
          isEmpty={list.data?.items.length === 0}
          emptyHint={
            canRegister
              ? "등록된 거래처가 없습니다. 위에서 코드·이름·유형을 입력해 등록하세요."
              : "등록된 거래처가 없습니다."
          }
        >
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left">
              <tr>
                <th className="cell-nowrap px-4 py-2">거래처코드</th>
                <th className="px-4 py-2">거래처명</th>
                <th className="px-4 py-2">유형</th>
                <th className="cell-nowrap px-4 py-2 num">여신한도</th>
                <th className="cell-nowrap px-4 py-2 num">DG 취급</th>
              </tr>
            </thead>
            <tbody>
              {list.data?.items.map((partner) => (
                <tr key={partner.id} className="border-t border-gray-100">
                  <td className="cell-nowrap px-4 py-2">{partner.partner_code}</td>
                  <td className="px-4 py-2">{partner.name_ko}</td>
                  <td className="px-4 py-2">
                    {partner.type_codes.map((code) => partnerTypeLabel(code)).join(" · ")}
                  </td>
                  <td className="cell-nowrap px-4 py-2 num">
                    {/* ★ 통화표가 아직 없으면 값을 만들지 않는다 — formatMoney는
                        모르는 통화(빈 표 포함)에 예외를 던지고, 렌더 중 예외는
                        화면 백지가 된다(주의 인계 ⑦). */}
                    {partner.credit_limit_amount === null ||
                    partner.credit_limit_currency === null ||
                    currencies.data === undefined
                      ? orEmpty(null)
                      : formatMoney(
                          partner.credit_limit_amount,
                          partner.credit_limit_currency,
                          currencies.data.items,
                        )}
                  </td>
                  <td className="cell-nowrap px-4 py-2 num">
                    {partner.dg_capable === null ? "미확인" : partner.dg_capable ? "가능" : "불가"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </ListState>
      </div>
    </section>
  );
}
