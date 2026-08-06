// SKU 목록·등록 (DESIGN.md §4.1·§4.2 / ADR-0016).
//
// ★ 화면이 막는 것은 **표시**일 뿐 보안이 아니다. 단품에 처방을 강제하고 세트에
//   처방·위험물 칸을 감추는 것은 실수를 줄이려는 것이지, 그것이 방어선은 아니다.
//   실제 차단은 서버의 검증과 DB CHECK가 한다(§18.1 "인가는 API에서 강제",
//   §17.5 "가능한 불변식은 CHECK"). 개발자 도구로 폼을 바꿔 보내도 서버가 막는다.

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";
import { ListState } from "../components/list-state";
import { apiFetch } from "../lib/api";
import { kindLabel, orEmpty, statusLabel } from "../lib/labels";
import { usePagedQuery } from "../lib/paging";
import { hasRole, useSession } from "../lib/session";
import { fieldMessage } from "./brands";
import { ITEM_PROFILES_QUERY_KEY, type ItemProfile } from "./item-profiles";
import { PARTNERS_QUERY_KEY, PARTNERS_SELECT_PATH, type Partner } from "./partners";
import { PRODUCTS_QUERY_KEY, type Product } from "./products";

export interface Sku {
  id: number;
  sku_code: string;
  name_ko: string;
  name_en: string | null;
  status: string;
  kind: string;
  product_id: number | null;
  product_code: string | null;
  product_name_ko: string | null;
  brand_name_ko: string | null;
  item_profile_id: number | null;
  item_profile_name_ko: string | null;
  barcode: string | null;
  unit_weight_g: string | null;
  box_qty: number | null;
  shelf_life_months: number | null;
  manufacturer_partner_id: number | null;
  manufacturer_name: string | null;
  dg_flag: boolean;
  un_number: string | null;
  dg_class: string | null;
  packing_group: string | null;
  flash_point_c: string | null;
  alcohol_content_pct: string | null;
  is_aerosol: boolean;
  is_limited_quantity: boolean;
  // MSDS는 §4.7 documents로 승격 완료(ADR-0020) — SKU 필드가 아니다.
}

export const SKUS_QUERY_KEY = ["skus"] as const;

const BLANK = {
  barcode: "",
  unit_weight_g: "",
  box_qty: "",
  shelf_life_months: "",
  un_number: "",
  dg_class: "",
  packing_group: "",
  flash_point_c: "",
  alcohol_content_pct: "",
};

/** 빈 문자열은 보내지 않는다 — 서버가 "값 없음"과 "빈 값"을 구분하게. */
function optional(value: string): string | undefined {
  const trimmed = value.trim();
  return trimmed === "" ? undefined : trimmed;
}

function optionalNumber(value: string): number | undefined {
  const trimmed = value.trim();
  return trimmed === "" ? undefined : Number(trimmed);
}

export function SkuListPage() {
  const { me } = useSession();
  const client = useQueryClient();
  const canRegister = hasRole(me, "TRADE");

  const [kind, setKind] = useState("SINGLE");
  const [code, setCode] = useState("");
  const [nameKo, setNameKo] = useState("");
  const [nameEn, setNameEn] = useState("");
  const [productId, setProductId] = useState("");
  const [profileId, setProfileId] = useState("");
  const [manufacturerId, setManufacturerId] = useState("");
  const [dgFlag, setDgFlag] = useState(false);
  const [isAerosol, setIsAerosol] = useState(false);
  const [isLq, setIsLq] = useState(false);
  const [extra, setExtra] = useState({ ...BLANK });

  const isSet = kind === "SET";

  const list = usePagedQuery<Sku>(SKUS_QUERY_KEY, "/v1/skus");
  const products = usePagedQuery<Product>(PRODUCTS_QUERY_KEY, "/v1/products", canRegister);
  const profiles = usePagedQuery<ItemProfile>(
    ITEM_PROFILES_QUERY_KEY,
    "/v1/item-profiles",
    canRegister,
  );
  const partners = usePagedQuery<Partner>(PARTNERS_QUERY_KEY, PARTNERS_SELECT_PATH, canRegister);
  // 제조사는 OEM 유형 거래처만 지정할 수 있다([M1] 보강(S1-3) ⑤) — 서버가
  // 최종 판정하고, 화면은 후보를 미리 좁혀 실수를 줄인다.
  const oemPartners = partners.data?.items.filter((p) => p.type_codes.includes("OEM"));

  const register = useMutation({
    mutationFn: (input: Record<string, unknown>) =>
      // 멱등 키는 apiFetch가 자동으로 붙인다(§17.4) — 더블클릭해도 1건이다.
      apiFetch<Sku>("/v1/skus", { method: "POST", body: input }),
    onSuccess: () => {
      setCode("");
      setNameKo("");
      setNameEn("");
      setManufacturerId("");
      setDgFlag(false);
      setIsAerosol(false);
      setIsLq(false);
      setExtra({ ...BLANK });
      void client.invalidateQueries({ queryKey: SKUS_QUERY_KEY });
    },
  });

  const message = fieldMessage(register.error);
  const noProducts = products.data?.items.length === 0;

  function submit() {
    register.mutate({
      sku_code: code,
      name_ko: nameKo,
      name_en: optional(nameEn),
      kind,
      // 세트는 처방을 갖지 않는다(§4.2) — 아예 보내지 않는다.
      product_id: isSet ? undefined : Number(productId),
      // 품목군은 선택이다 — 안 고르면 아예 보내지 않는다.
      item_profile_id: profileId === "" ? undefined : Number(profileId),
      barcode: optional(extra.barcode),
      unit_weight_g: optional(extra.unit_weight_g),
      box_qty: optionalNumber(extra.box_qty),
      shelf_life_months: optionalNumber(extra.shelf_life_months),
      manufacturer_partner_id: manufacturerId === "" ? undefined : Number(manufacturerId),
      // 세트에는 위험물 정보를 담지 않는다(ADR-0016 부기 — P4 DG 게이트까지 미결).
      dg_flag: isSet ? false : dgFlag,
      un_number: isSet || !dgFlag ? undefined : optional(extra.un_number),
      dg_class: isSet || !dgFlag ? undefined : optional(extra.dg_class),
      packing_group: isSet || !dgFlag ? undefined : optional(extra.packing_group),
      flash_point_c: isSet ? undefined : optional(extra.flash_point_c),
      alcohol_content_pct: isSet ? undefined : optional(extra.alcohol_content_pct),
      is_aerosol: isSet ? false : isAerosol,
      is_limited_quantity: isSet || !dgFlag ? false : isLq,
    });
  }

  const field = (key: keyof typeof BLANK) => ({
    value: extra[key],
    onChange: (event: React.ChangeEvent<HTMLInputElement>) =>
      setExtra((previous) => ({ ...previous, [key]: event.target.value })),
    className: "rounded border border-gray-300 px-3 py-2",
  });

  return (
    <section>
      <header className="flex items-baseline justify-between">
        <h1 className="text-2xl font-bold">SKU</h1>
        <div className="flex items-baseline gap-4">
          <a href="/api/v1/skus/export.csv" className="cell-nowrap text-sm text-gray-700 underline">
            CSV 내보내기
          </a>
          <a
            href="/api/v1/labels/export.csv"
            className="cell-nowrap text-sm text-gray-700 underline"
          >
            라벨 CSV 내보내기
          </a>
        </div>
      </header>

      {canRegister && (
        <form
          className="mt-6 rounded-lg border border-gray-200 p-4"
          onSubmit={(event) => {
            event.preventDefault();
            submit();
          }}
        >
          <div className="flex flex-wrap items-end gap-3">
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">종류</span>
              <select
                name="kind"
                value={kind}
                onChange={(event) => setKind(event.target.value)}
                className="rounded border border-gray-300 px-3 py-2"
              >
                <option value="SINGLE">단품</option>
                <option value="SET">세트</option>
              </select>
            </label>

            {/* 단품만 처방을 갖는다(§4.1·§4.2 / ADR-0016). 세트는 칸 자체가 없다. */}
            {!isSet && (
              <label className="flex flex-col gap-1 text-sm">
                <span className="cell-nowrap text-gray-600">제품(처방)</span>
                <select
                  name="product_id"
                  required
                  value={productId}
                  onChange={(event) => setProductId(event.target.value)}
                  className="rounded border border-gray-300 px-3 py-2"
                >
                  <option value="">선택하세요</option>
                  {products.data?.items.map((product) => (
                    <option key={product.id} value={product.id}>
                      {product.name_ko} ({product.product_code})
                    </option>
                  ))}
                </select>
              </label>
            )}

            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">품번</span>
              <input
                name="sku_code"
                required
                maxLength={40}
                value={code}
                onChange={(event) => setCode(event.target.value)}
                className="rounded border border-gray-300 px-3 py-2"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">품명(국문)</span>
              <input
                name="name_ko"
                required
                value={nameKo}
                onChange={(event) => setNameKo(event.target.value)}
                className="rounded border border-gray-300 px-3 py-2"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">품명(영문)</span>
              <input
                name="name_en"
                value={nameEn}
                onChange={(event) => setNameEn(event.target.value)}
                className="rounded border border-gray-300 px-3 py-2"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">품목군 (선택)</span>
              <select
                name="item_profile_id"
                value={profileId}
                onChange={(event) => setProfileId(event.target.value)}
                className="rounded border border-gray-300 px-3 py-2"
              >
                <option value="">없음</option>
                {profiles.data?.items.map((profile) => (
                  <option key={profile.id} value={profile.id}>
                    {profile.name_ko}
                  </option>
                ))}
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

          <details className="mt-4">
            <summary className="cursor-pointer text-sm text-gray-600">
              물류·위험물 정보 (선택)
            </summary>
            <div className="mt-3 flex flex-wrap items-end gap-3">
              <label className="flex flex-col gap-1 text-sm">
                <span className="cell-nowrap text-gray-600">바코드</span>
                <input name="barcode" maxLength={20} {...field("barcode")} />
              </label>
              <label className="flex flex-col gap-1 text-sm">
                <span className="cell-nowrap text-gray-600">중량(g)</span>
                <input name="unit_weight_g" inputMode="decimal" {...field("unit_weight_g")} />
              </label>
              <label className="flex flex-col gap-1 text-sm">
                <span className="cell-nowrap text-gray-600">박스입수</span>
                <input name="box_qty" inputMode="numeric" {...field("box_qty")} />
              </label>
              <label className="flex flex-col gap-1 text-sm">
                <span className="cell-nowrap text-gray-600">사용기한(개월)</span>
                <input
                  name="shelf_life_months"
                  inputMode="numeric"
                  {...field("shelf_life_months")}
                />
              </label>
              <label className="flex flex-col gap-1 text-sm">
                <span className="cell-nowrap text-gray-600">제조사 (OEM 거래처)</span>
                <select
                  name="manufacturer_partner_id"
                  value={manufacturerId}
                  onChange={(event) => setManufacturerId(event.target.value)}
                  className="rounded border border-gray-300 px-3 py-2"
                >
                  <option value="">없음</option>
                  {oemPartners?.map((partner) => (
                    <option key={partner.id} value={partner.id}>
                      {partner.name_ko} ({partner.partner_code})
                    </option>
                  ))}
                </select>
              </label>
            </div>

            {isSet ? (
              <p className="mt-3 text-sm text-gray-500">
                세트에는 위험물 정보를 입력하지 않습니다. 위험물 판정은 구성품 단위입니다.
              </p>
            ) : (
              <div className="mt-3 flex flex-wrap items-end gap-3">
                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    name="dg_flag"
                    checked={dgFlag}
                    onChange={(event) => setDgFlag(event.target.checked)}
                  />
                  <span className="cell-nowrap text-gray-600">위험물</span>
                </label>
                <label className="flex flex-col gap-1 text-sm">
                  <span className="cell-nowrap text-gray-600">인화점(℃)</span>
                  <input name="flash_point_c" inputMode="decimal" {...field("flash_point_c")} />
                </label>
                <label className="flex flex-col gap-1 text-sm">
                  <span className="cell-nowrap text-gray-600">알코올함량(%)</span>
                  <input
                    name="alcohol_content_pct"
                    inputMode="decimal"
                    {...field("alcohol_content_pct")}
                  />
                </label>
                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    name="is_aerosol"
                    checked={isAerosol}
                    onChange={(event) => setIsAerosol(event.target.checked)}
                  />
                  <span className="cell-nowrap text-gray-600">에어로졸</span>
                </label>
                {/* MSDS는 문서보관소(§4.7)에서 파일·링크로 등록한다 — SKU 등록
                    항목이 아니다(ADR-0020 승격 완료). */}

                {/* UN번호·Class·포장등급·LQ는 위험물일 때만 의미가 있다(ADR-0016 정정).
                    인화점·알코올함량·에어로졸은 "왜 위험물이 아닌지"의 근거라 위에 둔다. */}
                <fieldset
                  disabled={!dgFlag}
                  className="flex flex-wrap items-end gap-3 disabled:opacity-50"
                >
                  <label className="flex flex-col gap-1 text-sm">
                    <span className="cell-nowrap text-gray-600">UN번호</span>
                    <input name="un_number" maxLength={10} {...field("un_number")} />
                  </label>
                  <label className="flex flex-col gap-1 text-sm">
                    <span className="cell-nowrap text-gray-600">Class</span>
                    <input name="dg_class" maxLength={10} {...field("dg_class")} />
                  </label>
                  <label className="flex flex-col gap-1 text-sm">
                    <span className="cell-nowrap text-gray-600">포장등급</span>
                    <input name="packing_group" maxLength={3} {...field("packing_group")} />
                  </label>
                  <label className="flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      name="is_limited_quantity"
                      checked={isLq}
                      onChange={(event) => setIsLq(event.target.checked)}
                    />
                    <span className="cell-nowrap text-gray-600">LQ</span>
                  </label>
                </fieldset>
              </div>
            )}
          </details>

          {!isSet && noProducts && (
            <p className="mt-3 text-sm text-gray-500">
              먼저 제품(처방)을 등록하세요. 단품 SKU는 처방 없이 만들 수 없습니다.
            </p>
          )}
          {message && (
            <p role="alert" className="mt-3 text-sm text-signal-red">
              {message}
            </p>
          )}
        </form>
      )}

      <p className="mt-6 text-sm text-gray-500">
        전체 {list.data?.total ?? 0}건
        {list.data ? ` (${list.data.page}쪽 · ${list.data.size}건씩)` : ""}
      </p>

      <div className="mt-3 overflow-x-auto rounded-lg border border-gray-200">
        <ListState
          isPending={list.isPending}
          error={list.error}
          isEmpty={list.data?.items.length === 0}
          emptyHint={
            canRegister
              ? "등록된 SKU가 없습니다. 위에서 품번과 품명을 입력해 등록하세요."
              : "등록된 SKU가 없습니다."
          }
        >
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left">
              <tr>
                <th className="cell-nowrap px-4 py-2">품번</th>
                <th className="px-4 py-2">품명(국문)</th>
                <th className="cell-nowrap px-4 py-2 num">종류</th>
                <th className="px-4 py-2">제품(처방)</th>
                <th className="cell-nowrap px-4 py-2">브랜드</th>
                <th className="cell-nowrap px-4 py-2 num">위험물</th>
                <th className="cell-nowrap px-4 py-2 num">상태</th>
              </tr>
            </thead>
            <tbody>
              {list.data?.items.map((sku) => (
                <tr key={sku.id} className="border-t border-gray-100">
                  <td className="cell-nowrap px-4 py-2">
                    <Link to={`/skus/${sku.id}`} className="underline">
                      {sku.sku_code}
                    </Link>
                  </td>
                  <td className="px-4 py-2">{sku.name_ko}</td>
                  <td className="cell-nowrap px-4 py-2 num">{kindLabel(sku.kind)}</td>
                  <td className="px-4 py-2">{orEmpty(sku.product_name_ko)}</td>
                  <td className="cell-nowrap px-4 py-2">{orEmpty(sku.brand_name_ko)}</td>
                  <td className="cell-nowrap px-4 py-2 num">{sku.dg_flag ? "예" : "아니오"}</td>
                  <td className="cell-nowrap px-4 py-2 num">{statusLabel(sku.status)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </ListState>
      </div>
    </section>
  );
}
