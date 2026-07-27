"""업무 모듈 — 도메인 하나가 디렉터리 하나다.

레이아웃(고정):

    app/modules/<도메인>/
        models.py    ORM 모델. 만들면 반드시 app/registry.py에 임포트를 추가한다
                     (안 하면 autogenerate가 DROP TABLE 마이그레이션을 만든다 —
                      test_every_model_module_is_registered가 잡는다)
        schemas.py   요청·응답 pydantic 모델. ORM 모델을 그대로 노출하지 않는다
                     (원가·마진 마스킹이 응답 레벨에서 일어나야 한다 — §18.1)
        service.py   업무 로직. **트랜잭션 경계는 여기서만** 연다(unit_of_work — §17.1)
        router.py    HTTP 표면. 인증·인가 의존성 부착, 입력 검증, 서비스 호출.
                     commit/rollback/begin 금지(test_routers_do_not_control_transactions)

왜 계층별(models/, services/)이 아니라 도메인별인가 — 이 시스템은 M1~M10으로
도메인이 이미 갈려 있고(§3), 한 세션이 손대는 범위가 대체로 도메인 하나다.
계층별로 자르면 세션마다 디렉터리 4개를 동시에 열게 되고, 도메인 경계가
디렉터리에 남지 않아 나중에 무엇이 무엇을 부르는지 추적이 안 된다.
"""
