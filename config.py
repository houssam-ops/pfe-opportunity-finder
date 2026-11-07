"""Validated local configuration; secrets never enter reports or the database."""
from dataclasses import dataclass
from pathlib import Path
import os
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
WEIGHTS = {"skills": 35, "timing": 20, "location": 15, "experience": 15, "interests": 10, "recency": 5}

@dataclass
class Settings:
    root: Path = ROOT
    session_minutes: float = 10
    source_timeout: float = 35
    request_timeout: float = 12
    max_items: int = 150
    cv_path: str = "data/cv/CV_Houssam_Zouheir.pdf"
    ai_enabled: bool = False
    ai_base_url: str = "https://api.openai.com/v1"
    ai_api_key: str = ""
    ai_model: str = ""
    ai_timeout: float = 20

    @property
    def db_path(self): return self.root / "data" / "opportunities.sqlite3"
    @property
    def output_dir(self): return self.root / "output"
    @property
    def cv_file(self):
        p = Path(self.cv_path)
        return p if p.is_absolute() else self.root / p

    def prepare(self):
        for p in (self.root / "data/cv", self.root / "data/cache", self.output_dir):
            p.mkdir(parents=True, exist_ok=True)

def load_settings(root=ROOT):
    load_dotenv(root / ".env", override=False)
    def number(name, default, low, high):
        try: value = float(os.getenv(name, default))
        except ValueError: raise ValueError(f"{name} must be a number") from None
        if not low <= value <= high: raise ValueError(f"{name} must be between {low} and {high}")
        return value
    return Settings(root=root,
        session_minutes=number("SESSION_DURATION_MINUTES", 10, .01, 120),
        source_timeout=number("SOURCE_TIMEOUT_SECONDS", 35, 1, 120),
        request_timeout=number("REQUEST_TIMEOUT_SECONDS", 12, 1, 60),
        max_items=int(number("MAX_ITEMS_PER_SOURCE", 150, 1, 1000)),
        cv_path=os.getenv("CV_PATH", "data/cv/CV_Houssam_Zouheir.pdf"),
        ai_enabled=os.getenv("AI_ENABLED", "false").lower() == "true",
        ai_base_url=os.getenv("AI_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
        ai_api_key=os.getenv("AI_API_KEY", ""), ai_model=os.getenv("AI_MODEL", ""),
        ai_timeout=number("AI_TIMEOUT_SECONDS", 20, 1, 90))
