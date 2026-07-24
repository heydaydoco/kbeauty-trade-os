# ADR-0002: DB 역할 2종 분리와 기본권한을 S0-1로 당김

- **상태**: 채택
- **날짜**: 2026-07-24
- **관련**: DESIGN.md §17.5·§18.1 / GC-A4 / WBS.md S0-1·S0-2·S4-1 / WBS v1.1

**맥락** — WBS v1.0은 §17.5(원장·audit_log의 UPDATE/DELETE 권한 제거)를 S4-1에 배치했으나, 바로 다음 세션인 **S0-2의 DoD가 이미 "audit_log 불변(UPDATE 거부)"을 요구**한다. PostgreSQL에서 테이블 소유자는 자신의 권한을 REVOKE로 뺏을 수 없고 금지 트리거도 스스로 DISABLE할 수 있으므로, 앱 계정이 소유자인 한 이 DoD는 원천적으로 성립하지 않는다.

**결정** — S0-1에서 역할 2종(`kbos_owner` = 스키마 소유·마이그레이션 전용 / `kbos_app` = 런타임, 스키마 CREATE 없음)과 `ALTER DEFAULT PRIVILEGES`를 만든다. 개별 테이블 REVOKE는 WBS대로 audit_log는 S0-2, stock_movements·확정 분개는 S4-1에 그대로 둔다.

**근거** — §17.5의 "DB 권한으로 강제"와 GC-A4의 "UPDATE/DELETE 불가(DB 권한 레벨에서 차단)"는 소유권 분리를 전제한다. 또한 역할만 나누고 기본권한을 빠뜨리면 owner가 만든 테이블에 app 계정 권한이 0이 되어 S0-2의 SKU 관통이 `permission denied for table skus`로 전건 실패하는데, **테이블이 0개인 S0-1에서는 증상이 나타나지 않는다** — 가장 늦게 발견되는 실패 형태다.

**기각한 대안** — WBS 문면대로 S4-1까지 단일 계정 유지: S0-2가 자기 DoD를 만족시킬 수 없고, S4-1 시점에는 compose·.env·alembic env.py·CI·dev 볼륨을 동시에 수술해야 하며 이미 쌓인 dev 데이터의 소유자 이전까지 따라온다.

**되돌리기 비용** — 지금은 SQL 12줄. 나중이면 위 5개 파일 동시 변경 + 데이터 소유권 이전 + 전 세션 인계 오염. 비대칭이 크다.
