FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    gdal-bin libgdal-dev gcc g++ \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY openpvscope ./openpvscope
RUN pip install --no-cache-dir -e .

EXPOSE 8787
CMD ["uvicorn", "openpvscope.api.app:app", "--host", "0.0.0.0", "--port", "8787"]
