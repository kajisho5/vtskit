#!/bin/bash
# SessionStart hook for Claude Code cloud sessions: the tools the tests need.
#
# vtskit is stdlib-only, so there is nothing to pip install. The tests build a
# disc with ffmpeg and dvdauthor and play it back with libdvdnav; without
# them most tests are skipped. This installs them once (the container state
# is cached after the hook), and only in a remote session.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

need_install=0
for tool in ffmpeg ffprobe dvdauthor; do
  command -v "$tool" >/dev/null 2>&1 || need_install=1
done
ldconfig -p 2>/dev/null | grep -q 'libdvdnav\.so\.4' || need_install=1

if [ "$need_install" = 1 ] || [ -n "${VTSKIT_HOOK_FORCE_INSTALL:-}" ]; then
  SUDO=""
  if [ "$(id -u)" != 0 ]; then SUDO="sudo"; fi
  export DEBIAN_FRONTEND=noninteractive
  $SUDO apt-get update -qq
  $SUDO apt-get install -y -qq --no-install-recommends ffmpeg dvdauthor libdvdnav4
fi

echo "export PYTHONPATH=\"$CLAUDE_PROJECT_DIR\"" >> "${CLAUDE_ENV_FILE:-/dev/null}"
