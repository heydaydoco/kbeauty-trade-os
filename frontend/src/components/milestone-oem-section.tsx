// 발주 상세의 OEM '생산 일정' 섹션 — 원료수급 → 충진 → 포장 → 출하검사 (S3-2 PR-4b — design-D D7 / M7~M9 / PROGRESS 'S3-2 PR-4c' 인계 보강).
//
// ★ OEM 생산 발주(`po_kind=OEM_PRODUCTION`)에서만 그린다 — 일반 구매 발주는 서버가 422 OWNER_NOT_OEM이라 섹션 자체를 두지 않는다(부모가 판단).
// ★ 버튼은 보드의 서버 `allowed_actions`(EDIT_MILESTONES)로만 — 역할·발주 상태를 화면이 다시 판정하지 않는다.
// ★ 행 모양은 선적 보드와 같다 → 같은 타임라인·대화상자 재사용. 전부 날짜형, 휴일·통관·롤오버 배지 0(서버가 0을 준다 — 표시만, 알림 없음).
// ★ 계획 변경은 사유 필수(빈칸 제출 불가), 실적 정정도 사유 필수. 통보 통로는 선적 전용이라 여기엔 없다(이력만).
// ★ 쓰기 응답 `{board, change}`의 보드로 캐시를 바꾸고(진행 중 재조회 취소 — 늦은 응답 폐기), 이력은 다시 받는다.

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ListState } from "./list-state";
import { MilestoneChangesSection } from "./milestone-changes";
import { MilestoneValueDialog } from "./milestone-dialogs";
import { MilestoneTimeline, type MilestoneEditMode } from "./milestone-timeline";
import { apiFetch } from "../lib/api";
import { milestoneChangesKey, oemBoardKey, type MilestoneRow, type OemMilestoneBoard } from "../lib/milestone";

const NOUN = "발주";

export function OemScheduleSection({ poId }: { poId: number }) {
  const client = useQueryClient();
  const key = oemBoardKey(poId);
  const board = useQuery({
    queryKey: key,
    queryFn: () => apiFetch<OemMilestoneBoard>(`/v1/purchase-orders/${poId}/milestones`),
    staleTime: 0,
  });
  const [editing, setEditing] = useState<{ mode: MilestoneEditMode; row: MilestoneRow } | null>(null);

  function applyBoard(next: OemMilestoneBoard) {
    void client.cancelQueries({ queryKey: key, exact: true });
    client.setQueryData(key, next);
    void client.invalidateQueries({ queryKey: milestoneChangesKey("PURCHASE_ORDER", poId) });
  }

  function reloadBoard() {
    void client.invalidateQueries({ queryKey: key, exact: true });
    void client.invalidateQueries({ queryKey: milestoneChangesKey("PURCHASE_ORDER", poId) });
  }

  const data = board.data;
  const canEdit = data?.allowed_actions.includes("EDIT_MILESTONES") ?? false;

  return (
    <section aria-labelledby="po-oem-schedule-title" className="flex flex-col gap-4">
      <div>
        <h2 id="po-oem-schedule-title" className="text-lg font-semibold">
          생산 일정
        </h2>
        <p className="mt-1 break-keep text-xs text-gray-500">
          OEM 생산 발주의 원료수급 → 충진 → 포장 → 출하검사 일정입니다. 알림·휴일 경고 대상이 아닙니다(표시만). 계획 변경과 실적 정정은 사유와
          함께 이력으로 남습니다.
        </p>
        {data !== undefined && !canEdit && (
          <p className="mt-1 break-keep text-xs text-gray-500">
            지금은 이 발주의 생산 일정을 기록할 수 없습니다(무역 담당·관리자, 발행·공급사 확인 중인 발주만 — 서버가 판정합니다).
          </p>
        )}
      </div>
      <ListState isPending={board.isPending} error={board.error} isEmpty={false} emptyHint="">
        {data !== undefined && (
          <MilestoneTimeline board={data} canEdit={canEdit} onEdit={(mode, row) => setEditing({ mode, row })} label="생산 일정" />
        )}
      </ListState>
      <MilestoneChangesSection
        owner="PURCHASE_ORDER"
        ownerId={poId}
        path={`/v1/purchase-orders/${poId}/milestone-changes`}
        withNotices={false}
        canNotice={false}
        todayKst={data?.today_kst ?? ""}
        noun={NOUN}
        headingId="po-oem-changes-title"
        title="생산 일정 변경 이력"
      />
      {editing !== null && data !== undefined && canEdit && (
        <MilestoneValueDialog<OemMilestoneBoard>
          key={`${editing.mode}-${editing.row.milestone_type}`}
          mode={editing.mode}
          row={editing.row}
          basePath={`/v1/purchase-orders/${poId}/milestones`}
          noun={NOUN}
          defaultZone={null}
          todayKst={data.today_kst}
          onSaved={(result) => applyBoard(result.board)}
          onClose={() => setEditing(null)}
          onReload={() => {
            setEditing(null);
            reloadBoard();
          }}
        />
      )}
    </section>
  );
}
