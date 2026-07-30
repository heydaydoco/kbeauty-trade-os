// SKU 상세 — 속성 + 국가별 HS 세번 + 세트 구성 (DESIGN.md §4.1·§4.2 / ADR-0019).
//
// ★ HS는 **기록**이지 판정이 아니다(§1 비범위). 화면에 "추천"·"자동 조회" 버튼을
//   만들지 않는다 — 그 버튼은 곧 사람이 확인하지 않은 세번이 된다.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router";
import { ListState } from "../components/list-state";
import { apiFetch } from "../lib/api";
import { approvalStatusLabel, kindLabel, orEmpty, statusLabel } from "../lib/labels";
import { formatMoney, useCurrencies } from "../lib/money";
import { usePagedQuery } from "../lib/paging";
import { hasRole, useSession } from "../lib/session";
import { fieldMessage } from "./brands";
import type { Sku } from "./skus";

interface HsCode {
  id: number;
  country_code: string;
  hs_version: string;
  hs_code: string;
  tariff_note: string | null;
  source_url: string;
  last_verified_on: string;
}

interface SkuPriceRow {
  id: number;
  price_type: string;
  currency: string;
  /** 정수 최소단위. 표시 변환은 통화별 자릿수(서버 제공)로만 한다. */
  amount: number;
  effective_from: string;
  note: string | null;
  is_current: boolean;
}

const PRICE_TYPE_LABEL: Record<string, string> = {
  SALES: "판가",
  PURCHASE: "매입가",
};

interface LabelRow {
  id: number;
  country_code: string;
  label_version: number;
  language: string;
  approval_status: string;
  cut_in_date: string | null;
  file_url: string | null;
  inci_local_verified: boolean;
  origin_mark_verified: boolean;
  note: string | null;
}

interface SetComponentRow {
  id: number;
  component_sku_id: number;
  component_sku_code: string;
  component_name_ko: string;
  component_shelf_life_months: number | null;
  quantity: number;
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-2 border-b border-gray-100 py-2 text-sm">
      <span className="cell-nowrap w-32 shrink-0 text-gray-500">{label}</span>
      <span>{children}</span>
    </div>
  );
}

export function SkuDetailPage() {
  const { skuId } = useParams();
  const { me } = useSession();
  const client = useQueryClient();
  const canEdit = hasRole(me, "TRADE");

  const skuKey = ["sku", skuId] as const;
  const hsKey = ["sku", skuId, "hs-codes"] as const;
  const componentsKey = ["sku", skuId, "components"] as const;

  const sku = useQuery({
    queryKey: skuKey,
    queryFn: () => apiFetch<Sku>(`/v1/skus/${skuId}`),
  });

  const hsCodes = usePagedQuery<HsCode>(hsKey, `/v1/skus/${skuId}/hs-codes`);
  const isSet = sku.data?.kind === "SET";
  const components = usePagedQuery<SetComponentRow>(
    componentsKey,
    `/v1/skus/${skuId}/components`,
    isSet,
  );

  const labelsKey = ["sku", skuId, "labels"] as const;
  const labels = usePagedQuery<LabelRow>(labelsKey, `/v1/skus/${skuId}/labels`);

  const [label, setLabel] = useState({
    country_code: "",
    label_version: "1",
    language: "en",
    approval_status: "DRAFT",
    cut_in_date: "",
    file_url: "",
    inci_local_verified: false,
    origin_mark_verified: false,
  });

  const addLabel = useMutation({
    mutationFn: () =>
      apiFetch<LabelRow>(`/v1/skus/${skuId}/labels`, {
        method: "POST",
        body: {
          country_code: label.country_code,
          label_version: Number(label.label_version),
          language: label.language,
          approval_status: label.approval_status,
          // 빈 값은 "없음"이다 — 빈 문자열을 보내면 서버가 형식 오류로 본다.
          cut_in_date: label.cut_in_date === "" ? undefined : label.cut_in_date,
          file_url: label.file_url === "" ? undefined : label.file_url,
          inci_local_verified: label.inci_local_verified,
          origin_mark_verified: label.origin_mark_verified,
        },
      }),
    onSuccess: () => {
      setLabel((previous) => ({
        ...previous,
        country_code: "",
        cut_in_date: "",
        file_url: "",
      }));
      void client.invalidateQueries({ queryKey: labelsKey });
    },
  });

  const pricesKey = ["sku", skuId, "prices"] as const;
  const prices = usePagedQuery<SkuPriceRow>(pricesKey, `/v1/skus/${skuId}/prices`);
  const currencies = useCurrencies();

  const [price, setPrice] = useState({
    price_type: "SALES",
    currency: "KRW",
    amount: "",
    effective_from: "",
  });

  const addPrice = useMutation({
    mutationFn: () =>
      apiFetch<SkuPriceRow>(`/v1/skus/${skuId}/prices`, { method: "POST", body: price }),
    onSuccess: () => {
      setPrice((previous) => ({ ...previous, amount: "", effective_from: "" }));
      void client.invalidateQueries({ queryKey: pricesKey });
    },
  });

  const [hs, setHs] = useState({
    country_code: "",
    hs_version: "HS2022",
    hs_code: "",
    tariff_note: "",
    source_url: "",
    last_verified_on: "",
  });

  const addHs = useMutation({
    mutationFn: () =>
      apiFetch<HsCode>(`/v1/skus/${skuId}/hs-codes`, {
        method: "POST",
        body: {
          ...hs,
          tariff_note: hs.tariff_note.trim() === "" ? undefined : hs.tariff_note,
        },
      }),
    onSuccess: () => {
      setHs((previous) => ({ ...previous, country_code: "", hs_code: "", source_url: "" }));
      void client.invalidateQueries({ queryKey: hsKey });
    },
  });

  const [component, setComponent] = useState({ component_sku_id: "", quantity: "1" });

  const addComponent = useMutation({
    mutationFn: () =>
      apiFetch<SetComponentRow>(`/v1/skus/${skuId}/components`, {
        method: "POST",
        body: {
          component_sku_id: Number(component.component_sku_id),
          quantity: Number(component.quantity),
        },
      }),
    onSuccess: () => {
      setComponent({ component_sku_id: "", quantity: "1" });
      void client.invalidateQueries({ queryKey: componentsKey });
    },
  });

  const hsField = (key: keyof typeof hs) => ({
    value: hs[key],
    onChange: (event: React.ChangeEvent<HTMLInputElement>) =>
      setHs((previous) => ({ ...previous, [key]: event.target.value })),
    className: "rounded border border-gray-300 px-3 py-2",
  });

  if (sku.isPending) return <p className="text-gray-500">불러오는 중…</p>;
  if (sku.error || !sku.data) {
    return (
      <div role="alert" className="text-signal-red">
        <p>{fieldMessage(sku.error) ?? "SKU를 찾을 수 없습니다."}</p>
        <Link to="/skus" className="mt-2 inline-block text-sm text-gray-700 underline">
          목록으로
        </Link>
      </div>
    );
  }

  const item = sku.data;

  return (
    <section className="flex flex-col gap-8">
      <header>
        <Link to="/skus" className="text-sm text-gray-500 underline">
          ← SKU 목록
        </Link>
        <h1 className="mt-2 text-2xl font-bold">{item.name_ko}</h1>
        <p className="mt-1 text-sm text-gray-500">
          {item.sku_code} · {kindLabel(item.kind)} · {statusLabel(item.status)}
        </p>
      </header>

      <div>
        <h2 className="text-lg font-semibold">기본 정보</h2>
        <div className="mt-2">
          <Row label="제품(처방)">
            {item.product_name_ko ? (
              `${item.product_name_ko} (${item.product_code})`
            ) : (
              <span className="text-gray-500">
                세트는 처방을 갖지 않습니다 — 인증·원산지는 구성품별로 관리합니다.
              </span>
            )}
          </Row>
          <Row label="브랜드">{orEmpty(item.brand_name_ko)}</Row>
          <Row label="품명(영문)">{orEmpty(item.name_en)}</Row>
          <Row label="바코드">{orEmpty(item.barcode)}</Row>
          <Row label="중량(g)">{orEmpty(item.unit_weight_g)}</Row>
          <Row label="박스입수">{orEmpty(item.box_qty)}</Row>
          <Row label="사용기한(개월)">{orEmpty(item.shelf_life_months)}</Row>
          <Row label="제조사">{orEmpty(item.manufacturer_name)}</Row>
        </div>
      </div>

      {!isSet && (
        <div>
          <h2 className="text-lg font-semibold">위험물(DG)</h2>
          <div className="mt-2">
            <Row label="위험물">{item.dg_flag ? "예" : "아니오"}</Row>
            <Row label="UN번호">{orEmpty(item.un_number)}</Row>
            <Row label="Class">{orEmpty(item.dg_class)}</Row>
            <Row label="포장등급">{orEmpty(item.packing_group)}</Row>
            <Row label="인화점(℃)">{orEmpty(item.flash_point_c)}</Row>
            <Row label="알코올함량(%)">{orEmpty(item.alcohol_content_pct)}</Row>
            <Row label="에어로졸">{item.is_aerosol ? "예" : "아니오"}</Row>
            <Row label="LQ">{item.is_limited_quantity ? "예" : "아니오"}</Row>
            <Row label="MSDS 링크">{orEmpty(item.msds_url)}</Row>
          </div>
        </div>
      )}

      {isSet && (
        <div>
          <h2 className="text-lg font-semibold">세트 구성</h2>
          <p className="mt-1 text-sm text-gray-500">
            세트 로트의 유통기한은 구성품 로트 유통기한 중 가장 이른 값을 따릅니다. 실제 로트
            연결은 재고 단계에서 이뤄집니다.
          </p>

          {canEdit && (
            <form
              className="mt-3 flex flex-wrap items-end gap-3 rounded-lg border border-gray-200 p-4"
              onSubmit={(event) => {
                event.preventDefault();
                addComponent.mutate();
              }}
            >
              <label className="flex flex-col gap-1 text-sm">
                <span className="cell-nowrap text-gray-600">구성품 SKU 번호</span>
                <input
                  name="component_sku_id"
                  required
                  inputMode="numeric"
                  value={component.component_sku_id}
                  onChange={(event) =>
                    setComponent((previous) => ({
                      ...previous,
                      component_sku_id: event.target.value,
                    }))
                  }
                  className="rounded border border-gray-300 px-3 py-2"
                />
              </label>
              <label className="flex flex-col gap-1 text-sm">
                <span className="cell-nowrap text-gray-600">수량</span>
                <input
                  name="quantity"
                  required
                  inputMode="numeric"
                  value={component.quantity}
                  onChange={(event) =>
                    setComponent((previous) => ({ ...previous, quantity: event.target.value }))
                  }
                  className="rounded border border-gray-300 px-3 py-2"
                />
              </label>
              <button
                type="submit"
                disabled={addComponent.isPending}
                className="rounded bg-gray-900 px-4 py-2 text-sm text-white disabled:opacity-50"
              >
                구성품 추가
              </button>
              {fieldMessage(addComponent.error) && (
                <p role="alert" className="w-full text-sm text-signal-red">
                  {fieldMessage(addComponent.error)}
                </p>
              )}
            </form>
          )}

          <div className="mt-3 overflow-x-auto rounded-lg border border-gray-200">
            <ListState
              isPending={components.isPending}
              error={components.error}
              isEmpty={components.data?.items.length === 0}
              emptyHint="구성품이 없습니다. 세트에 들어갈 단품과 수량을 추가하세요."
            >
              <table className="w-full text-sm">
                <thead className="bg-gray-50 text-left">
                  <tr>
                    <th className="cell-nowrap px-4 py-2">구성품 품번</th>
                    <th className="px-4 py-2">품명(국문)</th>
                    <th className="cell-nowrap px-4 py-2 num">수량</th>
                    <th className="cell-nowrap px-4 py-2 num">사용기한(개월)</th>
                  </tr>
                </thead>
                <tbody>
                  {components.data?.items.map((row) => (
                    <tr key={row.id} className="border-t border-gray-100">
                      <td className="cell-nowrap px-4 py-2">{row.component_sku_code}</td>
                      <td className="px-4 py-2">{row.component_name_ko}</td>
                      <td className="cell-nowrap px-4 py-2 num">{row.quantity}</td>
                      <td className="cell-nowrap px-4 py-2 num">
                        {orEmpty(row.component_shelf_life_months)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </ListState>
          </div>
        </div>
      )}

      <div>
        <h2 className="text-lg font-semibold">단가 이력</h2>
        <p className="mt-1 text-sm text-gray-500">
          발효일부터 적용됩니다. 다음 발효일이 곧 이전 단가의 종료라, 기간이 겹칠 수 없습니다.
          매입가는 원가라 조회 권한만 있는 사용자에게는 보이지 않습니다.
        </p>

        {canEdit && (
          <form
            className="mt-3 flex flex-wrap items-end gap-3 rounded-lg border border-gray-200 p-4"
            onSubmit={(event) => {
              event.preventDefault();
              addPrice.mutate();
            }}
          >
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">종류</span>
              <select
                name="price_type"
                value={price.price_type}
                onChange={(event) =>
                  setPrice((previous) => ({ ...previous, price_type: event.target.value }))
                }
                className="rounded border border-gray-300 px-3 py-2"
              >
                <option value="SALES">판가</option>
                <option value="PURCHASE">매입가</option>
              </select>
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">통화</span>
              <select
                name="currency"
                value={price.currency}
                onChange={(event) =>
                  setPrice((previous) => ({ ...previous, currency: event.target.value }))
                }
                className="rounded border border-gray-300 px-3 py-2"
              >
                {currencies.data?.items.map((currency) => (
                  <option key={currency.code} value={currency.code}>
                    {currency.code}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">금액</span>
              {/* 사람이 쓰는 표기 그대로 보낸다(12000 / 12.34). 최소단위 환산은
                  서버가 한다 — 화면마다 환산하면 그 규칙이 갈린다. */}
              <input
                name="amount"
                required
                inputMode="decimal"
                value={price.amount}
                onChange={(event) =>
                  setPrice((previous) => ({ ...previous, amount: event.target.value }))
                }
                className="rounded border border-gray-300 px-3 py-2"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">발효일</span>
              <input
                name="effective_from"
                required
                type="date"
                value={price.effective_from}
                onChange={(event) =>
                  setPrice((previous) => ({ ...previous, effective_from: event.target.value }))
                }
                className="rounded border border-gray-300 px-3 py-2"
              />
            </label>
            <button
              type="submit"
              disabled={addPrice.isPending}
              className="rounded bg-gray-900 px-4 py-2 text-sm text-white disabled:opacity-50"
            >
              단가 등록
            </button>
            {fieldMessage(addPrice.error) && (
              <p role="alert" className="w-full text-sm text-signal-red">
                {fieldMessage(addPrice.error)}
              </p>
            )}
          </form>
        )}

        <div className="mt-3 overflow-x-auto rounded-lg border border-gray-200">
          <ListState
            isPending={prices.isPending || currencies.isPending}
            error={prices.error ?? currencies.error}
            isEmpty={prices.data?.items.length === 0}
            emptyHint="등록된 단가가 없습니다. 종류·통화·금액과 발효일을 입력하세요."
          >
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-left">
                <tr>
                  <th className="cell-nowrap px-4 py-2 num">종류</th>
                  <th className="cell-nowrap px-4 py-2 num">금액</th>
                  <th className="cell-nowrap px-4 py-2 num">발효일</th>
                  <th className="cell-nowrap px-4 py-2 num">현재 적용</th>
                  <th className="px-4 py-2">비고</th>
                </tr>
              </thead>
              <tbody>
                {prices.data?.items.map((row) => (
                  <tr key={row.id} className="border-t border-gray-100">
                    <td className="cell-nowrap px-4 py-2 num">
                      {PRICE_TYPE_LABEL[row.price_type] ?? row.price_type}
                    </td>
                    <td className="cell-nowrap px-4 py-2 num">
                      {formatMoney(row.amount, row.currency, currencies.data?.items ?? [])}
                    </td>
                    <td className="cell-nowrap px-4 py-2 num">{row.effective_from}</td>
                    <td className="cell-nowrap px-4 py-2 num">{row.is_current ? "○" : ""}</td>
                    <td className="px-4 py-2">{orEmpty(row.note)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </ListState>
        </div>
      </div>

      <div>
        <h2 className="text-lg font-semibold">라벨·아트웍</h2>
        <p className="mt-1 text-sm text-gray-500">
          시장(국가)×판번으로 관리합니다. 검증 항목은 <strong>사람이 확인했다는 기록</strong>이고,
          시스템이 라벨을 읽어 판정하지 않습니다. 새 판이면 판번을 올려 등록하세요.
        </p>

        {canEdit && (
          <form
            className="mt-3 flex flex-wrap items-end gap-3 rounded-lg border border-gray-200 p-4"
            onSubmit={(event) => {
              event.preventDefault();
              addLabel.mutate();
            }}
          >
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">시장(2자리)</span>
              <input
                name="country_code"
                required
                maxLength={2}
                value={label.country_code}
                onChange={(event) =>
                  setLabel((previous) => ({ ...previous, country_code: event.target.value }))
                }
                className="rounded border border-gray-300 px-3 py-2"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">판번</span>
              <input
                name="label_version"
                required
                inputMode="numeric"
                value={label.label_version}
                onChange={(event) =>
                  setLabel((previous) => ({ ...previous, label_version: event.target.value }))
                }
                className="rounded border border-gray-300 px-3 py-2"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">언어</span>
              <input
                name="language"
                required
                maxLength={10}
                value={label.language}
                onChange={(event) =>
                  setLabel((previous) => ({ ...previous, language: event.target.value }))
                }
                className="rounded border border-gray-300 px-3 py-2"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">승인상태</span>
              <select
                name="approval_status"
                value={label.approval_status}
                onChange={(event) =>
                  setLabel((previous) => ({ ...previous, approval_status: event.target.value }))
                }
                className="rounded border border-gray-300 px-3 py-2"
              >
                <option value="DRAFT">초안</option>
                <option value="APPROVED">승인</option>
                <option value="RETIRED">폐기</option>
              </select>
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">컷인일 (선택)</span>
              <input
                name="cut_in_date"
                type="date"
                value={label.cut_in_date}
                onChange={(event) =>
                  setLabel((previous) => ({ ...previous, cut_in_date: event.target.value }))
                }
                className="rounded border border-gray-300 px-3 py-2"
              />
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                name="inci_local_verified"
                checked={label.inci_local_verified}
                onChange={(event) =>
                  setLabel((previous) => ({
                    ...previous,
                    inci_local_verified: event.target.checked,
                  }))
                }
              />
              <span className="cell-nowrap text-gray-600">INCI 현지어 확인</span>
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                name="origin_mark_verified"
                checked={label.origin_mark_verified}
                onChange={(event) =>
                  setLabel((previous) => ({
                    ...previous,
                    origin_mark_verified: event.target.checked,
                  }))
                }
              />
              <span className="cell-nowrap text-gray-600">원산지 표기 확인</span>
            </label>
            <button
              type="submit"
              disabled={addLabel.isPending}
              className="rounded bg-gray-900 px-4 py-2 text-sm text-white disabled:opacity-50"
            >
              라벨 판 등록
            </button>
            {fieldMessage(addLabel.error) && (
              <p role="alert" className="w-full text-sm text-signal-red">
                {fieldMessage(addLabel.error)}
              </p>
            )}
          </form>
        )}

        <div className="mt-3 overflow-x-auto rounded-lg border border-gray-200">
          <ListState
            isPending={labels.isPending}
            error={labels.error}
            isEmpty={labels.data?.items.length === 0}
            emptyHint="등록된 라벨 판이 없습니다. 시장·판번·언어를 입력해 등록하세요."
          >
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-left">
                <tr>
                  <th className="cell-nowrap px-4 py-2 num">시장</th>
                  <th className="cell-nowrap px-4 py-2 num">판번</th>
                  <th className="cell-nowrap px-4 py-2 num">언어</th>
                  <th className="cell-nowrap px-4 py-2 num">승인상태</th>
                  <th className="cell-nowrap px-4 py-2 num">컷인일</th>
                  <th className="cell-nowrap px-4 py-2 num">INCI 현지어</th>
                  <th className="cell-nowrap px-4 py-2 num">원산지 표기</th>
                  <th className="px-4 py-2">파일</th>
                </tr>
              </thead>
              <tbody>
                {labels.data?.items.map((row) => (
                  <tr key={row.id} className="border-t border-gray-100">
                    <td className="cell-nowrap px-4 py-2 num">{row.country_code}</td>
                    <td className="cell-nowrap px-4 py-2 num">{row.label_version}</td>
                    <td className="cell-nowrap px-4 py-2 num">{row.language}</td>
                    <td className="cell-nowrap px-4 py-2 num">
                      {approvalStatusLabel(row.approval_status)}
                    </td>
                    <td className="cell-nowrap px-4 py-2 num">{orEmpty(row.cut_in_date)}</td>
                    <td className="cell-nowrap px-4 py-2 num">
                      {row.inci_local_verified ? "○" : ""}
                    </td>
                    <td className="cell-nowrap px-4 py-2 num">
                      {row.origin_mark_verified ? "○" : ""}
                    </td>
                    <td className="px-4 py-2">
                      {row.file_url ? (
                        <a href={row.file_url} className="underline" rel="noreferrer noopener">
                          링크
                        </a>
                      ) : (
                        orEmpty(null)
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </ListState>
        </div>
      </div>

      <div>
        <h2 className="text-lg font-semibold">국가별 HS 세번</h2>
        <p className="mt-1 text-sm text-gray-500">
          시스템은 HS를 판정하지 않습니다. 확인한 세번과 <strong>근거 링크·확인일</strong>을 함께
          적습니다. 세율은 참고 메모이며 계산에 쓰이지 않습니다.
        </p>

        {canEdit && (
          <form
            className="mt-3 flex flex-wrap items-end gap-3 rounded-lg border border-gray-200 p-4"
            onSubmit={(event) => {
              event.preventDefault();
              addHs.mutate();
            }}
          >
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">국가(2자리)</span>
              <input name="country_code" required maxLength={2} {...hsField("country_code")} />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">HS 버전</span>
              <input name="hs_version" required maxLength={10} {...hsField("hs_version")} />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">HS 세번</span>
              <input name="hs_code" required {...hsField("hs_code")} />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">근거 링크</span>
              <input
                name="source_url"
                required
                type="url"
                placeholder="https://"
                {...hsField("source_url")}
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">최종확인일</span>
              <input
                name="last_verified_on"
                required
                type="date"
                {...hsField("last_verified_on")}
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">세율 메모</span>
              <input name="tariff_note" {...hsField("tariff_note")} />
            </label>
            <button
              type="submit"
              disabled={addHs.isPending}
              className="rounded bg-gray-900 px-4 py-2 text-sm text-white disabled:opacity-50"
            >
              세번 기록
            </button>
            {fieldMessage(addHs.error) && (
              <p role="alert" className="w-full text-sm text-signal-red">
                {fieldMessage(addHs.error)}
              </p>
            )}
          </form>
        )}

        <div className="mt-3 overflow-x-auto rounded-lg border border-gray-200">
          <ListState
            isPending={hsCodes.isPending}
            error={hsCodes.error}
            isEmpty={hsCodes.data?.items.length === 0}
            emptyHint="기록된 HS 세번이 없습니다. 확인한 세번과 근거 링크를 적어 주세요."
          >
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-left">
                <tr>
                  <th className="cell-nowrap px-4 py-2 num">국가</th>
                  <th className="cell-nowrap px-4 py-2 num">HS 버전</th>
                  <th className="cell-nowrap px-4 py-2 num">HS 세번</th>
                  <th className="px-4 py-2">세율 메모</th>
                  <th className="px-4 py-2">근거</th>
                  <th className="cell-nowrap px-4 py-2 num">최종확인일</th>
                </tr>
              </thead>
              <tbody>
                {hsCodes.data?.items.map((row) => (
                  <tr key={row.id} className="border-t border-gray-100">
                    <td className="cell-nowrap px-4 py-2 num">{row.country_code}</td>
                    <td className="cell-nowrap px-4 py-2 num">{row.hs_version}</td>
                    <td className="cell-nowrap px-4 py-2 num">{row.hs_code}</td>
                    <td className="px-4 py-2">{orEmpty(row.tariff_note)}</td>
                    <td className="px-4 py-2">
                      <a href={row.source_url} className="underline" rel="noreferrer noopener">
                        링크
                      </a>
                    </td>
                    <td className="cell-nowrap px-4 py-2 num">{row.last_verified_on}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </ListState>
        </div>
      </div>
    </section>
  );
}
