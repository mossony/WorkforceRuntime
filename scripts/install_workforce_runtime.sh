#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${ROOT_DIR}/docker-compose.yml"
FRONTEND_DIR="${ROOT_DIR}/workforce_runtime/dashboard/frontend"
DASHBOARD_STATIC_INDEX="${ROOT_DIR}/workforce_runtime/dashboard/static/index.html"
IMAGE_NAME="workforce-runtime:local"
PORT="${WORKFORCE_RUNTIME_PORT:-8765}"
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-workforceruntime}"
COMPOSE_NETWORK="${COMPOSE_PROJECT_NAME}_default"
NON_INTERACTIVE=0

for arg in "$@"; do
  case "$arg" in
    --yes|-y)
      NON_INTERACTIVE=1
      ;;
    --help|-h)
      cat <<'EOF'
Usage: scripts/install_workforce_runtime.sh [--yes]

Builds the Workforce Runtime Docker image and starts:
  - workforce-mysql
  - workforce-rabbitmq
  - workforce-runtime dashboard

Environment:
  OPENROUTER_API_KEY       optional, passed into the app container
  NVIDIA_API_KEY           optional, passed into the app container
  WORKFORCE_RUNTIME_PORT   host dashboard port, defaults to 8765
  COMPOSE_PROJECT_NAME     compose project name, defaults to workforceruntime

If neither the official Codex CLI (`codex`) nor Claude Code (`claude`) is
available on PATH, the installer prompts to install one before continuing.

The web dashboard is built with Vite + React. If npm or frontend dependencies
are missing, the installer prints the required Node.js/npm setup steps and can
run `npm ci` plus `npm run build` before building the Docker image.
EOF
      exit 0
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      exit 2
      ;;
  esac
done

need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

refresh_agent_path() {
  local dir
  for dir in "${HOME}/.local/bin" "${HOME}/bin" /opt/homebrew/bin /usr/local/bin; do
    if [ -d "$dir" ]; then
      case ":${PATH}:" in
        *":${dir}:"*) ;;
        *) PATH="${dir}:${PATH}" ;;
      esac
    fi
  done
  export PATH
  hash -r 2>/dev/null || true
}

has_terminal_agent() {
  command -v codex >/dev/null 2>&1 || command -v claude >/dev/null 2>&1
}

print_terminal_agents() {
  local found=0
  if command -v codex >/dev/null 2>&1; then
    echo "Detected Codex CLI: $(command -v codex)"
    found=1
  fi
  if command -v claude >/dev/null 2>&1; then
    echo "Detected Claude Code: $(command -v claude)"
    found=1
  fi
  if [ "$found" -eq 0 ]; then
    echo "No supported terminal agent detected."
  fi
}

install_codex_cli() {
  need curl
  echo "Installing official Codex CLI..."
  if [ "$NON_INTERACTIVE" -eq 1 ]; then
    curl -fsSL https://chatgpt.com/codex/install.sh | CODEX_NON_INTERACTIVE=1 sh
  else
    curl -fsSL https://chatgpt.com/codex/install.sh | sh
  fi
  refresh_agent_path
}

install_claude_code() {
  need curl
  echo "Installing official Claude Code..."
  curl -fsSL https://claude.ai/install.sh | bash
  refresh_agent_path
}

ensure_terminal_agent() {
  refresh_agent_path
  if has_terminal_agent; then
    print_terminal_agents
    return
  fi

  echo "No supported terminal agent was found on PATH."
  echo "Workforce Runtime requires at least one terminal agent: official Claude Code or Codex CLI."

  if [ "$NON_INTERACTIVE" -eq 1 ]; then
    echo "--yes was provided; installing official Codex CLI by default."
    install_codex_cli
  else
    echo
    echo "Install an official terminal agent now?"
    echo "  1) Claude Code (official Anthropic installer)"
    echo "  2) Codex CLI (official OpenAI installer)"
    echo "  3) Both"
    echo "  4) Skip"
    read -r -p "Select an option [1-4]: " agent_choice
    case "${agent_choice:-}" in
      1)
        install_claude_code
        ;;
      2)
        install_codex_cli
        ;;
      3)
        install_claude_code
        install_codex_cli
        ;;
      *)
        echo "At least one terminal agent must be installed to continue." >&2
        echo "Install official Claude Code or Codex CLI, then rerun this installer." >&2
        exit 1
        ;;
    esac
  fi

  if ! has_terminal_agent; then
    echo "At least one terminal agent must be installed to continue." >&2
    echo "The installer finished the requested install step, but neither 'claude' nor 'codex' is available on PATH." >&2
    echo "Open a new shell or add the installer target directory to PATH, then rerun this installer." >&2
    exit 1
  fi

  print_terminal_agents
}

confirm() {
  local prompt="$1"
  if [ "$NON_INTERACTIVE" -eq 1 ]; then
    return 0
  fi
  read -r -p "${prompt} [Y/n] " answer
  case "${answer:-Y}" in
    y|Y|yes|YES) return 0 ;;
    *) return 1 ;;
  esac
}

frontend_changed_since_build() {
  [ ! -f "$DASHBOARD_STATIC_INDEX" ] && return 0
  find "$FRONTEND_DIR" \
    -path "${FRONTEND_DIR}/node_modules" -prune -o \
    -type f -newer "$DASHBOARD_STATIC_INDEX" -print -quit | grep -q .
}

dashboard_frontend_deps_ready() {
  [ -d "${FRONTEND_DIR}/node_modules" ] || return 1
  (
    cd "$FRONTEND_DIR"
    npm ls --depth=0 react react-dom vite @vitejs/plugin-react >/dev/null 2>&1
  )
}

install_dashboard_frontend_deps() {
  (
    cd "$FRONTEND_DIR"
    if [ -f package-lock.json ]; then
      npm ci
    else
      npm install
    fi
  )
}

build_dashboard_frontend() {
  (
    cd "$FRONTEND_DIR"
    npm run build
  )
}

ensure_dashboard_frontend() {
  [ -f "${FRONTEND_DIR}/package.json" ] || return 0

  local needs_deps=0
  local needs_build=0
  if [ ! -f "$DASHBOARD_STATIC_INDEX" ] || frontend_changed_since_build; then
    needs_build=1
  fi

  if ! command -v npm >/dev/null 2>&1; then
    echo "npm was not found on PATH."
    echo "The dashboard frontend now uses Vite + React and needs Node.js/npm to install or rebuild assets."
    echo "Install Node.js, then rerun this installer. On macOS with Homebrew:"
    echo "  brew install node"
    if [ "$needs_build" -eq 0 ]; then
      echo "A built dashboard asset bundle already exists, so continuing without rebuilding frontend assets."
      return 0
    fi
    echo "Dashboard static assets are missing or stale, so npm is required before continuing." >&2
    exit 1
  fi

  echo "Detected npm: $(npm --version)"

  if ! dashboard_frontend_deps_ready; then
    needs_deps=1
  fi

  if [ "$needs_deps" -eq 1 ]; then
    echo "Dashboard frontend dependencies are missing or incomplete."
    if confirm "Install dashboard frontend dependencies with npm ci"; then
      install_dashboard_frontend_deps
      needs_build=1
    else
      echo "Frontend dependencies are required to rebuild the dashboard." >&2
      exit 1
    fi
  fi

  if [ "$needs_build" -eq 1 ]; then
    echo "Dashboard frontend assets are missing or older than the React/Vite source."
    if confirm "Build dashboard frontend assets with npm run build"; then
      build_dashboard_frontend
    else
      echo "Fresh dashboard static assets are required before building the Docker image." >&2
      exit 1
    fi
  fi
}

need docker

if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose -p "$COMPOSE_PROJECT_NAME" -f "$COMPOSE_FILE")
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose -p "$COMPOSE_PROJECT_NAME" -f "$COMPOSE_FILE")
else
  echo "Missing Docker Compose. Install Docker Desktop or docker compose." >&2
  exit 1
fi

container_exists() {
  docker container inspect "$1" >/dev/null 2>&1
}

compose_project_label() {
  docker inspect -f '{{ index .Config.Labels "com.docker.compose.project" }}' "$1" 2>/dev/null || true
}

is_external_container() {
  local name="$1"
  container_exists "$name" || return 1
  [ "$(compose_project_label "$name")" != "$COMPOSE_PROJECT_NAME" ]
}

connect_external_service() {
  local name="$1"
  local alias="$2"
  docker start "$name" >/dev/null
  docker network connect --alias "$alias" "$COMPOSE_NETWORK" "$name" 2>/dev/null || true
}

echo "Workforce Runtime installer"
echo "Project: ${ROOT_DIR}"
echo "Compose: ${COMPOSE_PROJECT_NAME}"
echo "Image:   ${IMAGE_NAME}"
echo "URL:     http://127.0.0.1:${PORT}"
echo

if [ -z "${OPENROUTER_API_KEY:-}" ]; then
  echo "OPENROUTER_API_KEY is not set. The dashboard will start, but real OpenRouter runs need this env var."
fi
if [ -z "${NVIDIA_API_KEY:-}" ]; then
  echo "NVIDIA_API_KEY is not set. NVIDIA fallback models will be unavailable."
fi
echo

ensure_terminal_agent
echo
ensure_dashboard_frontend
echo

confirm "Build image and start MySQL, RabbitMQ, and Workforce Runtime?" || {
  echo "Cancelled."
  exit 0
}

cd "$ROOT_DIR"

echo "Building Workforce Runtime image..."
"${COMPOSE[@]}" build workforce-runtime

echo "Starting Docker services..."
external_mysql=0
external_rabbitmq=0
if is_external_container workforce-mysql; then
  external_mysql=1
  echo "Reusing existing workforce-mysql container."
fi
if is_external_container workforce-rabbitmq; then
  external_rabbitmq=1
  echo "Reusing existing workforce-rabbitmq container."
fi

if [ "$external_mysql" -eq 0 ] && [ "$external_rabbitmq" -eq 0 ]; then
  "${COMPOSE[@]}" up -d workforce-mysql workforce-rabbitmq workforce-runtime
else
  if [ "$external_mysql" -eq 0 ]; then
    "${COMPOSE[@]}" up -d workforce-mysql
  fi
  if [ "$external_rabbitmq" -eq 0 ]; then
    "${COMPOSE[@]}" up -d workforce-rabbitmq
  fi
  "${COMPOSE[@]}" up -d --no-deps workforce-runtime
  if [ "$external_mysql" -eq 1 ]; then
    connect_external_service workforce-mysql workforce-mysql
  fi
  if [ "$external_rabbitmq" -eq 1 ]; then
    connect_external_service workforce-rabbitmq workforce-rabbitmq
  fi
  "${COMPOSE[@]}" restart workforce-runtime
fi

echo "Waiting for dashboard health..."
for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:${PORT}/healthz" >/dev/null 2>&1; then
    echo "Workforce Runtime is ready: http://127.0.0.1:${PORT}"
    echo "RabbitMQ management: http://127.0.0.1:15672 (workforce / workforce)"
    exit 0
  fi
  sleep 2
done

echo "Services started, but dashboard health did not become ready in time."
echo "Inspect logs with:"
echo "  docker compose -p ${COMPOSE_PROJECT_NAME} -f ${COMPOSE_FILE} logs -f workforce-runtime"
exit 1
