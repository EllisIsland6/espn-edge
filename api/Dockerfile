FROM python:3.12-slim

WORKDIR /app

# Install deps first for layer caching.
COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir \
      "fastapi>=0.115" "uvicorn[standard]>=0.32" "httpx>=0.27" \
      "sqlalchemy>=2.0" "pydantic>=2.9" "pydantic-settings>=2.6" \
      "cryptography>=43" "apscheduler>=3.10" "pandas>=2.2" "openpyxl>=3.1" \
      "nflreadpy>=0.1.5,<0.2" "psycopg2-binary>=2.9" \
      "anthropic>=0.40" "espn-api>=0.44" "python-dotenv>=1.0"

COPY api ./api

# APP_MODE is deliberately NOT set here.
#
# It is a required setting with no default, so a container started without it
# fails to construct `Settings` and does not serve. That is the intended
# behaviour: the deployer must say what this container is, because a hosted
# image that silently defaulted to `private_operator` would have the ESPN
# provider, the credential decrypt path and the raw cache all enabled, and the
# AI spend ceiling off. Set it at run time:
#
#   docker run -e APP_MODE=public_synthetic ...   hosted / CI
#   docker run -e APP_MODE=private_operator ...   the operator's own machine
#
# Closing this by baking a value in would put the decision back in an image
# rather than in the deployment that knows the answer.

EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
