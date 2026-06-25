import json
import os
import sqlite3
import sys

import numpy as np


class SubjectReferenceRag:
    def __init__(self, core):
        self.core = core
        self.root = self._find_project_root()
        self.db_dir = os.path.join(self.root, "resources", "vector_db", "subject_references")
        self.model_dir = os.path.join("/opt", "app", "models", "xenova-multilingual-e5-small")
        self._embeddings = None
        self._embedder = None

    def _find_project_root(self):
        here = os.path.abspath(__file__)
        root = here
        for _ in range(8):
            root = os.path.dirname(root)
            if os.path.isdir(os.path.join(root, "resources", "vector_db", "subject_references")):
                return root
        return os.getcwd()

    def _normalize(self, value):
        return str(value or "").strip()

    def _load_embedder(self):
        if self._embedder is not None:
            return self._embedder
        scripts_dir = os.path.join(self.root, "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        from local_onnx_embedder import LocalE5Embedder

        self._embedder = LocalE5Embedder(self.model_dir)
        return self._embedder

    def _load_embeddings(self):
        if self._embeddings is None:
            path = os.path.join(self.db_dir, "embeddings.npy")
            if not os.path.exists(path):
                raise Exception("전공 근거 벡터DB가 없습니다. 먼저 벡터DB를 구축해야 합니다.")
            self._embeddings = np.load(path)
        return self._embeddings

    def _connect(self):
        path = os.path.join(self.db_dir, "chunks.sqlite")
        if not os.path.exists(path):
            raise Exception("전공 근거 chunk DB가 없습니다. 먼저 벡터DB를 구축해야 합니다.")
        return sqlite3.connect(path)

    def _load_chunk(self, conn, embedding_index):
        row = conn.execute(
            """
            SELECT embedding_index, chunk_id, source_file, page_or_slide, chunk_index,
                   content, excerpt, metadata_json
            FROM chunks
            WHERE embedding_index = ?
            """,
            (int(embedding_index),),
        ).fetchone()
        if row is None:
            return None
        metadata = json.loads(row[7] or "{}")
        return dict(
            embedding_index=row[0],
            chunk_id=row[1],
            source_file=row[2],
            page_or_slide=row[3],
            chunk_index=row[4],
            content=row[5],
            excerpt=row[6],
            metadata=metadata,
        )

    def _matches(self, metadata, scope, level):
        subject = self._normalize(scope.get("subject"))
        field = self._normalize(scope.get("field"))
        area = self._normalize(scope.get("area"))
        detail = self._normalize(scope.get("detail"))

        if subject and self._normalize(metadata.get("subject")) != subject:
            return False
        if level in ["field", "area", "detail"] and field and self._normalize(metadata.get("field")) != field:
            return False
        if level in ["area", "detail"] and area and self._normalize(metadata.get("area")) != area:
            return False
        if level == "detail" and detail and self._normalize(metadata.get("sub_area")) != detail:
            return False
        return True

    def _query_for(self, scope, question_type="", difficulty=""):
        parts = [
            scope.get("subject"),
            scope.get("field"),
            scope.get("area"),
            scope.get("detail"),
            question_type,
            difficulty,
        ]
        return " ".join([self._normalize(part) for part in parts if self._normalize(part)])

    def _boost_terms(self, value):
        terms = []
        for token in self._normalize(value).replace(",", " ").split():
            token = token.strip()
            if len(token) >= 2 and token not in terms:
                terms.append(token)
        return terms

    def _apply_keyword_boost(self, conn, scores, boost_terms):
        if not boost_terms:
            return scores
        boosted = np.array(scores, copy=True)
        rows = conn.execute("SELECT embedding_index, content, excerpt, metadata_json FROM chunks").fetchall()
        for row in rows:
            text = " ".join([row[1] or "", row[2] or "", row[3] or ""])
            hit_count = sum(1 for term in boost_terms if term in text)
            if hit_count:
                boosted[int(row[0])] += min(0.12, 0.05 * hit_count)
        return boosted

    def search(self, query="", scope=None, top_k=6, boost_terms=None):
        scope = scope or {}
        query = self._normalize(query) or self._query_for(scope)
        if not query:
            raise Exception("검색어 또는 출제범위가 필요합니다.")

        embeddings = self._load_embeddings()
        embedder = self._load_embedder()
        query_vector = embedder.embed_queries([query], batch_size=1)[0]
        scores = embeddings @ query_vector

        conn = self._connect()
        try:
            ranked_scores = self._apply_keyword_boost(conn, scores, boost_terms or [])
            ranked = np.argsort(-ranked_scores)
            selected = []
            seen = set()
            for level in ["detail", "area", "field", "subject"]:
                for index in ranked:
                    chunk = self._load_chunk(conn, int(index))
                    if chunk is None or chunk["chunk_id"] in seen:
                        continue
                    metadata = chunk["metadata"]
                    if not self._matches(metadata, scope, level):
                        continue
                    seen.add(chunk["chunk_id"])
                    selected.append(self._result(chunk, ranked_scores[index], level))
                    if len(selected) >= top_k:
                        return dict(query=query, results=selected)
            for index in ranked:
                chunk = self._load_chunk(conn, int(index))
                if chunk is None or chunk["chunk_id"] in seen:
                    continue
                seen.add(chunk["chunk_id"])
                selected.append(self._result(chunk, ranked_scores[index], "global"))
                if len(selected) >= top_k:
                    break
            return dict(query=query, results=selected)
        finally:
            conn.close()

    def _result(self, chunk, score, match_level):
        metadata = chunk["metadata"]
        content = " ".join((chunk.get("content") or "").split())
        if len(content) > 1800:
            content = content[:1800].rstrip() + "..."
        return dict(
            score=round(float(score), 6),
            match_level=match_level,
            chunk_id=chunk.get("chunk_id"),
            source_file=chunk.get("source_file"),
            page_or_slide=chunk.get("page_or_slide"),
            chunk_index=chunk.get("chunk_index"),
            excerpt=chunk.get("excerpt"),
            content=content,
            exam_period=metadata.get("exam_period", ""),
            subject=metadata.get("subject", ""),
            field=metadata.get("field", ""),
            area=metadata.get("area", ""),
            detail=metadata.get("sub_area", ""),
            scope_id=metadata.get("scope_id", ""),
            learning_objective_id=metadata.get("learning_objective_id", ""),
            learning_objective=metadata.get("learning_objective", ""),
            question_generation_target_id=metadata.get("question_generation_target_id", ""),
            extraction_quality=metadata.get("extraction_quality", ""),
            mapping_status=metadata.get("scope_objective_mapping_status", ""),
        )

    def build_generation_input(self, data):
        scope = data.get("scope") or {}
        question_count = int(data.get("question_count") or 1)
        difficulty = self._normalize(data.get("difficulty") or "중")
        question_type = self._normalize(data.get("question_type") or "개념형")
        focus = self._normalize(data.get("focus"))
        top_k = int(data.get("top_k") or 6)
        query = self._normalize(data.get("query"))
        if not query and focus:
            query = f"{self._query_for(scope, question_type, difficulty)} {focus}"
        query = query or self._query_for(scope, question_type, difficulty)
        search_result = self.search(query=query, scope=scope, top_k=top_k, boost_terms=self._boost_terms(focus))
        evidence = search_result["results"]
        if not evidence:
            raise Exception("선택한 출제범위에 연결할 RAG 근거 chunk를 찾지 못했습니다.")

        return dict(
            generation_mode="rag_evidence_request",
            request_status="ready_for_llm",
            scope=scope,
            question_count=question_count,
            difficulty=difficulty,
            question_type=question_type,
            focus=focus,
            evidence_query=search_result["query"],
            source_evidence=evidence,
            generation_prompt_payload=dict(
                task="방사선사 국가고시 1·2교시용 5지선다 문항 생성",
                selected_scope=scope,
                question_count=question_count,
                difficulty=difficulty,
                question_type=question_type,
                focus=focus,
                evidence_chunks=[
                    dict(
                        chunk_id=item["chunk_id"],
                        source_file=item["source_file"],
                        page_or_slide=item["page_or_slide"],
                        content=item["content"],
                        learning_objective=item["learning_objective"],
                        scope=dict(
                            period=item["exam_period"],
                            subject=item["subject"],
                            field=item["field"],
                            area=item["area"],
                            detail=item["detail"],
                        ),
                    )
                    for item in evidence
                ],
                constraints=[
                    "반드시 근거 chunk에서 지원되는 내용만 사용한다.",
                    "문제, 보기, 해설은 원문 문장을 복사하지 않고 새 문장으로 작성한다.",
                    "정답은 정확히 하나만 존재해야 한다.",
                    "보기는 5개로 작성하고 모든 오답 해설을 포함한다.",
                    "출처 파일, 페이지, chunk_id를 evidence_refs에 남긴다.",
                    "검증 에이전트와 Harness 통과 전에는 최종 문제 DB에 저장하지 않는다.",
                ],
                output_format="question_schema.json 기준 JSON",
            ),
            validation_plan=dict(
                reviewer_agents=["scope", "uniqueness", "grounding", "copyright", "grammar"],
                harness_required=[
                    "보기 개수 = 5",
                    "정답 개수 = 1",
                    "정답 및 오답 해설 존재",
                    "출제범위와 학습목표 존재",
                    "근거 chunk와 출처 페이지 존재",
                    "저작권 위험 낮음",
                ],
            ),
        )


Model = SubjectReferenceRag
