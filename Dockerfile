# Adapted from dcc-docker-images fastapi template. The python/uv toolchain is
# installed by `mise install` from mise.toml; this file only holds app steps.
#
#   docker build -f Dockerfile .

# Stage 1: Build the application
FROM ghcr.io/dcc-bs/dcc-docker-images/mise:13-slim AS build

ENV APP_MODE=build
ENV DOCKER_BUILD=1
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV UV_HTTP_TIMEOUT=120
# uv installs its own python here; a stable path so the runtime copy below
# never hardcodes the python version or architecture.
ENV UV_PYTHON_INSTALL_DIR="/uv-python"

# Set the working directory
WORKDIR /app

# Copy source code (the `install` task's `uv sync` installs the project
# editable, so source must be present before `mise install` runs)
COPY . .

# Install the pinned toolchain (uv) from mise.toml. The postinstall hook runs
# the `install` task, which does `uv sync --locked` and auto-installs the
# python pinned by `requires-python`.
RUN mise trust -a && mise install

# Assemble a minimal runtime: only the resolved python (drop mise, uv,
# headers, tcl-tk, terminfo). Shared logic from the base image.
RUN assemble-runtime python

# Stage 2: Run the application
# ------------------------------------------------
FROM debian:13-slim

# Set the working directory
WORKDIR /app

# Security: Create and switch to a non-root user
RUN useradd --create-home --uid 1000 app

# Environment
ENV APP_MODE=prod
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Runtime python is the one assembled from uv in the build stage
ENV PATH="/app/.venv/bin:$PATH"

# Copy the built application and the minimal runtime from the build stage
COPY --from=build --chown=app:app /app /app
COPY --from=build --chown=app:app /runtime /runtime

# Switch to the non-root user
USER app

# Expose the port the app runs on
EXPOSE 8000

# Start the MCP server over streamable HTTP with uvicorn
ENTRYPOINT ["/bin/sh", "-c", "uvicorn main:http_app --host 0.0.0.0 --port \"${PORT:-8000}\" --no-access-log"]
