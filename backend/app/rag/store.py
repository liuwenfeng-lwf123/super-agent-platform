"""

logger = logging.getLogger(__name__)
Lightweight RAG knowledge base.
Stores documents as text chunks with TF-IDF-like scoring for retrieval.
No external vector DB dependency required.
"""
import json
import logging
import os
import re
import math
import uuid
import hashlib
from collections import Counter
from typing import Optional


CHUNK_SIZE = 500
CHUNK_OVERLAP = 50


def _tokenize(text: str) -> list[str]:
    text = text.lower()
    # Split into English words and individual CJK characters
    tokens = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", text)
    # Also generate bigrams for CJK to improve matching
    cjk_chars = [t for t in tokens if len(t) == 1 and '\u4e00' <= t <= '\u9fff']
    bigrams = [cjk_chars[i] + cjk_chars[i+1] for i in range(len(cjk_chars) - 1)]
    return tokens + bigrams


def _tf(tokens: list[str]) -> dict[str, float]:
    counts = Counter(tokens)
    total = len(tokens) or 1
    return {t: c / total for t, c in counts.items()}


class Document:
    def __init__(self, doc_id: str, name: str, content: str, metadata: dict | None = None):
        self.doc_id = doc_id
        self.name = name
        self.content = content
        self.metadata = metadata or {}


class Chunk:
    def __init__(self, chunk_id: str, doc_id: str, doc_name: str, text: str, index: int):
        self.chunk_id = chunk_id
        self.doc_id = doc_id
        self.doc_name = doc_name
        self.text = text
        self.index = index
        self.tokens = _tokenize(text)
        self.tf = _tf(self.tokens)


class KnowledgeBase:
    DATA_DIR = "./data/knowledge"

    def __init__(self):
        self._documents: dict[str, Document] = {}
        self._chunks: list[Chunk] = []
        self._idf: dict[str, float] = {}
        os.makedirs(self.DATA_DIR, exist_ok=True)
        self._load()

    def _save(self):
        data = {
            "documents": [
                {"doc_id": d.doc_id, "name": d.name, "content": d.content, "metadata": d.metadata}
                for d in self._documents.values()
            ]
        }
        with open(os.path.join(self.DATA_DIR, "kb.json"), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _load(self):
        path = os.path.join(self.DATA_DIR, "kb.json")
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for d in data.get("documents", []):
                doc = Document(d["doc_id"], d["name"], d["content"], d.get("metadata", {}))
                self._documents[doc.doc_id] = doc
            self._rebuild_index()
        except Exception as e:
            logger.debug("Suppressed error in store: %s", e)

    def _chunk_text(self, text: str) -> list[str]:
        chunks = []
        start = 0
        while start < len(text):
            end = start + CHUNK_SIZE
            chunks.append(text[start:end])
            start = end - CHUNK_OVERLAP
        return chunks

    def _rebuild_index(self):
        self._chunks = []
        for doc in self._documents.values():
            parts = self._chunk_text(doc.content)
            for i, part in enumerate(parts):
                cid = hashlib.md5(f"{doc.doc_id}:{i}".encode()).hexdigest()[:12]
                self._chunks.append(Chunk(cid, doc.doc_id, doc.name, part, i))
        self._compute_idf()

    def _compute_idf(self):
        n = len(self._chunks) or 1
        df: dict[str, int] = {}
        for chunk in self._chunks:
            for token in set(chunk.tokens):
                df[token] = df.get(token, 0) + 1
        self._idf = {t: math.log(n / (1 + count)) for t, count in df.items()}

    def add_document(self, name: str, content: str, metadata: dict | None = None) -> str:
        doc_id = str(uuid.uuid4())[:8]
        doc = Document(doc_id, name, content, metadata or {})
        self._documents[doc_id] = doc
        self._rebuild_index()
        self._save()
        return doc_id

    def add_file(self, file_path: str, metadata: dict | None = None) -> str:
        """Ingest a file (txt, md, pdf) into the knowledge base."""
        name = os.path.basename(file_path)
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".pdf":
            try:
                import subprocess
                result = subprocess.run(
                    ["python", "-c", f"from app.agents.system_tools import _extract_pdf_text; print(_extract_pdf_text('{file_path}'))"],
                    capture_output=True, text=True, timeout=30,
                )
                content = result.stdout.strip() if result.returncode == 0 else ""
            except Exception as e:
                logger.debug("Suppressed error in store: %s", e)
                content = ""
            if not content:
                return ""
        elif ext in (".md", ".markdown"):
            # Strip markdown syntax for better tokenization
            with open(file_path, "r", encoding="utf-8") as f:
                raw = f.read()
            content = re.sub(r"#{1,6}\s*", "", raw)
            content = re.sub(r"\*{1,2}|_{1,2}", "", content)
            content = re.sub(r"```[\s\S]*?```", "", content)
            content = re.sub(r"`[^`]+`", "", content)
        else:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
        if not content.strip():
            return ""
        meta = {"source": file_path, "format": ext}
        if metadata:
            meta.update(metadata)
        return self.add_document(name, content, meta)

    def get_document(self, doc_id: str) -> Optional[dict]:
        """Get a document by ID."""
        doc = self._documents.get(doc_id)
        if not doc:
            return None
        return {"doc_id": doc.doc_id, "name": doc.name, "content": doc.content, "metadata": doc.metadata}

    def remove_document(self, doc_id: str) -> bool:
        if doc_id in self._documents:
            del self._documents[doc_id]
            self._rebuild_index()
            self._save()
            return True
        return False

    def list_documents(self) -> list[dict]:
        return [
            {
                "doc_id": d.doc_id,
                "name": d.name,
                "size": len(d.content),
                "chunks": len([c for c in self._chunks if c.doc_id == d.doc_id]),
                "metadata": d.metadata,
            }
            for d in self._documents.values()
        ]

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        if not self._chunks:
            return []
        query_tokens = _tokenize(query)
        query_tf = _tf(query_tokens)

        scores = []
        for chunk in self._chunks:
            score = 0.0
            for token, tf_val in query_tf.items():
                if token in chunk.tf:
                    idf = self._idf.get(token, 0)
                    score += tf_val * idf * chunk.tf[token] * idf
            scores.append((score, chunk))

        scores.sort(key=lambda x: x[0], reverse=True)
        results = []
        for score, chunk in scores[:top_k]:
            if score <= 0:
                break
            results.append({
                "chunk_id": chunk.chunk_id,
                "doc_id": chunk.doc_id,
                "doc_name": chunk.doc_name,
                "text": chunk.text,
                "score": round(score, 4),
            })
        return results

    def get_context(self, query: str, max_chars: int = 3000) -> str:
        results = self.search(query, top_k=5)
        if not results:
            return ""
        parts = []
        total = 0
        for r in results:
            text = r["text"]
            if total + len(text) > max_chars:
                break
            parts.append(f"[{r['doc_name']}] {text}")
            total += len(text)
        return "\n\n".join(parts)


knowledge_base = KnowledgeBase()
