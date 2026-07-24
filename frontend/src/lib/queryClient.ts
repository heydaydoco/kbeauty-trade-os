import { QueryClient } from "@tanstack/react-query";
import { ApiError } from "./api";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // 인증(401)·권한(403)·검증(422) 오류는 재시도해도 결과가 같다.
      retry: (failureCount, error) => {
        if (error instanceof ApiError && [401, 403, 404, 422].includes(error.status)) {
          return false;
        }
        return failureCount < 2;
      },
      staleTime: 30_000,
    },
  },
});
