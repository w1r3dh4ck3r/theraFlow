#!/usr/bin/env node
// PreToolUse hook: injects relevant docs right before Claude edits a file.
// Recency bias: injection fires at the most-attended end of the context window.
//
// Customize ROUTES and BLOCKED_PATHS for your project.
// All-matches routing: every matching route injects, ordered general→specific.
//
// CRITICAL: $CLAUDE_PROJECT_DIR must be stripped from absolute paths or zero
// routes match — this silently disables all enforcement. See stripRoot().

import { readFileSync, existsSync } from 'fs';
import { join } from 'path';

const projectRoot = process.env.CLAUDE_PROJECT_DIR || process.cwd();

// ─── customize per project ───────────────────────────────────────────────────

// Dirs where new files should never be created.
// Each entry: [regex (matches relative path), guidance string].
const BLOCKED_PATHS = [
  [/^src\/theraflow\/[^/]+\.py$/, 'Top-level modules in src/theraflow/ are reserved for cross-cutting concerns (config, logging, utils, main). New features belong in a subdirectory: src/theraflow/conversation/, src/theraflow/whatsapp/, src/theraflow/llm/, src/theraflow/safety/, src/theraflow/sheets/, src/theraflow/notifications/'],
];

// Valid first-level dirs under src/. Leave empty Set to skip check.
const VALID_TOP_DIRS = new Set([]);

// Docs to inject per file path.
// Walk ALL entries (all-matches). Order general→specific so narrow docs land last
// (recency-privileged). Paths are relative to project root.
const ROUTES = [
  [/\/whatsapp\//, 'docs/agent/routes.md'],
  [/\/conversation\//, 'docs/agent/services.md'],
  [/\/llm\/|\/safety\/|\/sheets\/|\/notifications\//, 'docs/agent/services.md'],
  [/test_.*\.py$|.*_test\.py$/, 'docs/agent/testing.md'],
];

// ─────────────────────────────────────────────────────────────────────────────

// Pass 3: dedup advisory (advisory only — never exits 2)
function extractFunctionNames(content) {
  const names = new Set();
  const patterns = [
    /(?:^|\s)(?:async\s+)?function\s+(\w{3,})\s*\(/gm,
    /(?:^|\s)(?:const|let|var)\s+(\w{3,})\s*=\s*(?:async\s+)?\(/gm,
    /(?:^|\s)(?:const|let|var)\s+(\w{3,})\s*=\s*(?:async\s+)?function/gm,
    /^\s*(?:async\s+)?(\w{3,})\s*\([^)]*\)\s*(?::\s*\w+\s*)?\{/gm,  // class methods / Python-style
    /^def\s+(\w{3,})\s*\(/gm,  // Python
  ];
  for (const pat of patterns) {
    let m;
    while ((m = pat.exec(content)) !== null) {
      const name = m[1];
      if (name && !['const', 'let', 'var', 'async', 'function', 'return', 'if', 'for', 'while'].includes(name)) {
        names.add(name);
      }
    }
  }
  return [...names];
}

function stripRoot(absPath) {
  const prefix = projectRoot.endsWith('/') ? projectRoot : projectRoot + '/';
  return absPath.startsWith(prefix) ? absPath.slice(prefix.length) : absPath;
}

function main() {
  let input;
  try {
    const raw = readFileSync('/dev/stdin', 'utf8');
    input = JSON.parse(raw);
  } catch {
    process.exit(0);
  }

  const toolName = input.tool_name || '';
  if (!['Write', 'Edit'].includes(toolName)) process.exit(0);

  const rawPath = input.tool_input?.file_path || '';
  if (!rawPath) process.exit(0);

  const filePath = stripRoot(rawPath);
  const isNewFile = !existsSync(rawPath);
  const messages = [];

  // Pass 1: structure check — only for new files
  if (isNewFile) {
    for (const [pattern, guidance] of BLOCKED_PATHS) {
      if (pattern.test(filePath)) {
        console.error(`[inject-context] Blocked path: ${filePath}\n${guidance}`);
        process.exit(2);
      }
    }

    const placementDoc = join(projectRoot, 'docs/agent/file-placement.md');
    if (existsSync(placementDoc)) {
      messages.push(readFileSync(placementDoc, 'utf8'));
    }
  }

  // Pass 2: context injection — all-matches, general→specific
  for (const [pattern, docPath] of ROUTES) {
    if (pattern.test(filePath)) {
      const abs = join(projectRoot, docPath);
      if (existsSync(abs)) {
        messages.push(readFileSync(abs, 'utf8'));
      }
    }
  }

  // Pass 3: dedup advisory (advisory only — never exits 2)
  const newContent = input.tool_input?.content || input.tool_input?.new_string || '';
  const oldContent = input.tool_input?.old_string || '';
  if (newContent) {
    const newNames = extractFunctionNames(newContent);
    const oldNames = new Set(extractFunctionNames(oldContent));
    const addedNames = newNames.filter(n => !oldNames.has(n));
    if (addedNames.length > 0) {
      messages.push(
        `[dedup-check] New function(s) detected: ${addedNames.join(', ')}\n` +
        `Before implementing, verify no existing version: grep -r "${addedNames[0]}" src/ (or equivalent)`
      );
    }
  }

  if (messages.length === 0) process.exit(0);

  // PreToolUse supports additionalContext JSON
  console.log(JSON.stringify({ additionalContext: messages.join('\n\n---\n\n') }));
  process.exit(0);
}

main();
