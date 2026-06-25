# =============================================================================
# 프로젝트 루트 Struct (Composite Struct / Singleton)
# =============================================================================
# 호출 예시:
#   struct = wiz.model("struct")
#   struct.user.list()                    # 프로젝트 고유 User Sub-Struct
#   struct.user.authenticate(email, pw)   # 사용자 인증
# =============================================================================

class Struct:
    def __init__(self):
        self.orm = wiz.model("portal/season/orm")
        self.session = wiz.model("portal/season/session").use()

        # 프로젝트 고유 Sub-Struct 클래스 로드
        self._User = wiz.model("struct/user")
        self._Rules = wiz.model("struct/rules")
        self._ExamScope = wiz.model("struct/exam_scope")
        self._Validation = wiz.model("struct/validation")
        self._QuestionGenerationRequest = wiz.model("struct/question_generation_request")
        self._QuestionBankCandidate = wiz.model("struct/question_bank_candidate")
        self._Rag = wiz.model("struct/rag")
        self._QuestionGenerator = wiz.model("struct/question_generator")

        # 패키지 Struct 캐시
        self._packages = {}

        # 테이블 자동 생성
        self._init_tables()

    def _init_tables(self):
        """DB 테이블이 없으면 자동 생성"""
        tables = [
            "user",
            "exam_scope",
            "source_document",
            "source_chunk",
            "question_generation_request",
            "question_bank_candidate",
            "question_bank_candidate_evidence",
            "question_bank_candidate_validation",
        ]
        for name in tables:
            try:
                db = self.orm.use(name)
                db.orm.create_table(safe=True)
            except Exception:
                pass

    def db(self, name):
        """ORM Wrapper 반환 (src/model/db/{name}.py)"""
        return self.orm.use(name)

    @property
    def user(self):
        """User Sub-Struct 접근 (호출마다 새 인스턴스)"""
        return self._User(self)

    @property
    def rules(self):
        """문항 생성/검증 기준 Rules 접근"""
        return self._Rules(self)

    @property
    def exam_scope(self):
        """방사선사 국가시험 출제범위 접근"""
        return self._ExamScope(self)

    @property
    def validation(self):
        """문항 생성 결과 Harness 검증 접근"""
        return self._Validation(self)

    @property
    def question_generation_request(self):
        """문항 생성 요청 구조 접근"""
        return self._QuestionGenerationRequest(self)

    @property
    def question_bank_candidate(self):
        """전문가 검수 대기 문항 후보 접근"""
        return self._QuestionBankCandidate(self)

    @property
    def rag(self):
        """전공 근거 자료 RAG 검색 접근"""
        return self._Rag(self)

    @property
    def question_generator(self):
        """RAG 기반 문항 생성/검증 실행"""
        return self._QuestionGenerator(self)

    def __getattr__(self, name):
        """알 수 없는 속성 → 패키지 Struct 동적 로드"""
        if name.startswith('_'):
            raise AttributeError(name)
        if name not in self._packages:
            try:
                self._packages[name] = wiz.model(f"portal/{name}/struct")
            except Exception:
                raise AttributeError(f"Package '{name}' not found")
        return self._packages[name]

Model = Struct()
