// 자재 목록·등록 (DESIGN.md §4.4 — 원산지·원가·구매·사급의 공통 전제).
//
// ★ 단가는 여기 없다. 단가는 "이 처방에 이 자재가 얼마에 들어가는가"라서
//   BOM 라인의 값이고(제품 상세), 원가라 권한에 따라 보이지 않는다(ADR-0024).

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ListState } from "../components/list-state";
import { apiFetch } from "../lib/api";
import { materialTypeLabel, orEmpty } from "../lib/labels";
import { usePagedQuery } from "../lib/paging";
import { hasRole, useSession } from "../lib/session";
import { fieldMessage } from "./brands";
import { PARTNERS_QUERY_KEY, PARTNERS_SELECT_PATH, type Partner } from "./partners";

export interface Material {
  id: number;
  material_code: string;
  name_ko: string;
  material_type: string;
  hs6: string | null;
  default_supplier_partner_id: number | null;
  default_supplier_name: string | null;
  inventory_managed: boolean;
  lot_managed: boolean;
  note: string | null;
}

export const MATERIALS_QUERY_KEY = ["materials"] as const;

/** §4.4의 6종. 값은 서버 코드 그대로 보내고 라벨만 화면이 붙인다. */
const MATERIAL_TYPE_OPTIONS = [
  "BULK",
  "RAW_MATERIAL",
  "CONTAINER",
  "CARTON",
  "LABEL",
  "AUX",
] as const;

export function MaterialsPage() {
  const { me } = useSession();
  const client = useQueryClient();
  const canRegister = hasRole(me, "TRADE");

  const [form, setForm] = useState({
    material_code: "",
    name_ko: "",
    material_type: "RAW_MATERIAL",
    hs6: "",
    default_supplier_partner_id: "",
    inventory_managed: false,
    lot_managed: false,
  });

  const list = usePagedQuery<Material>(MATERIALS_QUERY_KEY, "/v1/materials");
  const partners = usePagedQuery<Partner>(PARTNERS_QUERY_KEY, PARTNERS_SELECT_PATH, canRegister);
  // 기본공급사는 SUPPLIER 유형 거래처만([M1] 보강(S1-3) ⑤) — 서버가 최종 판정.
  const supplierPartners = partners.data?.items.filter((p) => p.type_codes.includes("SUPPLIER"));

  const register = useMutation({
    mutationFn: () =>
      apiFetch<Material>("/v1/materials", {
        method: "POST",
        body: {
          material_code: form.material_code,
          name_ko: form.name_ko,
          material_type: form.material_type,
          hs6: form.hs6 === "" ? undefined : form.hs6,
          default_supplier_partner_id:
            form.default_supplier_partner_id === ""
              ? undefined
              : Number(form.default_supplier_partner_id),
          inventory_managed: form.inventory_managed,
          lot_managed: form.lot_managed,
        },
      }),
    onSuccess: () => {
      setForm((previous) => ({
        ...previous,
        material_code: "",
        name_ko: "",
        hs6: "",
        default_supplier_partner_id: "",
      }));
      void client.invalidateQueries({ queryKey: MATERIALS_QUERY_KEY });
    },
  });

  const message = fieldMessage(register.error);

  return (
    <section>
      <header className="flex items-baseline justify-between">
        <div>
          <h1 className="text-2xl font-bold">자재</h1>
          <p className="mt-1 text-sm text-gray-500">
            벌크·원료·용기·단상자·라벨·부자재를 등록합니다. 제품 BOM(제품 상세)이 이 자재를
            소요량·원산지와 함께 참조합니다. 로트관리는 재고관리를 전제합니다.
          </p>
        </div>
        <a
          href="/api/v1/materials/export.csv"
          className="cell-nowrap text-sm text-gray-700 underline"
        >
          CSV 내보내기
        </a>
      </header>

      {canRegister && (
        <form
          className="mt-6 flex flex-wrap items-end gap-3 rounded-lg border border-gray-200 p-4"
          onSubmit={(event) => {
            event.preventDefault();
            register.mutate();
          }}
        >
          <label className="flex flex-col gap-1 text-sm">
            <span className="cell-nowrap text-gray-600">자재코드</span>
            <input
              name="material_code"
              required
              maxLength={40}
              value={form.material_code}
              onChange={(event) =>
                setForm((previous) => ({ ...previous, material_code: event.target.value }))
              }
              className="rounded border border-gray-300 px-3 py-2"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="cell-nowrap text-gray-600">자재명(국문)</span>
            <input
              name="name_ko"
              required
              value={form.name_ko}
              onChange={(event) =>
                setForm((previous) => ({ ...previous, name_ko: event.target.value }))
              }
              className="rounded border border-gray-300 px-3 py-2"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="cell-nowrap text-gray-600">유형</span>
            <select
              name="material_type"
              value={form.material_type}
              onChange={(event) =>
                setForm((previous) => ({ ...previous, material_type: event.target.value }))
              }
              className="rounded border border-gray-300 px-3 py-2"
            >
              {MATERIAL_TYPE_OPTIONS.map((code) => (
                <option key={code} value={code}>
                  {materialTypeLabel(code)}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="cell-nowrap text-gray-600">HS6 (선택)</span>
            <input
              name="hs6"
              inputMode="numeric"
              maxLength={6}
              value={form.hs6}
              onChange={(event) => setForm((previous) => ({ ...previous, hs6: event.target.value }))}
              className="rounded border border-gray-300 px-3 py-2"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="cell-nowrap text-gray-600">기본공급사 (선택)</span>
            <select
              name="default_supplier_partner_id"
              value={form.default_supplier_partner_id}
              onChange={(event) =>
                setForm((previous) => ({
                  ...previous,
                  default_supplier_partner_id: event.target.value,
                }))
              }
              className="rounded border border-gray-300 px-3 py-2"
            >
              <option value="">없음</option>
              {supplierPartners?.map((partner) => (
                <option key={partner.id} value={partner.id}>
                  {partner.name_ko} ({partner.partner_code})
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              name="inventory_managed"
              checked={form.inventory_managed}
              onChange={(event) =>
                setForm((previous) => ({
                  ...previous,
                  inventory_managed: event.target.checked,
                  // 재고관리를 끄면 로트관리도 꺼진다 — 서버·DB가 그 조합을 거부한다.
                  lot_managed: event.target.checked ? previous.lot_managed : false,
                }))
              }
            />
            <span className="cell-nowrap text-gray-600">재고관리</span>
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              name="lot_managed"
              checked={form.lot_managed}
              onChange={(event) =>
                setForm((previous) => ({
                  ...previous,
                  lot_managed: event.target.checked,
                  inventory_managed: event.target.checked ? true : previous.inventory_managed,
                }))
              }
            />
            <span className="cell-nowrap text-gray-600">로트관리</span>
          </label>
          <button
            type="submit"
            disabled={register.isPending}
            className="rounded bg-gray-900 px-4 py-2 text-sm text-white disabled:opacity-50"
          >
            {register.isPending ? "등록 중…" : "등록"}
          </button>
          {message && (
            <p role="alert" className="w-full text-sm text-signal-red">
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
              ? "등록된 자재가 없습니다. 위에서 코드·이름·유형을 입력해 등록하세요."
              : "등록된 자재가 없습니다."
          }
        >
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left">
              <tr>
                <th className="cell-nowrap px-4 py-2">자재코드</th>
                <th className="px-4 py-2">자재명(국문)</th>
                <th className="cell-nowrap px-4 py-2">유형</th>
                <th className="cell-nowrap px-4 py-2 num">HS6</th>
                <th className="px-4 py-2">기본공급사</th>
                <th className="cell-nowrap px-4 py-2 num">재고관리</th>
                <th className="cell-nowrap px-4 py-2 num">로트관리</th>
              </tr>
            </thead>
            <tbody>
              {list.data?.items.map((material) => (
                <tr key={material.id} className="border-t border-gray-100">
                  <td className="cell-nowrap px-4 py-2">{material.material_code}</td>
                  <td className="px-4 py-2">{material.name_ko}</td>
                  <td className="cell-nowrap px-4 py-2">
                    {materialTypeLabel(material.material_type)}
                  </td>
                  <td className="cell-nowrap px-4 py-2 num">{orEmpty(material.hs6)}</td>
                  <td className="px-4 py-2">{orEmpty(material.default_supplier_name)}</td>
                  <td className="cell-nowrap px-4 py-2 num">
                    {material.inventory_managed ? "○" : ""}
                  </td>
                  <td className="cell-nowrap px-4 py-2 num">{material.lot_managed ? "○" : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </ListState>
      </div>
    </section>
  );
}
