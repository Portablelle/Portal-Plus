#!/usr/bin/env bash
[[ $# == 1 ]] || exit 2
slot=$1
case "$slot" in
  botty|portal|codex|botty-2|portal-2|codex-2) ;;
  *) exit 2 ;;
esac
family=${slot%-2}
workspace=/home/gh-runner/codex-workspace
image_file=/var/lib/plus-runner-codex/workspace.img
if [[ $slot == codex-2 ]]; then
  workspace=/home/gh-runner/codex-workspace-2
  image_file=/var/lib/plus-runner-codex/workspace-2.img
fi
