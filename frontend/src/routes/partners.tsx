// 거래처 목록·등록 (DESIGN.md §4.6 / ADR-0026).
//
// ★ 여신한도는 마스킹 비대상이다(마스킹 원장 2번 — 웹 세션 판정 2026-08-05).
//   그래도 표시 셀은 통화표 도착을 확인하고 formatMoney를 부른다 — ListState
//   가드는 children 평가를 못 막는다(주의 인계 ⑦, product-detail과 같은 패턴).

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Fragment, useState } from "react";
import { ListPager } from "../components/list-pager";
import { ListState } from "../components/list-state";
import { apiDelete, apiFetch } from "../lib/api";
import { orEmpty, partnerTypeLabel } from "../lib/labels";
import { formatMoney, useCurrencies } from "../lib/money";
import { usePagedList, usePagedQuery } from "../lib/paging";
import { hasRole, useSession } from "../lib/session";
import { fieldMessage } from "./brands";

export interface Partner {
  id: number;
  partner_code: string;
  name_ko: string;
  name_en?: string | null;
  address_en?: string | null;
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

/** 바이어 품번 매핑 행 (GET /v1/partners/{id}/item-codes). */
interface ItemCode {
  id: number;
  partner_id: number;
  sku_id: number;
  sku_code: string;
  buyer_item_code: string;
  note: string | null;
}

/** 여신 한도는 ADMIN만 값을 넣을 수 있다(E9) — 서버가 정본, 화면은 편의. */
export const CREDIT_LIMIT_ADMIN_NOTICE = "여신 한도는 관리자만 설정할 수 있습니다";

/** 여신 통화 선택지 — 자릿수 출처(서버 통화표)와 같은 목록을 쓴다. */
export function PartnersPage() {
  const { me } = useSession();
  const client = useQueryClient();
  const canRegister = hasRole(me, "TRADE");
  const isAdmin = me?.roles.includes("ADMIN") ?? false;
  const [openId, setOpenId] = useState<number | null>(null);

  const [form, setForm] = useState({
    partner_code: "",
    name_ko: "",
    name_en: "",
    address_en: "",
    credit_limit: "",
    credit_limit_currency: "",
    dg_capable: "",
    strengths: "",
    weaknesses: "",
  });
  const [types, setTypes] = useState<string[]>([]);

  const list = usePagedList<Partner>(PARTNERS_QUERY_KEY, "/v1/partners");
  const currencies = useCurrencies();

  const register = useMutation({
    mutationFn: () =>
      apiFetch<Partner>("/v1/partners", {
        method: "POST",
        body: {
          partner_code: form.partner_code,
          name_ko: form.name_ko,
          type_codes: types,
          name_en: form.name_en.trim() === "" ? undefined : form.name_en.trim(),
          address_en: form.address_en.trim() === "" ? undefined : form.address_en.trim(),
          // 비관리자는 여신 값을 아예 보내지 않는다(서버는 403 ADMIN_ONLY로 다시 막는다).
          credit_limit: !isAdmin || form.credit_limit === "" ? undefined : form.credit_limit,
          credit_limit_currency:
            !isAdmin || form.credit_limit_currency === ""
              ? undefined
              : form.credit_limit_currency,
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
        name_en: "",
        address_en: "",
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

  const input = (
    key:
      | "partner_code"
      | "name_ko"
      | "name_en"
      | "address_en"
      | "credit_limit"
      | "strengths"
      | "weaknesses",
  ) => ({
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
              <span className="cell-nowrap text-gray-600">거래처명(영문, 선택)</span>
              <input name="name_en" maxLength={200} {...input("name_en")} />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">영문주소 (선택)</span>
              <input name="address_en" {...input("address_en")} />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">여신한도 (선택)</span>
              <input
                name="credit_limit"
                inputMode="decimal"
                disabled={!isAdmin}
                {...input("credit_limit")}
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">여신통화</span>
              <select
                name="credit_limit_currency"
                disabled={!isAdmin}
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

          {!isAdmin && <p className="mt-3 text-sm text-gray-500">{CREDIT_LIMIT_ADMIN_NOTICE}</p>}
          {message && (
            <p role="alert" className="mt-3 text-sm text-signal-red">
              {message}
            </p>
          )}
        </form>
      )}

      <ListPager data={list.data} page={list.page} onPageChange={list.setPage} className="mt-6" />

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
                <th className="px-4 py-2">영문명</th>
                <th className="px-4 py-2">유형</th>
                <th className="cell-nowrap px-4 py-2 num">여신한도</th>
                <th className="cell-nowrap px-4 py-2 num">DG 취급</th>
                <th className="cell-nowrap px-4 py-2">상세</th>
              </tr>
            </thead>
            <tbody>
              {list.data?.items.map((partner) => (
                <Fragment key={partner.id}>
                <tr className="border-t border-gray-100">
                  <td className="cell-nowrap px-4 py-2">{partner.partner_code}</td>
                  <td className="px-4 py-2">{partner.name_ko}</td>
                  <td className="px-4 py-2">{orEmpty(partner.name_en ?? null)}</td>
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
                  <td className="cell-nowrap px-4 py-2">
                    <button
                      type="button"
                      aria-expanded={openId === partner.id}
                      onClick={() => setOpenId(openId === partner.id ? null : partner.id)}
                      className="text-gray-700 underline"
                    >
                      {openId === partner.id ? "닫기" : "상세·품번"}
                    </button>
                  </td>
                </tr>
                {openId === partner.id && (
                  <tr className="border-t border-gray-100 bg-gray-50">
                    <td colSpan={7} className="px-4 py-3">
                      <PartnerDetail partner={partner} canEdit={canRegister} />
                    </td>
                  </tr>
                )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </ListState>
      </div>
    </section>
  );
}

/** 목록 행 아래에 펼치는 상세 — 영문명·영문주소와 바이어 품번 매핑(삭제 포함). */
function PartnerDetail({ partner, canEdit }: { partner: Partner; canEdit: boolean }) {
  const client = useQueryClient();
  const key = [...PARTNERS_QUERY_KEY, partner.id, "item-codes"] as const;
  const codes = usePagedQuery<ItemCode>(key, `/v1/partners/${partner.id}/item-codes`);

  const remove = useMutation({
    mutationFn: (itemCodeId: number) =>
      apiDelete(`/v1/partners/${partner.id}/item-codes/${itemCodeId}`),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: key });
    },
  });
  const message = fieldMessage(remove.error);

  return (
    <div className="text-sm">
      <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1">
        <dt className="cell-nowrap text-gray-600">영문명</dt>
        <dd>{orEmpty(partner.name_en ?? null)}</dd>
        <dt className="cell-nowrap text-gray-600">영문주소</dt>
        <dd>{orEmpty(partner.address_en ?? null)}</dd>
      </dl>

      <h2 className="mt-3 font-semibold">품번 매핑</h2>
      {codes.isPending ? (
        <p className="mt-1 text-gray-500">불러오는 중…</p>
      ) : codes.error ? (
        <p role="alert" className="mt-1 text-signal-red">
          {fieldMessage(codes.error) ?? "품번 매핑을 불러오지 못했습니다."}
        </p>
      ) : codes.data?.items.length === 0 ? (
        <p className="mt-1 text-gray-500">등록된 품번 매핑이 없습니다.</p>
      ) : (
        <table className="mt-1 w-full">
          <thead className="text-left">
            <tr>
              <th className="cell-nowrap py-1 pr-4">바이어 품번</th>
              <th className="cell-nowrap py-1 pr-4">SKU 품번</th>
              <th className="py-1 pr-4">비고</th>
              {canEdit && <th className="cell-nowrap py-1">삭제</th>}
            </tr>
          </thead>
          <tbody>
            {codes.data?.items.map((code) => (
              <tr key={code.id} className="border-t border-gray-200">
                <td className="cell-nowrap py-1 pr-4">{code.buyer_item_code}</td>
                <td className="cell-nowrap py-1 pr-4">{code.sku_code}</td>
                <td className="py-1 pr-4">{orEmpty(code.note)}</td>
                {canEdit && (
                  <td className="cell-nowrap py-1">
                    <button
                      type="button"
                      disabled={remove.isPending}
                      aria-label={`품번 ${code.buyer_item_code} 삭제`}
                      onClick={() => {
                        if (
                          window.confirm(
                            `바이어 품번 ${code.buyer_item_code} 매핑을 삭제할까요? 잘못 등록했다면 삭제 후 다시 등록하세요.`,
                          )
                        ) {
                          remove.mutate(code.id);
                        }
                      }}
                      className="text-signal-red underline disabled:opacity-50"
                    >
                      삭제
                    </button>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {message && (
        <p role="alert" className="mt-2 text-signal-red">
          {message}
        </p>
      )}
    </div>
  );
}
