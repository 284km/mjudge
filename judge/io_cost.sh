#!/bin/sh
# judge/io_cost.sh — how much of a row's time is the act of writing the answer?
#
# Mere's compiled main() calls setvbuf(stdout, NULL, _IOLBF, 0), which is the
# right default for a server (a log that only appears when the process exits is
# not a log) and the wrong one for a program a judge pipes to a file: line
# buffering means one write(2) per output line. C's own default does the
# opposite -- when stdout is not a tty, stdio switches to full buffering, which
# is why the C++ reference pays nothing here.
#
# This script measures the difference instead of arguing about it: each solution
# runs twice on its largest case, once with stdout to a file and once to
# /dev/null. Neither number is the truth on its own. The GAP is.
#
#   MERE=<mere checkout> sh judge/io_cost.sh [problem ...]
#
# Note that /dev/null is not free either: a write syscall to it still traps into
# the kernel. So this is a LOWER bound on what buffering would save.
set -u

cd "$(dirname "$0")/.." || exit 2
LC_DIR="${LC_DIR:-$(cd .. && pwd)/library-checker-problems}"
[ -d "$LC_DIR" ] || { echo "io_cost: no library-checker-problems (LC_DIR=)" >&2; exit 2; }

secs() { # secs <exe> <input> <redirect target>
  s=$( { /usr/bin/time -p "$1" < "$2" > "$3"; } 2>&1 | awk '/^real/{print $2}' )
  echo "${s:-0}"
}

printf '%-26s %8s %8s %8s %9s\n' problem to_file to_null gap lines
for src in solutions/*.mere; do
  stem=$(basename "$src" .mere)
  case "$stem" in *__*) continue ;; esac
  exe="build/$stem"
  [ -x "$exe" ] || { printf '%-26s %8s\n' "$stem" "(not built; run judge/run.py first)"; continue; }
  dir=$(find "$LC_DIR" -maxdepth 2 -type d -name "$stem" | head -1)
  [ -n "$dir" ] || continue
  big=$(ls -S "$dir"/in/*.in 2>/dev/null | head -1)
  [ -n "$big" ] || continue

  "$exe" < "$big" > /tmp/io_cost_out.txt 2>/dev/null   # warm: first exec of a
                                                       # fresh binary is ~0.2 s
  f=$(secs "$exe" "$big" /tmp/io_cost_out.txt)
  n=$(secs "$exe" "$big" /dev/null)
  lines=$(wc -l < /tmp/io_cost_out.txt | tr -d ' ')
  gap=$(awk -v a="$f" -v b="$n" 'BEGIN{printf "%.2f", a - b}')
  printf '%-26s %8s %8s %8s %9s\n' "$stem" "$f" "$n" "$gap" "$lines"
done
