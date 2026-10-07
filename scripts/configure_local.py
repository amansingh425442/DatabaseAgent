"""Generate local development credentials without overwriting existing secrets."""
from pathlib import Path
import secrets

target = Path(__file__).resolve().parents[1] / ".env"
if target.exists():
    raise SystemExit(".env already exists; leaving it unchanged.")
admin, analytics, app = (secrets.token_urlsafe(32) for _ in range(3))
target.write_text(
    f"POSTGRES_PASSWORD={admin}\nANALYTICS_PASSWORD={analytics}\nAPP_PASSWORD={app}\n"
    f"ADMIN_DATABASE_URL=postgresql+psycopg://olist_admin:{admin}@127.0.0.1:55432/olist\n"
    f"ANALYTICS_DATABASE_URL=postgresql+psycopg://olist_analytics:{analytics}@127.0.0.1:55432/olist\n"
    f"APP_DATABASE_URL=postgresql+psycopg://olist_app:{app}@127.0.0.1:55432/olist\n"
    "MODEL_FACTORY=\n", encoding="utf-8"
)
print("Created .env with local development credentials; values were not printed.")
