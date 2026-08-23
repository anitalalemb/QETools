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
      3. Conservative relaxation filename fallback
      4. Standard PWSCF SCF evidence
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
    # Conservative relaxation filename fallback
    # --------------------------------------------------------
    # Some truncated/early relaxation outputs may contain normal PWSCF
    # electronic iterations but never reach the BFGS markers. In those
    # cases use only explicit calculation-style relaxation tokens.
    #
    # Do NOT match generic words such as "relaxed", "pre_relaxed",
    # "post_relaxed", etc.

    if (
        re.search(r"(^|[_\-.])relax([_\-.]|$)", name)
        or re.search(r"(^|[_\-.])vc-relax([_\-.]|$)", name)
        or re.search(r"(^|[_\-.])rx([_\-.]|$)", name)
    ):
        if "vc-relax" in name:
            return "VC-RELAX"

        return "RELAX"

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
    """
    Research-grade chronological Quantum ESPRESSO calculation history.

    Usage:
        qehistory
        qehistory DIRECTORY
        qehistory FILE.out
        qehistory --help

    The history view distinguishes:
      - SCF versus structural-relaxation calculations
      - completed versus interrupted electronic SCF cycles
      - ionic convergence versus stopped/unterminated relaxations
      - newest attempt versus strongest converged result
    """

    args = sys.argv[1:]

    if path is None:
        path = "."

    if args:
        if args[0] in ("-h", "--help"):
            print("""
Usage:
  qehistory
  qehistory DIRECTORY
  qehistory FILE.out
  qehistory --help

Purpose:
  Show Quantum ESPRESSO calculation history chronologically.

For SCF calculations:
  reports convergence status and residual quality.

For RELAX / VC-RELAX calculations:
  distinguishes ionic convergence from electronic SCF state and separates
  completed electronic cycles from a possibly unfinished final cycle.

The summary identifies:
  latest SCF attempt
  best converged SCF
  latest relaxation
  latest converged relaxation
""".strip())
            return

        path = args[0]

    p = Path(path)

    files = discover_outputs(p)

    print("=" * 154)
    print(
        "QEHISTORY — RESEARCH-GRADE QUANTUM ESPRESSO "
        "CALCULATION HISTORY v0.7"
    )
    print("=" * 154)

    if not files:
        print(
            f"No QE output files found in: {p.resolve()}"
        )
        return

    # Chronological ordering is most useful for restart chains.
    files = sorted(
        files,
        key=lambda x: x.stat().st_mtime,
    )

    def history_relax_status(text0, calc_type):
        """
        More explicit relaxation status for history display.

        This intentionally distinguishes an unterminated file from a
        calculation that QE explicitly stopped.
        """

        flags = termination_flags(text0)

        if flags["error"]:
            return "ERROR"

        ion = ionic_status(
            text0,
            calc_type,
        )

        if ion == "CONVERGED":
            return "RELAX CONV"

        if ion == "STOPPED / INCOMPLETE":
            return "RELAX STOP"

        # No convergence and no explicit stop/error.
        core0 = output_core(text0)

        if (
            core0.get("energies")
            or core0.get("forces")
            or core0.get("iters")
        ):
            if not flags["jobdone"]:
                return "UNTERMINATED"

        if flags["jobdone"]:
            return "RELAX INCOMP"

        return "UNKNOWN"

    def electronic_history_status(text0, calc_type):
        """
        For relaxation histories, show the state of the latest completed
        electronic SCF separately from a possibly unfinished current cycle.
        """

        if calc_type in ("RELAX", "VC-RELAX"):
            detail = relaxation_electronic_detail(
                text0
            )

            return (
                detail["last_completed_status"],
                detail["current_status"],
                detail["n_converged"],
            )

        status = latest_electronic_status(
            text0
        )

        nconv = len(
            re.findall(
                r"convergence has been achieved in\s+\d+\s+iterations",
                text0,
                re.I,
            )
        )

        return (
            status,
            status,
            nconv,
        )

    print(
        f"{'DATE':<18}"
        f"{'TYPE':<10}"
        f"{'BEST SCF':<13}"
        f"{'LAST SCF':<13}"
        f"{'LAST DONE':<13}"
        f"{'CURRENT':<13}"
        f"{'OVERALL':<15}"
        f"FILE"
    )

    print("-" * 154)

    rows = []

    for f in files:
        try:
            text0 = read(f)
        except Exception:
            continue

        typ = classify_output(
            f,
            text0,
        )

        core = output_core(
            text0
        )

        best = (
            min(core["acc"])
            if core["acc"]
            else None
        )

        last = (
            core["acc"][-1]
            if core["acc"]
            else None
        )

        last_done, current, nconv = electronic_history_status(
            text0,
            typ,
        )

        if typ in ("RELAX", "VC-RELAX"):
            overall = history_relax_status(
                text0,
                typ,
            )
        else:
            overall = summary_status(
                text0,
                typ,
            )

        rows.append(
            {
                "file": f,
                "text": text0,
                "type": typ,
                "best": best,
                "last": last,
                "last_done": last_done,
                "current": current,
                "nconv": nconv,
                "overall": overall,
            }
        )

        print(
            f"{fmt_time(f):<18}"
            f"{typ:<10}"
            f"{fmt_float(best):<13}"
            f"{fmt_float(last):<13}"
            f"{last_done:<13}"
            f"{current:<13}"
            f"{overall:<15}"
            f"{f.name}"
        )

    # ========================================================
    # HISTORY SUMMARY
    # ========================================================

    print("\n" + "=" * 154)
    print("HISTORY SUMMARY")
    print("=" * 154)

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

    converged_relax = [
        r
        for r in relax_rows
        if r["overall"] == "RELAX CONV"
    ]

    if scf_rows:
        latest_scf = max(
            scf_rows,
            key=lambda r:
                r["file"].stat().st_mtime,
        )

        print(
            f"{'Latest SCF attempt':<32}: "
            f"{latest_scf['file'].name}"
        )

        print(
            f"{'Latest SCF status':<32}: "
            f"{latest_scf['overall']}"
        )

        print(
            f"{'Latest SCF residual':<32}: "
            f"{fmt_float(latest_scf['last'])} Ry"
        )

    else:
        latest_scf = None

        print(
            f"{'Latest SCF attempt':<32}: none detected"
        )

    if converged_scfs:
        best_scf = min(
            converged_scfs,
            key=lambda r: (
                r["best"]
                if r["best"] is not None
                else float("inf"),
                -r["file"].stat().st_mtime,
            ),
        )

        print(
            f"{'Best converged SCF':<32}: "
            f"{best_scf['file'].name}"
        )

        print(
            f"{'Best converged residual':<32}: "
            f"{fmt_float(best_scf['best'])} Ry"
        )

    else:
        best_scf = None

        print(
            f"{'Best converged SCF':<32}: none detected"
        )

    if relax_rows:
        latest_relax = max(
            relax_rows,
            key=lambda r:
                r["file"].stat().st_mtime,
        )

        print(
            f"{'Latest relaxation':<32}: "
            f"{latest_relax['file'].name}"
        )

        print(
            f"{'Latest relaxation status':<32}: "
            f"{latest_relax['overall']}"
        )

        print(
            f"{'Latest completed relax SCF':<32}: "
            f"{latest_relax['last_done']}"
        )

        print(
            f"{'Current relax SCF cycle':<32}: "
            f"{latest_relax['current']}"
        )

        print(
            f"{'Completed SCFs in latest relax':<32}: "
            f"{latest_relax['nconv']}"
        )

    else:
        latest_relax = None

        print(
            f"{'Latest relaxation':<32}: none detected"
        )

    if converged_relax:
        latest_conv_relax = max(
            converged_relax,
            key=lambda r:
                r["file"].stat().st_mtime,
        )

        print(
            f"{'Latest converged relaxation':<32}: "
            f"{latest_conv_relax['file'].name}"
        )

    else:
        latest_conv_relax = None

        print(
            f"{'Latest converged relaxation':<32}: none detected"
        )

    # ========================================================
    # RESEARCH STATE
    # ========================================================

    print("\nRESEARCH STATE")

    if latest_scf is not None:
        if latest_scf["overall"] == "CONVERGED":
            print(
                "Latest standalone SCF result  : usable as electronically "
                "converged evidence, subject to input/physical validation."
            )
        else:
            print(
                "Latest standalone SCF result  : not established as a "
                "converged final SCF."
            )

    if latest_relax is not None:
        if latest_relax["overall"] == "RELAX CONV":
            print(
                "Latest structural relaxation  : ionically converged."
            )

        elif latest_relax["overall"] == "RELAX STOP":
            print(
                "Latest structural relaxation  : stopped before ionic "
                "convergence; do not treat the final geometry as relaxed."
            )

        elif latest_relax["overall"] == "UNTERMINATED":
            print(
                "Latest structural relaxation  : output is unterminated; "
                "from the output alone it is not proven finished."
            )

        else:
            print(
                "Latest structural relaxation  : final ionic state is "
                "not proven."
            )

    print("\nRECOMMENDED COMMANDS")

    print(
        "  qescf -l             # standalone SCF attempts"
    )

    print(
        "  qescf --best         # strongest converged standalone SCF"
    )

    print(
        "  qerelax -l           # relaxation/restart attempts"
    )

    print(
        "  qerelax --best       # strongest converged relaxation"
    )

    if best_scf is not None:
        print(
            f"  qescf {best_scf['file'].name}"
        )

    if latest_conv_relax is not None:
        print(
            f"  qerelax {latest_conv_relax['file'].name}"
        )

    print(
        "  qecheck .            # input/restart/script integrity"
    )

    print("=" * 154)

# ============================================================
# QECOMPARE
# ============================================================


def compare(a=None, b=None):
    """
    Research-grade side-by-side Quantum ESPRESSO comparison.

    Usage:
        qecompare A.out B.out
        qecompare --help

    The comparison distinguishes:
      - calculation/input differences,
      - numerical/electronic convergence,
      - relaxation behaviour,
      - scientific diagnostics,
      - performance evidence.

    It reports associations only. It does not infer that a changed
    parameter caused an observed change in convergence or physics.
    """

    args = sys.argv[1:]

    if a is None or b is None:
        if args and args[0] in ("-h", "--help"):
            print("""
Usage:
  qecompare A.out B.out
  qecompare --help

Purpose:
  Compare two Quantum ESPRESSO calculations side by side.

Included:
  calculation type and status
  system size and numerical settings
  matching input-file settings when available
  SCF residual history
  total energies
  forces / relaxation convergence
  negative-rho diagnostics
  unconverged eigenvalue warnings
  termination evidence
  QE timing information

Important:
  qecompare reports differences and numerical outcomes.
  It does NOT claim that a changed setting caused a change in convergence
  or physical behaviour.
""".strip())
            return

        if len(args) != 2:
            raise SystemExit(
                "Usage: qecompare A.out B.out"
            )

        a, b = args

    pa = Path(a)
    pb = Path(b)

    if not pa.is_file():
        raise SystemExit(
            f"File A not found: {a}"
        )

    if not pb.is_file():
        raise SystemExit(
            f"File B not found: {b}"
        )

    ta = read(pa)
    tb = read(pb)

    if not is_qe_output(pa):
        raise SystemExit(
            f"File A does not look like a Quantum ESPRESSO output: {pa}"
        )

    if not is_qe_output(pb):
        raise SystemExit(
            f"File B does not look like a Quantum ESPRESSO output: {pb}"
        )

    ca = output_core(ta)
    cb = output_core(tb)

    type_a = classify_output(pa, ta)
    type_b = classify_output(pb, tb)

    status_a = summary_status(ta, type_a)
    status_b = summary_status(tb, type_b)

    # --------------------------------------------------------
    # LOCAL HELPERS
    # --------------------------------------------------------

    def first_match(text0, pattern, cast=None):
        m = re.search(pattern, text0, re.I | re.S)

        if not m:
            return None

        value = m.group(1)

        if cast is None:
            return value

        try:
            return cast(value)
        except Exception:
            return value

    def ffloat(x):
        if x is None:
            return "—"

        try:
            return f"{float(x):.6e}"
        except Exception:
            return str(x)

    def fplain(x):
        return "—" if x is None else str(x)

    def same_value(x, y):
        if x is None and y is None:
            return True

        if x is None or y is None:
            return False

        try:
            return math.isclose(
                float(x),
                float(y),
                rel_tol=1e-10,
                abs_tol=1e-12,
            )
        except Exception:
            return str(x).strip().lower() == str(y).strip().lower()

    def row(label, va, vb, width=31):
        atext = fplain(va)
        btext = fplain(vb)

        # Keep ordinary values in a compact two-column table.
        # For long strings/paths/timing lines, print A and B separately
        # so values can never visually run together.
        if len(atext) > 34 or len(btext) > 34:
            print(f"{label:<{width}}")
            print(f"  A: {atext}")
            print(f"  B: {btext}")
        else:
            print(
                f"{label:<{width}}"
                f"{atext:<36}"
                f"{btext}"
            )

    def scirow(label, va, vb, unit=""):
        aa = ffloat(va)
        bb = ffloat(vb)

        if unit:
            if va is not None:
                aa += f" {unit}"
            if vb is not None:
                bb += f" {unit}"

        row(label, aa, bb)

    def output_metadata(text0):
        meta = {}

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

        for key, pat in patterns.items():
            m = re.search(pat, text0, re.I)

            if m:
                meta[key] = m.group(1)

        sm = re.search(
            r"(Marzari-Vanderbilt|Methfessel-Paxton|Gaussian|cold)"
            r" smearing.*?width \(Ry\)\s*=\s*("
            + FLOAT
            + r")",
            text0,
            re.I | re.S,
        )

        if sm:
            meta["smearing"] = sm.group(1)
            meta["degauss"] = sm.group(2)

        return meta

    def matching_input(output_path):
        """
        Prefer an exact same-stem .in file.

        Examples:
          scf_test.out -> scf_test.in
          relax_restart4.out -> relax_restart4.in

        If no exact sidecar exists, do not guess aggressively.
        """
        exact = output_path.with_suffix(".in")

        if exact.is_file():
            return exact

        return None

    def input_settings(output_path):
        inp = matching_input(output_path)

        if inp is None:
            return None, {}

        try:
            return inp, parse_qe_input_settings(
                read(inp)
            )
        except Exception:
            return inp, {}

    def electronic_metrics(text0, core):
        conv = re.findall(
            r"convergence has been achieved in\s+(\d+)\s+iterations",
            text0,
            re.I,
        )

        eig = [
            int(x)
            for x in re.findall(
                r"(\d+)\s+eigenvalues? not converged",
                text0,
                re.I,
            )
        ]

        timing = last_or(
            re.findall(
                r"(?m)^.*PWSCF.*WALL.*$",
                text0
            )
        )

        return {
            "best_residual":
                min(core["acc"]) if core["acc"] else None,

            "last_residual":
                core["acc"][-1] if core["acc"] else None,

            "residual_samples":
                len(core["acc"]),

            "residual_trend":
                trend(core["acc"]) if core["acc"] else "—",

            "highest_iteration":
                max(core["iters"]) if core["iters"] else None,

            "converged_cycles":
                len(conv),

            "last_converged_iterations":
                int(conv[-1]) if conv else None,

            "last_energy":
                core["energies"][-1]
                if core["energies"]
                else None,

            "energy_samples":
                len(core["energies"]),

            "last_force":
                core["forces"][-1]
                if core.get("forces")
                else None,

            "best_force":
                min(core["forces"])
                if core.get("forces")
                else None,

            "force_samples":
                len(core.get("forces", [])),

            "negative_rho_max":
                max(core["neg"])
                if core["neg"]
                else None,

            "eigen_warning_lines":
                len(eig),

            "max_unconverged_eigenvalues":
                max(eig) if eig else 0,

            "charge_wrong":
                bool(
                    re.search(
                        r"charge is wrong",
                        text0,
                        re.I,
                    )
                ),

            "job_done":
                "JOB DONE." in text0,

            "wall_stop":
                bool(
                    re.search(
                        r"Maximum CPU time exceeded",
                        text0,
                        re.I,
                    )
                ),

            "calculation_stopped":
                bool(
                    re.search(
                        r"Calculation stopped",
                        text0,
                        re.I,
                    )
                ),

            "qe_error":
                bool(
                    re.search(
                        r"Error in routine",
                        text0,
                        re.I,
                    )
                ),

            "bfgs_converged":
                bool(
                    re.search(
                        r"\bbfgs converged\b|"
                        r"End of BFGS Geometry Optimization",
                        text0,
                        re.I,
                    )
                ),

            "timing":
                timing.strip() if timing else None,
        }

    ma = output_metadata(ta)
    mb = output_metadata(tb)

    ia_file, ia = input_settings(pa)
    ib_file, ib = input_settings(pb)

    ea = electronic_metrics(ta, ca)
    eb = electronic_metrics(tb, cb)

    # --------------------------------------------------------
    # HEADER
    # --------------------------------------------------------

    print("=" * 112)
    print(
        "QECOMPARE — RESEARCH-GRADE QUANTUM ESPRESSO "
        "COMPARISON v0.6"
    )
    print("=" * 112)

    print(f"A : {pa.resolve()}")
    print(f"B : {pb.resolve()}")

    print(
        f"Input A : "
        f"{ia_file.name if ia_file else 'not found automatically'}"
    )

    print(
        f"Input B : "
        f"{ib_file.name if ib_file else 'not found automatically'}"
    )

    print(
        f"\n{'QUANTITY':<31}"
        f"{'A':<28}"
        f"B"
    )

    print("-" * 112)

    # --------------------------------------------------------
    # CALCULATION IDENTITY
    # --------------------------------------------------------

    print("\nCALCULATION IDENTITY / STATUS")

    row("Calculation type", type_a, type_b)
    row("Overall status", status_a, status_b)
    row("File modified", fmt_time(pa), fmt_time(pb))
    row("QE version", ma.get("QE version"), mb.get("QE version"))

    # --------------------------------------------------------
    # SYSTEM / NUMERICS
    # --------------------------------------------------------

    print("\nSYSTEM / NUMERICS")

    for key in (
        "Atoms",
        "Atomic species",
        "Electrons",
        "KS states",
        "k-points",
        "ecutwfc",
        "ecutrho",
        "smearing",
        "degauss",
    ):
        va = ma.get(key)
        vb = mb.get(key)

        if key in ("ecutwfc", "ecutrho", "degauss"):
            scirow(
                key,
                dfloat(va) if va is not None else None,
                dfloat(vb) if vb is not None else None,
                "Ry",
            )
        else:
            row(key, va, vb)

    # --------------------------------------------------------
    # INPUT SETTINGS
    # --------------------------------------------------------

    print("\nINPUT SETTINGS")

    if not ia and not ib:
        print(
            "No exact same-stem .in files were found for either output."
        )

    input_keys = (
        "calculation",
        "prefix",
        "outdir",
        "pseudo_dir",
        "nat",
        "ntyp",
        "nbnd",
        "occupations",
        "smearing",
        "degauss",
        "ecutwfc",
        "ecutrho",
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
    )

    changed_settings = []

    operational_keys = {
        "outdir",
        "pseudo_dir",
        "prefix",
        "max_seconds",
    }

    scientific_changed_settings = []

    for key in input_keys:
        va = ia.get(key)
        vb = ib.get(key)

        if va is None and vb is None:
            continue

        marker = ""

        if not same_value(va, vb):
            marker = "  <-- DIFFERENT"
            changed_settings.append(
                (key, va, vb)
            )

            if key not in operational_keys:
                scientific_changed_settings.append(
                    (key, va, vb)
                )

        atext = fplain(va)
        btext = fplain(vb)

        if len(atext) > 34 or len(btext) > 34:
            print(f"{key:<31}{marker}")
            print(f"  A: {atext}")
            print(f"  B: {btext}")
        else:
            print(
                f"{key:<31}"
                f"{atext:<36}"
                f"{btext}"
                f"{marker}"
            )

    # --------------------------------------------------------
    # ELECTRONIC CONVERGENCE
    # --------------------------------------------------------

    print("\nELECTRONIC CONVERGENCE")

    scirow(
        "Best SCF residual",
        ea["best_residual"],
        eb["best_residual"],
        "Ry",
    )

    scirow(
        "Last SCF residual",
        ea["last_residual"],
        eb["last_residual"],
        "Ry",
    )

    row(
        "Residual samples",
        ea["residual_samples"],
        eb["residual_samples"],
    )

    row(
        "Recent residual trend",
        ea["residual_trend"],
        eb["residual_trend"],
    )

    row(
        "Highest iteration #",
        ea["highest_iteration"],
        eb["highest_iteration"],
    )

    row(
        "Converged SCF records",
        ea["converged_cycles"],
        eb["converged_cycles"],
    )

    row(
        "Last converged iterations",
        ea["last_converged_iterations"],
        eb["last_converged_iterations"],
    )

    # --------------------------------------------------------
    # ENERGY
    # --------------------------------------------------------

    print("\nENERGY")

    scirow(
        "Last total energy",
        ea["last_energy"],
        eb["last_energy"],
        "Ry",
    )

    row(
        "Energy samples",
        ea["energy_samples"],
        eb["energy_samples"],
    )

    energy_difference = None

    if (
        ea["last_energy"] is not None
        and eb["last_energy"] is not None
    ):
        energy_difference = (
            eb["last_energy"]
            - ea["last_energy"]
        )

        print(
            f"{'ΔE = E(B) - E(A)':<31}"
            f"{energy_difference:.8e} Ry"
        )

    same_system = (
        same_value(ma.get("Atoms"), mb.get("Atoms"))
        and same_value(ma.get("Electrons"), mb.get("Electrons"))
    )

    comparability_checks = []

    def add_compare_check(label, va, vb, required=True):
        same = same_value(va, vb)

        comparability_checks.append(
            {
                "label": label,
                "a": va,
                "b": vb,
                "same": same,
                "required": required,
            }
        )

    add_compare_check(
        "Calculation type",
        type_a,
        type_b,
        required=True,
    )

    add_compare_check(
        "Atoms",
        ma.get("Atoms"),
        mb.get("Atoms"),
        required=True,
    )

    add_compare_check(
        "Electrons",
        ma.get("Electrons"),
        mb.get("Electrons"),
        required=True,
    )

    add_compare_check(
        "ecutwfc",
        ma.get("ecutwfc"),
        mb.get("ecutwfc"),
        required=True,
    )

    add_compare_check(
        "ecutrho",
        ma.get("ecutrho"),
        mb.get("ecutrho"),
        required=True,
    )

    add_compare_check(
        "k-points",
        ma.get("k-points"),
        mb.get("k-points"),
        required=True,
    )

    add_compare_check(
        "occupations",
        ia.get("occupations"),
        ib.get("occupations"),
        required=False,
    )

    add_compare_check(
        "smearing",
        ia.get("smearing") or ma.get("smearing"),
        ib.get("smearing") or mb.get("smearing"),
        required=False,
    )

    add_compare_check(
        "degauss",
        ia.get("degauss") or ma.get("degauss"),
        ib.get("degauss") or mb.get("degauss"),
        required=False,
    )

    required_known = all(
        item["a"] is not None and item["b"] is not None
        for item in comparability_checks
        if item["required"]
    )

    required_same = all(
        item["same"]
        for item in comparability_checks
        if item["required"]
    )

    converged_labels = {
        "CONVERGED",
        "RELAX CONV",
    }

    both_completed_converged = (
        status_a in converged_labels
        and status_b in converged_labels
    )

    # Passing these checks means the raw energies are numerically
    # comparable at the parsed level. It still does NOT prove a fully
    # controlled physical relative-energy comparison because geometry,
    # pseudopotential identity/content and XC functional are not yet
    # completely verified here.
    energy_numerically_comparable = (
        required_known
        and required_same
        and both_completed_converged
    )

    # Be deliberately conservative: physical ΔE is not certified until
    # the stronger Hamiltonian/structure identity checks are implemented.
    energy_physically_comparable = False

    print("\nPHYSICAL COMPARABILITY")

    print(
        f"{'Criterion':<31}"
        f"{'A':<36}"
        f"{'B':<36}"
        f"Result"
    )

    print("-" * 118)

    comparability_rows = [
        (
            "Calculation type",
            type_a,
            type_b,
        ),
        (
            "Atoms",
            ma.get("Atoms"),
            mb.get("Atoms"),
        ),
        (
            "Electrons",
            ma.get("Electrons"),
            mb.get("Electrons"),
        ),
        (
            "ecutwfc",
            ma.get("ecutwfc"),
            mb.get("ecutwfc"),
        ),
        (
            "ecutrho",
            ma.get("ecutrho"),
            mb.get("ecutrho"),
        ),
        (
            "k-points",
            ma.get("k-points"),
            mb.get("k-points"),
        ),
        (
            "occupations",
            ia.get("occupations") or ma.get("occupations"),
            ib.get("occupations") or mb.get("occupations"),
        ),
        (
            "smearing",
            ia.get("smearing") or ma.get("smearing"),
            ib.get("smearing") or mb.get("smearing"),
        ),
        (
            "degauss",
            ia.get("degauss") or ma.get("degauss"),
            ib.get("degauss") or mb.get("degauss"),
        ),
    ]

    comparability_results = {}

    for label, va, vb in comparability_rows:
        if va is None and vb is None:
            result = "UNKNOWN"

        elif va is None or vb is None:
            result = "UNKNOWN"

        elif same_value(va, vb):
            result = "SAME"

        else:
            result = "DIFFERENT"

        comparability_results[label] = result

        print(
            f"{label:<31}"
            f"{fplain(va):<36}"
            f"{fplain(vb):<36}"
            f"{result}"
        )

    print()

    converged_labels = {
        "CONVERGED",
        "RELAX CONV",
    }

    both_completed_converged = (
        status_a in converged_labels
        and status_b in converged_labels
    )

    if both_completed_converged:
        print(
            f"{'Final-energy comparison':<31}: "
            "NUMERICALLY AVAILABLE"
        )
    else:
        print(
            f"{'Final-energy comparison':<31}: "
            "NOT VALID AS FINAL ENERGIES"
        )

        unconverged = []

        if status_a not in converged_labels:
            unconverged.append(
                f"A ({status_a})"
            )

        if status_b not in converged_labels:
            unconverged.append(
                f"B ({status_b})"
            )

        print(
            f"{'Reason':<31}: "
            + ", ".join(unconverged)
            + " is not explicitly converged/completed."
        )

    required_same = (
        "Calculation type",
        "Atoms",
        "Electrons",
        "ecutwfc",
        "ecutrho",
        "k-points",
        "occupations",
    )

    core_comparable = all(
        comparability_results.get(key) == "SAME"
        for key in required_same
    )

    if core_comparable:
        print(
            f"{'Core numerical setup':<31}: CONSISTENT"
        )
    else:
        print(
            f"{'Core numerical setup':<31}: NOT FULLY CONSISTENT"
        )

    print(
        f"{'Physical relative-energy ΔE':<31}: NOT CERTIFIED"
    )

    print(
        f"{'Reason':<31}: geometry, pseudopotentials and XC equivalence "
        "are not yet fully proven by qecompare."
    )

    if energy_difference is not None:
        print(
            f"{'Energy interpretation':<31}: displayed ΔE is a raw "
            "numerical difference unless full physical comparability "
            "is independently established."
        )

    # --------------------------------------------------------
    # RELAXATION / FORCES
    # --------------------------------------------------------

    if (
        type_a in ("RELAX", "VC-RELAX")
        or type_b in ("RELAX", "VC-RELAX")
    ):
        print("\nRELAXATION / FORCES")

        row(
            "BFGS converged",
            "YES" if ea["bfgs_converged"] else "NO",
            "YES" if eb["bfgs_converged"] else "NO",
        )

        scirow(
            "Last total force",
            ea["last_force"],
            eb["last_force"],
            "Ry/Bohr",
        )

        scirow(
            "Best total force",
            ea["best_force"],
            eb["best_force"],
            "Ry/Bohr",
        )

        row(
            "Force samples",
            ea["force_samples"],
            eb["force_samples"],
        )

    elif ea["force_samples"] or eb["force_samples"]:
        print("\nFORCES REPORTED BY SCF")
        print(
            "These are force diagnostics printed during SCF calculations; "
            "they do not imply that an ionic relaxation was performed."
        )

        scirow(
            "Last reported force",
            ea["last_force"],
            eb["last_force"],
            "Ry/Bohr",
        )

        row(
            "Force records",
            ea["force_samples"],
            eb["force_samples"],
        )

    # --------------------------------------------------------
    # SCIENTIFIC SANITY
    # --------------------------------------------------------

    print("\nSCIENTIFIC SANITY")

    scirow(
        "negative rho max",
        ea["negative_rho_max"],
        eb["negative_rho_max"],
    )

    row(
        "Eigenvalue warning lines",
        ea["eigen_warning_lines"],
        eb["eigen_warning_lines"],
    )

    row(
        "Max unconverged eigenvals",
        ea["max_unconverged_eigenvalues"],
        eb["max_unconverged_eigenvalues"],
    )

    row(
        "Charge-is-wrong warning",
        "YES" if ea["charge_wrong"] else "NO",
        "YES" if eb["charge_wrong"] else "NO",
    )

    # --------------------------------------------------------
    # TERMINATION
    # --------------------------------------------------------

    print("\nTERMINATION EVIDENCE")

    row(
        "JOB DONE",
        "YES" if ea["job_done"] else "NO",
        "YES" if eb["job_done"] else "NO",
    )

    row(
        "Wall-time stop",
        "YES" if ea["wall_stop"] else "NO",
        "YES" if eb["wall_stop"] else "NO",
    )

    row(
        "Calculation stopped",
        "YES" if ea["calculation_stopped"] else "NO",
        "YES" if eb["calculation_stopped"] else "NO",
    )

    row(
        "QE runtime error",
        "YES" if ea["qe_error"] else "NO",
        "YES" if eb["qe_error"] else "NO",
    )

    # --------------------------------------------------------
    # PERFORMANCE
    # --------------------------------------------------------

    print("\nPERFORMANCE")

    row(
        "PWSCF timing",
        ea["timing"],
        eb["timing"],
    )

    # --------------------------------------------------------
    # DIFFERENCE SUMMARY
    # --------------------------------------------------------

    print("\n" + "=" * 112)
    print("DIFFERENCE SUMMARY")
    print("=" * 112)

    if changed_settings:
        operational_keys = {
            "outdir",
            "prefix",
            "pseudo_dir",
            "max_seconds",
        }

        scientific_changes = [
            (key, va, vb)
            for key, va, vb in changed_settings
            if key not in operational_keys
        ]

        operational_changes = [
            (key, va, vb)
            for key, va, vb in changed_settings
            if key in operational_keys
        ]

        if scientific_changes:
            print("SCIENTIFIC / NUMERICAL DIFFERENCES")

            for key, va, vb in scientific_changes:
                print(
                    f"  {key:<20}: "
                    f"{fplain(va)}  ->  {fplain(vb)}"
                )

        else:
            print("SCIENTIFIC / NUMERICAL DIFFERENCES")
            print("  none detected")

        if operational_changes:
            print("\nOPERATIONAL / PATH DIFFERENCES")

            for key, va, vb in operational_changes:
                print(
                    f"  {key:<20}: "
                    f"{fplain(va)}  ->  {fplain(vb)}"
                )

    elif ia and ib:
        print(
            "No differences were detected among the parsed input settings."
        )

    else:
        print(
            "Input-setting comparison is incomplete because an exact "
            "same-stem input file was not available for both outputs."
        )

    # --------------------------------------------------------
    # NUMERICAL OUTCOME
    # --------------------------------------------------------

    print("\nNUMERICAL OUTCOME")

    converged_labels = {
        "CONVERGED",
        "RELAX CONV",
    }

    a_conv = status_a in converged_labels
    b_conv = status_b in converged_labels

    if a_conv and not b_conv:
        print(
            "A has the stronger completion/convergence outcome: "
            f"A={status_a}, B={status_b}."
        )

    elif b_conv and not a_conv:
        print(
            "B has the stronger completion/convergence outcome: "
            f"A={status_a}, B={status_b}."
        )

    elif a_conv and b_conv:
        print(
            "Both calculations reached an explicitly converged/completed state."
        )

        if (
            type_a == "SCF"
            and type_b == "SCF"
            and ea["last_residual"] is not None
            and eb["last_residual"] is not None
        ):
            if ea["last_residual"] < eb["last_residual"]:
                print(
                    "A ended with the smaller parsed SCF residual."
                )
            elif eb["last_residual"] < ea["last_residual"]:
                print(
                    "B ended with the smaller parsed SCF residual."
                )
            else:
                print(
                    "The parsed final SCF residuals are equal."
                )

        if (
            type_a in ("RELAX", "VC-RELAX")
            and type_b in ("RELAX", "VC-RELAX")
            and ea["last_force"] is not None
            and eb["last_force"] is not None
        ):
            if ea["last_force"] < eb["last_force"]:
                print(
                    "A ended with the smaller parsed total force."
                )
            elif eb["last_force"] < ea["last_force"]:
                print(
                    "B ended with the smaller parsed total force."
                )
            else:
                print(
                    "The parsed final total forces are equal."
                )

    else:
        print(
            "Neither calculation provides a stronger explicitly converged "
            "final outcome based on the parsed QE status."
        )

    # --------------------------------------------------------
    # RESEARCH INTERPRETATION
    # --------------------------------------------------------

    print("\nRESEARCH INTERPRETATION")

    if changed_settings:
        print(
            "The calculations differ in one or more parsed input settings "
            "listed above."
        )

        print(
            "Any difference in convergence rate, residuals, energy, forces "
            "or runtime is therefore associated with those changed settings "
            "and/or other differences between the calculations."
        )

        print(
            "This two-run comparison alone does NOT establish that any one "
            "changed parameter caused the observed numerical behaviour."
        )

        if len(scientific_changed_settings) > 1:
            print(
                "Because multiple scientific/numerical settings changed, "
                "individual causal effects cannot be isolated from this pair alone."
            )
        elif len(scientific_changed_settings) == 1:
            key, va, vb = scientific_changed_settings[0]
            print(
                f"Only one parsed scientific/numerical setting differs: "
                f"{key} ({fplain(va)} -> {fplain(vb)})."
            )
            print(
                "This is a cleaner controlled comparison, although a two-run "
                "comparison by itself still does not prove causality."
            )

        operational_differences = [
            item for item in changed_settings
            if item[0] in operational_keys
        ]

        if operational_differences:
            print(
                "Operational/path differences were also detected but are not "
                "treated as changes to the physical Hamiltonian."
            )

    else:
        print(
            "No parsed input-setting change was established from the available "
            "sidecar inputs."
        )

    if type_a != type_b:
        print(
            "CAUTION: the two files have different calculation classifications "
            f"({type_a} vs {type_b}); some numerical metrics are not directly "
            "equivalent."
        )

    if not same_system:
        print(
            "CAUTION: system-size/electron-count evidence differs, so total "
            "energies should not be treated as directly comparable physical "
            "relative energies."
        )

    print("\nUSEFUL NEXT COMMANDS")
    print(f"  qescf {pa}    # detailed A analysis if A is SCF-like")
    print(f"  qescf {pb}    # detailed B analysis if B is SCF-like")
    print("  qehistory      # inspect chronology in the current directory")
    print("  qecheck .      # inspect inputs/restarts/scripts")
    print("=" * 112)


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
