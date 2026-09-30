"""전표 커널(L0) — QT·PI·SO·PO 4종 전표가 공유하는 규칙의 유일한 정의 (S3-1 ADR-0051~0056).

이 패키지는 **모델·서비스를 임포트하지 않는다**(전표별 모듈이 이 패키지를 임포트한다 — 단방향 DAG,
tests/architecture/test_import_direction.py가 고정). 전표 테이블은 이름(`DOC_TABLES`)으로 다룬다.
"""
