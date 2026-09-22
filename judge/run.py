#!/usr/bin/env python3
"""judge/run.py -- run Mere solutions against Library Checker cases, next to C++.

    MERE=<mere checkout> python3 judge/run.py [problem ...]

WHY THE INSTRUMENT IS NOT WRITTEN IN MERE. Every other dogfood in this family is
a Mere program. This one is not, on purpose: a compiler regression that breaks
the subject would break the measurement with it, and a gate cannot be built out
of the thing it is measuring. Python is a dependency of library-checker-problems
already, so it costs nothing extra.

WHAT EACH COLUMN IS FOR.

  verdict    every violation, not the first one. TLE and MLE are separate kinds
             and a row can say `TLE+MLE`: reporting one hides the other, and the
             two have different fixes.
  time/rss   the WORST case, not the mean. A judge's limit is per case, and a
             mean over 18 cases rewards being fast on the 17 small ones.
  ref_*      the same numbers for the problem's own `sol/correct.cpp`, built and
             run on this machine in this loop. Wall clock on its own measures the
             machine; the ratio is the part that travels.
  alloc      `MERE_REGION_STATS`'s alloc_total -- how much the program allocated,
             which peak RSS cannot answer (RSS is quantised, and says nothing
             about what was reclaimed). It is the column that shows a curried
             comparator costing 24 bytes per comparison.

A row with no `ref` did not get a reference build; that is a hole in the
measurement, printed as `--`, never silently treated as a pass.
"""

import os
import re
import shutil
import signal
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
ML_MB = float(os.environ.get("ML_MB", "1024"))
KEEP_GOING = os.environ.get("KEEP_GOING", "1") != "0"
# How many times the one timed case is run. The fastest is kept; see best_of.
REPEATS = max(1, int(os.environ.get("REPEATS", "3")))
# A row whose repeats disagree by more than this is named under the board. 1.25
# is not a tuned constant -- it is "an eighth of a second on a one-second row",
# which is more than a program's own cost varies and less than a busy machine.
SPREAD_WARN = 1.25


def die(msg: str, code: int = 2):
    print(f"mjudge: {msg}", file=sys.stderr)
    sys.exit(code)


def lc_dir() -> Path:
    for cand in [os.environ.get("LC_DIR"),
                 ROOT.parent / "library-checker-problems",
                 Path.home() / "src/github.com/yosupo06/library-checker-problems"]:
        if cand and Path(cand).is_dir():
            return Path(cand)
    die("library-checker-problems not found. Point LC_DIR= at it, or run judge/setup.sh")


def mere_exe() -> Path:
    m = os.environ.get("MERE")
    if not m:
        die("set MERE=<mere checkout>")
    exe = Path(m) / "_build/default/bin/mere.exe"
    if not exe.is_file():
        die(f"no compiler at {exe} (did you run dune build?)")
    return exe


def cxx() -> str:
    for c in ("c++", "g++", "clang++"):
        p = shutil.which(c)
        if p:
            return p
    die("no C++ compiler")


def cc() -> str:
    for c in ("clang", "cc", "gcc"):
        p = shutil.which(c)
        if p:
            return p
    die("no C compiler")


# ---------------------------------------------------------------- measurement

@dataclass
class Run:
    seconds: float
    rss_bytes: int
    status: int
    timed_out: bool

    @property
    def signalled(self) -> bool:
        return os.WIFSIGNALED(self.status)

    @property
    def exit_code(self) -> int:
        return os.WEXITSTATUS(self.status) if os.WIFEXITED(self.status) else -1


def _maxrss_to_bytes(ru_maxrss: int) -> int:
    # macOS reports bytes, Linux reports kilobytes. Getting this wrong is a
    # 1024x error in the column a MLE verdict is decided from.
    return ru_maxrss if sys.platform == "darwin" else ru_maxrss * 1024


def run_measured(argv, stdin_path, stdout_path, stderr_path, timeout, extra_env=None) -> Run:
    """Run one process, and measure THAT process (os.wait4), not the children as
    a group: RUSAGE_CHILDREN's maxrss is a running maximum over every child this
    runner has ever spawned, so a big reference run would be reported again as
    the next subject's peak."""
    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)
    fa = [
        (os.POSIX_SPAWN_OPEN, 0, str(stdin_path), os.O_RDONLY, 0o666),
        (os.POSIX_SPAWN_OPEN, 1, str(stdout_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o666),
        (os.POSIX_SPAWN_OPEN, 2, str(stderr_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o666),
    ]
    t0 = time.monotonic()
    pid = os.posix_spawn(str(argv[0]), [str(a) for a in argv], env, file_actions=fa)
    timed_out = False
    while True:
        wpid, status, ru = os.wait4(pid, os.WNOHANG)
        if wpid != 0:
            break
        if not timed_out and time.monotonic() - t0 > timeout:
            timed_out = True
            os.kill(pid, signal.SIGKILL)
        time.sleep(0.002)
    return Run(time.monotonic() - t0, _maxrss_to_bytes(ru.ru_maxrss), status, timed_out)


# ---------------------------------------------------------------- the problem

@dataclass
class Problem:
    name: str
    dir: Path
    timelimit: float


def find_problem(lc: Path, name: str) -> Problem:
    hits = [p.parent for p in lc.glob(f"*/{name}/info.toml")]
    if not hits:
        die(f"problem `{name}` is not in {lc}")
    with open(hits[0] / "info.toml", "rb") as f:
        info = tomllib.load(f)
    return Problem(name, hits[0], float(info.get("timelimit", 5.0)))


def cases(prob: Problem):
    ins = sorted((prob.dir / "in").glob("*.in"))
    out = []
    for i in ins:
        o = prob.dir / "out" / (i.stem + ".out")
        if o.is_file():
            out.append((i, o))
    return out


# ---------------------------------------------------------------- build steps

def build_subject(exe_out: Path, src: Path, mere: Path) -> str | None:
    c_out = exe_out.with_suffix(".c")
    try:
        r = subprocess.run([str(mere), "-c", str(src)], capture_output=True, text=True)
    except OSError as e:
        # MERE points at a live build tree, and a build in that tree replaces
        # the binary. The check at startup said it was there; this is the same
        # question asked again at the moment it matters, because between the
        # two somebody can run `dune build`. A traceback here reads like a bug
        # in the board.
        return f"{mere}: {e.strerror} (is something rebuilding the compiler?)"
    if r.returncode != 0:
        return (r.stderr or r.stdout).strip().splitlines()[0] if (r.stderr or r.stdout) else "emit failed"
    c_out.write_text(r.stdout)
    r = subprocess.run([cc(), "-O2", "-o", str(exe_out), str(c_out)], capture_output=True, text=True)
    if r.returncode != 0:
        return "cc: " + (r.stderr.strip().splitlines() or ["failed"])[0]
    return None


def build_cpp(exe_out: Path, src: Path, include: Path | None = None) -> str | None:
    cmd = [cxx(), "-O2", "-std=c++17", "-w", "-o", str(exe_out), str(src)]
    if include:
        cmd[1:1] = ["-I", str(include)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        return "c++: " + (r.stderr.strip().splitlines() or ["failed"])[0]
    return None


ALLOC_RE = re.compile(rb"alloc_total=(\d+)")


def best_of(exe: Path, inp: Path, timeout: float, env, first: float):
    """Run one case REPEATS-1 more times and keep the FASTEST, with max/min as
    the spread.

    THE MINIMUM, NOT THE MEAN. Timing noise is one-sided: another process can
    only ever ADD time to this one. So the fastest run is the closest thing to
    the program's own cost, and a mean mixes in how busy the machine was and
    then reports it as a property of the program.

    ONLY THE WORST CASE IS REPEATED, because only the worst case reaches the
    board -- `time` is already a max over the cases. Repeating all eighteen
    would cost three times the run for numbers nobody reads: measured on this
    corpus, a full board is 176 s, repeating everything is 528 s, and repeating
    just the timed case is about 206 s.

    Why this exists at all: three consecutive runs of the same two rows, with
    no code change, reported lca at 0.8x, 0.5x and 1.1x. The README says "the
    ratio is the part that travels to another machine", and that sentence was
    not yet true."""
    times = [first]
    for _ in range(REPEATS - 1):
        r = run_measured([exe], inp, BUILD / "rep.txt", BUILD / "reperr.txt", timeout, env)
        # A repeat that times out or dies says nothing about speed, and the
        # verdict for those was already decided in the pass above.
        if r.timed_out or r.signalled:
            break
        times.append(r.seconds)
    lo, hi = min(times), max(times)
    return lo, (hi / lo if lo > 0 else 1.0)


# ---------------------------------------------------------------- the verdict

@dataclass
class Row:
    problem: str
    verdicts: set = field(default_factory=set)
    seconds: float = 0.0
    rss: int = 0
    alloc: int = 0
    ref_seconds: float | None = None
    ref_rss: int | None = None
    note: str = ""
    worst_case: str = ""
    ref_worst_case: str = ""
    # max/min across the repeats of the one timed case. 1.0 means the runs
    # agreed; anything above SPREAD_WARN is the machine talking, not the
    # program, and the board says so rather than printing a confident ratio.
    spread: float = 1.0
    ref_spread: float = 1.0

    @property
    def verdict(self) -> str:
        if self.note:
            return self.note
        return "+".join(sorted(self.verdicts)) if self.verdicts else "AC"


def judge_one(stem: str, prob: Problem, mere: Path, lc: Path) -> Row:
    """`stem` is the solution file's name; `prob` is the problem it is judged
    against. They differ for variants (`unionfind__poison_tle` is judged against
    `unionfind`), which is how a deliberately bad solution can be pointed at real
    test data -- the only way to find out whether this board can print red."""
    row = Row(stem)
    tests = cases(prob)
    if not tests:
        row.note = "NO-DATA"
        return row

    src = ROOT / "solutions" / f"{stem}.mere"
    if not src.is_file():
        row.note = "NO-SOL"
        return row

    BUILD.mkdir(exist_ok=True)
    subj = BUILD / stem
    err = build_subject(subj, src, mere)
    if err:
        row.note = "CE"
        print(f"  {stem}: {err}", file=sys.stderr)
        return row

    ref = BUILD / f"{prob.name}.ref"
    ref_err = build_cpp(ref, prob.dir / "sol" / "correct.cpp")
    checker = BUILD / f"{prob.name}.checker"
    chk_err = build_cpp(checker, prob.dir / "checker.cpp", include=lc / "common")

    # WARM UP BOTH BINARIES FIRST, and throw the result away. A binary that was
    # just written to disk costs ~0.16-0.25 s on its first exec on macOS (code
    # signing validation); every run afterwards is free. Since this runner
    # rebuilds on every invocation, EVERY first case paid it -- and both the
    # subject and the reference paid the same constant, which made the ratio
    # column read 1.0x for a program that is really 2.3x. A flattering number is
    # as much a reason to suspect the harness as an unflattering one.
    warm = min(tests, key=lambda t: t[0].stat().st_size)[0]
    for exe, ok in ((subj, True), (ref, ref_err is None)):
        if ok:
            run_measured([exe], warm, BUILD / "warm.txt", BUILD / "warmerr.txt", 60.0,
                         {"MERE_REGION_STATS": "1"})

    ml_bytes = ML_MB * 1024 * 1024
    for inp, expected in tests:
        so = BUILD / "out.txt"
        se = BUILD / "err.txt"
        r = run_measured([subj], inp, so, se, prob.timelimit, {"MERE_REGION_STATS": "1"})
        if r.seconds > row.seconds:
            row.seconds, row.worst_case = r.seconds, inp.stem
        row.rss = max(row.rss, r.rss_bytes)
        m = ALLOC_RE.search(se.read_bytes())
        if m:
            row.alloc = max(row.alloc, int(m.group(1)))

        if r.timed_out:
            row.verdicts.add("TLE")
        elif r.signalled:
            row.verdicts.add("RE")
        elif r.exit_code != 0:
            row.verdicts.add("RE")
        if r.rss_bytes > ml_bytes:
            row.verdicts.add("MLE")

        if not chk_err and not r.timed_out and not r.signalled:
            c = subprocess.run([str(checker), str(inp), str(expected), str(so)],
                               capture_output=True)
            if c.returncode != 0:
                row.verdicts.add("WA")
        elif chk_err:
            row.verdicts.add("NO-CHECKER")

        if ref_err is None:
            rr = run_measured([ref], inp, BUILD / "refout.txt", BUILD / "referr.txt",
                              max(prob.timelimit * 4, 10.0))
            if rr.seconds > (row.ref_seconds or 0.0):
                row.ref_seconds, row.ref_worst_case = rr.seconds, inp.stem
            row.ref_rss = max(row.ref_rss or 0, rr.rss_bytes)

        if row.verdicts and not KEEP_GOING:
            break
    if ref_err:
        print(f"  {prob.name}: reference build failed -- {ref_err}", file=sys.stderr)

    # CONFIRM THE TWO NUMBERS THAT REACH THE BOARD. Each program's own worst
    # case is run again and the fastest kept -- the subject's and the
    # reference's, which are not always the same case, because each column is
    # that program's worst.
    #
    # Skipped for a row that already has a verdict: its headline is TLE or WA,
    # not a time, and re-running a TLE case costs a full time limit every time.
    if REPEATS > 1 and not row.verdicts and not row.note:
        by_stem = {i.stem: i for i, _ in tests}
        if row.worst_case in by_stem:
            row.seconds, row.spread = best_of(
                subj, by_stem[row.worst_case], prob.timelimit,
                {"MERE_REGION_STATS": "1"}, row.seconds)
        if ref_err is None and row.ref_worst_case in by_stem and row.ref_seconds:
            row.ref_seconds, row.ref_spread = best_of(
                ref, by_stem[row.ref_worst_case], max(prob.timelimit * 4, 10.0),
                None, row.ref_seconds)
    return row


# ---------------------------------------------------------------- the board

def mb(n) -> str:
    return "--" if n is None else f"{n / 1048576:.1f}"


def sec(n) -> str:
    return "--" if n is None else f"{n:.3f}"


def compiler_version(mere: Path) -> str:
    """Which compiler produced these numbers.

    The upstream line names what was measured and the conditions line names the
    machine; this names the SUBJECT, which was the one missing. A board pasted
    somewhere without it invites the reader to assume the current release.

    It is a claim, not a check: the binary reports the version in its source
    tree, so a build made from an edited checkout still says the released
    number. When the numbers are going somewhere permanent, build from a
    committed revision."""
    try:
        r = subprocess.run([str(mere / "_build/default/bin/mere.exe"), "--version"],
                           capture_output=True, text=True)
        return r.stdout.strip().splitlines()[0] if r.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def upstream_rev(lc) -> str:
    """The revision the problems, the time limits and the reference solutions
    came from. Printed with the board because every number on it is measured
    against them: a row pasted anywhere without this is a number whose
    conditions have been left behind."""
    try:
        r = subprocess.run(["git", "-C", str(lc), "rev-parse", "HEAD"],
                           capture_output=True, text=True)
        return r.stdout.strip()[:12] if r.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def board(rows, lc=None, mere=None):
    head = ("problem", "verdict", "time", "ref", "ratio", "peak_rss", "ref_rss", "alloc_MB")
    print(f"{head[0]:<26}{head[1]:<12}{head[2]:>8}{head[3]:>8}{head[4]:>8}"
          f"{head[5]:>10}{head[6]:>10}{head[7]:>11}")
    bad = 0
    for r in rows:
        ratio = "--"
        if r.ref_seconds and r.ref_seconds > 0 and r.seconds > 0:
            ratio = f"{r.seconds / r.ref_seconds:.1f}x"
        print(f"{r.problem:<26}{r.verdict:<12}{sec(r.seconds):>8}{sec(r.ref_seconds):>8}"
              f"{ratio:>8}{mb(r.rss):>10}{mb(r.ref_rss):>10}{mb(r.alloc):>11}")
        if r.verdict != "AC":
            bad += 1
    print()
    print(f"{len(rows)} problems, {len(rows) - bad} AC, {bad} not AC "
          f"(TL is the problem's own, ML is {ML_MB:.0f} MB)")
    if lc is not None:
        print(f"upstream: library-checker-problems @ {upstream_rev(lc)}")

    # The upstream line records WHAT was measured. This one records UNDER WHAT
    # CONDITIONS, for the same reason: a board pasted somewhere without it is a
    # number whose conditions have been left behind.
    try:
        load = f"{os.getloadavg()[0]:.2f}"
    except OSError:
        load = "unknown"
    subject = f"{compiler_version(mere)}, " if mere is not None else ""
    print(f"conditions: {subject}load average {load}, "
          f"{REPEATS} run{'s' if REPEATS != 1 else ''} of each timed case"
          f"{' (fastest kept)' if REPEATS > 1 else ''}")

    # A row whose repeats disagreed is named. Silence here is the claim that
    # the times are the programs'; without it the ratio column would keep its
    # two significant figures on a machine that was doing something else.
    shaky = []
    for r in rows:
        if r.spread > SPREAD_WARN:
            shaky.append(f"{r.problem} (time {r.spread:.1f}x)")
        if r.ref_spread > SPREAD_WARN:
            shaky.append(f"{r.problem} (ref {r.ref_spread:.1f}x)")
    if shaky:
        print("UNSTABLE: " + ", ".join(shaky))
        print(f"          Repeats of one case disagreed by more than {SPREAD_WARN:.2f}x.")
        print("          The ratio column is measuring this machine's other work")
        print("          too; re-run on a quiet machine before quoting any of it.")
    elif REPEATS < 2:
        print("NOTE: REPEATS=1, so nothing checked whether these times are "
              "repeatable.")
    return bad


def main(argv):
    lc, mere = lc_dir(), mere_exe()
    # A solution named `<problem>__<tag>.mere` is a VARIANT of `<problem>`: it is
    # judged against that problem's data but kept out of the default board, so a
    # poison (§ judge/poison.sh) never inflates the not-AC count of real work.
    names = argv[1:] or sorted(p.stem for p in (ROOT / "solutions").glob("*.mere")
                               if "__" not in p.stem)
    if not names:
        die("there are no solutions/*.mere")
    rows = [judge_one(n, find_problem(lc, n.split("__")[0]), mere, lc) for n in names]
    return 1 if board(rows, lc, mere) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
