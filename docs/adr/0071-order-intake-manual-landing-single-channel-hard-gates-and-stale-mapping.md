# ADR-0071: 오더 인테이크(MANUAL) — 별도 2테이블·착지 단일 통로·PENDING 점유 중복 PO 거부·접수 하드 게이트와 STALE_MAPPING·INTAKE phase 어댑터

- **상태**: 자율 확정(2026-10-01) — 사후 번복 가능 (S3-1 PR-13a)
- **날짜**: 2026-10-01
- **관련**: DESIGN.md §7.2·§7.4·§12.2·§17.1~§17.5 / ADR-0028·0038·0040·0041·0059·0069·0070 / docs/plans/s3-1/design-D.md D1·D2·D4·D5·D7, design-integrated.md X-13·X-40

**맥락** — DoD ② "중복 바이어 PO 0건"은 SO 부분 유니크만으로는 닫히지 않는다: 접수(스테이징) 단계의 PENDING 인테이크끼리·인테이크와 SO 사이 중복은 사람이 검토하는 동안 쌓인다. 또 인테이크 확정(SO 접수 생성)은 AI·CSV 유래라도 **사람 1클릭**이어야 하고(GC-H1), 검토 뒤 품번 매핑이 바뀌면 검토 내용이 낡은 채 SO가 만들어지면 안 된다. 설계 침묵: 11a가 인테이크측 게이트(INTAKE phase 어댑터)를 PR-13로 미뤘고, 확정 오케스트레이터의 계층 위치(`order_intake`는 trade_chain 임포트 금지)가 정해져 있지 않았다.

**결정** — ① **`order_intakes`·`order_intake_lines`(M11) 별도 2테이블**: 상태 3값(허용 2·미허용 4)·상태 대입 단일 통로 `apply_intake_transition`·불변 열(원본 스냅샷·거래처·통화 등)은 DB CHECK+ORM 가드+AST 스캔 3층. 중복 PO는 **PENDING 한정 부분 유니크**(CONFIRMED=SO가 점유·REJECTED=해방). ② **착지 단일 통로 `register_intake`**(`status` 인자 없음·요청 스키마에 상태·`sku_id`·PO 키 필드 없음)가 중복 PO(PENDING 인테이크·비취소 SO)를 선조회해 409로 거부하고 동시 경합은 제약명으로 같은 409로 번역한다. ③ **확정 `confirm_intake`는 trade_chain 오케스트레이터**(방향 반전 — 하위 모듈이 상위를 임포트하지 않는다): 인테이크 행 `FOR UPDATE`(LOCK_ORDER ⓪→(1))→입력 완결성→**하드 게이트(품번 매핑·중복 PO)만** INTAKE phase로 재평가(통과 판정은 `clearance` 하나)→SO 착지→CONFIRMED, **저장본 SKU≠현재 해석이면 409 `STALE_MAPPING`**(재해석 후 재검토). 가격·MOQ·준비도·여신·PI는 접수를 막지 않는다(SO 확정 시점 판정). ④ **INTAKE phase 어댑터**는 SO와 같은 평가기·등록부를 쓰고(5종), 저장본과 현재 매핑이 다르면 단종 경고보다 먼저 `MAPPING_CHANGED`로 막으며, 인테이크 단계에는 override·증거 저장을 두지 않는다. ⑤ **자동 확정 부재**를 `no_auto_confirm` 엔트리·시그니처·호출처 스캔으로 고정한다.

**근거** — PENDING 점유를 DB가 보증해야 "검토 중인 PO가 두 번 들어온다"가 구조적으로 사라진다(선조회는 친절한 오류, 유니크가 최종 방어). 하드 게이트를 2종으로 좁힌 것은 §7.2 "접수는 미확정"이라 상업 정책 이탈(가격·MOQ)을 접수에서 막으면 사람이 SO 편집으로 고칠 기회를 잃기 때문이다 — 그 판정은 SO 확정에서 override·승인 규칙으로 한다. STALE 판정을 평가기에 두면 검토 화면(`GET …/gates`)과 확정 통로가 같은 규칙을 쓴다.

**기각한 대안** — 인테이크를 `import_staging`에 얹기(마스터 왕복 불변식과 충돌), SO를 인테이크 겸용 상태로(접수 SO가 검토 단계와 혼재), 인테이크 확정에서 가격·MOQ를 BLOCK(검토·정정 기회 상실), 저장본 없이 확정 시 재해석만(검토자가 본 값과 다른 SKU로 조용히 확정), 편집 단위 이력 테이블(원본 스냅샷 대 현재 diff로 충족), 인테이크 확정 실패 증거를 `gate_evaluations`에 저장(`subject_type`은 SALES_ORDER만).

**예약·미결** — CSV 입구·파일 해시 멱등 코드(`FILE.*`)·원본 파일 보관(`documents.owner_type='ORDER_INTAKE'` 12자 ≤ 13, `order_intakes.document_id` 추가형)은 PR-14·S6-1 몫. 같은 원본 SO를 복제하는 PENDING 인테이크를 하나로 제한한 부분 유니크는 설계 밖 추가(X-08 정신).

**되돌리기 비용** — 중간. 마이그레이션 전은 낮음, 테이블 생성 후 구조 변경은 데이터 이관+CHECK 재정의가 필요하다. 하드 게이트 집합·STALE 규칙은 상수·평가기 1곳(낮음).
