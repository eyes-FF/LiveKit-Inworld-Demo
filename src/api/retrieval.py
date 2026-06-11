"""语义检索:fastembed(ONNX) + 内存向量索引 + 关键词混合计分。

不引入外部向量数据库:注入同步阻塞在 LLM 生成之前,检索必须 <50ms,
本地 bge-small-zh-v1.5 单条查询 ~10ms,几百条语料 numpy 点积足够。
"""

import json
from pathlib import Path

import numpy as np
from fastembed import TextEmbedding

MODEL_NAME = "BAAI/bge-small-zh-v1.5"

# 关键词每命中 1 个加 0.15(最多算 2 个),向量低于阈值且无关键词命中则不注入
KEYWORD_BOOST = 0.15
# 实测校准(bge-small-zh):同义改写 ~0.47-0.55,无关 ~0.42 以下
SHOT_SIM_THRESHOLD = 0.46
KNOWLEDGE_SIM_THRESHOLD = 0.55


def _normalize(vecs: np.ndarray) -> np.ndarray:
    return vecs / np.linalg.norm(vecs, axis=-1, keepdims=True)


class Retriever:
    def __init__(self, shots_path: Path, knowledge_path: Path):
        self.model = TextEmbedding(MODEL_NAME)

        with open(shots_path, encoding="utf-8") as f:
            self.shots: list[dict] = json.load(f)
        with open(knowledge_path, encoding="utf-8") as f:
            self.knowledge: list[dict] = json.load(f)

        # 示例用 input + keywords 拼接做被检文本;知识直接用正文
        shot_texts = [
            s["input"] + " " + " ".join(s.get("keywords", [])) for s in self.shots
        ]
        kn_texts = [k["text"] for k in self.knowledge]
        self.shot_vecs = _normalize(
            np.array(list(self.model.passage_embed(shot_texts)))
        )
        self.kn_vecs = _normalize(np.array(list(self.model.passage_embed(kn_texts))))

    def _embed_query(self, text: str) -> np.ndarray:
        vec = np.array(next(iter(self.model.query_embed([text]))))
        return vec / np.linalg.norm(vec)

    def search(self, query: str, top_shots: int = 3, top_knowledge: int = 3) -> dict:
        q = self._embed_query(query)

        shot_sims = self.shot_vecs @ q
        scored_shots = []
        for i, s in enumerate(self.shots):
            kw_hits = sum(1 for k in s.get("keywords", []) if k in query)
            score = float(shot_sims[i]) + KEYWORD_BOOST * min(kw_hits, 2)
            if shot_sims[i] >= SHOT_SIM_THRESHOLD or kw_hits > 0:
                scored_shots.append((score, i))
        scored_shots.sort(reverse=True)
        shots = [
            {
                "input": self.shots[i]["input"],
                "output": self.shots[i]["output"],
                "score": round(score, 3),
            }
            for score, i in scored_shots[:top_shots]
        ]

        kn_sims = self.kn_vecs @ q
        order = np.argsort(-kn_sims)[:top_knowledge]
        knowledge = [
            {"text": self.knowledge[i]["text"], "score": round(float(kn_sims[i]), 3)}
            for i in order
            if kn_sims[i] >= KNOWLEDGE_SIM_THRESHOLD
        ]

        return {"shots": shots, "knowledge": knowledge}
