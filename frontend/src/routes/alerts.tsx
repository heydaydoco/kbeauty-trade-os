// 알림센터 (DESIGN.md §2 ADR-07 "화면 알림센터" / §18.1 소유권 / S2-3 PR-1).
//
// ★ 이 화면은 **내 알림함**이다 — 관리자도 남의 알림을 보지 않는다. 서버가
//   수신자로 좁혀 주고(§18.1), 화면은 그 결과를 그대로 보여 준다.
// ★ '확인'은 재발송 금지의 기준이다(ADR-07) — 누르면 같은 사건이 다시 오지
//   않는다. 되돌리는 버튼을 두지 않았다(취소 경로는 문면에 없다 — 관찰 등재).
// ★ 관리자에게는 알림 규칙·배치 레지스트리 패널이 함께 보인다(§14 ⑭ 관리).
//   규칙 편집이 관리자 전용인 이유: 기일 알림의 관리자 폴백 게이트가 여기 걸린다.

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ListPager } from "../components/list-pager";
import { ListState } from "../components/list-state";
import { apiFetch } from "../lib/api";
import { toKstDisplay } from "../lib/datetime";
import { orEmpty } from "../lib/labels";
import { usePagedList, usePagedQuery } from "../lib/paging";
import { hasRole, useSession } from "../lib/session";

export interface Alert {
  id: number;
  alert_rule_id: number | null;
  recipient_user_id: number;
  title: string;
  body: string | null;
  severity: string;
  dedup_key: string;
  acknowledged_at: string | null;
  entity_type: string | null;
  entity_id: number | null;
}

export interface AlertRule {
  id: number;
  code: string;
  name_ko: string;
  event_type: string;
  severity: string;
  recipient_user_id: number | null;
  recipient_role: string | null;
  is_enabled: boolean;
  version: number;
}

export interface ScheduledJob {
  id: number;
  code: string;
  name_ko: string;
  schedule: string;
  is_enabled: boolean;
  last_run_at: string | null;
  last_status: string | null;
  last_error: string | null;
  is_mapped: boolean;
  version: number;
}

export const ALERTS_QUERY_KEY = ["alerts"] as const;
export const ALERT_RULES_QUERY_KEY = ["alert-rules"] as const;
export const SCHEDULED_JOBS_QUERY_KEY = ["scheduled-jobs"] as const;

const SEVERITY_LABEL: Record<string, string> = {
  INFO: "안내",
  WARN: "주의",
  CRITICAL: "긴급",
};

const JOB_STATUS_LABEL: Record<string, string> = {
  OK: "정상",
  FAILED: "실패",
  RUNNING: "실행 중",
};

/** 수신 대상 표기 — 두 컬럼이 다 비면 "담당자"다(서버 라우팅 규칙과 같은 말). */
function recipientLabel(rule: AlertRule): string {
  if (rule.recipient_user_id !== null) return `사용자 #${rule.recipient_user_id}`;
  if (rule.recipient_role !== null) return `${rule.recipient_role} 역할 전원`;
  return "담당자";
}

export function AlertsPage() {
  const { me } = useSession();
  const client = useQueryClient();
  const isAdmin = hasRole(me);

  const list = usePagedList<Alert>(ALERTS_QUERY_KEY, "/v1/alerts");
  const rules = usePagedQuery<AlertRule>(ALERT_RULES_QUERY_KEY, "/v1/alert-rules?size=200", isAdmin);
  const jobs = usePagedQuery<ScheduledJob>(
    SCHEDULED_JOBS_QUERY_KEY,
    "/v1/scheduled-jobs?size=200",
    isAdmin,
  );

  const acknowledge = useMutation({
    mutationFn: (alertId: number) =>
      apiFetch<Alert>(`/v1/alerts/${alertId}/ack`, { method: "POST" }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ALERTS_QUERY_KEY });
    },
  });

  const unread = list.data?.items.filter((alert) => alert.acknowledged_at === null).length ?? 0;

  return (
    <section>
      <header className="flex items-baseline justify-between">
        <div>
          <h1 className="text-2xl font-bold">알림센터</h1>
          <p className="mt-1 break-keep text-sm text-gray-500">
            나에게 온 알림입니다. '확인'을 누르면 같은 사건으로는 다시 알림이 오지 않습니다.
          </p>
        </div>
        <span className="cell-nowrap text-sm text-gray-500">이 쪽 미확인 {unread}건</span>
      </header>

      <ListPager data={list.data} page={list.page} onPageChange={list.setPage} className="mt-6" />

      <div className="mt-3 overflow-x-auto rounded-lg border border-gray-200">
        <ListState
          isPending={list.isPending}
          error={list.error}
          isEmpty={list.data?.items.length === 0}
          emptyHint="받은 알림이 없습니다. 기일·상태 변화가 생기면 여기에 쌓입니다."
        >
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left text-gray-600">
              <tr>
                <th className="cell-nowrap px-4 py-2">등급</th>
                <th className="px-4 py-2">내용</th>
                <th className="cell-nowrap px-4 py-2">대상</th>
                <th className="cell-nowrap px-4 py-2 text-center">확인</th>
              </tr>
            </thead>
            <tbody>
              {list.data?.items.map((alert) => (
                <tr key={alert.id} className="border-t border-gray-100 align-top">
                  <td className="cell-nowrap px-4 py-2 text-center">
                    {SEVERITY_LABEL[alert.severity] ?? alert.severity}
                  </td>
                  <td className="break-keep px-4 py-2">
                    <p className="font-medium">{alert.title}</p>
                    {alert.body && <p className="mt-1 text-gray-500">{alert.body}</p>}
                  </td>
                  <td className="cell-nowrap px-4 py-2 text-gray-500">
                    {alert.entity_type ? `${alert.entity_type} #${alert.entity_id}` : orEmpty(null)}
                  </td>
                  <td className="cell-nowrap px-4 py-2 text-center">
                    {alert.acknowledged_at ? (
                      <span className="text-gray-500">{toKstDisplay(alert.acknowledged_at)}</span>
                    ) : (
                      <button
                        type="button"
                        onClick={() => acknowledge.mutate(alert.id)}
                        disabled={acknowledge.isPending}
                        className="rounded border border-gray-300 px-3 py-1 disabled:opacity-50"
                      >
                        확인
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </ListState>
      </div>

      {isAdmin && (
        <>
          <section className="mt-10">
            <h2 className="text-lg font-semibold">알림 규칙</h2>
            <p className="mt-1 break-keep text-sm text-gray-500">
              어떤 사건을 누구에게 보낼지 정합니다. 수신 대상을 비우면 그 건의 담당자에게 갑니다.
              기일 알림은 담당자도 규칙도 없을 때만 관리자에게 갑니다.
            </p>
            <div className="mt-3 overflow-x-auto rounded-lg border border-gray-200">
              <ListState
                isPending={rules.isPending}
                error={rules.error}
                isEmpty={rules.data?.items.length === 0}
                emptyHint="등록된 알림 규칙이 없습니다. 기일 알림은 관리자에게 전달되고, 그 밖의 사건은 발송되지 않습니다."
              >
                <table className="w-full text-sm">
                  <thead className="bg-gray-50 text-left text-gray-600">
                    <tr>
                      <th className="cell-nowrap px-4 py-2">코드</th>
                      <th className="px-4 py-2">이름</th>
                      <th className="cell-nowrap px-4 py-2">사건</th>
                      <th className="cell-nowrap px-4 py-2">수신 대상</th>
                      <th className="cell-nowrap px-4 py-2 text-center">사용</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rules.data?.items.map((rule) => (
                      <tr key={rule.id} className="border-t border-gray-100">
                        <td className="cell-nowrap px-4 py-2">{rule.code}</td>
                        <td className="break-keep px-4 py-2">{rule.name_ko}</td>
                        <td className="cell-nowrap px-4 py-2 text-gray-500">{rule.event_type}</td>
                        <td className="cell-nowrap px-4 py-2">{recipientLabel(rule)}</td>
                        <td className="cell-nowrap px-4 py-2 text-center">
                          {rule.is_enabled ? "사용" : "중지"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </ListState>
            </div>
          </section>

          <section className="mt-10">
            <h2 className="text-lg font-semibold">배치 레지스트리</h2>
            <p className="mt-1 break-keep text-sm text-gray-500">
              등록된 배치와 마지막 실행 결과입니다. 실행 주기는 코드와 함께 관리되며 화면에서
              바꾸지 않습니다.
            </p>
            <div className="mt-3 overflow-x-auto rounded-lg border border-gray-200">
              <ListState
                isPending={jobs.isPending}
                error={jobs.error}
                isEmpty={jobs.data?.items.length === 0}
                emptyHint="등록된 배치가 없습니다."
              >
                <table className="w-full text-sm">
                  <thead className="bg-gray-50 text-left text-gray-600">
                    <tr>
                      <th className="cell-nowrap px-4 py-2">코드</th>
                      <th className="px-4 py-2">이름</th>
                      <th className="cell-nowrap px-4 py-2">주기</th>
                      <th className="cell-nowrap px-4 py-2">마지막 실행</th>
                      <th className="cell-nowrap px-4 py-2 text-center">결과</th>
                    </tr>
                  </thead>
                  <tbody>
                    {jobs.data?.items.map((job) => (
                      <tr key={job.id} className="border-t border-gray-100">
                        <td className="cell-nowrap px-4 py-2">
                          {job.code}
                          {!job.is_mapped && (
                            <span className="ml-2 text-signal-red">실행 함수 없음</span>
                          )}
                        </td>
                        <td className="break-keep px-4 py-2">{job.name_ko}</td>
                        <td className="cell-nowrap px-4 py-2 text-gray-500">{job.schedule}</td>
                        <td className="cell-nowrap px-4 py-2 text-gray-500">
                          {job.last_run_at ? toKstDisplay(job.last_run_at) : orEmpty(null)}
                        </td>
                        <td className="cell-nowrap px-4 py-2 text-center">
                          {job.is_enabled
                            ? (JOB_STATUS_LABEL[job.last_status ?? ""] ?? orEmpty(null))
                            : "중지"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </ListState>
            </div>
          </section>
        </>
      )}
    </section>
  );
}
