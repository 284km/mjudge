#!/bin/sh
# judge/poison.sh — find out whether this board can print red.
#
# What is genuinely unknown about a new instrument is not whether it can call a
# correct program correct. It is whether it can call a wrong one wrong. A board
# that has only ever been green has not been shown to have a red state at all.
# So: three deliberately bad solutions, run against the real test data, and the
# board is required to name each failure.
#
#   MERE=<mere checkout> sh judge/poison.sh
#
# THE RULE: if a poison comes back AC, this script fails. A poison that misses
# its target looks exactly like a green run, so the passing condition is "the
# board named the failure", not "the poison was run".
#
# A verdict is a SET, so a row reading `TLE+MLE` passes the TLE check: a test
# that insists on exactly one kind would hide the other kind, which is the whole
# reason the verdict column is a set.
#
# The last check is the control. Poisons alone would also be satisfied by a
# board that is red about everything.
set -u

cd "$(dirname "$0")/.." || exit 2
fail=0

expect() { # expect <solution stem> <substring of the verdict>
  stem="$1"; want="$2"
  line=$(python3 judge/run.py "$stem" 2>/dev/null | awk -v s="$stem" '$1 == s')
  got=$(echo "$line" | awk '{print $2}')
  case "$got" in
    "") echo "  FAIL  $stem — no row on the board (did run.py die?)"; fail=$((fail + 1)); return ;;
    AC) echo "  FAIL  $stem — came back AC. Either the poison misses, or the board has no red"
        echo "        $line"; fail=$((fail + 1)); return ;;
  esac
  case "$got" in
    *"$want"*) echo "  ok    $stem — $got" ;;
    *) echo "  FAIL  $stem — wanted $want, got $got"
       echo "        $line"; fail=$((fail + 1)) ;;
  esac
}

echo "poison: can this board print red? (3 poisons + 1 control)"
expect unionfind__poison_tle TLE
expect unionfind__poison_mle MLE
expect unionfind__poison_wa  WA

line=$(python3 judge/run.py unionfind 2>/dev/null | awk '$1 == "unionfind"')
got=$(echo "$line" | awk '{print $2}')
if [ "$got" = "AC" ]; then
  echo "  ok    unionfind — AC (control)"
else
  echo "  FAIL  unionfind — the correct solution came back $got; the board may be red about everything"
  echo "        $line"
  fail=$((fail + 1))
fi

echo
if [ "$fail" -gt 0 ]; then
  echo "poison: $fail FAILED"
  exit 1
fi
echo "poison: all 4 as expected (TLE / MLE / WA / AC)"
