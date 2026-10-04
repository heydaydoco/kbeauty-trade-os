import { fireEvent, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { jsonResponse, renderWithProviders } from "../test/render";
import { SearchSelect } from "./search-select";

interface Row {
  id: number;
  name: string;
}

function Harness({ onPick }: { onPick?: (row: Row | null) => void }) {
  const [value, setValue] = useState<Row | null>(null);
  return (
    <SearchSelect<Row>
      label="거래처 검색"
      path="/v1/partners"
      params={{ type: "OEM" }}
      queryKey={["partners", "test-search"]}
      value={value}
      onChange={(next) => {
        setValue(next);
        onPick?.(next);
      }}
      getKey={(row) => row.id}
      getLabel={(row) => row.name}
    />
  );
}

function stub(rows: Row[], total = rows.length) {
  const fetchMock = vi.fn((_input: string) =>
    Promise.resolve(jsonResponse({ items: rows, total, page: 1, size: 20 })),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const qCalls = (fetchMock: ReturnType<typeof stub>) =>
  fetchMock.mock.calls.map(([url]) => String(url)).filter((url) => url.includes("q="));

describe("SearchSelect", () => {
  beforeEach(() => {
    vi.stubGlobal("crypto", { randomUUID: () => "test-key" });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("빠르게 여러 글자를 쳐도 디바운스 후 검색 요청은 1회, size=20·고정 필터가 붙는다", async () => {
    const fetchMock = stub([{ id: 1, name: "콜마" }]);
    renderWithProviders(<Harness />);
    const box = screen.getByRole("combobox", { name: "거래처 검색" });
    fireEvent.focus(box);
    fireEvent.change(box, { target: { value: "콜" } });
    fireEvent.change(box, { target: { value: "콜마" } });
    fireEvent.change(box, { target: { value: "콜마코" } });

    // 입력 중에는 q 요청이 없고(첫 화면 조회만), 300ms 뒤 최종 검색어로 정확히 1회 나간다.
    expect(qCalls(fetchMock)).toHaveLength(0);
    await waitFor(() => {
      expect(qCalls(fetchMock)).toHaveLength(1);
    });
    await screen.findByRole("option", { name: "콜마" });
    const calls = qCalls(fetchMock);
    expect(calls).toHaveLength(1);
    expect(calls[0]).toContain("q=%EC%BD%9C%EB%A7%88%EC%BD%94");
    expect(calls[0]).toContain("size=20");
    expect(calls[0]).toContain("type=OEM");
  });

  it("결과가 total보다 적으면 잘림 안내를 보인다", async () => {
    stub([{ id: 1, name: "가" }], 57);
    renderWithProviders(<Harness />);
    fireEvent.focus(screen.getByRole("combobox"));

    expect(
      await screen.findByText("57건 중 1건만 표시 — 검색어를 더 입력하세요"),
    ).toBeInTheDocument();
  });

  it("전부 보이면 잘림 안내가 없다", async () => {
    stub([{ id: 1, name: "가" }]);
    renderWithProviders(<Harness />);
    fireEvent.focus(screen.getByRole("combobox"));

    await screen.findByRole("option", { name: "가" });
    expect(screen.queryByText(/건만 표시/)).not.toBeInTheDocument();
  });

  it("클릭으로 선택하고 선택 해제로 되돌린다", async () => {
    stub([{ id: 5, name: "한국콜마" }]);
    const onPick = vi.fn();
    renderWithProviders(<Harness onPick={onPick} />);
    fireEvent.focus(screen.getByRole("combobox"));

    fireEvent.click(await screen.findByRole("option", { name: "한국콜마" }));
    expect(onPick).toHaveBeenLastCalledWith({ id: 5, name: "한국콜마" });
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "거래처 검색 선택 해제" }));
    expect(onPick).toHaveBeenLastCalledWith(null);
    expect(screen.getByRole("combobox")).toBeInTheDocument();
  });

  it("결과가 없으면 표준 문구를 보인다", async () => {
    stub([]);
    renderWithProviders(<Harness />);
    fireEvent.focus(screen.getByRole("combobox"));

    expect(await screen.findByText("검색 결과가 없습니다")).toBeInTheDocument();
  });

  it("↓ ↓ Enter로 두 번째 항목을 고르고, Esc는 목록을 닫는다", async () => {
    stub([
      { id: 1, name: "첫째" },
      { id: 2, name: "둘째" },
    ]);
    const onPick = vi.fn();
    renderWithProviders(<Harness onPick={onPick} />);
    const box = screen.getByRole("combobox");
    fireEvent.focus(box);
    await screen.findByRole("option", { name: "첫째" });

    fireEvent.keyDown(box, { key: "ArrowDown" });
    fireEvent.keyDown(box, { key: "Enter" });
    expect(onPick).toHaveBeenCalledWith({ id: 2, name: "둘째" });
  });

  // PR-16 부채 ⑤(S3-2 PR-3b 수정) — 디바운스 대기 중 옛 결과(검색 전 첫 바이어)를 빠르게 눌러 엉뚱한 거래처가 골라지던 결함.
  it("빠른 입력 직후에는 옛 결과가 사라지고 클릭·Enter로 아무것도 고르지 않는다(오선택 0) — 새 결과가 오면 그것만 고른다", async () => {
    const fetchMock = vi.fn((input: string) => {
      const rows = input.includes("q=") ? [{ id: 5, name: "콜마" }] : [{ id: 1, name: "첫 바이어" }];
      return Promise.resolve(jsonResponse({ items: rows, total: rows.length, page: 1, size: 20 }));
    });
    vi.stubGlobal("fetch", fetchMock);
    const onPick = vi.fn();
    renderWithProviders(<Harness onPick={onPick} />);
    const box = screen.getByRole("combobox", { name: "거래처 검색" });
    fireEvent.focus(box);
    const stale = await screen.findByRole("option", { name: "첫 바이어" });

    fireEvent.change(box, { target: { value: "콜마" } });
    // 디바운스(300ms) 대기 중 — 옛 목록은 화면에 없고 '검색 중…'만 보인다.
    expect(screen.queryByRole("option")).toBeNull();
    expect(screen.getByRole("status")).toHaveTextContent("검색 중…");
    fireEvent.click(stale);
    fireEvent.keyDown(box, { key: "Enter" });
    fireEvent.keyDown(box, { key: "ArrowDown" });
    fireEvent.keyDown(box, { key: "Enter" });
    expect(onPick).not.toHaveBeenCalled();

    fireEvent.click(await screen.findByRole("option", { name: "콜마" }));
    expect(onPick).toHaveBeenCalledTimes(1);
    expect(onPick).toHaveBeenCalledWith({ id: 5, name: "콜마" });
  });

  it("검색어를 지워도 디바운스 동안은 옛(검색어) 결과를 고를 수 없다", async () => {
    const fetchMock = vi.fn((input: string) => {
      const rows = input.includes("q=") ? [{ id: 5, name: "콜마" }] : [{ id: 1, name: "첫 바이어" }];
      return Promise.resolve(jsonResponse({ items: rows, total: rows.length, page: 1, size: 20 }));
    });
    vi.stubGlobal("fetch", fetchMock);
    const onPick = vi.fn();
    renderWithProviders(<Harness onPick={onPick} />);
    const box = screen.getByRole("combobox", { name: "거래처 검색" });
    fireEvent.focus(box);
    fireEvent.change(box, { target: { value: "콜마" } });
    await screen.findByRole("option", { name: "콜마" });

    fireEvent.change(box, { target: { value: "" } });
    expect(screen.queryByRole("option", { name: "콜마" })).toBeNull();
    fireEvent.keyDown(box, { key: "Enter" });
    expect(onPick).not.toHaveBeenCalled();
    expect(await screen.findByRole("option", { name: "첫 바이어" })).toBeInTheDocument();
  });

  it("Esc로 목록이 닫힌다", async () => {
    stub([{ id: 1, name: "첫째" }]);
    renderWithProviders(<Harness />);
    const box = screen.getByRole("combobox");
    fireEvent.focus(box);
    await screen.findByRole("option", { name: "첫째" });

    fireEvent.keyDown(box, { key: "Escape" });
    await waitFor(() => {
      expect(screen.queryByRole("option")).not.toBeInTheDocument();
    });
  });
});
