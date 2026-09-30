// 확인 다이얼로그 접근성 — 포커스 트랩·Esc(문서 레벨)·포커스 복귀.

import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { ConfirmDialog } from "./confirm-dialog";

function Host() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        열기
      </button>
      {open && (
        <ConfirmDialog
          title="제목"
          description="설명"
          confirmLabel="확정"
          reasonLabel="사유"
          onCancel={() => setOpen(false)}
          onConfirm={() => undefined}
        />
      )}
    </>
  );
}

describe("ConfirmDialog", () => {
  it("열리면 첫 입력에 포커스, Tab은 안에서만 돌고, Esc(문서)로 닫히며 연 버튼으로 돌아간다", () => {
    render(<Host />);
    const opener = screen.getByRole("button", { name: "열기" });
    opener.focus();
    fireEvent.click(opener);
    const textarea = screen.getByLabelText("사유");
    expect(textarea).toHaveFocus();
    // 사유를 채워 확정 버튼을 활성화한 뒤 마지막 요소에서 Tab → 첫 요소
    fireEvent.change(textarea, { target: { value: "x" } });
    screen.getByRole("button", { name: "확정" }).focus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(textarea).toHaveFocus();
    // 첫 요소에서 Shift+Tab → 마지막 요소
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(screen.getByRole("button", { name: "확정" })).toHaveFocus();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
  });
});
