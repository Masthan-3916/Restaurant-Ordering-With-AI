from pathlib import Path

import chromadb

from app.ai.gemini import GeminiClient
from app.config.settings import settings
from app.rag.documents import TextChunk


class PolicyVectorStore:
    def __init__(self) -> None:
        self.client = chromadb.PersistentClient(path=str(Path(settings.chroma_path)))
        self.collection = self.client.get_or_create_collection(
            name="restaurant_policies",
            metadata={"hnsw:space": "cosine"},
        )

    def index(self, document_id: int, filename: str, version: int, chunks: list[TextChunk], gemini: GeminiClient) -> None:
        if not chunks:
            return
        embeddings = [gemini.embed(chunk.text) for chunk in chunks]
        ids = [f"doc-{document_id}-chunk-{chunk.chunk_index}" for chunk in chunks]
        metadatas = [
            {
                "document_id": document_id,
                "filename": filename,
                "version": version,
                "page": chunk.page if chunk.page is not None else -1,
                "chunk_index": chunk.chunk_index,
                "active": True,
            }
            for chunk in chunks
        ]
        previous = self.collection.get(where={"filename": filename}, include=["metadatas"])
        if previous["ids"]:
            inactive = [{**metadata, "active": False} for metadata in previous["metadatas"]]
            self.collection.update(ids=previous["ids"], metadatas=inactive)
        self.collection.add(ids=ids, documents=[chunk.text for chunk in chunks], embeddings=embeddings, metadatas=metadatas)

    def search(self, question: str, gemini: GeminiClient, limit: int = 4) -> list[dict]:
        if self.collection.count() == 0:
            return []
        result = self.collection.query(
            query_embeddings=[gemini.embed(question, task_type="RETRIEVAL_QUERY")],
            n_results=limit,
            where={"active": True},
            include=["documents", "metadatas", "distances"],
        )
        matches: list[dict] = []
        for text, metadata, distance in zip(result["documents"][0], result["metadatas"][0], result["distances"][0]):
            if distance > 0.75:
                continue
            matches.append(
                {
                    "text": text,
                    "filename": metadata["filename"],
                    "page": metadata["page"] if metadata["page"] >= 0 else None,
                    "chunk_id": f"doc-{metadata['document_id']}-chunk-{metadata['chunk_index']}",
                    "version": metadata["version"],
                }
            )
        return matches
