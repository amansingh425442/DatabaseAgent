import os
from dataclasses import dataclass
from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    analytics_url: str = ""
    app_url: str = ""
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    model_factory: str = ""
    max_tool_calls: int = 12
    max_seconds: int = 90
    max_retries: int = 1
    statement_timeout_ms: int = 10000
    max_rows: int = 500
    max_payload_bytes: int = 250000

    @classmethod
    def load(cls):
        load_dotenv()
        return cls(
            analytics_url=os.getenv("ANALYTICS_DATABASE_URL", ""),
            app_url=os.getenv("APP_DATABASE_URL", ""),
            model_factory=os.getenv("MODEL_FACTORY", ""),
            embedding_model=os.getenv("EMBEDDING_MODEL", cls.embedding_model),
            max_tool_calls=int(os.getenv("MAX_TOOL_CALLS", "12")),
            max_seconds=int(os.getenv("MAX_EXECUTION_SECONDS", "90")),
            max_retries=int(os.getenv("MAX_RETRIES", "1")),
            statement_timeout_ms=int(os.getenv("STATEMENT_TIMEOUT_MS", "10000")),
            max_rows=int(os.getenv("MAX_RESULT_ROWS", "500")),
            max_payload_bytes=int(os.getenv("MAX_PAYLOAD_BYTES", "250000")),
        )

    def __post_init__(self):
        for field in ("max_tool_calls", "max_seconds", "statement_timeout_ms", "max_rows", "max_payload_bytes"):
            if getattr(self, field) <= 0:
                raise ValueError(f"{field} must be positive")
        if self.max_retries < 0:
            raise ValueError("max_retries must be nonnegative")
