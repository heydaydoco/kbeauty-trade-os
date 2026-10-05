// 마일스톤 변경 이력(롤오버·실적 정정 — 불변) + 통보 기록 (S3-2 PR-4b — design-D D6 ⑧ / M5·M6·M9).
//
// ★ 이력은 IMMUTABLE이라 화면에 수정·삭제 버튼이 없다. 통보는 '실제로 알린 사실의 기록'이다(발송 0 — 버튼 문구에 '보내기' 없음).
// ★ '통보 기록 없음' 배지는 롤오버(ETD·ETA·Cargo Closing의 계획 변경)에만 — 보드의 미통보 수와 같은 규칙(서버 ROLLOVER_TYPES).
// ★ 통보는 변경 1건당 20건까지(서버 상한) — 닿으면 버튼을 끈다(21번째는 서버 422 NOTICE_LIMIT_REACHED).
// ★ 목록은 Page 50(usePagedList + ListPager), 표는 섹션 안 overflow-x-auto에서만 가로 스크롤.

import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ListPager } from "./list-pager";
import { ListState } from "./list-state";
import { MilestoneNoticeDialog } from "./milestone-dialogs";
import { MilestoneValueText } from "./milestone-timeline";
import { toKstDisplay } from "../lib/datetime";
import {
  NOTICE_LIMIT_PER_CHANGE,
  ROLLOVER_TYPES,
  changeKindLabel,
  milestoneChangesKey,
  milestoneTypeLabel,
  type MilestoneChange,
} from "../lib/milestone";
import { usePagedList } from "../lib/paging";

interface Props {
  owner: "SHIPMENT" | "PURCHASE_ORDER";
  ownerId: number;
  /** 예: `/v1/shipments/31/milestone-changes` · `/v1/purchase-orders/7/milestone-changes`. */
  path: string;
  /** 통보 기록 경로가 있는 소유자(선적)만 통보 열을 둔다(OEM은 통보 통로 없음 — 표시만). */
  withNotices: boolean;
  /** 서버 allowed_actions의 EDIT_MILESTONES — '통보 기록' 버튼 근거. */
  canNotice: boolean;
  todayKst: string;
  noun: string;
  /** 통보가 생기면 보드의 '통보 기록 없음' 수가 바뀐다 — 부모가 보드를 다시 받는다. */
  onNoticeSaved?: () => void;
  headingId: string;
  title: string;
}

export function MilestoneChangesSection({
  owner,
  ownerId,
  path,
  withNotices,
  canNotice,
  todayKst,
  noun,
  onNoticeSaved,
  headingId,
  title,
}: Props) {
  const client = useQueryClient();
  const list = usePagedList<MilestoneChange>(milestoneChangesKey(owner, ownerId), path, true, { staleTime: 0 });
  const [noticeTarget, setNoticeTarget] = useState<MilestoneChange | null>(null);

  return (
    <section aria-labelledby={headingId}>
      <h2 id={headingId} className="text-lg font-semibold">
        {title}
      </h2>
      <p className="mt-1 break-keep text-xs text-gray-500">
        계획 설정·변경(롤오버)·실적 기록·정정이 남는 불변 이력입니다(수정·삭제 없음).
        {withNotices && " 통보는 실제로 알린 사실을 기록합니다(이 시스템은 메일을 보내지 않습니다)."}
      </p>
      <ListPager data={list.data} page={list.page} onPageChange={list.setPage} className="mt-2" />
      <div className="mt-2 overflow-x-auto rounded-lg border border-gray-200">
        <ListState
          isPending={list.isPending}
          error={list.error}
          isEmpty={list.data?.items.length === 0}
          emptyHint="아직 변경 이력이 없습니다."
        >
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left text-gray-600">
              <tr>
                <th scope="col" className="cell-nowrap px-3 py-2">종류</th>
                <th scope="col" className="cell-nowrap px-3 py-2 text-center">변경</th>
                <th scope="col" className="cell-nowrap px-3 py-2">이전 → 새 값</th>
                <th scope="col" className="cell-nowrap min-w-40 px-3 py-2">사유</th>
                <th scope="col" className="cell-nowrap px-3 py-2">기록자</th>
                <th scope="col" className="cell-nowrap px-3 py-2 text-center">기록 시각</th>
                {withNotices && <th scope="col" className="cell-nowrap min-w-48 px-3 py-2">통보</th>}
              </tr>
            </thead>
            <tbody>
              {list.data?.items.map((change) => {
                const rollover = change.change_kind === "PLAN_CHANGED" && ROLLOVER_TYPES.has(change.milestone_type);
                const full = change.notices.length >= NOTICE_LIMIT_PER_CHANGE;
                return (
                  <tr key={change.id} className="border-t border-gray-100 align-top">
                    <td className="cell-nowrap px-3 py-2">{milestoneTypeLabel(change.milestone_type)}</td>
                    <td className="cell-nowrap px-3 py-2 text-center">{changeKindLabel(change.change_kind, change.milestone_type)}</td>
                    <td className="px-3 py-2">
                      <MilestoneValueText value={change.old} /> → <MilestoneValueText value={change.new} />
                    </td>
                    <td className="break-keep px-3 py-2">{change.reason ?? "—"}</td>
                    <td className="cell-nowrap px-3 py-2">{change.actor_name ?? `#${change.actor_user_id}`}</td>
                    <td className="cell-nowrap px-3 py-2 text-center">{toKstDisplay(change.created_at)}</td>
                    {withNotices && (
                      <td className="px-3 py-2">
                        {change.notices.length > 0 ? (
                          <ul className="flex flex-col gap-1">
                            {change.notices.map((notice) => (
                              <li key={notice.comm_log_id} className="break-keep">
                                <span className="num cell-nowrap">{notice.occurred_on}</span> ·{" "}
                                <span className="cell-nowrap">{notice.partner_name ?? "상대 미지정"}</span> —{" "}
                                <span className="whitespace-pre-line">{notice.summary}</span>
                              </li>
                            ))}
                          </ul>
                        ) : rollover ? (
                          <span className="cell-nowrap rounded border border-signal-amber bg-signal-amber/20 px-1.5 py-0.5 text-xs">
                            통보 기록 없음
                          </span>
                        ) : (
                          <span className="text-gray-400">—</span>
                        )}
                        {canNotice && (
                          <div className="mt-1">
                            <button
                              type="button"
                              disabled={full}
                              onClick={() => setNoticeTarget(change)}
                              className="cell-nowrap rounded border border-gray-300 px-2 py-1 text-xs disabled:opacity-50"
                            >
                              통보 기록
                            </button>
                            {full && (
                              <span className="ml-2 break-keep text-xs text-gray-500">
                                통보 기록은 변경 1건당 {NOTICE_LIMIT_PER_CHANGE}건까지입니다.
                              </span>
                            )}
                          </div>
                        )}
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </ListState>
      </div>
      {noticeTarget !== null && canNotice && (
        <MilestoneNoticeDialog
          change={noticeTarget}
          path={`${path}/${noticeTarget.id}/notices`}
          todayKst={todayKst}
          noun={noun}
          onSaved={() => {
            void client.invalidateQueries({ queryKey: milestoneChangesKey(owner, ownerId) });
            onNoticeSaved?.();
          }}
          onRefresh={() => {
            void client.invalidateQueries({ queryKey: milestoneChangesKey(owner, ownerId) });
            onNoticeSaved?.();
          }}
          onClose={() => setNoticeTarget(null)}
        />
      )}
    </section>
  );
}
