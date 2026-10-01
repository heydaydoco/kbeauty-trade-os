# ADR-0069: 게이트 코어 — 평가기 등록부(도메인 무임포트)·UNKNOWN=fail-closed·`clearance` 단일 판정·override 판정 해시 결속·평가기 배치 방향 반전

- **상태**: 자율 확정(2026-10-01) — 사후 번복 가능 (S3-1 PR-11a)
- **날짜**: 2026-10-01
- **관련**: DESIGN.md §7.4 게이트 명세·§17.5 / ADR-0060·0064·0065·0068 / docs/plans/s3-1/design-D.md D3·D4·D7, design-E.md E6·E8, design-integrated.md X-07·X-12·X-26·X-27·X-36·G-04

**맥락** — 게이트 6종+PI 입금을 한 모델로 표현하고 확정 통로(PR-12)와 S5-1 채널 리스팅이 같은 틀을 재사용하게 해야 한다. 설계는 `gates`가 도메인을 임포트하지 않고(등록부) 구체 평가기는 L2에 두라고 하며, 입금 판정(E6)은 `payments`, 여신(E1)은 `credit`이 소유한다. 평가 불능(원천 부재·GRAY 준비도·예외)을 통과로 읽는 사고가 가장 위험하고, "확정 가능" 판정이 여러 곳에 복제되면 우회 구멍이 생긴다.

**결정** — ① **`gates`는 도메인 무임포트**(타입·명세·등록부·`clearance`·override 서비스·불변 2표). 평가기 7종과 SO 어댑터·오케스트레이터는 `trade_chain`(L2: `gate_evaluators.py`·`gate_flow.py`)이 소유·**등록**하고(PR-10a 방향 반전 선례), 입금 판정은 `payments/pi_gate.py`의 **순수 함수**(모드 변환은 어댑터)다. ② **결과 4값×해소 3값**, **UNKNOWN은 통과가 아니며 효과는 BLOCK**(`clearance`가 BLOCK과 같이 다룬다). 미등록·예외·명세 밖 결과 조합은 UNKNOWN/NONE(예외는 SAVEPOINT 격리·클래스명만 노출, **55P03·40P01은 전파**). 결과는 `GATE_SPECS`를 단일 출처로 하는 `outcome()` 팩토리로만 만든다(명세에 없는 조합=ValueError). ③ **통과 판정은 `gates.service.clearance` 하나** — 결과값 비교로 통과를 가르는 코드가 그 밖에 있으면 스캔이 실패한다. 결과가 0건이면 통과가 아니다. ④ **override는 별도 액션**: `(gate_code, line_id, basis_hash)`에 결속(입력이 바뀌면 자동 무효, 서버 재평가 해시≠요청 해시면 409)·사유 5~500자·역할은 서비스가 판정(가격·MOQ=무역·관리자, 준비도·PI=관리자, 품번·중복 PO·여신은 DB CHECK로 불가)·접수 SO만·철회는 REVOKE 행 추가(부여자 본인 또는 ADMIN). ⑤ **설계 침묵 보충(자율 확정)**: (a) PI 입금 BLOCK 모드에서 PI 미연결·사용 불가(`PI_MISSING`·`PI_NOT_USABLE`)=UNKNOWN/OVERRIDE(D4), 결제유형 부재=UNKNOWN/NONE(비활성 아님), WARN 모드는 세 비충족 모두 WARN; (b) override 부여·철회에 **audit도 남긴다**(통합 G-04는 "이중 기록 없음"이었으나 관리자 감사 화면의 단일 조회축을 위해 id·게이트·역할만 추가 — 사유·금액 없음); (c) `GateSubject`에 `advance_pct_bp`·`authoritative`(확정 통로 평가=거래처 잠금 하 여신)를 더하고 조회는 참고값(`advisory=true`); (d) 여신 수치(한도·노출)는 TRADE·ADMIN 응답에만(그 외 역할은 `basis` 비움 — ADR-0024 방식); (e) 바이어 PO 키 필드를 `po_no_key`로 이름 지어 SO 모듈 밖 `buyer_po_no_key` 직접 대입 스캔과 충돌하지 않게 한다.

**근거** — 등록부는 승인 코어 `TargetSpec`과 같은 방향이라 순환이 없고 S5-1이 평가기·`subject_type`만 더해 재사용한다. UNKNOWN을 별도 값으로 두면 화면이 "통과 아님·평가 불능"을 구분해 보여 주면서 확정은 BLOCK과 같이 막는다. 단일 `clearance`+복제 스캔은 "다른 경로가 PASS 문자열로 통과시키는" 회귀를 기계로 막는다. 판정 해시 결속은 사람이 본 판정이 낡았을 때의 조용한 통과를 없애고 철회를 행 추가로 풀어 불변 테이블 규율을 지킨다.

**기각한 대안** — `gates`가 SO를 직접 임포트해 평가(순환·S5-1 재사용 불가), UNKNOWN을 PASS+경고로 흡수(평가 불능이 통과가 됨), override를 확정 요청 본문에 첨부(벌크·자동 확정 경로가 부여를 끼워 넣을 표면), `gate_policies` 별도 테이블(통합 X-12로 `policy_settings` 단일), 조회 시 증거 스냅샷 저장(조회 부작용), override 유효성을 SO 열로 캐시(낡은 캐시가 통과로 오독).

**되돌리기 비용** — 낮음~중간. 명세·역할 표는 상수 1곳+테스트이고(메타 테스트가 조합별 테스트를 강제), 테이블은 신규라 배포 전 무비용(이후는 CHECK 재정의 마이그레이션). `subject_type` 확장(S5-1)은 CHECK 확장 1건.
