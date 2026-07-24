// 백엔드 호출 래퍼.
//
// - 항상 상대경로(/api)로 부른다. 절대 URL을 쓰면 dev/prod에서 오리진이 갈린다.
// - 서버 에러 봉투 {error:{code,message,detail,request_id}}를 파싱해
//   화면이 한국어 message를 그대로 보여줄 수 있게 한다.
// - 확정·생성 요청(POST 등)에는 Idempotency-Key를 붙일 자리를 마련해 둔다
//   (서버 구현은 S0-2 — §17.4).

export interface ApiErrorBody {
  code: string;
  message: string;
  detail: Record<string, unknown>;
  requestId: string | null;
}

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly detail: Record<string, unknown>;
  readonly requestId: string | null;

  constructor(status: number, body: ApiErrorBody) {
    super(body.message);
    this.name = "ApiError";
    this.code = body.code;
    this.status = status;
    this.detail = body.detail;
    this.requestId = body.requestId;
  }
}

const NETWORK_ERROR: ApiErrorBody = {
  code: "CLIENT.NETWORK.UNREACHABLE",
  message: "서버에 연결할 수 없습니다. 백엔드가 기동 중이 아닐 수 있습니다.",
  detail: {},
  requestId: null,
};

function newIdempotencyKey(): string {
  return crypto.randomUUID();
}

export interface RequestOptions {
  method?: string;
  body?: unknown;
  /** POST·확정 요청의 재시도 흡수용. 재시도 시 같은 키를 재사용한다. */
  idempotencyKey?: string;
}

export async function apiFetch<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = options.method ?? "GET";
  const headers: Record<string, string> = { Accept: "application/json" };

  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
  }
  if (method !== "GET" && method !== "HEAD") {
    // 확정·생성 요청에 멱등 키를 부착한다(서버 처리는 S0-2).
    headers["Idempotency-Key"] = options.idempotencyKey ?? newIdempotencyKey();
  }

  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      method,
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
  } catch {
    throw new ApiError(0, NETWORK_ERROR);
  }

  if (!response.ok) {
    // 서버 봉투는 snake_case(request_id)로 온다.
    interface WireError {
      code?: string;
      message?: string;
      detail?: Record<string, unknown>;
      request_id?: string | null;
    }
    const parsed = await response.json().catch(() => null);
    const err = (parsed as { error?: WireError } | null)?.error;
    throw new ApiError(response.status, {
      code: err?.code ?? "CLIENT.RESPONSE.UNKNOWN",
      message: err?.message ?? "요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.",
      detail: err?.detail ?? {},
      requestId: err?.request_id ?? null,
    });
  }

  return (await response.json()) as T;
}
