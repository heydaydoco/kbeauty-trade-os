import { useState } from "react";
import { ApiError } from "../lib/api";
import { useLogin } from "../lib/session";

export function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const login = useLogin();

  // 서버가 준 한국어 문구를 그대로 보여 준다(§18.4) — 같은 상황에 서버와 화면이
  // 다른 말을 하지 않게. 잠금(423)·비활성(403)도 서버 문구가 조치를 담고 있다.
  const message =
    login.error instanceof ApiError
      ? login.error.message
      : login.error
        ? "로그인 중 문제가 발생했습니다. 잠시 후 다시 시도해 주세요."
        : null;

  return (
    <main className="mx-auto max-w-sm p-8">
      <h1 className="text-2xl font-bold">K-Beauty Trade OS</h1>
      <p className="mt-1 text-sm text-gray-500">로그인</p>

      <form
        className="mt-6 flex flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          login.mutate({ email, password });
        }}
      >
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-gray-600">이메일</span>
          <input
            type="email"
            name="email"
            autoComplete="username"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            className="rounded border border-gray-300 px-3 py-2"
          />
        </label>

        <label className="flex flex-col gap-1 text-sm">
          <span className="text-gray-600">비밀번호</span>
          <input
            type="password"
            name="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            className="rounded border border-gray-300 px-3 py-2"
          />
        </label>

        {message && (
          <p role="alert" className="text-sm text-signal-red">
            {message}
          </p>
        )}

        <button
          type="submit"
          disabled={login.isPending}
          className="rounded bg-gray-900 px-4 py-2 text-white disabled:opacity-50"
        >
          {login.isPending ? "확인 중…" : "로그인"}
        </button>
      </form>

      <p className="mt-6 text-xs text-gray-500">
        비밀번호를 5회 연속 틀리면 계정이 잠깁니다. 잠기면 15분 뒤 자동으로 풀리며, 급하면
        관리자에게 즉시 해제를 요청하세요.
      </p>
    </main>
  );
}
