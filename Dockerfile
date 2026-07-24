# Target is the rpi-k8s cluster (Raspberry Pi 4), so this image must build for
# linux/arm64. The CI workflow builds multi-arch; see .github/workflows/image.yml.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# UV_COMPILE_BYTECODE trades a little build time for faster cold starts, which
# matters more than usual on a Pi. UV_LINK_MODE=copy avoids hardlink warnings
# when the cache and the venv sit on different layers.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Dependencies first, in their own layer: they change far less often than the
# source, so an app-only edit reuses this layer instead of re-resolving.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project --no-dev

COPY src ./src
RUN uv sync --frozen --no-dev

# Non-root. The data PVC is mounted with fsGroup 1000 on the cluster, matching
# the pattern apps/recipe-box uses.
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin parts \
    && chown -R parts:parts /app
USER parts

EXPOSE 8000

CMD ["uvicorn", "bench_parts.app:app", "--host", "0.0.0.0", "--port", "8000"]
