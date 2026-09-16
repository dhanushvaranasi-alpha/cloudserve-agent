"""Central place every component reads its configuration from."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    model_name: str = os.getenv("MODEL_NAME", "llama-3.3-70b-versatile")
    classifier_model: str = os.getenv("CLASSIFIER_MODEL", "llama-3.1-8b-instant")
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")

    chroma_path: str = os.getenv("CHROMA_PATH", "./storage/chroma")
    decision_log_path: str = os.getenv("DECISION_LOG_PATH", "./storage/decisions.db")

    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    confidence_threshold: float = float(os.getenv("CONFIDENCE_THRESHOLD", "0.80"))
    retrieval_top_k: int = int(os.getenv("RETRIEVAL_TOP_K", "5"))
    retrieval_min_score: float = float(os.getenv("RETRIEVAL_MIN_SCORE", "0.35"))

    prompt_version_classify: str = "PR-01 v1.0"
    prompt_version_generate: str = "PR-02 v1.0"


SETTINGS = Settings()
