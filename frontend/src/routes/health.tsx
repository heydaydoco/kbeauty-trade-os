import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "../lib/api";
import { toKstDisplay } from "../lib/datetime";

interface Readiness {
  status: string;
  app_env: string;
  version: string;
  checks: { db: { status: string }; migration: { status: string } };
  checked_at: string;
}

function fetchReadiness(): Promise<Readiness> {
  return apiFetch<Readiness>("/v1/system/readyz");
}

export function HealthPage() {
  const { data, isPending, isError, error } = useQuery({
    queryKey: ["readiness"],
    queryFn: fetchReadiness,
  });

  return (
    <main className="mx-auto max-w-xl p-8">
      <h1 className="text-2xl font-bold">K-Beauty Trade OS</h1>
      <p className="mt-1 text-sm text-gray-500">시스템 상태</p>

      <section className="mt-6 rounded-lg border border-gray-200 p-5">
        {isPending && <p className="text-gray-500">확인 중…</p>}

        {/* 비개발자의 첫 실행에서 가장 흔한 상태가 바로 '이상'이다.
            영문 오류 화면이 아니라 한국어로 원인과 조치를 보여준다(§18.4). */}
        {isError && (
          <div>
            <p className="text-lg font-semibold text-signal-red">API 연결: 이상</p>
            <p className="mt-2 text-sm text-gray-600">{error.message}</p>
            <p className="mt-1 text-sm text-gray-500">
              터미널에서 <code className="cell-nowrap">docker compose ps</code> 로 상태를 확인하세요.
            </p>
          </div>
        )}

        {data && (
          <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm">
            <dt className="text-gray-500">API 연결</dt>
            <dd className="font-semibold text-signal-green">정상</dd>

            <dt className="text-gray-500">환경</dt>
            <dd className="cell-nowrap">{data.app_env}</dd>

            <dt className="text-gray-500">앱 버전</dt>
            <dd className="cell-nowrap">{data.version}</dd>

            <dt className="text-gray-500">데이터베이스</dt>
            <dd>{data.checks.db.status === "ok" ? "정상" : data.checks.db.status}</dd>

            <dt className="text-gray-500">마이그레이션</dt>
            <dd>{data.checks.migration.status === "ok" ? "최신" : data.checks.migration.status}</dd>

            <dt className="text-gray-500">확인 시각</dt>
            <dd className="cell-nowrap">{toKstDisplay(data.checked_at)}</dd>
          </dl>
        )}
      </section>
    </main>
  );
}
