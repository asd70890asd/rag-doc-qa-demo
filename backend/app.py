import time
import uuid
import json
import requests
import faiss
import numpy as np
from io import BytesIO
from typing import List, Optional
from pypdf import PdfReader
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

# Initialize FastAPI App
app = FastAPI(title="RAG Doc Q&A Demo API")

# Enable CORS to allow the frontend to be opened via file:// or simple HTTP server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Load embedding model globally (downloads ~90MB on first run, cached thereafter)
print("Loading sentence-transformers model...")
embedder = SentenceTransformer('all-MiniLM-L6-v2')
print("Model loaded successfully.")

# In-memory storage for documents (zero infrastructure requirement)
# Structure: doc_id -> {"chunks": List[str], "index": faiss.Index, "history": List[dict]}
DOCUMENTS_STORE = {}

class AskRequest(BaseModel):
    doc_id: str
    question: str
    top_k: int = 3
    min_score: float = 0.2
    api_key: Optional[str] = None
    api_base: Optional[str] = "https://api.openai.com/v1"
    model: Optional[str] = "gpt-3.5-turbo"

def extract_text(file_bytes: bytes, filename: str) -> str:
    """Extract text from uploaded bytes based on file extension."""
    if filename.lower().endswith(".pdf"):
        reader = PdfReader(BytesIO(file_bytes))
        text = []
        for page in reader.pages:
            extracted = page.extract_text()
            if extracted:
                text.append(extracted)
        return "\n".join(text)
    elif filename.lower().endswith(".txt"):
        return file_bytes.decode("utf-8", errors="replace")
    else:
        raise ValueError("Unsupported file type. Please upload a PDF or TXT file.")

def chunk_text(text: str, chunk_size: int = 250, overlap: int = 50) -> List[str]:
    """Chunk text into overlapping windows of words."""
    words = text.split()
    chunks = []
    if not words:
        return chunks

    for i in range(0, len(words), max(1, chunk_size - overlap)):
        chunk = " ".join(words[i:i + chunk_size])
        chunks.append(chunk)
        if i + chunk_size >= len(words):
            break
    return chunks

@app.post("/api/upload")
async def upload_document(file: UploadFile = File(...)):
    start_time = time.time()
    try:
        content = await file.read()
        text = extract_text(content, file.filename)

        if not text.strip():
            raise HTTPException(status_code=400, detail="No readable text found in document.")

        chunks = chunk_text(text)
        if not chunks:
            raise HTTPException(status_code=400, detail="Document could not be chunked.")

        # Generate embeddings and normalize for inner-product (cosine similarity)
        embeddings = embedder.encode(chunks, normalize_embeddings=True)
        dimension = embeddings.shape[1]

        # Build FAISS index
        index = faiss.IndexFlatIP(dimension) # Inner Product on normalized vectors = Cosine Similarity
        index.add(np.array(embeddings, dtype=np.float32))

        doc_id = str(uuid.uuid4())
        DOCUMENTS_STORE[doc_id] = {
            "chunks": chunks,
            "index": index,
            "history": []
        }

        latency = (time.time() - start_time) * 1000
        return {
            "doc_id": doc_id,
            "chunk_count": len(chunks),
            "latency_ms": round(latency, 2),
            "message": "Document indexed successfully."
        }
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/ask")
def ask_question(req: AskRequest):
    start_time = time.time()

    if req.doc_id not in DOCUMENTS_STORE:
        raise HTTPException(status_code=404, detail="Document not found.")

    doc = DOCUMENTS_STORE[req.doc_id]
    index = doc["index"]
    chunks = doc["chunks"]

    # 1. Embed the question
    retrieval_start = time.time()
    q_embedding = embedder.encode([req.question], normalize_embeddings=True)

    # 2. Search FAISS index
    scores, indices = index.search(np.array(q_embedding, dtype=np.float32), req.top_k)

    # 3. Filter by min_score
    retrieved_chunks = []
    for score, idx in zip(scores[0], indices[0]):
        if score >= req.min_score and idx != -1:
            retrieved_chunks.append({
                "text": chunks[idx],
                "score": float(score),
                "chunk_idx": int(idx)
            })
    retrieval_latency = (time.time() - retrieval_start) * 1000

    if not retrieved_chunks:
        return {
            "answer": "No relevant information found in the document to answer your question.",
            "citations": [],
            "retrieval_latency_ms": round(retrieval_latency, 2),
            "total_latency_ms": round((time.time() - start_time) * 1000, 2),
            "mode": "none"
        }

    # 4. Generate Answer (Extractive or Generative)
    if req.api_key and req.api_key.strip():
        mode = "generative"
        context = "\n\n".join([f"Source [{i+1}]: {c['text']}" for i, c in enumerate(retrieved_chunks)])
        system_prompt = "You are a helpful assistant. Use the provided context to answer the user's question. If the answer is not in the context, say 'I cannot find the answer in the document.' Always cite your sources using the [number] format provided in the context."

        headers = {
            "Authorization": f"Bearer {req.api_key.strip()}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": req.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {req.question}"}
            ],
            "temperature": 0.3
        }

        try:
            resp = requests.post(f"{req.api_base.rstrip('/')}/chat/completions", headers=headers, json=payload, timeout=30)
            resp.raise_for_status()
            answer = resp.json()["choices"][0]["message"]["content"]
        except Exception as e:
            answer = f"Error calling Generative API: {str(e)}. Falling back to extractive mode."
            mode = "extractive"
            answer = "\n\n".join([f"({c['score']:.2f}) {c['text']}" for c in retrieved_chunks])
    else:
        mode = "extractive"
        answer = "Here are the most relevant excerpts from the document:\n\n" + \
                 "\n\n".join([f"Snippet {i+1} (Match: {c['score']:.2f}): {c['text']}" for i, c in enumerate(retrieved_chunks)])

    # Record history
    entry = {
        "question": req.question,
        "answer": answer,
        "mode": mode,
        "citations": retrieved_chunks
    }
    doc["history"].append(entry)

    total_latency = (time.time() - start_time) * 1000
    return {
        "answer": answer,
        "citations": retrieved_chunks,
        "mode": mode,
        "retrieval_latency_ms": round(retrieval_latency, 2),
        "total_latency_ms": round(total_latency, 2)
    }

@app.get("/api/history/{doc_id}")
def get_history(doc_id: str):
    if doc_id not in DOCUMENTS_STORE:
        raise HTTPException(status_code=404, detail="Document not found.")
    return {"history": DOCUMENTS_STORE[doc_id]["history"]}
