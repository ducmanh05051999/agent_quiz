import os
import json
import logging
from pathlib import Path
from typing import List, Dict
import numpy as np
import faiss
from openai import OpenAI
from src.document_processor import DocumentProcessor, SUPPORTED_EXTENSIONS

logger = logging.getLogger("RAGEngine")


class RAGEngine:
    def __init__(self, docs_dir: Path, data_dir: Path, embedding_model: str = "text-embedding-3-small"):
        self.docs_dir = docs_dir
        self.data_dir = data_dir
        self.embedding_model = embedding_model
        self.index_path = self.data_dir / "faiss.index"
        self.meta_path = self.data_dir / "metadata.json"
        self.manifest_path = self.data_dir / "manifest.json"

        self.dimension = 1536
        self.index = faiss.IndexFlatIP(self.dimension)
        self.chunks_meta: List[Dict] = []
        self.manifest: Dict[str, Dict] = {}
        self.load_from_disk()

    def load_from_disk(self):
        try:
            if self.index_path.exists() and self.meta_path.exists() and self.manifest_path.exists():
                self.index = faiss.read_index(str(self.index_path))
                with open(self.meta_path, "r", encoding="utf-8") as f:
                    self.chunks_meta = json.load(f)
                with open(self.manifest_path, "r", encoding="utf-8") as f:
                    self.manifest = json.load(f)
                logger.info(f"Đã tải chỉ mục RAG từ đĩa: {len(self.chunks_meta)} đoạn văn bản.")
        except Exception as e:
            logger.error(f"Lỗi tải chỉ mục từ đĩa, sẽ tạo mới: {e}")
            self.reset_index()

    def save_to_disk(self):
        tmp_idx = self.index_path.with_suffix(".tmp")
        faiss.write_index(self.index, str(tmp_idx))
        os.replace(tmp_idx, self.index_path)

        with open(self.meta_path, "w", encoding="utf-8") as f:
            json.dump(self.chunks_meta, f, ensure_ascii=False, indent=2)
        with open(self.manifest_path, "w", encoding="utf-8") as f:
            json.dump(self.manifest, f, ensure_ascii=False, indent=2)

    def reset_index(self):
        self.index = faiss.IndexFlatIP(self.dimension)
        self.chunks_meta = []
        self.manifest = {}

    def _embed_texts(self, client: OpenAI, texts: List[str]) -> np.ndarray:
        all_embeddings = []
        batch_size = 64
        for i in range(0, len(texts), batch_size):
            batch = texts[i: i + batch_size]
            resp = client.embeddings.create(model=self.embedding_model, input=batch)
            vecs = [item.embedding for item in resp.data]
            all_embeddings.extend(vecs)
        arr = np.array(all_embeddings, dtype=np.float32)
        faiss.normalize_L2(arr)
        return arr

    def sync_documents(self, api_key: str, progress_callback=None) -> str:
        current_files = {}
        for p in self.docs_dir.iterdir():
            if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS:
                stat = p.stat()
                current_files[p.name] = {"size": stat.st_size, "mtime": stat.st_mtime, "path": p}

        deleted_or_modified = [
            fname for fname, info in self.manifest.items()
            if fname not in current_files
            or current_files[fname]["size"] != info["size"]
            or current_files[fname]["mtime"] != info["mtime"]
        ]

        new_files = [
            fname for fname in current_files
            if fname not in self.manifest or fname in deleted_or_modified
        ]

        if not deleted_or_modified and not new_files:
            return "Chỉ mục đã đồng bộ, không có thay đổi."

        if not api_key:
            raise ValueError("Chưa cấu hình OpenAI API Key để lập chỉ mục tài liệu.")

        client = OpenAI(api_key=api_key, timeout=30.0)

        if deleted_or_modified:
            valid_sources = set(current_files.keys()) - set(new_files)
            kept_chunks = [c for c in self.chunks_meta if c["source"] in valid_sources]
            self.reset_index()
            if kept_chunks:
                if progress_callback:
                    progress_callback("Đang cập nhật lại chỉ mục sau khi xóa tài liệu...")
                vecs = self._embed_texts(client, [c["text"] for c in kept_chunks])
                self.index.add(vecs)
                self.chunks_meta = kept_chunks
            for fname in valid_sources:
                self.manifest[fname] = {
                    "size": current_files[fname]["size"],
                    "mtime": current_files[fname]["mtime"],
                }

        for idx, fname in enumerate(new_files):
            if progress_callback:
                progress_callback(f"Đang xử lý ({idx + 1}/{len(new_files)}): {fname}")
            f_info = current_files[fname]
            extracted = DocumentProcessor.extract_chunks(f_info["path"])
            if extracted:
                vecs = self._embed_texts(client, [c["text"] for c in extracted])
                self.index.add(vecs)
                self.chunks_meta.extend(extracted)
            self.manifest[fname] = {"size": f_info["size"], "mtime": f_info["mtime"]}

        self.save_to_disk()
        return f"Đã đồng bộ {len(current_files)} tài liệu ({len(self.chunks_meta)} đoạn chỉ mục)."

    def search(self, query: str, api_key: str, top_k: int = 4) -> List[Dict]:
        if self.index.ntotal == 0 or not self.chunks_meta:
            return []
        client = OpenAI(api_key=api_key, timeout=20.0)
        q_vec = self._embed_texts(client, [query])
        k = min(top_k, self.index.ntotal)
        scores, indices = self.index.search(q_vec, k)
        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0 or idx >= len(self.chunks_meta):
                continue
            item = dict(self.chunks_meta[idx])
            item["similarity"] = float(score)
            results.append(item)
        return results
