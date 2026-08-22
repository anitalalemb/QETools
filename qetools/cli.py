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
    """
    Parse common Quantum ESPRESSO input variables.

    Handles:
      key = 'quoted/string/value'
      key = "quoted/string/value"
      key = unquoted_value

    In particular, absolute paths such as
    /pscratch/... are preserved rather than being truncated at '/'.
    """

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
        pattern = (
            rf"(?im)^\s*{re.escape(key)}\s*=\s*"
            rf"(?:'([^']*)'|\"([^\"]*)\"|([^,\n/]+))"
        )

        m = re.search(pattern, text)

        if not m:
            continue

        value = next(
            (
                g
                for g in m.groups()
                if g is not None
            ),
            None,
        )

        if value is not None:
            out[key] = value.strip()

    return out


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
    Classify a Quantum ESPRESSO output using physical/output evidence first.

    Priority:
      1. Explicit relaxation / variable-cell evidence in QE output
      2. Strong NSCF / bands filename evidence
      3. Standard PWSCF SCF evidence
      4. Conservative filename fallback
      5. UNKNOWN

    Parent-directory names are never used for classification.
    Generic words such as "relaxed" must not turn an SCF into RELAX.
    """

    p = Path(path)

    if text is None:
        try:
            text = read(p)
        except Exception:
            return "UNKNOWN"

    name = p.name.lower()
    low = text.lower()

    # --------------------------------------------------------
    # Strong QE relaxation evidence
    # --------------------------------------------------------

    has_bfgs = (
        "bfgs geometry optimization" in low
        or "end of bfgs geometry optimization" in low
        or "number of bfgs steps" in low
        or "bfgs converged" in low
    )

    if has_bfgs:
        has_vc_evidence = (
            "variable cell" in low
            or (
                "cell_parameters" in low
                and "new unit-cell volume" in low
            )
        )

        if has_vc_evidence:
            return "VC-RELAX"

        return "RELAX"

    # --------------------------------------------------------
    # Explicit NSCF / bands filename evidence
    # --------------------------------------------------------

    if re.search(r"(^|[_\-.])nscf([_\-.]|$)", name):
        return "NSCF"

    if (
        re.search(r"(^|[_\-.])bands?([_\-.]|$)", name)
        or name.startswith("bands")
    ):
        return "BANDS"

    # --------------------------------------------------------
    # Standard electronic PWSCF evidence
    # --------------------------------------------------------

    if (
        "program pwscf" in low
        and (
            "estimated scf accuracy" in low
            or "convergence has been achieved" in low
            or "number of kohn-sham states" in low
        )
    ):
        return "SCF"

    # --------------------------------------------------------
    # Conservative filename fallback
    # --------------------------------------------------------
    # Only accept explicit calculation-style relaxation tokens.
    # Do NOT match "relaxed", "pre_relaxed", "post_relaxed", etc.

    if (
        re.search(r"(^|[_\-.])relax([_\-.]|$)", name)
        or re.search(r"(^|[_\-.])vc-relax([_\-.]|$)", name)
        or re.search(r"(^|[_\-.])rx([_\-.]|$)", name)
    ):
        if "vc-relax" in name:
            return "VC-RELAX"

        return "RELAX"

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



def relaxation_electronic_detail(text):
    """
    Separate completed electronic SCF cycles from a possibly unfinished
    current SCF cycle at the end of a relaxation output.
    """

    conv_matches = list(re.finditer(
        r"convergence has been achieved in\s+(\d+)\s+iterations",
        text,
        re.I,
    ))

    iter_matches = list(re.finditer(
        r"iteration #\s*(\d+)",
        text,
        re.I,
    ))

    notconv_matches = list(re.finditer(
        r"convergence NOT achieved",
        text,
        re.I,
    ))

    error_matches = list(re.finditer(
        r"Error in routine",
        text,
        re.I,
    ))

    n_converged = len(conv_matches)

    last_conv_pos = conv_matches[-1].start() if conv_matches else -1
    last_iter_pos = iter_matches[-1].start() if iter_matches else -1
    last_notconv_pos = notconv_matches[-1].start() if notconv_matches else -1
    last_error_pos = error_matches[-1].start() if error_matches else -1

    if last_error_pos >= 0:
        current_status = "ERROR"
    elif last_notconv_pos > last_conv_pos:
        current_status = "NOT CONVERGED"
    elif last_iter_pos > last_conv_pos:
        current_status = "INCOMPLETE"
    elif last_conv_pos >= 0:
        current_status = "CONVERGED"
    else:
        current_status = "UNKNOWN"

    if n_converged > 0:
        last_completed_status = "CONVERGED"
        last_completed_iterations = int(
            conv_matches[-1].group(1)
        )
    else:
        last_completed_status = "UNKNOWN"
        last_completed_iterations = None

    return {
        "n_converged": n_converged,
        "last_completed_status": last_completed_status,
        "last_completed_iterations": last_completed_iterations,
        "current_status": current_status,
    }

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
    """
    Research-grade Quantum ESPRESSO structural-relaxation analyzer.

    Usage:
        qerelax
        qerelax FILE.out
        qerelax DIRECTORY
        qerelax -l
        qerelax --list
        qerelax --best
        qerelax --help

    Default:
        Analyze the newest relaxation-like QE output in the directory.

    --best:
        Prefer an ionically converged relaxation. Among converged
        candidates, choose the one with the smallest final parsed force.
        If none converged, choose the newest relaxation attempt.
    """

    args = sys.argv[1:]

    if path is None:
        path = "."

    show_list = False
    choose_best = False

    if args:
        if args[0] in ("-h", "--help"):
            print("""
Usage:
  qerelax
  qerelax FILE.out
  qerelax DIRECTORY
  qerelax -l
  qerelax --list
  qerelax --best

Default:
  Analyze the newest true RELAX / VC-RELAX QE output in the directory.

Explicit file:
  Analyze exactly that file.

--best:
  Prefer an ionically converged relaxation with the smallest final force.

--list:
  Show all detected relaxation candidates.
""".strip())
            return

        if args[0] in ("-l", "--list"):
            show_list = True
            if len(args) > 1:
                path = args[1]

        elif args[0] == "--best":
            choose_best = True
            if len(args) > 1:
                path = args[1]

        else:
            path = args[0]

    def parse_relaxation(text):
        # Use "!" energies as the cleanest representation of completed
        # self-consistent ionic configurations.
        ionic_energies = [
            dfloat(x)
            for x in re.findall(
                r"(?im)^\s*!\s+total energy\s*=\s*(" + FLOAT + r")\s*Ry",
                text
            )
        ]

        forces = [
            dfloat(x)
            for x in re.findall(
                r"Total force\s*=\s*(" + FLOAT + r")",
                text,
                re.I
            )
        ]

        scf_cycles = [
            int(x)
            for x in re.findall(
                r"convergence has been achieved in\s+(\d+)\s+iterations",
                text,
                re.I
            )
        ]

        energy_errors = [
            dfloat(x)
            for x in re.findall(
                r"Energy error\s*=\s*(" + FLOAT + r")\s*Ry",
                text,
                re.I
            )
        ]

        gradient_errors = [
            dfloat(x)
            for x in re.findall(
                r"Gradient error\s*=\s*(" + FLOAT + r")\s*Ry/Bohr",
                text,
                re.I
            )
        ]

        bfgs_conv = bool(
            re.search(r"\bbfgs converged\b", text, re.I)
        )

        end_bfgs = bool(
            re.search(
                r"End of BFGS Geometry Optimization",
                text,
                re.I
            )
        )

        bfgs_summary = re.findall(
            r"bfgs converged in\s+(\d+)\s+scf cycles\s+and\s+(\d+)\s+bfgs steps",
            text,
            re.I
        )

        final_energy = re.findall(
            r"Final energy\s*=\s*(" + FLOAT + r")\s*Ry",
            text,
            re.I
        )

        final_energy = (
            dfloat(final_energy[-1])
            if final_energy
            else None
        )

        job_done = "JOB DONE." in text

        wall_stop = bool(
            re.search(
                r"Maximum CPU time exceeded",
                text,
                re.I
            )
        )

        stopped = bool(
            re.search(
                r"Calculation stopped",
                text,
                re.I
            )
        )

        explicit_notconv = bool(
            re.search(
                r"convergence NOT achieved",
                text,
                re.I
            )
        )

        qe_error = re.search(
            r"Error in routine\s+([^( \n]+)",
            text,
            re.I
        )

        final_coord_begin = len(
            re.findall(
                r"Begin final coordinates",
                text,
                re.I
            )
        )

        final_coord_end = len(
            re.findall(
                r"End final coordinates",
                text,
                re.I
            )
        )

        atomic_position_blocks = len(
            re.findall(
                r"(?im)^\s*ATOMIC_POSITIONS\b",
                text
            )
        )

        cell_blocks = len(
            re.findall(
                r"(?im)^\s*CELL_PARAMETERS\b",
                text
            )
        )

        stresses = [
            dfloat(x)
            for x in re.findall(
                r"total\s+stress\s+\(Ry/bohr\*\*3\)\s*=\s*(" + FLOAT + r")",
                text,
                re.I
            )
        ]

        timing = last_or(
            re.findall(
                r"(?m)^.*PWSCF.*WALL.*$",
                text
            )
        )

        return {
            "ionic_energies": ionic_energies,
            "forces": forces,
            "scf_cycles": scf_cycles,
            "energy_errors": energy_errors,
            "gradient_errors": gradient_errors,
            "bfgs_conv": bfgs_conv,
            "end_bfgs": end_bfgs,
            "bfgs_summary": bfgs_summary,
            "final_energy": final_energy,
            "job_done": job_done,
            "wall_stop": wall_stop,
            "stopped": stopped,
            "explicit_notconv": explicit_notconv,
            "qe_error": qe_error,
            "final_coord_begin": final_coord_begin,
            "final_coord_end": final_coord_end,
            "atomic_position_blocks": atomic_position_blocks,
            "cell_blocks": cell_blocks,
            "stresses": stresses,
            "timing": timing,
        }

    def ionic_verdict(r):
        if r["qe_error"]:
            return "ERROR"

        if r["bfgs_conv"] or r["end_bfgs"]:
            return "CONVERGED"

        if r["wall_stop"] or r["stopped"]:
            return "STOPPED"

        if r["job_done"]:
            return "INCOMPLETE"

        # Output exists and contains relaxation progress, but QE has
        # neither terminated nor reported ionic convergence.
        # This commonly represents a currently active or abruptly
        # interrupted calculation. Do not call it converged.
        if r["ionic_energies"] or r["forces"] or r["scf_cycles"]:
            return "ACTIVE"

        return "UNKNOWN"

    p0 = Path(path)

    if p0.is_file():
        candidates = [p0]

    elif p0.is_dir():
        candidates = []

        for f in discover_outputs(p0):
            try:
                t = read(f)
            except Exception:
                continue

            if classify_output(f, t) in (
                "RELAX",
                "VC-RELAX",
            ):
                candidates.append(f)

        candidates = sorted(
            candidates,
            key=lambda x: x.stat().st_mtime,
            reverse=True
        )

        if not candidates:
            raise SystemExit(
                f"No relaxation-like QE output found for: {path}"
            )

    else:
        raise SystemExit(
            f"No readable relaxation output found for: {path}"
        )

    if show_list:
        print("=" * 118)
        print("QERELAX — RELAXATION CANDIDATES")
        print("=" * 118)

        print(
            f"{'#':<4}"
            f"{'DATE':<18}"
            f"{'TYPE':<10}"
            f"{'IONIC':<12}"
            f"{'FINAL FORCE':<15}"
            f"{'BEST FORCE':<15}"
            f"FILE"
        )

        print("-" * 118)

        for i, f in enumerate(candidates, 1):
            t = read(f)
            typ = classify_output(f, t)
            r = parse_relaxation(t)
            status = ionic_verdict(r)

            final_force = (
                f"{r['forces'][-1]:.3e}"
                if r["forces"]
                else "—"
            )

            best_force = (
                f"{min(r['forces']):.3e}"
                if r["forces"]
                else "—"
            )

            dt = f.stat().st_mtime
            import datetime
            datestr = datetime.datetime.fromtimestamp(
                dt
            ).strftime("%Y-%m-%d %H:%M")

            print(
                f"{i:<4}"
                f"{datestr:<18}"
                f"{typ:<10}"
                f"{status:<12}"
                f"{final_force:<15}"
                f"{best_force:<15}"
                f"{f.name}"
            )

        print("=" * 118)
        print("Default qerelax selection: newest relaxation candidate.")
        print(
            "Use qerelax --best for the strongest completed "
            "relaxation candidate."
        )
        print(
            "Use qerelax FILE.out to analyze exactly one file."
        )
        return

    selection_mode = "explicit"

    if p0.is_dir():
        if choose_best:
            converged = []

            for f in candidates:
                t = read(f)
                r = parse_relaxation(t)

                if ionic_verdict(r) == "CONVERGED":
                    final_force = (
                        r["forces"][-1]
                        if r["forces"]
                        else float("inf")
                    )

                    converged.append(
                        (final_force, -f.stat().st_mtime, f)
                    )

            if converged:
                converged.sort(
                    key=lambda x: (x[0], x[1])
                )
                p = converged[0][2]
                selection_mode = "best"

            else:
                print("=" * 94)
                print("QERELAX — BEST RELAXATION SEARCH")
                print("=" * 94)
                print("NO CONVERGED RELAXATION FOUND.")
                print()
                print(
                    "qerelax --best only returns an ionically converged "
                    "RELAX / VC-RELAX calculation."
                )

                latest = candidates[0]
                latest_r = parse_relaxation(read(latest))

                print()
                print(f"Latest relaxation attempt   : {latest.name}")
                print(
                    f"Latest ionic status          : "
                    f"{ionic_verdict(latest_r)}"
                )

                incomplete_with_force = []

                for f in candidates:
                    rr = parse_relaxation(read(f))

                    if rr["forces"]:
                        incomplete_with_force.append(
                            (
                                rr["forces"][-1],
                                -f.stat().st_mtime,
                                f,
                                ionic_verdict(rr),
                            )
                        )

                if incomplete_with_force:
                    incomplete_with_force.sort(
                        key=lambda x: (x[0], x[1])
                    )

                    force, _, ff, status = incomplete_with_force[0]

                    print(
                        f"Lowest final force observed  : "
                        f"{force:.6e} Ry/Bohr"
                    )
                    print(
                        f"File with lowest final force : {ff.name}"
                    )
                    print(
                        f"Ionic status of that file    : {status}"
                    )

                print()
                print(
                    "Use `qerelax -l` to inspect incomplete attempts, "
                    "or `qerelax FILE.out` to analyze one explicitly."
                )
                print("=" * 94)
                return

        else:
            p = candidates[0]
            selection_mode = "latest"

    else:
        p = p0

    text = read(p)
    typ = classify_output(p, text)
    r = parse_relaxation(text)
    core = output_core(text)

    import datetime

    datestr = datetime.datetime.fromtimestamp(
        p.stat().st_mtime
    ).strftime("%Y-%m-%d %H:%M")

    print("=" * 94)
    print(
        "QERELAX — RESEARCH-GRADE QUANTUM ESPRESSO "
        "RELAXATION ANALYZER v0.4"
    )
    print("=" * 94)

    print(f"File                        : {p.resolve()}")
    print(f"Calculation classification  : {typ}")
    print(f"Selection mode              : {selection_mode}")
    print(f"File modified               : {datestr}")

    # --------------------------------------------------------
    # BASIC SYSTEM INFORMATION
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

    for key, pat in patterns.items():
        m = re.search(pat, text, re.I)

        if m:
            meta[key] = m.group(1)

    print("\nSYSTEM / NUMERICS")

    for key, value in meta.items():
        print(f"{key:<30}: {value}")

    sm = re.search(
        r"number of k points=.*?"
        r"(Marzari-Vanderbilt|Methfessel-Paxton|Gaussian|cold)"
        r" smearing.*?width \(Ry\)=\s*(" + FLOAT + r")",
        text,
        re.I
    )

    if sm:
        print(
            f"{'Smearing':<30}: {sm.group(1)}"
        )
        print(
            f"{'Smearing width':<30}: {sm.group(2)} Ry"
        )

    # --------------------------------------------------------
    # IONIC TRAJECTORY
    # --------------------------------------------------------

    print("\nIONIC RELAXATION")

    nsteps = max(
        len(r["ionic_energies"]),
        len(r["forces"])
    )

    print(
        f"{'Parsed ionic configurations':<30}: {nsteps}"
    )

    if r["ionic_energies"]:
        E = r["ionic_energies"]

        print(
            f"{'First ionic energy':<30}: "
            f"{E[0]:.12f} Ry"
        )

        print(
            f"{'Last ionic energy':<30}: "
            f"{E[-1]:.12f} Ry"
        )

        print(
            f"{'Overall ΔE':<30}: "
            f"{E[-1] - E[0]:.6e} Ry"
        )

        if len(E) >= 2:
            print(
                f"{'Last ionic ΔE':<30}: "
                f"{E[-1] - E[-2]:.6e} Ry"
            )

        if len(E) >= 5:
            spread = max(E[-5:]) - min(E[-5:])

            print(
                f"{'Last-5 energy spread':<30}: "
                f"{spread:.6e} Ry"
            )

    if r["final_energy"] is not None:
        print(
            f"{'QE final energy':<30}: "
            f"{r['final_energy']:.12f} Ry"
        )

    if r["forces"]:
        F = r["forces"]

        print(
            f"{'Force samples':<30}: {len(F)}"
        )

        print(
            f"{'First total force':<30}: "
            f"{F[0]:.6e} Ry/Bohr"
        )

        print(
            f"{'Last total force':<30}: "
            f"{F[-1]:.6e} Ry/Bohr"
        )

        print(
            f"{'Best total force':<30}: "
            f"{min(F):.6e} Ry/Bohr"
        )

        if len(F) >= 2:
            print(
                f"{'Force change, first → last':<30}: "
                f"{F[-1] - F[0]:.6e} Ry/Bohr"
            )

        print(
            f"{'Recent force behaviour':<30}: "
            f"{trend(F)}"
        )

        if len(F) >= 10:
            recent = F[-10:]

            print(
                f"{'Last-10 force min / max':<30}: "
                f"{min(recent):.6e} / "
                f"{max(recent):.6e} Ry/Bohr"
            )

    # --------------------------------------------------------
    # BFGS INTERNAL CONVERGENCE
    # --------------------------------------------------------

    print("\nBFGS / OPTIMIZER")

    status_line(
        "BFGS converged marker",
        "YES" if r["bfgs_conv"] else "NO"
    )

    status_line(
        "End of BFGS marker",
        "YES" if r["end_bfgs"] else "NO"
    )

    if r["bfgs_summary"]:
        scf_n, bfgs_n = r["bfgs_summary"][-1]

        print(
            f"{'QE BFGS summary':<30}: "
            f"{scf_n} SCF cycles, "
            f"{bfgs_n} BFGS steps"
        )

    if r["energy_errors"]:
        vals = r["energy_errors"]

        print(
            f"{'Last BFGS energy error':<30}: "
            f"{vals[-1]:.6e} Ry"
        )

        print(
            f"{'Best BFGS energy error':<30}: "
            f"{min(vals):.6e} Ry"
        )

    if r["gradient_errors"]:
        vals = r["gradient_errors"]

        print(
            f"{'Last BFGS gradient error':<30}: "
            f"{vals[-1]:.6e} Ry/Bohr"
        )

        print(
            f"{'Best BFGS gradient error':<30}: "
            f"{min(vals):.6e} Ry/Bohr"
        )

    # --------------------------------------------------------
    # ELECTRONIC PERFORMANCE DURING RELAXATION
    # --------------------------------------------------------

    print("\nELECTRONIC SCF INSIDE RELAXATION")

    edetail = relaxation_electronic_detail(text)
    electronic = edetail["current_status"]

    print(
        f"{'Completed converged SCFs':<30}: "
        f"{edetail['n_converged']}"
    )

    print(
        f"{'Latest completed SCF':<30}: "
        f"{edetail['last_completed_status']}"
    )

    if edetail["last_completed_iterations"] is not None:
        print(
            f"{'Latest completed iterations':<30}: "
            f"{edetail['last_completed_iterations']}"
        )

    print(
        f"{'Current electronic cycle':<30}: "
        f"{edetail['current_status']}"
    )

    if r["scf_cycles"]:
        scfs = r["scf_cycles"]

        print(
            f"{'Converged SCF cycle records':<30}: "
            f"{len(scfs)}"
        )

        print(
            f"{'Last SCF iterations':<30}: "
            f"{scfs[-1]}"
        )

        print(
            f"{'SCF iteration min / max':<30}: "
            f"{min(scfs)} / {max(scfs)}"
        )

        print(
            f"{'Mean SCF iterations':<30}: "
            f"{sum(scfs)/len(scfs):.2f}"
        )

        print(
            f"{'Total parsed SCF iterations':<30}: "
            f"{sum(scfs)}"
        )

    if core.get("acc"):
        a = core["acc"]

        print(
            f"{'Best electronic residual':<30}: "
            f"{min(a):.6e} Ry"
        )

        print(
            f"{'Latest electronic residual':<30}: "
            f"{a[-1]:.6e} Ry"
        )

    # --------------------------------------------------------
    # GEOMETRY / CELL EVIDENCE
    # --------------------------------------------------------

    print("\nGEOMETRY EVIDENCE")

    print(
        f"{'ATOMIC_POSITIONS blocks':<30}: "
        f"{r['atomic_position_blocks']}"
    )

    print(
        f"{'Final-coordinate begin markers':<30}: "
        f"{r['final_coord_begin']}"
    )

    print(
        f"{'Final-coordinate end markers':<30}: "
        f"{r['final_coord_end']}"
    )

    if typ == "VC-RELAX":
        print(
            f"{'CELL_PARAMETERS blocks':<30}: "
            f"{r['cell_blocks']}"
        )

        if r["stresses"]:
            print(
                f"{'Last total stress':<30}: "
                f"{r['stresses'][-1]:.6e} Ry/Bohr^3"
            )

    # --------------------------------------------------------
    # SANITY DIAGNOSTICS
    # --------------------------------------------------------

    print("\nSCIENTIFIC SANITY")

    if core.get("neg"):
        nrho = core["neg"]

        print(
            f"{'negative rho min / max':<30}: "
            f"{min(nrho):.3e} / {max(nrho):.3e}"
        )

    else:
        print(
            f"{'negative rho':<30}: not reported"
        )

    eigwarn = [
        int(x)
        for x in re.findall(
            r"(\d+)\s+eigenvalues? not converged",
            text,
            re.I
        )
    ]

    if eigwarn:
        print(
            f"{'Eigenvector warning lines':<30}: "
            f"{len(eigwarn)}"
        )

        print(
            f"{'Max unconverged eigenvals':<30}: "
            f"{max(eigwarn)}"
        )

    else:
        print(
            f"{'Eigenvector warnings':<30}: none parsed"
        )

    # --------------------------------------------------------
    # PERFORMANCE
    # --------------------------------------------------------

    if r["timing"]:
        print("\nPERFORMANCE")
        print(r["timing"].strip())

    # --------------------------------------------------------
    # TERMINATION
    # --------------------------------------------------------

    print("\nTERMINATION EVIDENCE")

    status_line(
        "JOB DONE marker",
        "YES" if r["job_done"] else "NO"
    )

    status_line(
        "Wall-time limit reached",
        "YES" if r["wall_stop"] else "NO"
    )

    status_line(
        "Calculation stopped marker",
        "YES" if r["stopped"] else "NO"
    )

    status_line(
        "Explicit SCF non-convergence",
        "YES" if r["explicit_notconv"] else "NO"
    )

    if r["qe_error"]:
        status_line(
            "QE runtime error",
            "YES",
            f"Error in routine {r['qe_error'].group(1)}"
        )
    else:
        status_line(
            "QE runtime error",
            "NO"
        )

    # --------------------------------------------------------
    # ASSESSMENT
    # --------------------------------------------------------

    ionic = ionic_verdict(r)

    print("\nASSESSMENT")

    execution = (
        "FAIL"
        if r["qe_error"]
        else (
            "PASS"
            if r["job_done"]
            else "UNKNOWN"
        )
    )

    status_line(
        "Execution integrity",
        execution
    )

    status_line(
        "Electronic SCF health",
        electronic
    )

    if ionic == "CONVERGED":
        status_line(
            "Ionic relaxation",
            "CONVERGED",
            "QE BFGS convergence established"
        )

    elif ionic == "STOPPED":
        status_line(
            "Ionic relaxation",
            "INCOMPLETE",
            "calculation stopped before BFGS convergence"
        )

    elif ionic == "ERROR":
        status_line(
            "Ionic relaxation",
            "FAIL",
            "QE runtime error"
        )

    elif ionic == "ACTIVE":
        status_line(
            "Ionic relaxation",
            "ACTIVE / UNTERMINATED",
            "relaxation progress exists, but no ionic convergence or termination marker yet"
        )

    else:
        status_line(
            "Ionic relaxation",
            "UNPROVEN",
            "no BFGS convergence marker found"
        )

    if r["forces"]:
        ftrend = trend(r["forces"])

        status_line(
            "Force trajectory",
            "INFO",
            ftrend
        )

    if core.get("neg"):
        mx = max(core["neg"])

        status_line(
            "Negative-rho diagnostic",
            "WARNING" if mx >= 0.1 else "INFO",
            f"max={mx:.3e}"
        )

    if eigwarn:
        status_line(
            "Diagonalization sanity",
            "WARNING",
            "unconverged eigenvalues reported"
        )

    else:
        status_line(
            "Diagonalization sanity",
            "PASS"
        )

    # --------------------------------------------------------
    # RESEARCH DECISION
    # --------------------------------------------------------

    print("\nRESEARCH DECISION")

    if r["qe_error"]:
        print(
            "FAIL — Quantum ESPRESSO encountered a runtime error. "
            "Do not use this structure as a final relaxed geometry."
        )

    elif ionic == "CONVERGED":
        print(
            "RELAXATION CONVERGED — QE explicitly reports successful "
            "BFGS geometry optimization."
        )

        if electronic != "CONVERGED":
            print(
                "CAUTION — inspect the electronic SCF status of the "
                "final ionic step before production post-processing."
            )

    elif ionic == "STOPPED":
        print(
            "RELAXATION INCOMPLETE — the run stopped before ionic "
            "BFGS convergence. Continue/restart from the latest valid "
            "geometry rather than treating it as final."
        )

    elif ionic == "ACTIVE":
        print(
            "RELAXATION ACTIVE / UNTERMINATED — valid relaxation progress "
            "is present, but QE has not reported ionic convergence or a "
            "termination condition. Do not treat the current geometry as "
            "the final relaxed structure."
        )

    else:
        print(
            "RELAXATION UNPROVEN — no explicit ionic-convergence "
            "evidence was found. Inspect/restart before using the "
            "geometry as a final relaxed structure."
        )

    print("\nNEXT USEFUL COMMANDS")

    print(
        "  qerelax -l        # list relaxation candidates"
    )

    print(
        "  qerelax --best    # strongest converged relaxation"
    )

    print(
        "  qehistory         # full SCF + relaxation history"
    )

    print(
        "  qescf --best      # strongest final SCF"
    )

    print(
        "  qecheck .         # directory/input/restart integrity"
    )

    print("=" * 94)


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
    """
    Research-grade Quantum ESPRESSO calculation-integrity checker.

    Usage:
        qecheck
        qecheck .
        qecheck INPUT.in
        qecheck DIRECTORY
        qecheck --all
        qecheck --help

    The checker validates input syntax/consistency, pseudopotentials,
    save/restart state, corresponding QE outputs and Slurm launch scripts.
    """

    args = sys.argv[1:]

    if path is None:
        path = "."

    show_all = False

    if args:
        if args[0] in ("-h", "--help"):
            print("""
Usage:
  qecheck
  qecheck .
  qecheck INPUT.in
  qecheck DIRECTORY
  qecheck --all
  qecheck --all DIRECTORY

Default:
  Inspect QE inputs in the current directory and perform research-oriented
  consistency checks.

Explicit input:
  Inspect exactly one Quantum ESPRESSO input file.

--all:
  Show additional detailed evidence for every inspected input.
""".strip())
            return

        if args[0] == "--all":
            show_all = True
            path = args[1] if len(args) > 1 else "."
        else:
            path = args[0]

    target = Path(path)

    if not target.exists():
        raise SystemExit(
            f"No readable file or directory found for: {path}"
        )

    if target.is_file():
        if target.suffix.lower() != ".in":
            raise SystemExit(
                "qecheck explicit-file mode currently expects a QE .in file."
            )
        inputs = [target]
        base_dir = target.parent

    else:
        base_dir = target
        inputs = sorted(
            f for f in target.glob("*.in")
            if f.is_file()
        )

    if not inputs:
        raise SystemExit(
            f"No Quantum ESPRESSO .in files found in: {base_dir.resolve()}"
        )

    # --------------------------------------------------------
    # HELPERS LOCAL TO QECHECK
    # --------------------------------------------------------

    def clean_value(v):
        if v is None:
            return None
        return str(v).strip().strip("'\"")

    def parse_bool(v):
        if v is None:
            return None
        s = clean_value(v).lower()
        if s in (".true.", "true", "t"):
            return True
        if s in (".false.", "false", "f"):
            return False
        return None

    def resolve_from_input(value, input_file):
        """
        Resolve QE paths relative to the directory containing the input.
        Absolute paths are preserved.
        """
        if not value:
            return None

        raw = Path(clean_value(value)).expanduser()

        if raw.is_absolute():
            return raw

        return (input_file.parent / raw).resolve()

    def parse_atomic_species(text):
        """
        Return [(symbol, mass, pseudo_filename), ...].
        """
        lines = text.splitlines()
        out = []

        for i, line in enumerate(lines):
            if re.match(
                r"^\s*ATOMIC_SPECIES\b",
                line,
                re.I,
            ):
                j = i + 1

                while j < len(lines):
                    s = lines[j].strip()

                    if not s:
                        j += 1
                        continue

                    if re.match(
                        r"^(ATOMIC_POSITIONS|K_POINTS|CELL_PARAMETERS|"
                        r"ATOMIC_FORCES|CONSTRAINTS|OCCUPATIONS|"
                        r"HUBBARD|SOLVENTS)\b",
                        s,
                        re.I,
                    ):
                        break

                    parts = s.split()

                    if len(parts) >= 3:
                        try:
                            float(parts[1].replace("D", "E").replace("d", "e"))
                            out.append(
                                (parts[0], parts[1], parts[2])
                            )
                        except Exception:
                            break
                    else:
                        break

                    j += 1

                break

        return out

    def count_atomic_positions(text):
        lines = text.splitlines()

        for i, line in enumerate(lines):
            if re.match(
                r"^\s*ATOMIC_POSITIONS\b",
                line,
                re.I,
            ):
                n = 0
                j = i + 1

                while j < len(lines):
                    s = lines[j].strip()

                    if not s:
                        j += 1
                        continue

                    if re.match(
                        r"^(K_POINTS|CELL_PARAMETERS|ATOMIC_SPECIES|"
                        r"ATOMIC_FORCES|CONSTRAINTS|OCCUPATIONS|"
                        r"HUBBARD|SOLVENTS|&)",
                        s,
                        re.I,
                    ):
                        break

                    parts = s.split()

                    if len(parts) < 4:
                        break

                    try:
                        float(parts[1].replace("D", "E").replace("d", "e"))
                        float(parts[2].replace("D", "E").replace("d", "e"))
                        float(parts[3].replace("D", "E").replace("d", "e"))
                        n += 1
                    except Exception:
                        break

                    j += 1

                return n

        return None

    def parse_kpoints(text):
        lines = text.splitlines()

        for i, line in enumerate(lines):
            m = re.match(
                r"^\s*K_POINTS(?:\s*\{?\s*([^}\s]+)\s*\}?)?",
                line,
                re.I,
            )

            if not m:
                continue

            mode = (
                m.group(1).lower()
                if m.group(1)
                else "tpiba"
            )

            info = {
                "mode": mode,
                "grid": None,
                "count": None,
            }

            if mode == "automatic":
                if i + 1 < len(lines):
                    vals = lines[i + 1].split()

                    if len(vals) >= 6:
                        info["grid"] = " ".join(vals[:6])

            elif mode in (
                "crystal",
                "tpiba",
                "crystal_b",
                "tpiba_b",
            ):
                if i + 1 < len(lines):
                    try:
                        info["count"] = int(
                            lines[i + 1].split()[0]
                        )
                    except Exception:
                        pass

            elif mode == "gamma":
                info["count"] = 1

            return info

        return None

    def detect_input_calc(text):
        s = parse_qe_input_settings(text)
        return s.get("calculation", "UNKNOWN").upper()

    def candidate_outputs_for_input(input_file, settings):
        """
        Prefer outputs sharing the input stem/prefix, but also inspect
        directory outputs and verify metadata before claiming a match.
        """
        outs = [
            f for f in input_file.parent.glob("*.out")
            if f.is_file()
        ]

        prefix = settings.get("prefix")
        stem = input_file.stem.lower()

        scored = []

        for f in outs:
            score = 0
            name = f.name.lower()

            if stem in name:
                score += 5

            if prefix and clean_value(prefix).lower() in name:
                score += 4

            try:
                t = read(f)
            except Exception:
                continue

            if "Program PWSCF" not in t:
                continue

            if prefix:
                # QE outputs do not always print prefix plainly, so
                # absence is not grounds for rejection.
                if clean_value(prefix) in t:
                    score += 2

            scored.append(
                (score, f.stat().st_mtime, f, t)
            )

        scored.sort(
            key=lambda x: (x[0], x[1]),
            reverse=True,
        )

        return scored

    def parse_slurm_script(script):
        txt = read(script)

        def sbatch(name):
            m = re.search(
                rf"(?m)^\s*#SBATCH\s+--{re.escape(name)}(?:=|\s+)(\S+)",
                txt,
            )
            return m.group(1) if m else None

        module_lines = [
            x.strip()
            for x in txt.splitlines()
            if re.match(
                r"^\s*module\s+(load|reset|swap)\b",
                x,
            )
        ]

        srun_lines = [
            x.strip()
            for x in txt.splitlines()
            if re.search(r"\bsrun\b.*\bpw\.x\b", x)
        ]

        return {
            "nodes": sbatch("nodes"),
            "ntasks_per_node": sbatch("ntasks-per-node"),
            "cpus_per_task": sbatch("cpus-per-task"),
            "time": sbatch("time"),
            "constraint": sbatch("constraint"),
            "modules": module_lines,
            "srun": srun_lines,
            "text": txt,
        }

    def find_related_scripts(input_file):
        scripts = sorted(
            f for f in input_file.parent.glob("*.sh")
            if f.is_file()
        )

        exact = []

        for f in scripts:
            try:
                t = read(f)
            except Exception:
                continue

            if input_file.name in t:
                exact.append(f)

        return exact, scripts

    overall_failures = 0
    overall_warnings = 0
    overall_passes = 0

    print("=" * 100)
    print(
        "QECHECK — RESEARCH-GRADE QUANTUM ESPRESSO "
        "INTEGRITY CHECKER v0.5"
    )
    print("=" * 100)

    print(f"Target                      : {target.resolve()}")
    print(f"Inputs detected             : {len(inputs)}")

    # --------------------------------------------------------
    # EACH INPUT
    # --------------------------------------------------------

    for idx, inp in enumerate(inputs, 1):
        txt = read(inp)
        settings = parse_qe_input_settings(txt)

        local_fail = []
        local_warn = []
        local_pass = []

        # Restart-state problems are treated separately from intrinsic
        # input/calculation-integrity problems.  Historical calculations
        # may have completed successfully even if their scratch/restart
        # files were later removed.
        restart_issues = []

        print("\n" + "=" * 100)
        print(f"[{idx}/{len(inputs)}] INPUT: {inp.name}")
        print("=" * 100)

        calc = clean_value(
            settings.get("calculation")
        )

        prefix = clean_value(
            settings.get("prefix")
        )

        outdir_raw = clean_value(
            settings.get("outdir")
        )

        pseudo_raw = clean_value(
            settings.get("pseudo_dir")
        )

        nat_raw = clean_value(
            settings.get("nat")
        )

        ntyp_raw = clean_value(
            settings.get("ntyp")
        )

        print("\nINPUT CLASSIFICATION / CORE SETTINGS")

        print(
            f"{'calculation':<30}: "
            f"{calc if calc else 'MISSING'}"
        )

        print(
            f"{'prefix':<30}: "
            f"{prefix if prefix else 'MISSING'}"
        )

        print(
            f"{'outdir':<30}: "
            f"{outdir_raw if outdir_raw else 'MISSING'}"
        )

        print(
            f"{'pseudo_dir':<30}: "
            f"{pseudo_raw if pseudo_raw else 'MISSING'}"
        )

        for key in [
            "nat",
            "ntyp",
            "nbnd",
            "ecutwfc",
            "ecutrho",
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
            "restart_mode",
            "startingwfc",
            "startingpot",
            "max_seconds",
        ]:
            if key in settings:
                print(
                    f"{key:<30}: {settings[key]}"
                )

        if not calc:
            local_fail.append(
                "calculation is missing from &CONTROL"
            )
        else:
            local_pass.append(
                f"calculation identified as {calc}"
            )

        if not prefix:
            local_warn.append(
                "prefix is not explicitly defined"
            )

        # ----------------------------------------------------
        # ATOM / SPECIES CONSISTENCY
        # ----------------------------------------------------

        species = parse_atomic_species(txt)
        npos = count_atomic_positions(txt)

        print("\nSTRUCTURE CONSISTENCY")

        print(
            f"{'ATOMIC_SPECIES entries':<30}: {len(species)}"
        )

        print(
            f"{'ATOMIC_POSITIONS count':<30}: "
            f"{npos if npos is not None else 'NOT FOUND'}"
        )

        if nat_raw is not None:
            try:
                nat = int(float(nat_raw))

                print(
                    f"{'nat declared':<30}: {nat}"
                )

                if npos is None:
                    local_warn.append(
                        "nat is declared but no ATOMIC_POSITIONS block was parsed"
                    )
                elif nat == npos:
                    local_pass.append(
                        f"nat matches ATOMIC_POSITIONS ({nat})"
                    )
                else:
                    local_fail.append(
                        f"nat={nat} but parsed {npos} atomic positions"
                    )

            except Exception:
                local_fail.append(
                    f"could not parse nat={nat_raw}"
                )

        else:
            local_fail.append(
                "nat is missing"
            )

        if ntyp_raw is not None:
            try:
                ntyp = int(float(ntyp_raw))

                print(
                    f"{'ntyp declared':<30}: {ntyp}"
                )

                if species:
                    if ntyp == len(species):
                        local_pass.append(
                            f"ntyp matches ATOMIC_SPECIES ({ntyp})"
                        )
                    else:
                        local_fail.append(
                            f"ntyp={ntyp} but found {len(species)} "
                            "ATOMIC_SPECIES entries"
                        )

            except Exception:
                local_fail.append(
                    f"could not parse ntyp={ntyp_raw}"
                )

        elif species:
            local_warn.append(
                "ntyp is not explicitly parsed from input settings"
            )

        # ----------------------------------------------------
        # PSEUDOPOTENTIALS
        # ----------------------------------------------------

        print("\nPSEUDOPOTENTIAL INTEGRITY")

        pseudo_dir = resolve_from_input(
            pseudo_raw,
            inp,
        )

        if pseudo_dir is None:
            print(
                f"{'pseudo_dir resolved':<30}: MISSING"
            )
            local_fail.append(
                "pseudo_dir is missing"
            )

        else:
            print(
                f"{'pseudo_dir resolved':<30}: {pseudo_dir}"
            )

            if pseudo_dir.is_dir():
                local_pass.append(
                    "pseudo_dir exists"
                )
            else:
                local_fail.append(
                    f"pseudo_dir does not exist: {pseudo_dir}"
                )

        if not species:
            print("No ATOMIC_SPECIES entries parsed.")
            local_fail.append(
                "ATOMIC_SPECIES could not be parsed"
            )

        else:
            for symbol, mass, pseudo_name in species:
                pp = (
                    pseudo_dir / pseudo_name
                    if pseudo_dir is not None
                    else None
                )

                exists = (
                    pp.is_file()
                    if pp is not None
                    else False
                )

                print(
                    f"  {symbol:<4} "
                    f"{pseudo_name:<42} "
                    f"{'FOUND' if exists else 'MISSING'}"
                )

                if exists:
                    local_pass.append(
                        f"{symbol} pseudopotential exists"
                    )
                else:
                    local_fail.append(
                        f"missing pseudopotential for {symbol}: "
                        f"{pseudo_name}"
                    )

        # ----------------------------------------------------
        # K-POINTS
        # ----------------------------------------------------

        print("\nK-POINT SAMPLING")

        kp = parse_kpoints(txt)

        if kp is None:
            print(
                f"{'K_POINTS':<30}: NOT FOUND"
            )
            local_fail.append(
                "K_POINTS card not found"
            )

        else:
            print(
                f"{'K_POINTS mode':<30}: {kp['mode']}"
            )

            if kp["grid"]:
                print(
                    f"{'Automatic grid + shift':<30}: {kp['grid']}"
                )

            if kp["count"] is not None:
                print(
                    f"{'Explicit k-point count':<30}: {kp['count']}"
                )

            local_pass.append(
                f"K_POINTS detected ({kp['mode']})"
            )

        # ----------------------------------------------------
        # NUMERICAL SANITY
        # ----------------------------------------------------

        print("\nNUMERICAL / ELECTRONIC SETTINGS")

        occ = clean_value(
            settings.get("occupations")
        )

        smear = clean_value(
            settings.get("smearing")
        )

        degauss = clean_value(
            settings.get("degauss")
        )

        if occ:
            print(
                f"{'occupations':<30}: {occ}"
            )

        if smear:
            print(
                f"{'smearing':<30}: {smear}"
            )

        if degauss:
            print(
                f"{'degauss':<30}: {degauss}"
            )

        if occ and occ.lower() == "smearing":
            if not smear:
                local_warn.append(
                    "occupations='smearing' but smearing type was not parsed"
                )

            if not degauss:
                local_warn.append(
                    "occupations='smearing' but degauss was not parsed"
                )

        for key in (
            "ecutwfc",
            "ecutrho",
            "conv_thr",
            "electron_maxstep",
            "diagonalization",
            "mixing_beta",
            "mixing_mode",
        ):
            if key not in settings:
                local_warn.append(
                    f"{key} not explicitly parsed from input"
                )

        try:
            ewfc = dfloat(settings["ecutwfc"])
            erho = dfloat(settings["ecutrho"])

            ratio = erho / ewfc

            print(
                f"{'ecutrho / ecutwfc':<30}: {ratio:.3f}"
            )

            if ratio <= 1:
                local_fail.append(
                    "ecutrho <= ecutwfc is suspicious/inconsistent"
                )
            else:
                local_pass.append(
                    "ecutrho > ecutwfc"
                )

        except Exception:
            pass

        # ----------------------------------------------------
        # RESTART / SAVE DIRECTORY
        # ----------------------------------------------------

        print("\nRESTART / SAVE-DIRECTORY INTEGRITY")

        restart_mode = clean_value(
            settings.get("restart_mode")
        )

        startingwfc = clean_value(
            settings.get("startingwfc")
        )

        startingpot = clean_value(
            settings.get("startingpot")
        )

        outdir = resolve_from_input(
            outdir_raw,
            inp,
        )

        print(
            f"{'restart_mode':<30}: "
            f"{restart_mode if restart_mode else 'default/from_scratch'}"
        )

        print(
            f"{'startingwfc':<30}: "
            f"{startingwfc if startingwfc else 'default'}"
        )

        print(
            f"{'startingpot':<30}: "
            f"{startingpot if startingpot else 'default'}"
        )

        if outdir is None:
            print(
                f"{'outdir resolved':<30}: MISSING"
            )
            local_fail.append(
                "outdir is missing"
            )
            save_dir = None

        else:
            print(
                f"{'outdir resolved':<30}: {outdir}"
            )

            if outdir.exists():
                local_pass.append(
                    "outdir exists"
                )
            else:
                local_warn.append(
                    f"outdir does not currently exist: {outdir}"
                )

            save_dir = (
                outdir / f"{prefix}.save"
                if prefix
                else None
            )

        if save_dir is not None:
            print(
                f"{'expected .save directory':<30}: {save_dir}"
            )

            if save_dir.is_dir():
                local_pass.append(
                    "expected prefix.save directory exists"
                )

                charge_files = list(
                    save_dir.glob("charge-density*")
                )

                xml_files = list(
                    save_dir.glob("data-file-schema*")
                )

                wfc_in_save = list(
                    save_dir.glob("*.wfc*")
                )

                print(
                    f"{'charge-density files':<30}: "
                    f"{len(charge_files)}"
                )

                print(
                    f"{'data-file-schema files':<30}: "
                    f"{len(xml_files)}"
                )

                print(
                    f"{'wavefunctions in .save':<30}: "
                    f"{len(wfc_in_save)}"
                )

                if charge_files:
                    local_pass.append(
                        "charge density exists"
                    )
                else:
                    local_warn.append(
                        "no charge-density file found in prefix.save"
                    )

                if xml_files:
                    local_pass.append(
                        "data-file-schema exists"
                    )
                else:
                    local_warn.append(
                        "no data-file-schema file found in prefix.save"
                    )

            else:
                needs_restart_files = (
                    (restart_mode and restart_mode.lower() == "restart")
                    or (
                        startingwfc
                        and startingwfc.lower() == "file"
                    )
                    or (
                        startingpot
                        and startingpot.lower() == "file"
                    )
                )

                if needs_restart_files:
                    restart_issues.append(
                        "restart/file initialization requested but expected "
                        "prefix.save directory is absent"
                    )
                else:
                    local_warn.append(
                        "expected prefix.save directory does not exist yet"
                    )

        if outdir is not None and outdir.is_dir() and prefix:
            restart_scf = list(
                outdir.glob(
                    f"{prefix}.restart_scf*"
                )
            )

            restart_k = list(
                outdir.glob(
                    f"{prefix}.restart_k*"
                )
            )

            wfcs = list(
                outdir.glob(
                    f"{prefix}.wfc*"
                )
            )

            print(
                f"{'restart_scf files':<30}: {len(restart_scf)}"
            )

            print(
                f"{'restart_k files':<30}: {len(restart_k)}"
            )

            print(
                f"{'top-level wfc files':<30}: {len(wfcs)}"
            )

        if restart_mode and restart_mode.lower() == "restart":
            if save_dir is not None and save_dir.is_dir():
                local_pass.append(
                    "restart_mode='restart' has existing save directory"
                )
            else:
                restart_issues.append(
                    "restart_mode='restart' without usable prefix.save directory"
                )

        if startingpot and startingpot.lower() == "file":
            if save_dir is not None and save_dir.is_dir():
                local_pass.append(
                    "startingpot='file' has save-state evidence"
                )
            else:
                restart_issues.append(
                    "startingpot='file' but no prefix.save directory exists"
                )

        # startingwfc='file' can use WFCs in outdir rather than inside .save
        if startingwfc and startingwfc.lower() == "file":
            any_wfc = False

            if outdir is not None and outdir.is_dir() and prefix:
                any_wfc = bool(
                    list(outdir.glob(f"{prefix}.wfc*"))
                )

            if save_dir is not None and save_dir.is_dir():
                any_wfc = (
                    any_wfc
                    or bool(list(save_dir.glob("*.wfc*")))
                )

            if any_wfc:
                local_pass.append(
                    "startingwfc='file' has wavefunction evidence"
                )
            else:
                restart_issues.append(
                    "startingwfc='file' but no matching wavefunction files found"
                )

        # ----------------------------------------------------
        # OUTPUT CROSS-CHECK
        # ----------------------------------------------------

        print("\nINPUT ↔ OUTPUT CROSS-CHECK")

        historical_output_found = False
        historical_output_success = False
        historical_output_status = None
        historical_output_file = None

        output_candidates = candidate_outputs_for_input(
            inp,
            settings,
        )

        if not output_candidates:
            print(
                f"{'QE output candidate':<30}: NONE"
            )
            local_warn.append(
                "no corresponding PWSCF output candidate found"
            )

        else:
            best_score, _, out_file, out_text = output_candidates[0]

            print(
                f"{'Best output candidate':<30}: {out_file.name}"
            )

            print(
                f"{'Output match score':<30}: {best_score}"
            )

            out_core = output_core(out_text)

            mnat = re.search(
                r"number of atoms/cell\s*=\s*(\d+)",
                out_text,
                re.I,
            )

            if mnat and nat_raw is not None:
                try:
                    out_nat = int(mnat.group(1))
                    in_nat = int(float(nat_raw))

                    print(
                        f"{'Output atom count':<30}: {out_nat}"
                    )

                    if out_nat == in_nat:
                        local_pass.append(
                            "input/output atom counts agree"
                        )
                    else:
                        local_fail.append(
                            f"input nat={in_nat}, output atoms={out_nat}"
                        )
                except Exception:
                    pass

            mks = re.search(
                r"number of Kohn-Sham states\s*=\s*(\d+)",
                out_text,
                re.I,
            )

            if mks and settings.get("nbnd"):
                try:
                    out_nbnd = int(mks.group(1))
                    in_nbnd = int(
                        float(settings["nbnd"])
                    )

                    print(
                        f"{'Output KS states':<30}: {out_nbnd}"
                    )

                    if out_nbnd == in_nbnd:
                        local_pass.append(
                            "input nbnd matches output KS states"
                        )
                    else:
                        local_warn.append(
                            f"input nbnd={in_nbnd}, output states={out_nbnd}"
                        )
                except Exception:
                    pass

            flags = termination_flags(
                out_text
            )

            typ = classify_output(
                out_file,
                out_text,
            )

            print(
                f"{'Output classification':<30}: {typ}"
            )

            historical_output_found = True
            historical_output_file = out_file

            historical_output_status = summary_status(
                out_text,
                typ
            )

            print(
                f"{'Output status':<30}: "
                f"{historical_output_status}"
            )

            historical_output_success = (
                historical_output_status in (
                    "CONVERGED",
                    "RELAX CONV",
                )
            )

            if flags["error"]:
                local_fail.append(
                    f"candidate output contains QE runtime error: {out_file.name}"
                )

            if show_all:
                print(
                    f"{'Residual samples':<30}: "
                    f"{len(out_core.get('acc', []))}"
                )

        # ----------------------------------------------------
        # HISTORICAL INTEGRITY VS CURRENT RESTARTABILITY
        # ----------------------------------------------------

        print("\nHISTORICAL INTEGRITY / CURRENT RESTARTABILITY")

        restart_requested = bool(
            (restart_mode and restart_mode.lower() == "restart")
            or (startingwfc and startingwfc.lower() == "file")
            or (startingpot and startingpot.lower() == "file")
        )

        restartable_now = (
            restart_requested
            and not restart_issues
        )

        if historical_output_success:
            print(
                f"{'Historical run integrity':<30}: PASS"
            )
            print(
                f"{'Evidence':<30}: "
                f"{historical_output_file.name} "
                f"[{historical_output_status}]"
            )

            local_pass.append(
                "historical output demonstrates successful calculation"
            )

            if restart_requested:
                if restartable_now:
                    print(
                        f"{'Restartable right now':<30}: YES"
                    )
                    local_pass.append(
                        "requested restart state is currently available"
                    )
                else:
                    print(
                        f"{'Restartable right now':<30}: NO"
                    )

                    for issue in restart_issues:
                        local_warn.append(
                            "current restartability: " + issue
                        )

                    print(
                        f"{'Interpretation':<30}: "
                        "historical run succeeded, but its required "
                        "restart state is no longer fully present"
                    )
            else:
                print(
                    f"{'Restartability required':<30}: NO"
                )

        else:
            if historical_output_found:
                print(
                    f"{'Historical run integrity':<30}: "
                    f"NOT ESTABLISHED"
                )
                print(
                    f"{'Evidence':<30}: "
                    f"{historical_output_file.name} "
                    f"[{historical_output_status}]"
                )
            else:
                print(
                    f"{'Historical run integrity':<30}: UNKNOWN"
                )

            if restart_requested:
                if restartable_now:
                    print(
                        f"{'Restartable right now':<30}: YES"
                    )
                    local_pass.append(
                        "requested restart state is currently available"
                    )
                else:
                    print(
                        f"{'Restartable right now':<30}: NO"
                    )

                    # For an unproven/current restart calculation, missing
                    # restart files remain real integrity failures.
                    for issue in restart_issues:
                        local_fail.append(issue)
            else:
                print(
                    f"{'Restartability required':<30}: NO"
                )

        # ----------------------------------------------------
        # SLURM / EXECUTION SCRIPT
        # ----------------------------------------------------

        print("\nSLURM / LAUNCH SCRIPT")

        related_scripts, all_scripts = find_related_scripts(
            inp
        )

        scripts_to_show = (
            related_scripts
            if related_scripts
            else []
        )

        if not scripts_to_show:
            print(
                f"{'Script referencing input':<30}: NONE"
            )

            if all_scripts:
                local_warn.append(
                    "no .sh script explicitly references this input file"
                )

                if show_all:
                    print(
                        "Available shell scripts:"
                    )
                    for sh in all_scripts[:15]:
                        print(
                            f"  {sh.name}"
                        )
            else:
                local_warn.append(
                    "no Slurm/shell scripts found in calculation directory"
                )

        else:
            for sh in scripts_to_show:
                info = parse_slurm_script(
                    sh
                )

                print(
                    f"{'Script':<30}: {sh.name}"
                )

                print(
                    f"{'  nodes':<30}: "
                    f"{info['nodes'] or 'NOT FOUND'}"
                )

                print(
                    f"{'  ntasks-per-node':<30}: "
                    f"{info['ntasks_per_node'] or 'NOT FOUND'}"
                )

                print(
                    f"{'  cpus-per-task':<30}: "
                    f"{info['cpus_per_task'] or 'NOT FOUND'}"
                )

                print(
                    f"{'  time':<30}: "
                    f"{info['time'] or 'NOT FOUND'}"
                )

                if info["constraint"]:
                    print(
                        f"{'  constraint':<30}: "
                        f"{info['constraint']}"
                    )

                if info["modules"]:
                    for line in info["modules"]:
                        print(
                            f"{'  module':<30}: {line}"
                        )

                else:
                    local_warn.append(
                        f"{sh.name}: no module command found"
                    )

                if info["srun"]:
                    for line in info["srun"]:
                        print(
                            f"{'  launch':<30}: {line}"
                        )

                    local_pass.append(
                        f"{sh.name} launches pw.x"
                    )

                else:
                    local_fail.append(
                        f"{sh.name}: no srun ... pw.x launch found"
                    )

                has_reset = any(
                    re.search(
                        r"\bmodule\s+reset\b",
                        x,
                    )
                    for x in info["modules"]
                )

                if not has_reset:
                    local_warn.append(
                        f"{sh.name}: no 'module reset' before QE environment setup"
                    )

                qe_modules = [
                    x for x in info["modules"]
                    if "espresso" in x.lower()
                ]

                if qe_modules:
                    local_pass.append(
                        f"{sh.name} loads Quantum ESPRESSO module"
                    )
                else:
                    local_warn.append(
                        f"{sh.name}: no espresso module load detected"
                    )

                # Cross-check MPI ranks if values are plain integers.
                try:
                    nodes = int(info["nodes"])
                    ntpn = int(info["ntasks_per_node"])
                    expected_ranks = nodes * ntpn

                    if info["srun"]:
                        launch = info["srun"][0]

                        m = re.search(
                            r"(?:^|\s)-n\s+(\d+)",
                            launch,
                        )

                        if not m:
                            m = re.search(
                                r"--ntasks(?:=|\s+)(\d+)",
                                launch,
                            )

                        if m:
                            launched = int(
                                m.group(1)
                            )

                            print(
                                f"{'  expected MPI ranks':<30}: "
                                f"{expected_ranks}"
                            )

                            print(
                                f"{'  launched MPI ranks':<30}: "
                                f"{launched}"
                            )

                            if launched == expected_ranks:
                                local_pass.append(
                                    f"{sh.name}: srun ranks match "
                                    "nodes × ntasks-per-node"
                                )
                            else:
                                local_warn.append(
                                    f"{sh.name}: srun -n {launched} but "
                                    f"nodes×ntasks-per-node={expected_ranks}"
                                )

                except Exception:
                    pass

        # ----------------------------------------------------
        # FINAL PER-INPUT VERDICT
        # ----------------------------------------------------

        print("\nCHECK SUMMARY")

        print(
            f"{'PASS checks':<30}: {len(local_pass)}"
        )

        print(
            f"{'Warnings':<30}: {len(local_warn)}"
        )

        print(
            f"{'Failures':<30}: {len(local_fail)}"
        )

        if local_fail:
            verdict = "FAIL"

        elif local_warn:
            verdict = "WARNING"

        else:
            verdict = "PASS"

        print(
            f"{'INTEGRITY VERDICT':<30}: {verdict}"
        )

        if local_fail:
            print("\nFAILURES")
            for item in local_fail:
                print(
                    f"  [FAIL] {item}"
                )

        if local_warn:
            print("\nWARNINGS")
            for item in local_warn:
                print(
                    f"  [WARN] {item}"
                )

        if show_all and local_pass:
            print("\nPASSED CHECKS")
            for item in local_pass:
                print(
                    f"  [PASS] {item}"
                )

        overall_failures += len(
            local_fail
        )

        overall_warnings += len(
            local_warn
        )

        overall_passes += len(
            local_pass
        )

    # --------------------------------------------------------
    # DIRECTORY-WIDE SUMMARY
    # --------------------------------------------------------

    print("\n" + "=" * 100)
    print("QECHECK — DIRECTORY / REQUEST SUMMARY")
    print("=" * 100)

    print(
        f"{'Inputs inspected':<30}: {len(inputs)}"
    )

    print(
        f"{'Total passed checks':<30}: {overall_passes}"
    )

    print(
        f"{'Total warnings':<30}: {overall_warnings}"
    )

    print(
        f"{'Total failures':<30}: {overall_failures}"
    )

    if overall_failures:
        final = "FAIL"

    elif overall_warnings:
        final = "WARNING"

    else:
        final = "PASS"

    print(
        f"{'OVERALL INTEGRITY':<30}: {final}"
    )

    print("\nINTERPRETATION")

    if final == "PASS":
        print(
            "PASS — no input/path/restart/launch inconsistencies were "
            "detected by the implemented checks."
        )

    elif final == "WARNING":
        print(
            "WARNING — no hard inconsistency was detected, but one or "
            "more settings or pieces of evidence should be reviewed."
        )

    else:
        print(
            "FAIL — at least one concrete calculation-integrity problem "
            "was detected. Correct it before relying on or restarting "
            "the affected calculation."
        )

    print("\nUSEFUL COMMANDS")
    print(
        "  qecheck .                # inspect inputs in current directory"
    )
    print(
        "  qecheck INPUT.in         # inspect one exact input"
    )
    print(
        "  qecheck --all            # include detailed passed evidence"
    )
    print(
        "  qescf -l                 # SCF candidates/results"
    )
    print(
        "  qerelax -l               # relaxation candidates/results"
    )
    print(
        "  qehistory                # chronological QE calculation history"
    )

    print("=" * 100)


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
