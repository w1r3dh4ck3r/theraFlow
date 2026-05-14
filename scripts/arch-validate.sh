#!/usr/bin/env bash
# PostToolUse hook: grep-based architecture validation.
# Exit code 2 blocks the agent until violations are fixed.
# stderr is the only feedback channel (additionalContext unsupported in PostToolUse).
#
# CRITICAL: strip $CLAUDE_PROJECT_DIR from absolute paths, same as inject-context.mjs.
# RATCHET RULE: only add a check when existing hits in the codebase are 0-2.
#   Test first: grep -rl 'pattern' src/ | wc -l

set -euo pipefail

INPUT=$(cat)
PROJECT_ROOT="${CLAUDE_PROJECT_DIR:-$(pwd)}"
ABS_FILE=$(echo "$INPUT" | jq -r '.tool_input.file_path // ""')
FILE="${ABS_FILE#"$PROJECT_ROOT/"}"
VIOLATIONS=""

[ -z "$FILE" ] && exit 0
[ ! -f "$ABS_FILE" ] && exit 0

# ─── customize per project ───────────────────────────────────────────────────

# Code quality: print() outside tests — theraFlow uses structured logging throughout
if [[ "$FILE" == src/theraflow/*.py ]] || [[ "$FILE" == src/theraflow/**/*.py ]]; then
  if [[ "$FILE" != tests/* ]]; then
    grep -qE "^\s*print\(" "$ABS_FILE" && \
      VIOLATIONS+="  - print() in source code (use get_logger() from theraflow.logging instead)\n"
  fi
fi

# Layer boundary: whatsapp/webhook.py must stay thin — dispatch only, no LLM/safety logic inline.
# Business logic belongs in conversation/engine.py and safety/detector.py.
if [[ "$FILE" == src/theraflow/whatsapp/webhook.py ]]; then
  grep -qE "^\s*(await llm|llm\.|anthropic\.|openai\.)" "$ABS_FILE" && \
    VIOLATIONS+="  - Direct LLM call in webhook handler (delegate to ConversationEngine)\n"
fi

# Layer boundary: conversation/ must not import from whatsapp/ (dependency direction)
if [[ "$FILE" == src/theraflow/conversation/* ]]; then
  grep -qE "from theraflow\.whatsapp|import theraflow\.whatsapp" "$ABS_FILE" && \
    VIOLATIONS+="  - conversation/ imports whatsapp/ (reverse dependency — whatsapp depends on conversation, not vice versa)\n"
fi

# ─────────────────────────────────────────────────────────────────────────────

if [ -n "$VIOLATIONS" ]; then
  echo -e "[arch-validate] Violations in ${FILE}:\n${VIOLATIONS}" >&2
  exit 2
fi

exit 0
