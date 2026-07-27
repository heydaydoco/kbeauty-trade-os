import { QueryClientProvider } from "@tanstack/react-query";
import { queryClient } from "./lib/queryClient";
import { useSession } from "./lib/session";
import { HealthPage } from "./routes/health";
import { LoginPage } from "./routes/login";
import { SkuPage } from "./routes/skus";

/**
 * 로그인 여부에 따라 화면을 가른다.
 *
 * ★ 여기서 막는 것은 **표시**일 뿐 보안이 아니다. 실제 차단은 서버가 한다
 *   (§18.1 "인가는 API에서 강제"). 프런트 게이트만 믿으면 브라우저 개발자
 *   도구로 상태를 바꾸는 것만으로 뚫린다.
 */
function AuthenticatedApp() {
  const { me, isPending, unauthenticated, error } = useSession();

  if (isPending) {
    return <p className="p-8 text-gray-500">확인 중…</p>;
  }

  // 서버가 안 뜬 상태와 로그인 안 한 상태는 다르다. 전자는 헬스 화면이
  // 원인과 조치를 알려 준다(비개발자의 첫 실행에서 가장 흔한 상태다).
  if (error) {
    return <HealthPage />;
  }

  if (unauthenticated || !me) {
    return <LoginPage />;
  }

  return <SkuPage />;
}

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthenticatedApp />
    </QueryClientProvider>
  );
}
