FROM python:3.12-slim

WORKDIR /app

# Install deps first for layer caching.
COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir \
      "fastapi>=0.115" "uvicorn[standard]>=0.32" "httpx>=0.27" \
      "sqlalchemy>=2.0" "pydantic>=2.9" "pydantic-settings>=2.6" \
      "cryptography>=43" "apscheduler>=3.10" "pandas>=2.2" "openpyxl>=3.1" \
      "anthropic>=0.40" "espn-api>=0.44" "python-dotenv>=1.0"

COPY api ./api

EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
