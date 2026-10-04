# ADR-0076: SO COMPLETED 엣지·short-close를 S3-2에서 열지 않는다 — 여신 노출 공백 방지·provider 결속 아키텍처 테스트·WBS 문면 S3-3 이관

- **상태**: 자율 확정 — 사후 번복 가능 (S3-2 계획 2026-10-04 — 오너 지시 2026-09-29에 따라 판정 후보는 더 엄격한(fail-closed) 권장안으로 확정, ADR-0011 부기)
- **날짜**: 2026-10-04
- **관련**: DESIGN.md §7.2·§7.10(S3-2 [M4] 보강) / WBS S3-2·S3-3(v1.6 주석) / ADR-0064 / PROGRESS P-01·P-02 / docs/plans/s3-2-plan.md · docs/plans/s3-2/design-integrated.md(§9 적대 검토 정정 우선) — sA §A7-3·A15, sC §C12 / 구현 PR-3a(테스트)

**맥락** — WBS S3-2는 RESERVED 엣지 COMPLETED 추가와 short-close 판정을 요구한다. 그런데 여신 노출 술어(`credit/exposure.py`)는 COMPLETED를 통째로 제외하고, 미수 provider는 S3-1 기본 구현(`reflected=False`)이다. 미수 반영 없이 COMPLETED를 열면 선적 완료 SO가 노출에서 빠지는데 미수에도 잡히지 않는 **노출 공백**이 생긴다(P-01·ADR-0064·S3-3 DoD "노출 공백 0").

**결정** — ① COMPLETED는 `RESERVED[SO]`에 남기고 `credit/exposure.py`·`CLOSED_STATUSES`·`open_order_amount`는 무변경. ② **short-close(부분출하 후 잔량 종결)도 미개방** — 판정 결과 S3-3 provider PR과 같은 PR. ③ 아키텍처 테스트 신설: "receivable provider가 기본값(`is_default_provider()`)인 동안 COMPLETED ∈ RESERVED[SO]" — COMPLETED 엣지는 S3-3 미수 provider `reflected=True` 등록·선적분 차감과 **같은 PR에서만** 열린다. ④ IN_SHIPMENT SO는 노출에 전액 산입(상태별 산입 표 테스트에 행 추가). ⑤ WBS v1.6에 이관 주석(S3-2 행 ①·S3-3 행).

**근거** — 노출 공백은 소급 재평가가 불가능하다(과소 노출로 확정된 SO는 되돌릴 수 없다). COMPLETED의 자연 트리거(선적 전건 종결)는 RESERVED 5상태 뒤라 P4 전에는 어차피 도달하지 않는다 — 지금 열면 도달 불가한 죽은 엣지이거나 공백이다.

**기각한 대안** — COMPLETED를 열고 `CLOSED_STATUSES`를 `("CANCELLED",)`로 좁히기(부분 인덱스 술어 재생성 마이그레이션 + S3-3 왕복 비용 2배), 진입 조건만 건 엣지 개방(도달 불가 죽은 엣지), short-close 사람 엣지 선개방(잔량 종결 = 노출 감소라 같은 공백).

**되돌리기 비용** — **낮음**(S3-3이 엣지 1개 가산·RESERVED 1개 감소·총수 갱신). **번복 가능성 가장 높음** — S3-3 provider PR에서 반드시 연다(그때 이 ADR을 "대체" 표기). 반대로 지금 열었다 공백이 실재하면 높음.
