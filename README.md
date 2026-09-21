# mjudge

Competitive programming in [Mere](https://merelang.org/): a local judge that
runs Mere solutions against [Library Checker](https://judge.yosupo.jp/) test
data, and prints them next to the problem's own C++ reference solution.

```sh
sh judge/setup.sh                                   # clone upstream, generate cases
MERE=<mere checkout> python3 judge/run.py           # the board
MERE=<mere checkout> sh judge/poison.sh             # can the board print red?
```

```
problem                   verdict         time     ref   ratio  peak_rss   ref_rss   alloc_MB
many_aplusb               AC             0.233   0.370    0.6x     161.6       1.3      159.9
shortest_path             AC             0.173   0.143    1.2x     122.6      39.2      122.1
unionfind                 AC             0.021   0.043    0.5x      16.9       2.9       14.3
zalgorithm                AC             0.095   0.050    1.9x      26.8       4.2       25.7
```

Four rows of twelve, with the table's own footer left off. A full run prints
that footer — the pinned upstream revision, the machine's load average, and how
many times each timed case was run — because a board pasted somewhere without
its conditions is a number that has left them behind. What these rows cost
before this repository existed is in **What this board has found**, below.

## Why this exists

Not to collect solutions. Competitive programming is the first user of this
language that brings its own limits: a time limit and a memory limit that were
set by someone else, for a program someone else wrote, and that cannot be
negotiated with. Every other program written in Mere so far has decided for
itself what "fast enough" meant.

It is also the first user with no way to avoid passing a comparator to a
generic container, and no way to avoid printing half a million lines. Both of
those turned out to cost something.

## What this board has found

Three, so far. Each one is something this repository measured or tripped over,
a change in the compiler, and the evidence that the change landed.

**One write syscall per output line** (mere v0.1.480). Ten of the twelve rows
ran four to twelve times the reference, and the spread was not about
algorithms. `judge/io_cost.sh` runs each solution twice on its largest case,
once with stdout to a file and once to `/dev/null`; the gap was the same
constant everywhere — about **1.6 microseconds per output line**. `main` set
stdout line buffered, so an `fwrite` of an already-assembled answer flushed at
every newline, and the idiom the docs recommend — accumulate, print once —
bought nothing. `print_no_nl` goes through `write(2)` now. The million-line row
went from 2.40 s to 0.23 s against a reference of 0.37.

The two rows whose whole answer is a single line did not move. That is what
confirmed the attribution rather than assuming it: a fix that also changed the
control would have been measuring something else.

**An environment allocated per comparison** (mere v0.1.481-482). Only one
problem here hands a comparator to a generic sort, and it was spending
**60.4%** of everything it allocated on the closure environments built to pass
the second argument — 1,855,936 of them. A closure whose return type is an
arrow now carries an entry point that takes both arguments at once. That row
went from 134.8 MB allocated and 140.6 MB peak RSS to **39.2 and 45.1**.

The other eleven rows did not move, and that is the point again: this was
never a corpus-wide cost, it was the cost of abstracting over a function.

**A trailing `()`** (mere v0.1.494). A program whose value is unit printed
`()` when it finished, which on a judge that compares output exactly is one
unconditional wrong answer. Every solution in this repository ended with the
same one-line workaround — and twelve programs writing the same line is
evidence about the default, not about the programs. They no longer do.

## What the board measures

| column | what it is, and why it is that and not something else |
| --- | --- |
| `verdict` | Every violation, not the first one. `TLE` and `MLE` are separate kinds with different fixes, and a row may read `TLE+MLE`; a verdict that reported only the first would hide the second. |
| `time`, `peak_rss` | The **worst** case, never the mean. A judge's limit is per case, and a mean over eighteen cases rewards being fast on the seventeen small ones. The worst case is then run again (`REPEATS`, default 3) and the **fastest** of those kept: timing noise is one-sided, so the quickest run is the closest thing to the program's own cost, and a mean would fold in how busy the machine was and report it as a property of the program. Only that one case is repeated, because only that one reaches the board. |
| `ref`, `ref_rss` | The same numbers for the problem's own `sol/correct.cpp`, built and run on this machine, in this loop. Wall clock on its own measures the machine; the ratio is the part that travels to another machine. |
| `alloc_MB` | `MERE_REGION_STATS`'s `alloc_total`: how many bytes the program allocated. Peak RSS cannot answer this — it is quantised, and it says nothing about what was reclaimed. |

A missing reference prints `--`. A hole in the measurement is never rendered as
a pass.

Under the board are its conditions: the upstream revision, the machine's load
average, and how many times each timed case was run. If the repeats of a case
disagreed by more than 1.25x, the row is named as `UNSTABLE` — three
consecutive runs of the same two rows, unchanged, once reported one of them at
0.8x, 0.5x and 1.1x, and nothing on the board said so. A ratio printed to two
significant figures on a machine that was doing something else is not a
measurement of the program.

## The poisons

`judge/poison.sh` runs three deliberately bad solutions against the real test
data and requires the board to name each failure: a correct but O(NQ) union-find
(`TLE`), a correct solution that touches 1.3 GB (`MLE`), and one that inverts
its answer (`WA`). A fourth check runs the correct solution and requires `AC`,
because three poisons alone would also be satisfied by a board that is red about
everything.

**If a poison comes back AC, the solution is not good — the board is broken.**
A board that has only ever been green has not been shown to have a red state.

## Layout

```
judge/      the instrument: setup.sh, run.py, poison.sh
solutions/  one .mere per problem; <problem>__<tag>.mere is a variant, off the
            default board (that is how the poisons reach real test data)
lib/        the competitive programming library — empty on purpose, for now
```

`lib/` stays empty until the board can show what a library change costs. Writing
it first would mean choosing a design with no way to measure whether it helped.

## The instrument is not written in Mere

Every other dogfood in this family is a Mere program. This one is Python, on
purpose: a compiler regression that broke the subject would break the
measurement with it, and a gate cannot be built out of the thing it measures.
Python is already a dependency of the upstream problem repository.

## Test data is not committed

Library Checker generates its cases from generators that are compiled and run at
setup time. `judge/setup.sh` does that. Twenty megabytes of generated bytes in
git would make the diff of a one-line solution unreadable.

The upstream revision is pinned to `e6466056` — the one every number above was
measured against. It has to be, because everything on the board comes from
upstream: the time limit a verdict is decided against, the test data, and the
reference solution both `ref` columns are measured from. An upstream that moves
moves the measurement. `judge/setup.sh` refuses, loudly and by name, to run
against a clone sitting at a different revision, and the board prints the
revision under itself so that a row pasted somewhere else carries the
conditions it was measured under. `LC_REV=<sha>` asks the question against a
newer upstream on purpose; record the answer next to the revision it came
from.

## Upstream, and what this repository does not contain

The problems, their generators, their checkers and their reference solutions
belong to [yosupo06/library-checker-problems](https://github.com/yosupo06/library-checker-problems),
which is licensed Apache-2.0. **None of it is copied here.** `judge/setup.sh`
clones it and runs its own `generate.py`; `judge/run.py` then reads a problem's
`info.toml` for the time limit, builds its `sol/correct.cpp` as the reference,
and invokes its checker to decide a verdict. Everything in this repository —
the harness, the solutions, this README — is original work under the MIT
license in `LICENSE`.

Each solution names the problem it answers and links to its page. The
constraints quoted in those comments are facts about the problem, not its
statement; the statement stays upstream where it is maintained.
