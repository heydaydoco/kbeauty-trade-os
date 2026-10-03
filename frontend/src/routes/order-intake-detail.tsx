// 오더 인테이크 상세·검토 (S3-1 PR-13b — design-D D1·D2·D5·D7 / ADR-0071).
//
// 규칙(서버가 정본, 화면은 편의 — §18.1):
// - ★ 프런트는 하드 게이트 통과·확정 가능 여부를 다시 판정하지 않는다: 라인 `mapping_state`(MAPPED·UNMAPPED·STALE)·`po_occupied`·게이트 `intake_confirmable`·409/422는 서버 값을 그대로 표시한다.
//   UNMAPPED=품번 등록 유도(SKU 연결), STALE=재해석 필요 안내(자동 추종 없음 — 사람이 '품번 다시 확인'을 누른다).
// - 모든 쓰기는 화면이 본 기준 version(baseVersion)을 싣는다(낙관 잠금). 창 포커스 재조회로 서버 version이 앞서가도 기준은 내 쓰기(수정·품번 재해석 응답)·'최신 내용 불러오기'로만 바뀐다 —
//   확정·거부 뒤에는 자동으로 옮기지 않는다(처리 맥락 안내 후 불러오기).
// - 금액은 서버 문자열(단가·합계)만 표시한다. 프런트 산술 0. 조회는 전 역할, 쓰기 버튼은 무역·관리자(서버가 최종).
// - 거부 사유(자유 텍스트)와 점유 문서 번호는 서버가 줄 때(무역·관리자)만 보인다.

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router";
import { IntakeEditForm } from "../components/intake-edit-form";
import { IntakeGatesPanel } from "../components/intake-gates-panel";
import { IntakeItemCodeForm } from "../components/intake-item-code-form";
import { IntakeReviewPanel } from "../components/intake-review-panel";
import { apiFetch } from "../lib/api";
import { toKstDisplay } from "../lib/datetime";
import {
  ORDER_INTAKES_QUERY_KEY,
  intakeLoadErrorMessage,
  mappingBadgeClass,
  mappingGuide,
  mappingStateLabel,
  orderIntakeDetailKey,
  orderIntakeGatesKey,
  poOccupiedText,
  skuStatusLabel,
  sourceKindLabel,
  type IntakeDetail,
} from "../lib/order-intake";
import { usePagedQuery } from "../lib/paging";
import { hasRole, useSession } from "../lib/session";
import { IntakeStatusBadge } from "./order-intakes";

interface UserLookup {
  id: number;
  display_name: string;
}

const show = (value: unknown): string => (value === null || value === undefined || value === "" ? "—" : String(value));

export function OrderIntakeDetailPage() {
  const params = useParams();
  const id = Number(params.intakeId);
  const { me } = useSession();
  const client = useQueryClient();
  const [writeForbidden, setWriteForbidden] = useState(false);
  // 쓰기 라우트 403(역할 박탈 등)을 한 번 받으면 같은 화면에서는 쓰기 입력을 숨긴다(래치 — '최신 내용 불러오기'·새로고침으로 해제).
  const canWrite = hasRole(me, "TRADE") && !writeForbidden;

  const detail = useQuery({
    queryKey: orderIntakeDetailKey(id),
    queryFn: () => apiFetch<IntakeDetail>(`/v1/order-intakes/${id}`),
    enabled: Number.isInteger(id) && id > 0,
    // 검토 화면은 항상 서버의 최신 상태를 기준으로 한다.
    staleTime: 0,
  });
  const users = usePagedQuery<UserLookup>(["users", "lookup"], "/v1/users/lookup?size=200", canWrite);

  // ★ 화면이 "마지막으로 본/내가 쓴" version — 창 포커스 재조회로 서버 version이 앞서가도 쓰기는 이 값으로 보낸다(서버 409가 막는다).
  const [baseVersion, setBaseVersion] = useState<number | null>(null);
  const [formKey, setFormKey] = useState(0);
  const [reloadToken, setReloadToken] = useState(0);
  const [justFinished, setJustFinished] = useState(false);
  const [announce, setAnnounce] = useState<{ text: string; n: number } | null>(null);
  const say = (text: string) => setAnnounce((prev) => ({ text, n: (prev?.n ?? 0) + 1 }));

  const loadedVersion = detail.data?.version;
  useEffect(() => {
    // 첫 조회 값이 기준이다. 이후 서버 version이 앞서가도 기준은 내 쓰기·불러오기로만 바뀐다.
    if (baseVersion === null && loadedVersion !== undefined) setBaseVersion(loadedVersion);
  }, [baseVersion, loadedVersion]);

  // 다른 인테이크로 이동하면 화면 상태를 비운다(라우트가 같은 컴포넌트를 재사용한다).
  const lastId = useRef(id);
  useEffect(() => {
    if (lastId.current === id) return;
    lastId.current = id;
    setBaseVersion(null);
    setFormKey((v) => v + 1);
    setReloadToken((v) => v + 1);
    setJustFinished(false);
    setAnnounce(null);
  }, [id]);

  function invalidateRelated() {
    void client.invalidateQueries({ queryKey: orderIntakeGatesKey(id) });
    void client.invalidateQueries({ queryKey: [...ORDER_INTAKES_QUERY_KEY, "list"] });
  }

  /** 수정 저장 성공 — 응답이 화면 상태이므로 기준 version을 응답으로 옮기고 폼을 다시 시드한다. */
  function afterEdit(next: IntakeDetail) {
    client.setQueryData(orderIntakeDetailKey(id), next);
    setBaseVersion(next.version);
    setFormKey((v) => v + 1);
    invalidateRelated();
    say("수정을 저장했습니다. 바뀐 라인은 품번을 다시 해석했습니다 — 라인의 매핑 상태를 확인하세요.");
  }

  /** 품번 다시 확인 성공 — 응답(전 라인 재해석)이 화면 상태이므로 기준 version을 응답으로 옮긴다(입력 중인 폼은 지우지 않는다). */
  function afterResolve(next: IntakeDetail) {
    setBaseVersion(next.version);
  }

  function reload() {
    setJustFinished(false);
    setWriteForbidden(false);
    setAnnounce(null);
    // 재조회가 끝난 뒤에 기준 version·폼을 새로 시드한다(옛 캐시로 시드하면 곧바로 또 어긋난다).
    void detail.refetch().then((result) => {
      if (result.data) setBaseVersion(result.data.version);
      setFormKey((v) => v + 1);
      setReloadToken((v) => v + 1);
    });
    invalidateRelated();
  }

  if (!Number.isInteger(id) || id <= 0) {
    return (
      <section>
        <p role="alert" className="text-signal-red">인테이크를 찾을 수 없습니다.</p>
        <Link to="/orders/intakes" className="mt-3 inline-block text-sm underline">인테이크 목록으로</Link>
      </section>
    );
  }
  if (detail.isPending) return <p className="p-5 text-gray-500">불러오는 중…</p>;
  if (detail.error || !detail.data) {
    return (
      <section>
        <p role="alert" className="break-keep text-signal-red">
          {intakeLoadErrorMessage(detail.error)}
        </p>
        <Link to="/orders/intakes" className="mt-3 inline-block text-sm underline">
          인테이크 목록으로
        </Link>
      </section>
    );
  }

  const intake = detail.data;
  const base = baseVersion ?? intake.version;
  const stale = intake.version !== base;
  const pending = intake.status === "PENDING";
  const editable = canWrite && pending && !justFinished;
  const userItems = users.data?.items ?? [];
  const assigneeName = userItems.find((u) => u.id === intake.assignee_id)?.display_name ?? `담당자 #${intake.assignee_id}`;
  const unmapped = intake.lines.filter((l) => l.mapping_state === "UNMAPPED");

  return (
    <section>
      <Link to="/orders/intakes" className="cell-nowrap text-sm text-gray-500 underline">
        ← 인테이크 목록
      </Link>
      <header className="mt-2 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="flex flex-wrap items-center gap-3 text-2xl font-bold">
            <span className="cell-nowrap">PO {intake.buyer_po_no}</span>
            <IntakeStatusBadge status={intake.status} />
          </h1>
          <p className="mt-1 break-keep text-sm text-gray-500">
            {show(intake.buyer_name)} · {sourceKindLabel(intake.source_kind)} · 버전 <span className="num">{intake.version}</span>
          </p>
        </div>
      </header>

      <p role="status" aria-label="화면 안내" className={announce ? "mt-3 break-keep text-sm font-medium" : "sr-only"}>
        <span key={announce?.n ?? 0}>{announce?.text ?? ""}</span>
      </p>

      {stale && (
        <div role="status" className="mt-4 rounded border border-gray-400 p-3 text-sm">
          <p className="break-keep">
            {justFinished
              ? "방금 이 화면에서 처리되어 화면이 갱신되었습니다. 처리 결과를 확인한 뒤 '최신 내용 불러오기'를 눌러 화면 기준을 새 상태로 맞춰 주세요."
              : "다른 곳에서 이 인테이크가 수정되었습니다. 아래 표시는 최신이지만 편집 폼과 확정·거부는 이전 버전 기준이라, 그대로 처리하면 충돌(409)로 거절됩니다."}
          </p>
          <button type="button" onClick={reload} className="cell-nowrap mt-2 rounded border border-gray-400 px-3 py-1">
            최신 내용 불러오기
          </button>
        </div>
      )}

      {pending && intake.po_occupied !== null && (
        <div role="alert" className="mt-4 rounded border border-signal-red p-3 text-sm">
          <p className="break-keep font-medium text-signal-red">이 인테이크는 확정할 수 없는 상태입니다(막다른 대기).</p>
          <p className="mt-1 break-keep">{poOccupiedText(intake.po_occupied)} 서버가 중복 바이어 PO로 막습니다.</p>
          <p className="mt-1 break-keep text-gray-700">
            점유 문서를 확인하세요. 정정이라면 기존 수주를 취소하거나, 이 인테이크를 거부(사유 필수)한 뒤 필요하면 다시 등록하세요. 수주 쪽에서 PO번호가 바뀐 경우에도 이 표시가 사라집니다.
          </p>
        </div>
      )}

      <div className="mt-6 grid gap-6">
        <section aria-labelledby="intake-facts-title" className="rounded-lg border border-gray-200 p-4">
          <h2 id="intake-facts-title" className="text-lg font-semibold">
            접수 내용
          </h2>
          <dl className="mt-2 grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
            <dt className="cell-nowrap text-gray-600">바이어</dt>
            <dd className="break-keep">{show(intake.buyer_name)}</dd>
            <dt className="cell-nowrap text-gray-600">통화</dt>
            <dd>{intake.currency}</dd>
            <dt className="cell-nowrap text-gray-600">도착 시장</dt>
            <dd>{intake.dest_market_code}</dd>
            <dt className="cell-nowrap text-gray-600">바이어 PO 일자</dt>
            <dd className="num">{show(intake.buyer_po_date)}</dd>
            <dt className="cell-nowrap text-gray-600">담당자</dt>
            <dd>{assigneeName}</dd>
            <dt className="cell-nowrap text-gray-600">합계</dt>
            <dd className="num">
              {intake.total_text} {intake.currency}
            </dd>
            <dt className="cell-nowrap text-gray-600">접수일시</dt>
            <dd className="num">{toKstDisplay(intake.created_at)}</dd>
            {intake.copied_from_so_id !== null && (
              <>
                <dt className="cell-nowrap text-gray-600">복제 원본</dt>
                <dd>
                  <Link to={`/sales-orders/${intake.copied_from_so_id}`} className="underline">
                    수주 #{intake.copied_from_so_id}
                  </Link>
                </dd>
              </>
            )}
            {intake.sales_order_id !== null && (
              <>
                <dt className="cell-nowrap text-gray-600">생성된 수주</dt>
                <dd>
                  <Link to={`/sales-orders/${intake.sales_order_id}`} className="underline">
                    수주 #{intake.sales_order_id}
                  </Link>
                </dd>
              </>
            )}
            {intake.status !== "PENDING" && intake.decided_at !== null && (
              <>
                <dt className="cell-nowrap text-gray-600">처리일시</dt>
                <dd className="num">{toKstDisplay(intake.decided_at)}</dd>
              </>
            )}
            {intake.status === "REJECTED" && (
              <>
                <dt className="cell-nowrap text-gray-600">거부 사유</dt>
                <dd className="break-keep">{intake.reject_reason ?? "거부 사유는 무역·관리자만 볼 수 있습니다."}</dd>
              </>
            )}
          </dl>
        </section>

        <section aria-labelledby="intake-lines-title" className="rounded-lg border border-gray-200 p-4">
          <h2 id="intake-lines-title" className="text-lg font-semibold">
            라인과 품번 매핑
          </h2>
          <div className="mt-2 overflow-x-auto rounded border border-gray-200">
            <table className="w-full text-sm">
              <caption className="sr-only">인테이크 라인과 품번 매핑 상태</caption>
              <thead className="bg-gray-50 text-left text-gray-600">
                <tr>
                  <th scope="col" className="cell-nowrap px-3 py-2 text-center">번호</th>
                  <th scope="col" className="cell-nowrap px-3 py-2">바이어 품번</th>
                  <th scope="col" className="cell-nowrap px-3 py-2">SKU</th>
                  <th scope="col" className="cell-nowrap px-3 py-2 text-center">매핑 상태</th>
                  <th scope="col" className="cell-nowrap px-3 py-2 text-center">수량</th>
                  <th scope="col" className="cell-nowrap px-3 py-2 text-center">단가</th>
                  <th scope="col" className="cell-nowrap px-3 py-2 text-center">요청납기</th>
                </tr>
              </thead>
              <tbody>
                {intake.lines.map((line) => {
                  const guide = pending ? mappingGuide(line.mapping_state) : null;
                  return (
                    <tr key={line.id} className="border-t border-gray-100 align-top">
                      <td className="num cell-nowrap px-3 py-2">{line.line_no}</td>
                      <td className="cell-nowrap px-3 py-2">{line.buyer_item_code}</td>
                      <td className="px-3 py-2">
                        {line.sku_code === null ? (
                          <span className="text-gray-500">—</span>
                        ) : (
                          <span className="break-keep">
                            <span className="cell-nowrap">{line.sku_code}</span> {show(line.sku_name_ko)}
                            <span className="block text-xs text-gray-500">{skuStatusLabel(line.sku_status)}</span>
                          </span>
                        )}
                      </td>
                      <td className="px-3 py-2 text-center">
                        <span className={`cell-nowrap rounded border px-2 py-0.5 text-xs ${mappingBadgeClass(line.mapping_state)}`}>{mappingStateLabel(line.mapping_state)}</span>
                        {guide !== null && <span className="mt-1 block break-keep text-left text-xs text-gray-700">{guide}</span>}
                      </td>
                      <td className="num cell-nowrap px-3 py-2">{line.quantity}</td>
                      <td className="num cell-nowrap px-3 py-2">
                        {line.unit_price_text} {intake.currency}
                      </td>
                      <td className="num cell-nowrap px-3 py-2">{show(line.requested_delivery_date)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {pending && unmapped.length > 0 && (
            <div className="mt-4 grid gap-3">
              <h3 className="font-semibold">품번 등록 — 미매핑 라인 {unmapped.length}개</h3>
              <p className="break-keep text-sm text-gray-700">
                미매핑 라인이 있으면 서버가 접수 확정을 거부합니다. {canWrite ? "아래에서 바이어 품번을 SKU에 연결해 등록한 뒤 '품번 다시 확인'을 누르세요." : "무역 담당자가 품번을 등록해야 합니다."} 등록된 매핑은{" "}
                <Link to="/partners" className="underline">거래처 화면의 품번 매핑</Link>에서 확인·삭제할 수 있고, SKU는{" "}
                <Link to="/skus" className="underline">SKU 목록</Link>에서 찾을 수 있습니다.
              </p>
              {editable &&
                unmapped.map((line) => (
                  <IntakeItemCodeForm
                    key={`${intake.id}-${line.id}`}
                    partnerId={intake.buyer_partner_id}
                    lineNo={line.line_no}
                    buyerItemCode={line.buyer_item_code}
                    onRegistered={(lineNo) =>
                      say(`라인 ${lineNo}의 바이어 품번 매핑을 등록했습니다. '품번 다시 확인'을 눌러 이 인테이크에 반영하세요(자동으로 반영되지 않습니다).`)
                    }
                  />
                ))}
            </div>
          )}

          <details className="mt-4 text-sm">
            <summary className="cursor-pointer">접수 원본 보기 (최초 제출 — 수정해도 바뀌지 않음)</summary>
            <OriginalView intake={intake} />
          </details>
        </section>

        <IntakeGatesPanel intakeId={intake.id} enabled={pending && !justFinished} />

        <IntakeReviewPanel
          intake={intake}
          version={base}
          canWrite={canWrite}
          onReload={reload}
          reloadToken={reloadToken}
          onResolved={afterResolve}
          onFinished={() => setJustFinished(true)}
        />

        {editable && <IntakeEditForm key={`${intake.id}-${formKey}`} intake={intake} version={base} onSaved={afterEdit} onForbidden={() => setWriteForbidden(true)} onReload={reload} />}
      </div>
    </section>
  );
}

function OriginalView({ intake }: { intake: IntakeDetail }) {
  const original = intake.original ?? {};
  const header = original.header ?? {};
  const lines = Array.isArray(original.lines) ? original.lines : [];
  return (
    <div className="mt-2 grid gap-2">
      <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1">
        <dt className="cell-nowrap text-gray-600">바이어 PO번호</dt>
        <dd>{show(header.buyer_po_no)}</dd>
        <dt className="cell-nowrap text-gray-600">바이어 PO 일자</dt>
        <dd className="num">{show(header.buyer_po_date)}</dd>
        <dt className="cell-nowrap text-gray-600">통화</dt>
        <dd>{show(header.currency)}</dd>
        <dt className="cell-nowrap text-gray-600">도착 시장</dt>
        <dd>{show(header.dest_market_code)}</dd>
      </dl>
      <div className="overflow-x-auto rounded border border-gray-200">
        <table className="w-full text-sm">
          <caption className="sr-only">접수 원본 라인</caption>
          <thead className="bg-gray-50 text-left text-gray-600">
            <tr>
              <th scope="col" className="cell-nowrap px-3 py-2 text-center">번호</th>
              <th scope="col" className="cell-nowrap px-3 py-2">바이어 품번</th>
              <th scope="col" className="cell-nowrap px-3 py-2 text-center">수량</th>
              <th scope="col" className="cell-nowrap px-3 py-2 text-center">단가</th>
              <th scope="col" className="cell-nowrap px-3 py-2 text-center">요청납기</th>
            </tr>
          </thead>
          <tbody>
            {lines.map((line, index) => (
              <tr key={index} className="border-t border-gray-100">
                <td className="num cell-nowrap px-3 py-2">{index + 1}</td>
                <td className="cell-nowrap px-3 py-2">{show(line.buyer_item_code)}</td>
                <td className="num cell-nowrap px-3 py-2">{show(line.quantity)}</td>
                <td className="num cell-nowrap px-3 py-2">{show(line.unit_price)}</td>
                <td className="num cell-nowrap px-3 py-2">{show(line.requested_delivery_date)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
