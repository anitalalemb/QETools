from pathlib import Path
from datetime import datetime
import re
import sys
import math

FLOAT = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][-+]?\d+)?"


# ============================================================
# BASIC HELPERS
# ============================================================

def read(path):
    return Path(path).read_text(errors="ignore")


def dfloat(x):
    return float(x.replace("D", "E").replace("d", "e"))


def last_or(items, default=None):
    return items[-1] if items else default


def status_line(label, status, detail=""):
    pad = 30
    tail = f" — {detail}" if detail else ""
    print(f"{label:<{pad}}: {status}{tail}")


def fmt_float(x):
    if x is None or not math.isfinite(x):
        return "—"
    return f"{x:.3e}"


def fmt_time(path):
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "UNKNOWN"


# ============================================================
# NUMERICAL TREND ANALYSIS
# ============================================================

def trend(vals):
    if len(vals) < 4:
        return "insufficient data"

    recent = vals[-min(12, len(vals)):]
    x0 = recent[0]
    x1 = recent[-1]

    if x0 <= 0:
        return "unknown"

    if x1 < x0 / 3:
        return "strongly decreasing"

    if x1 < x0 * 0.8:
        return "decreasing"

    diffs = [
        recent[i + 1] - recent[i]
        for i in range(len(recent) - 1)
    ]

    sign_changes = sum(
        1
        for a, b in zip(diffs, diffs[1:])
        if a * b < 0
    )

    spread = max(recent) - min(recent)
    mean = sum(recent) / len(recent)

    if mean > 0:
        if sign_changes >= max(2, len(diffs) // 3) and spread > 0.15 * mean:
            return "oscillatory"

        if spread < 0.2 * mean:
            return "plateau"

    if x1 > x0 * 1.5:
        return "worsening"

    return "mixed / noisy"


# ============================================================
# INPUT PARSING
# ============================================================

def parse_qe_input_settings(text):
    keys = [
        "calculation",
        "restart_mode",
        "prefix",
        "outdir",
        "pseudo_dir",
        "nat",
        "ntyp",
        "nbnd",
        "occupations",
        "smearing",
        "degauss",
        "conv_thr",
        "etot_conv_thr",
        "forc_conv_thr",
        "electron_maxstep",
        "diagonalization",
        "mixing_beta",
        "mixing_mode",
        "startingwfc",
        "startingpot",
        "max_seconds",
        "ecutwfc",
        "ecutrho",
    ]

    out = {}

    for key in keys:
        m = re.search(
            rf"(?im)^\s*{re.escape(key)}\s*=\s*([^,\n/]+)",
            text,
        )

        if m:
            out[key] = m.group(1).strip().strip("'\"")

    return out


# ============================================================
# QE OUTPUT CORE PARSING
# ============================================================

def output_core(text):
    iters = []
    acc = []
    energies = []
    neg = []
    forces = []

    for line in text.splitlines():

        m = re.search(
            r"iteration #\s*(\d+)",
            line,
            re.I,
        )
        if m:
            iters.append(int(m.group(1)))

        m = re.search(
            r"estimated scf accuracy\s*<\s*(" + FLOAT + r")\s*Ry",
            line,
            re.I,
        )
        if m:
            acc.append(dfloat(m.group(1)))

        m = re.search(
            r"!\s+total energy\s*=\s*(" + FLOAT + r")\s*Ry",
            line,
            re.I,
        )

        if not m:
            m = re.search(
                r"(?<!estimated )\btotal energy\s*=\s*(" + FLOAT + r")\s*Ry",
                line,
                re.I,
            )

        if m:
            energies.append(dfloat(m.group(1)))

        m = re.search(
            r"negative rho.*?:\s*(" + FLOAT + r")",
            line,
            re.I,
        )
        if m:
            neg.append(abs(dfloat(m.group(1))))

        m = re.search(
            r"Total force\s*=\s*(" + FLOAT + r")",
            line,
            re.I,
        )
        if m:
            forces.append(dfloat(m.group(1)))

    return {
        "iters": iters,
        "acc": acc,
        "energies": energies,
        "neg": neg,
        "forces": forces,
    }


# ============================================================
# CALCULATION-TYPE CLASSIFICATION
# ============================================================

def classify_output(path, text=None):
    """
    Classify a QE output without trusting the filename alone.

    Returns:
        SCF
        RELAX
        VC-RELAX
        NSCF
        BANDS
        UNKNOWN
    """

    p = Path(path)

    if text is None:
        try:
            text = read(p)
        except Exception:
            return "UNKNOWN"

    name = p.name.lower()
    low = text.lower()

    # Strongest evidence first
    if (
        "bfgs geometry optimization" in low
        or "end of bfgs geometry optimization" in low
        or "number of bfgs steps" in low
    ):
        if (
            "vc-relax" in name
            or "variable cell" in low
            or "cell_parameters" in low and "new unit-cell volume" in low
        ):
            return "VC-RELAX"

        return "RELAX"

    # Filename clues for calculations whose type is often not printed
    if "nscf" in name:
        return "NSCF"

    if "bands" in name or "band_" in name:
        return "BANDS"

    if "relax" in name or "-rx-" in name or "_rx_" in name:
        return "RELAX"

    # Standard PWSCF electronic SCF output
    if (
        "program pwscf" in low
        and (
            "estimated scf accuracy" in low
            or "convergence has been achieved" in low
            or "number of kohn-sham states" in low
        )
    ):
        return "SCF"

    return "UNKNOWN"


def is_qe_output(path):
    try:
        text = read(path)
    except Exception:
        return False

    return (
        "Program PWSCF" in text
        or "estimated scf accuracy" in text
        or "BFGS Geometry Optimization" in text
        or "JOB DONE." in text
        or "Error in routine" in text
    )


# ============================================================
# STATUS INTERPRETATION
# ============================================================

def termination_flags(text):
    return {
        "jobdone": "JOB DONE." in text,
        "wall_stop": bool(
            re.search(r"Maximum CPU time exceeded", text, re.I)
        ),
        "stopped": bool(
            re.search(r"Calculation stopped", text, re.I)
        ),
        "notconv": bool(
            re.search(r"convergence NOT achieved", text, re.I)
        ),
        "error": bool(
            re.search(r"Error in routine", text, re.I)
        ),
    }


def last_position(text, pattern):
    matches = list(re.finditer(pattern, text, re.I))
    return matches[-1].start() if matches else -1


def latest_electronic_status(text):
    """
    Determine whether the most recent electronic SCF cycle actually converged.

    Important for relaxation outputs:
    an ionic relaxation can stop at wall time even if previous electronic
    SCF cycles converged.
    """

    pos_conv = last_position(
        text,
        r"convergence has been achieved in\s+\d+\s+iterations",
    )

    pos_iter = last_position(
        text,
        r"iteration #\s*\d+",
    )

    pos_notconv = last_position(
        text,
        r"convergence NOT achieved",
    )

    pos_error = last_position(
        text,
        r"Error in routine",
    )

    if pos_error >= 0:
        return "ERROR"

    if pos_notconv > pos_conv:
        return "NOT CONVERGED"

    if pos_conv >= 0 and pos_conv > pos_iter:
        return "CONVERGED"

    if pos_iter >= 0 and pos_iter > pos_conv:
        return "INCOMPLETE"

    if pos_conv >= 0:
        return "CONVERGED"

    return "UNKNOWN"


def ionic_status(text, calc_type):
    if calc_type not in ("RELAX", "VC-RELAX"):
        return "N/A"

    if re.search(
        r"bfgs converged",
        text,
        re.I,
    ):
        return "CONVERGED"

    if re.search(
        r"End of BFGS Geometry Optimization",
        text,
        re.I,
    ):
        return "CONVERGED"

    if re.search(
        r"Maximum CPU time exceeded|Calculation stopped",
        text,
        re.I,
    ):
        return "STOPPED / INCOMPLETE"

    return "UNPROVEN"


def summary_status(text, calc_type):
    flags = termination_flags(text)
    electronic = latest_electronic_status(text)

    if flags["error"]:
        return "ERROR"

    if calc_type in ("RELAX", "VC-RELAX"):
        ion = ionic_status(text, calc_type)

        if ion == "CONVERGED":
            return "RELAX CONV"

        if ion == "STOPPED / INCOMPLETE":
            if electronic == "CONVERGED":
                return "IONIC STOP"
            return "RELAX STOP"

        return "RELAX ?"

    if calc_type == "SCF":
        if electronic == "CONVERGED" and not (
            flags["wall_stop"]
            or flags["stopped"]
            or flags["notconv"]
        ):
            return "CONVERGED"

        if flags["wall_stop"] or flags["stopped"]:
            return "STOPPED"

        if electronic == "NOT CONVERGED":
            return "NOT CONV"

        if electronic == "INCOMPLETE":
            return "INCOMPLETE"

        return "UNKNOWN"

    return "UNKNOWN"


# ============================================================
# FILE DISCOVERY
# ============================================================

def discover_outputs(path="."):
    p = Path(path)

    if p.is_file():
        return [p]

    if not p.is_dir():
        return []

    files = []

    for f in p.glob("*.out"):
        if f.is_file() and is_qe_output(f):
            files.append(f)

    return sorted(
        files,
        key=lambda x: x.stat().st_mtime,
        reverse=True,
    )


def scf_candidates(path="."):
    files = discover_outputs(path)

    out = []

    for f in files:
        try:
            t = read(f)
        except Exception:
            continue

        if classify_output(f, t) == "SCF":
            out.append(f)

    return out


def select_scf_file(path=".", mode="latest"):
    p = Path(path)

    # Explicit file always wins.
    if p.is_file():
        return p

    candidates = scf_candidates(path)

    if not candidates:
        raise SystemExit(
            f"No SCF-like Quantum ESPRESSO output found for: {path}"
        )

    if mode == "latest":
        return candidates[0]

    if mode == "best":
        ranked = []

        for f in candidates:
            text = read(f)
            c = output_core(text)

            conv = summary_status(text, "SCF") == "CONVERGED"

            best = min(c["acc"]) if c["acc"] else float("inf")

            # Converged calculations first,
            # then lowest residual,
            # then newest file.
            ranked.append(
                (
                    0 if conv else 1,
                    best,
                    -f.stat().st_mtime,
                    f,
                )
            )

        ranked.sort(
            key=lambda x: (
                x[0],
                x[1],
                x[2],
            )
        )

        return ranked[0][-1]

    raise SystemExit(
        f"Unknown qescf selection mode: {mode}"
    )


def list_scf_candidates(path="."):
    candidates = scf_candidates(path)

    print("=" * 105)
    print("QESCF — SCF CANDIDATES")
    print("=" * 105)

    if not candidates:
        print("No SCF-like outputs found.")
        return

    print(
        f"{'#':<4}"
        f"{'DATE':<18}"
        f"{'STATUS':<14}"
        f"{'BEST':<13}"
        f"{'LAST':<13}"
        f"FILE"
    )

    print("-" * 105)

    for i, f in enumerate(candidates, 1):
        text = read(f)
        c = output_core(text)

        best = min(c["acc"]) if c["acc"] else None
        last = c["acc"][-1] if c["acc"] else None

        print(
            f"{i:<4}"
            f"{fmt_time(f):<18}"
            f"{summary_status(text, 'SCF'):<14}"
            f"{fmt_float(best):<13}"
            f"{fmt_float(last):<13}"
            f"{f.name}"
        )

    print("=" * 105)
    print("Default qescf selection: newest SCF candidate.")
    print("Use qescf --best for the strongest converged SCF candidate.")
    print("Use qescf FILE.out to analyze exactly one file.")


# ============================================================
# QESCF
# ============================================================

def scf(path=None):
    args = sys.argv[1:]

    if path is None:
        path = "."

    mode = "latest"

    # Console-script invocation
    if args:
        if args[0] in ("-l", "--list"):
            target = args[1] if len(args) > 1 else "."
            list_scf_candidates(target)
            return

        if args[0] == "--best":
            mode = "best"
            path = args[1] if len(args) > 1 else "."

        elif args[0] in ("-h", "--help"):
            print(
                """
Usage:
  qescf
  qescf FILE.out
  qescf DIRECTORY
  qescf -l
  qescf --list
  qescf --best

Default:
  Analyze the newest true SCF-like QE output in the directory.

Explicit file:
  Analyze exactly that file.

--best:
  Prefer an explicitly converged SCF with the lowest parsed residual.

--list:
  Show all detected SCF candidates.
"""
            )
            return

        else:
            path = args[0]

    p = select_scf_file(path, mode=mode)
    text = read(p)
    c = output_core(text)
    calc_type = classify_output(p, text)

    print("=" * 90)
    print("QESCF — RESEARCH-GRADE QUANTUM ESPRESSO SCF ANALYZER v0.3")
    print("=" * 90)
    print(f"File                        : {p.resolve()}")
    print(f"Calculation classification  : {calc_type}")
    print(f"Selection mode              : {'explicit file' if Path(path).is_file() else mode}")
    print(f"File modified               : {fmt_time(p)}")

    # --------------------------------------------------------
    # METADATA
    # --------------------------------------------------------

    patterns = {
        "QE version":
            r"Program PWSCF v\.([^\s]+)",

        "Atoms":
            r"number of atoms/cell\s*=\s*(\d+)",

        "Atomic species":
            r"number of atomic types\s*=\s*(\d+)",

        "Electrons":
            r"number of electrons\s*=\s*(" + FLOAT + r")",

        "KS states":
            r"number of Kohn-Sham states\s*=\s*(\d+)",

        "k-points":
            r"number of k points\s*=\s*(\d+)",

        "ecutwfc":
            r"kinetic-energy cutoff\s*=\s*(" + FLOAT + r")\s*Ry",

        "ecutrho":
            r"charge density cutoff\s*=\s*(" + FLOAT + r")\s*Ry",
    }

    meta = {}

    for key, pattern in patterns.items():
        m = re.search(pattern, text, re.I)

        if m:
            meta[key] = m.group(1)

    print("\nSYSTEM / NUMERICS")

    for k, v in meta.items():
        print(f"{k:<30}: {v}")

    sm = re.search(
        r"number of k points=.*?"
        r"(Marzari-Vanderbilt|Methfessel-Paxton|Gaussian|cold)"
        r" smearing.*?width \(Ry\)=\s*("
        + FLOAT
        + r")",
        text,
        re.I,
    )

    if sm:
        print(
            f"{'Smearing':<30}: {sm.group(1)}"
        )

        print(
            f"{'Smearing width':<30}: {sm.group(2)} Ry"
        )

    # --------------------------------------------------------
    # PSEUDOPOTENTIALS
    # --------------------------------------------------------

    pseudo = []

    for line in text.splitlines():
        if re.search(
            r"\bUPF\b|PseudoPot\.|pseudopotential|read from file",
            line,
            re.I,
        ):
            s = line.strip()

            if s and s not in pseudo:
                pseudo.append(s)

    if pseudo:
        print("\nPSEUDOPOTENTIAL TRACE")

        for line in pseudo[:20]:
            print(" ", line)

    # --------------------------------------------------------
    # CONVERGENCE
    # --------------------------------------------------------

    print("\nSCF CONVERGENCE")

    conv_hits = re.findall(
        r"convergence has been achieved in\s+(\d+)\s+iterations",
        text,
        re.I,
    )

    if c["acc"]:
        best = min(c["acc"])
        ibest = c["acc"].index(best) + 1

        print(
            f"Residual samples             : {len(c['acc'])}"
        )

        print(
            f"First estimated accuracy     : {c['acc'][0]:.10e} Ry"
        )

        print(
            f"Best estimated accuracy      : {best:.10e} Ry  [sample {ibest}]"
        )

        print(
            f"Last estimated accuracy      : {c['acc'][-1]:.10e} Ry"
        )

        print(
            f"Recent residual behaviour    : {trend(c['acc'])}"
        )

        if len(c["acc"]) >= 10:
            recent = c["acc"][-10:]

            print(
                f"Last-10 min / max            : "
                f"{min(recent):.10e} / {max(recent):.10e} Ry"
            )

            if best > 0:
                print(
                    f"Last / best ratio            : "
                    f"{c['acc'][-1] / best:.3f}"
                )
    else:
        print(
            "Estimated accuracy            : UNKNOWN"
        )

    if c["iters"]:
        print(
            f"Highest iteration number     : {max(c['iters'])}"
        )

    if conv_hits:
        print(
            f"QE convergence statements    : {len(conv_hits)}"
        )

        print(
            f"Last converged cycle          : {conv_hits[-1]} iterations"
        )

    if c["energies"]:
        print(
            f"Energy samples               : {len(c['energies'])}"
        )

        print(
            f"Last total energy            : {c['energies'][-1]:.12f} Ry"
        )

        if len(c["energies"]) >= 2:
            de = (
                c["energies"][-1]
                - c["energies"][-2]
            )

            print(
                f"Last ΔE                      : {de:.6e} Ry"
            )

        if len(c["energies"]) >= 5:
            last5 = c["energies"][-5:]
            spread = max(last5) - min(last5)

            print(
                f"Last-5 energy spread         : {spread:.6e} Ry"
            )

    # --------------------------------------------------------
    # SCIENTIFIC SANITY
    # --------------------------------------------------------

    print("\nSCIENTIFIC SANITY")

    if c["neg"]:
        print(
            f"negative rho min / max       : "
            f"{min(c['neg']):.3e} / {max(c['neg']):.3e}"
        )
    else:
        print(
            "negative rho                 : not reported"
        )

    eigwarn = []

    for m in re.finditer(
        r"(\d+)\s+eigenvalues? not converged",
        text,
        re.I,
    ):
        eigwarn.append(int(m.group(1)))

    if eigwarn:
        print(
            f"Eigenvector warning lines    : {len(eigwarn)}"
        )

        print(
            f"Max unconverged eigenvals    : {max(eigwarn)}"
        )
    else:
        print(
            "Eigenvector warnings         : none parsed"
        )

    charge_wrong = bool(
        re.search(
            r"charge is wrong",
            text,
            re.I,
        )
    )

    many_bands = bool(
        re.search(
            r"too many bands are not converged",
            text,
            re.I,
        )
    )

    # --------------------------------------------------------
    # PERFORMANCE
    # --------------------------------------------------------

    timing = last_or(
        re.findall(
            r"(?m)^.*PWSCF.*WALL.*$",
            text,
        )
    )

    if timing:
        print("\nPERFORMANCE")
        print(timing.strip())

    # --------------------------------------------------------
    # TERMINATION
    # --------------------------------------------------------

    flags = termination_flags(text)

    print("\nTERMINATION EVIDENCE")

    status_line(
        "JOB DONE marker",
        "YES" if flags["jobdone"] else "NO",
    )

    status_line(
        "Wall-time limit reached",
        "YES" if flags["wall_stop"] else "NO",
    )

    status_line(
        "Calculation stopped marker",
        "YES" if flags["stopped"] else "NO",
    )

    status_line(
        "Explicit non-convergence",
        "YES" if flags["notconv"] else "NO",
    )

    err = re.search(
        r"Error in routine\s+([^( \n]+)",
        text,
    )

    if err:
        status_line(
            "QE runtime error",
            "YES",
            f"Error in routine {err.group(1)}",
        )
    else:
        status_line(
            "QE runtime error",
            "NO",
        )

    # --------------------------------------------------------
    # ASSESSMENT
    # --------------------------------------------------------

    electronic = latest_electronic_status(text)

    print("\nASSESSMENT")

    status_line(
        "Execution integrity",
        "FAIL"
        if flags["error"]
        else (
            "PASS"
            if flags["jobdone"]
            else "UNKNOWN"
        ),
    )

    status_line(
        "Latest electronic SCF",
        electronic,
    )

    if c["acc"]:
        status_line(
            "Residual behaviour",
            "INFO",
            trend(c["acc"]),
        )

    if c["neg"]:
        mx = max(c["neg"])

        # Do not call negative rho automatically fatal.
        # This is a diagnostic quantity whose acceptable magnitude depends
        # on pseudopotentials/system/numerics.
        nrho_status = (
            "WARNING"
            if mx >= 0.1
            else "INFO"
        )

        status_line(
            "Negative-rho diagnostic",
            nrho_status,
            f"max={mx:.3e}",
        )

    if eigwarn or many_bands:
        status_line(
            "Diagonalization sanity",
            "WARNING",
            "unconverged eigenvectors/bands reported",
        )
    else:
        status_line(
            "Diagonalization sanity",
            "PASS",
        )

    if charge_wrong:
        status_line(
            "Charge consistency",
            "WARNING",
            "QE reported 'charge is wrong'",
        )
    else:
        status_line(
            "Charge consistency",
            "PASS",
        )

    # --------------------------------------------------------
    # RESEARCH DECISION
    # --------------------------------------------------------

    print("\nRESEARCH DECISION")

    result = summary_status(
        text,
        calc_type,
    )

    if calc_type != "SCF":
        print(
            f"WARNING — this file is classified as {calc_type}, not SCF."
        )
        print(
            "Use qerelax for ionic-relaxation interpretation."
        )

    elif result == "CONVERGED":
        print(
            "SCF CONVERGED — electronic self-consistency is explicitly established."
        )

    elif result == "STOPPED":
        print(
            "SCF NOT FINISHED — calculation stopped before a final converged SCF was established."
        )

    elif result == "ERROR":
        print(
            "SCF FAILED — QE runtime error. Do not use this as a final SCF result."
        )

    elif result in ("NOT CONV", "INCOMPLETE"):
        print(
            "SCF NOT CONVERGED / INCOMPLETE — do not use as a final SCF result."
        )

    else:
        print(
            "SCF STATUS UNPROVEN — inspect the output/history before production use."
        )

    print("\nNEXT USEFUL COMMANDS")

    print(
        "  qescf -l          # list SCF candidates"
    )

    print(
        "  qescf --best      # strongest converged SCF candidate"
    )

    print(
        "  qehistory         # inspect all QE outputs in this folder"
    )

    print(
        "  qecompare A.out B.out"
    )

    print(
        "  qecheck ."
    )

    print("=" * 90)


# ============================================================
# QERELAX — still intentionally basic for this v0.3 stage
# ============================================================

def relax(path=None):
    args = sys.argv[1:]

    if path is None:
        path = args[0] if args else "."

    p = Path(path)

    if p.is_dir():
        candidates = []

        for f in discover_outputs(p):
            t = read(f)

            if classify_output(f, t) in (
                "RELAX",
                "VC-RELAX",
            ):
                candidates.append(f)

        if not candidates:
            raise SystemExit(
                f"No relaxation-like QE output found for: {path}"
            )

        p = candidates[0]

    text = read(p)
    c = output_core(text)
    typ = classify_output(p, text)

    print("=" * 80)
    print("QERELAX — RELAXATION ANALYZER v0.3")
    print("=" * 80)

    print(
        f"File                        : {p.resolve()}"
    )

    print(
        f"Calculation classification  : {typ}"
    )

    if c["energies"]:
        print(
            f"Ionic/energy samples        : {len(c['energies'])}"
        )

        print(
            f"Last energy                 : {c['energies'][-1]:.12f} Ry"
        )

    if c["forces"]:
        print(
            f"Force samples               : {len(c['forces'])}"
        )

        print(
            f"Last total force            : {c['forces'][-1]:.6e}"
        )

        print(
            f"Best total force            : {min(c['forces']):.6e}"
        )

    print(
        f"Latest electronic SCF       : {latest_electronic_status(text)}"
    )

    print(
        f"Ionic relaxation            : {ionic_status(text, typ)}"
    )

    print(
        "\nFull research-grade force/geometry analysis "
        "will be added in the dedicated qerelax upgrade."
    )


# ============================================================
# QEHISTORY
# ============================================================

def history(path=None):
    args = sys.argv[1:]

    if path is None:
        path = args[0] if args else "."

    p = Path(path)

    files = discover_outputs(p)

    print("=" * 132)
    print("QEHISTORY — QUANTUM ESPRESSO CALCULATION HISTORY v0.3")
    print("=" * 132)

    if not files:
        print(
            f"No QE output files found in: {p.resolve()}"
        )
        return

    # chronological ordering is much easier for restart history
    files = sorted(
        files,
        key=lambda x: x.stat().st_mtime,
    )

    print(
        f"{'DATE':<18}"
        f"{'TYPE':<10}"
        f"{'BEST SCF':<13}"
        f"{'LAST SCF':<13}"
        f"{'ELECTRONIC':<14}"
        f"{'OVERALL':<14}"
        f"FILE"
    )

    print("-" * 132)

    rows = []

    for f in files:
        try:
            text = read(f)
        except Exception:
            continue

        typ = classify_output(
            f,
            text,
        )

        c = output_core(text)

        best = (
            min(c["acc"])
            if c["acc"]
            else None
        )

        last = (
            c["acc"][-1]
            if c["acc"]
            else None
        )

        electronic = latest_electronic_status(
            text
        )

        overall = summary_status(
            text,
            typ,
        )

        rows.append(
            {
                "file": f,
                "text": text,
                "type": typ,
                "best": best,
                "last": last,
                "electronic": electronic,
                "overall": overall,
            }
        )

        print(
            f"{fmt_time(f):<18}"
            f"{typ:<10}"
            f"{fmt_float(best):<13}"
            f"{fmt_float(last):<13}"
            f"{electronic:<14}"
            f"{overall:<14}"
            f"{f.name}"
        )

    # --------------------------------------------------------
    # SMART SUMMARY
    # --------------------------------------------------------

    print("\n" + "=" * 132)
    print("HISTORY SUMMARY")
    print("=" * 132)

    scf_rows = [
        r
        for r in rows
        if r["type"] == "SCF"
    ]

    converged_scfs = [
        r
        for r in scf_rows
        if r["overall"] == "CONVERGED"
    ]

    relax_rows = [
        r
        for r in rows
        if r["type"] in (
            "RELAX",
            "VC-RELAX",
        )
    ]

    if scf_rows:
        latest_scf = max(
            scf_rows,
            key=lambda r:
            r["file"].stat().st_mtime,
        )

        print(
            f"Latest SCF attempt           : {latest_scf['file'].name}"
        )

        print(
            f"Latest SCF status            : {latest_scf['overall']}"
        )

    if converged_scfs:
        best_scf = min(
            converged_scfs,
            key=lambda r: (
                r["best"]
                if r["best"] is not None
                else float("inf")
            ),
        )

        print(
            f"Best converged SCF          : {best_scf['file'].name}"
        )

        print(
            f"Best converged residual     : {fmt_float(best_scf['best'])} Ry"
        )

    else:
        print(
            "Best converged SCF          : none detected"
        )

    if relax_rows:
        latest_relax = max(
            relax_rows,
            key=lambda r:
            r["file"].stat().st_mtime,
        )

        print(
            f"Latest relaxation           : {latest_relax['file'].name}"
        )

        print(
            f"Latest relaxation status    : {latest_relax['overall']}"
        )

        print(
            f"Latest relax electronic SCF : {latest_relax['electronic']}"
        )

    print("\nRECOMMENDED COMMANDS")

    print(
        "  qescf -l"
    )

    print(
        "  qescf --best"
    )

    if converged_scfs:
        print(
            f"  qescf {best_scf['file'].name}"
        )

    print("=" * 132)


# ============================================================
# QECOMPARE
# ============================================================

def compare(a=None, b=None):
    args = sys.argv[1:]

    if a is None or b is None:
        if len(args) != 2:
            raise SystemExit(
                "Usage: qecompare A.out B.out"
            )

        a, b = args

    pa = Path(a)
    pb = Path(b)

    if not pa.is_file():
        raise SystemExit(
            f"File not found: {a}"
        )

    if not pb.is_file():
        raise SystemExit(
            f"File not found: {b}"
        )

    ta = read(pa)
    tb = read(pb)

    ca = output_core(ta)
    cb = output_core(tb)

    print("=" * 90)
    print("QECOMPARE")
    print("=" * 90)

    for label, va, vb in [
        (
            "Best residual",
            min(ca["acc"])
            if ca["acc"]
            else None,
            min(cb["acc"])
            if cb["acc"]
            else None,
        ),
        (
            "Last residual",
            ca["acc"][-1]
            if ca["acc"]
            else None,
            cb["acc"][-1]
            if cb["acc"]
            else None,
        ),
        (
            "Last energy",
            ca["energies"][-1]
            if ca["energies"]
            else None,
            cb["energies"][-1]
            if cb["energies"]
            else None,
        ),
    ]:
        print(
            f"{label:<24}: "
            f"{va}    |    {vb}"
        )

    print(
        f"Type A                  : {classify_output(pa, ta)}"
    )

    print(
        f"Type B                  : {classify_output(pb, tb)}"
    )

    print(
        f"Status A                : {summary_status(ta, classify_output(pa, ta))}"
    )

    print(
        f"Status B                : {summary_status(tb, classify_output(pb, tb))}"
    )

    print(
        f"A                       : {pa}"
    )

    print(
        f"B                       : {pb}"
    )


# ============================================================
# QECHECK
# ============================================================

def check(path=None):
    args = sys.argv[1:]

    if path is None:
        path = args[0] if args else "."

    p = Path(path)

    print("=" * 80)
    print("QECHECK — BASIC DIRECTORY INTEGRITY")
    print("=" * 80)

    print(
        f"Path: {p.resolve()}"
    )

    if not p.is_dir():
        raise SystemExit(
            "qecheck currently expects a directory."
        )

    ins = list(
        p.glob("*.in")
    )

    outs = list(
        p.glob("*.out")
    )

    errs = list(
        p.glob("*.err")
    )

    print(
        f"Input files                  : {len(ins)}"
    )

    print(
        f"Output files                 : {len(outs)}"
    )

    print(
        f"Error files                  : {len(errs)}"
    )

    for f in sorted(ins):
        text = read(f)
        settings = parse_qe_input_settings(
            text
        )

        print(
            f"\nINPUT: {f.name}"
        )

        for key in [
            "calculation",
            "restart_mode",
            "prefix",
            "outdir",
            "pseudo_dir",
            "nat",
            "nbnd",
            "occupations",
            "smearing",
            "degauss",
            "conv_thr",
            "etot_conv_thr",
            "mixing_beta",
            "mixing_mode",
            "startingwfc",
            "startingpot",
            "max_seconds",
        ]:
            if key in settings:
                print(
                    f"  {key:<18} {settings[key]}"
                )

        pdir = settings.get(
            "pseudo_dir"
        )

        if pdir:
            pp = Path(pdir)

            print(
                f"  {'pseudo_dir exists':<18} "
                f"{'YES' if pp.exists() else 'NO'}"
            )

    print(
        "\nFurther input ↔ save-dir ↔ Slurm ↔ output "
        "cross-checking will be added after SCF/history validation."
    )


# ============================================================
# QETOOLS MAIN
# ============================================================

def main():
    if len(sys.argv) < 2:
        print(
            """
Usage:
  qetools scf [path]
  qetools relax [path]
  qetools history [path]
  qetools compare A.out B.out
  qetools check [path]
"""
        )
        return

    cmd = sys.argv[1]
    args = sys.argv[2:]

    if cmd == "scf":
        old = sys.argv
        sys.argv = ["qescf"] + args

        try:
            scf()
        finally:
            sys.argv = old

    elif cmd == "relax":
        old = sys.argv
        sys.argv = ["qerelax"] + args

        try:
            relax()
        finally:
            sys.argv = old

    elif cmd == "history":
        old = sys.argv
        sys.argv = ["qehistory"] + args

        try:
            history()
        finally:
            sys.argv = old

    elif cmd == "compare":
        if len(args) != 2:
            raise SystemExit(
                "Usage: qetools compare A.out B.out"
            )

        compare(
            args[0],
            args[1],
        )

    elif cmd == "check":
        old = sys.argv
        sys.argv = ["qecheck"] + args

        try:
            check()
        finally:
            sys.argv = old

    else:
        raise SystemExit(
            f"Unknown command: {cmd}"
        )


if __name__ == "__main__":
    main()
