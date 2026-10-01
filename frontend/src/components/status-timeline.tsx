// 상태 이력 타임라인 (S3-1 G-03 — 전표 공용: QT 먼저, PI·SO·PO가 같은 컴포넌트를 재사용한다).
//
// 서버 `GET {basePath}/status-log`(최신순·불변 이력·페이지)를 그대로 그린다. 사유는 원문 그대로 보여 준다.
// 상태 라벨 표는 전표마다 다르므로 `statusLabel`로 주입받는다.

import { ListPager } from "./list-pager";
import { ListState } from "./list-state";
import { toKstDisplay } from "../lib/datetime";
import { usePagedList } from "../lib/paging";

export interface StatusLogEntry {
  id: number;
  occurred_at: string;
  from_status: string | null;
  to_status: string;
  reason: string | null;
  actor_user_id?: number | null;
  actor_name: string | null;
  automatic?: boolean;
  /** 대결로 처리된 경우 위임자 표시명(승인 이력만 싣는다). */
  on_behalf_of_name?: string | null;
}

interface StatusTimelineProps {
  /** 전표 경로(예: "/v1/quotations/12") — 뒤에 /status-log를 붙인다. */
  basePath: string;
  /** 쿼리 키 접두 — 전이 뒤 무효화가 부분 일치로 걸리도록 전표 상세 키 아래에 둔다. */
  queryKey: readonly unknown[];
  statusLabel: (code: string) => string;
  /** 이력 엔드포인트 경로 — 기본은 `${basePath}/status-log`(승인은 `/events`). */
  logPath?: string;
  /** 승인 이력은 오래된 순이라 제목 옆에 순서를 밝힐 때 쓴다(생략 가능). */
  title?: string;
}

export function StatusTimeline({ basePath, queryKey, statusLabel, logPath, title = "상태 이력" }: StatusTimelineProps) {
  const log = usePagedList<StatusLogEntry>([...queryKey, "status-log"], logPath ?? `${basePath}/status-log`);

  return (
    <section aria-labelledby="status-timeline-title">
      <h2 id="status-timeline-title" className="text-lg font-semibold">
        {title}
      </h2>
      <ListPager data={log.data} page={log.page} onPageChange={log.setPage} className="mt-2" />
      <div className="mt-2 rounded-lg border border-gray-200">
        <ListState
          isPending={log.isPending}
          error={log.error}
          isEmpty={log.data?.items.length === 0}
          emptyHint="상태 변경 이력이 없습니다."
        >
          <ol className="divide-y divide-gray-100">
            {log.data?.items.map((entry) => (
              <li key={entry.id} className="px-4 py-3 text-sm">
                <p className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                  <span className="cell-nowrap font-medium">
                    {entry.from_status === null
                      ? `${statusLabel(entry.to_status)} (작성)`
                      : `${statusLabel(entry.from_status)} → ${statusLabel(entry.to_status)}`}
                  </span>
                  <time dateTime={entry.occurred_at} className="cell-nowrap text-gray-500">
                    {toKstDisplay(entry.occurred_at)}
                  </time>
                  <span className="cell-nowrap text-gray-500">
                    {entry.automatic ? "자동(시스템)" : (entry.actor_name ?? "알 수 없음")}
                  </span>
                  {entry.on_behalf_of_name && (
                    <span className="cell-nowrap text-gray-500">(대결 — 위임자 {entry.on_behalf_of_name})</span>
                  )}
                </p>
                {entry.reason && (
                  <p className="mt-1 break-keep text-gray-700">사유: {entry.reason}</p>
                )}
              </li>
            ))}
          </ol>
        </ListState>
      </div>
    </section>
  );
}
