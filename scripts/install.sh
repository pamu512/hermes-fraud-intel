#!/usr/bin/env bash
# Install skill + 07:30 cron. Reuse Hermes/Ollama from finance-brief when present.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
BIN_DIR="${HERMES_FRAUD_BIN_DIR:-$HOME/.local/bin}"
WHATSAPP_TO="${FRAUD_BRIEF_WHATSAPP:-+85252238641}"
DELIVER="local,whatsapp:${WHATSAPP_TO}"
MODEL="7b"
SKIP_HERMES=0
SKIP_CRON=0
SKIP_GATEWAY=0
SKIP_MODEL=0

die() { printf 'install.sh: %s\n' "$*" >&2; exit 1; }
info() { printf 'install.sh: %s\n' "$*"; }

usage() {
  cat <<'EOF'
Usage: scripts/install.sh [--model 7b|14b] [--skip-hermes] [--skip-cron] [--skip-gateway] [--skip-model]
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --model)
      [[ $# -ge 2 ]] || die "--model needs 7b or 14b"
      MODEL="$2"
      shift 2
      ;;
    --skip-hermes) SKIP_HERMES=1; shift ;;
    --skip-cron) SKIP_CRON=1; shift ;;
    --skip-gateway) SKIP_GATEWAY=1; shift ;;
    --skip-model) SKIP_MODEL=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ "$MODEL" == "7b" || "$MODEL" == "14b" ]] || die "--model must be 7b or 14b"
MODEL_TAG="qwen2.5-${MODEL}-64k"
MODELFILE="$ROOT/ollama/Modelfile.${MODEL}"
[[ -f "$MODELFILE" ]] || die "missing $MODELFILE"
[[ -f "$ROOT/feeds/fraud.opml" ]] || die "missing feeds/fraud.opml"

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "missing command: $1"
}

ensure_path() {
  mkdir -p "$BIN_DIR"
  case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) export PATH="$BIN_DIR:$PATH" ;;
  esac
  if [[ -d "$HERMES_HOME/bin" ]]; then
    case ":$PATH:" in
      *":$HERMES_HOME/bin:"*) ;;
      *) export PATH="$HERMES_HOME/bin:$PATH" ;;
    esac
  fi
}

install_hermes() {
  if command -v hermes >/dev/null 2>&1; then
    info "hermes already on PATH: $(command -v hermes)"
    return
  fi
  [[ "$SKIP_HERMES" -eq 0 ]] || die "hermes not on PATH and --skip-hermes was set"
  info "installing Hermes Agent (--skip-setup)"
  curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash -s -- --skip-setup
  ensure_path
  hash -r 2>/dev/null || true
  command -v hermes >/dev/null 2>&1 || die "hermes install finished but hermes is not on PATH; add $BIN_DIR and $HERMES_HOME/bin"
}

ollama_has_tag() {
  curl -fsS http://localhost:11434/api/tags 2>/dev/null | python3 -c '
import json, sys
want = sys.argv[1]
data = json.load(sys.stdin)
names = [m.get("name", "") for m in data.get("models", [])]
raise SystemExit(0 if any(n == want or n.startswith(want + ":") for n in names) else 1)
' "$1"
}

create_ollama_model() {
  [[ "$SKIP_MODEL" -eq 0 ]] || { info "skipping model create"; return; }
  need_cmd ollama
  if ! curl -fsS http://localhost:11434/api/tags >/dev/null 2>&1; then
    die "Ollama is not reachable at http://localhost:11434 — start it, then re-run"
  fi
  if ollama_has_tag "$MODEL_TAG"; then
    info "Ollama tag $MODEL_TAG already exists — skip create"
    return
  fi
  local base="qwen2.5:${MODEL}"
  info "ensuring Ollama base model $base"
  ollama pull "$base"
  info "creating $MODEL_TAG from $MODELFILE"
  ollama create "$MODEL_TAG" -f "$MODELFILE"
}

patch_hermes_config() {
  local cfg="$HERMES_HOME/config.yaml"
  if command -v hermes >/dev/null 2>&1; then
    # qwen2.5-*-64k rejects Ollama thinking; medium reasoning_effort 400s the job.
    hermes config set agent.reasoning_effort none
    info "set agent.reasoning_effort=none (local qwen2.5 has no thinking)"
  fi
  if [[ -f "$cfg" ]] && grep -q "localhost:11434" "$cfg"; then
    info "Hermes already points at local Ollama — leave $cfg endpoint unchanged"
    return
  fi
  mkdir -p "$HERMES_HOME"
  if [[ ! -f "$cfg" ]]; then
    cat >"$cfg" <<EOF
model:
  default: ${MODEL_TAG}
  provider: custom
  base_url: http://localhost:11434/v1
  context_length: 65536
agent:
  disabled_toolsets:
    - browser
    - image_gen
    - tts
    - vision
    - web
EOF
    info "wrote $cfg"
    return
  fi
  info "$cfg exists without localhost:11434 — not overwriting; set custom Ollama endpoint yourself"
}

install_skill() {
  local dest="$HERMES_HOME/skills/fraud-daily-brief"
  rm -rf "$dest"
  mkdir -p "$dest"
  cp -R "$ROOT/skills/fraud-daily-brief/." "$dest/"
  info "copied skill to $dest"
}

install_wrapper() {
  local dest="$HERMES_HOME/scripts/fraud-daily-brief.sh"
  mkdir -p "$HERMES_HOME/scripts"
  cat >"$dest" <<EOF
#!/usr/bin/env bash
set -euo pipefail
export HERMES_FRAUD_ROOT=$(printf '%q' "$ROOT")
cd "\$HERMES_FRAUD_ROOT"
python3 scripts/dump_recent.py --write-brief
DAY="\$(date +%F)"
if [[ -f "briefs/\$DAY.md" ]]; then
  cat "briefs/\$DAY.md"
  exit 0
fi
if [[ -f "briefs/\$DAY.failed.md" ]]; then
  cat "briefs/\$DAY.failed.md"
  exit 1
fi
echo "fraud-daily-brief: no brief written for \$DAY" >&2
exit 1
EOF
  chmod +x "$dest"
  info "installed no-agent wrapper $dest"
}

whatsapp_hint() {
  if [[ -f "$HERMES_HOME/.env" ]] && grep -Eq '^WHATSAPP_ENABLED=true([[:space:]]|$)' "$HERMES_HOME/.env"; then
    info "WhatsApp enabled — cron delivers to $DELIVER"
    return
  fi
  info "WhatsApp gateway not enabled yet. Cron still targets $DELIVER."
  info "Next: hermes gateway setup  (WhatsApp, self-chat, scan QR). Then hermes gateway restart."
}

create_cron() {
  [[ "$SKIP_CRON" -eq 0 ]] || { info "skipping cron"; return; }
  need_cmd hermes
  install_wrapper
  local prompt
  prompt="$(cat "$ROOT/skills/fraud-daily-brief/references/cron-prompt.txt")"
  if hermes cron list 2>/dev/null | grep -qi "finance-daily-brief"; then
    info "finance-daily-brief cron present — leaving it untouched"
  fi
  whatsapp_hint
  if hermes cron list 2>/dev/null | grep -qi "fraud-daily-brief"; then
    info "refreshing cron job fraud-daily-brief (no-agent wrapper + $DELIVER)"
    if hermes cron edit fraud-daily-brief \
      --no-agent \
      --script fraud-daily-brief.sh \
      --deliver "$DELIVER" \
      --workdir "$ROOT" \
      --schedule "30 7 * * *"; then
      return
    fi
    info "no-agent edit failed — falling back to agent job that prints the brief"
    hermes cron edit fraud-daily-brief \
      --agent \
      --script "" \
      --prompt "$prompt" \
      --skill fraud-daily-brief \
      --deliver "$DELIVER" \
      --workdir "$ROOT" \
      --schedule "30 7 * * *"
    return
  fi
  info "creating daily cron 30 7 * * * (machine local timezone — use Asia/Singapore on this Mac)"
  if hermes cron create "30 7 * * *" \
    --name "fraud-daily-brief" \
    --no-agent \
    --script fraud-daily-brief.sh \
    --deliver "$DELIVER" \
    --workdir "$ROOT"; then
    return
  fi
  info "no-agent create failed — falling back to agent job that prints the brief"
  hermes cron create "30 7 * * *" "$prompt" \
    --name "fraud-daily-brief" \
    --skill fraud-daily-brief \
    --deliver "$DELIVER" \
    --workdir "$ROOT"
}

install_gateway() {
  [[ "$SKIP_GATEWAY" -eq 0 ]] || { info "skipping gateway"; return; }
  need_cmd hermes
  if [[ -f "$HOME/Library/LaunchAgents/ai.hermes.gateway.plist" ]]; then
    info "Hermes gateway launch agent already installed — skip"
    return
  fi
  info "installing Hermes gateway user service"
  hermes gateway install
}

main() {
  need_cmd curl
  need_cmd python3
  ensure_path
  install_hermes
  ensure_path
  create_ollama_model
  patch_hermes_config
  install_skill
  create_cron
  install_gateway
  python3 "$ROOT/scripts/check_brief.py" --offline
  info "done. health: hermes cron list && hermes cron status"
  info "model tag: $MODEL_TAG"
  info "trigger once: hermes cron run fraud-daily-brief"
}

main "$@"
