
from pathlib import Path
import re
import sys
import math
import json

FLOAT = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][-+]?\d+)?"

def read(path):
    return Path(path).read_text(errors="ignore")

def dfloat(x):
    return float(x.replace("D","E").replace("d","e"))

def last_or(items, default=None):
    return items[-1] if items else default

def status_line(label, status, detail=""):
    pad = 28
    tail = f" — {detail}" if detail else ""
    print(f"{label:<{pad}}: {status}{tail}")

def trend(vals):
    if len(vals) < 4:
        return "insufficient data"
    recent = vals[-min(12, len(vals)):]
    x0, x1 = recent[0], recent[-1]
    best = min(recent)
    if x1 < x0/3:
        return "strongly decreasing"
    if x1 < x0*0.8:
        return "decreasing"
    # oscillation / plateau heuristics
    diffs = [recent[i+1]-recent[i] for i in range(len(recent)-1)]
    sign_changes = sum(1 for a,b in zip(diffs, diffs[1:]) if a*b < 0)
    spread = max(recent)-min(recent)
    mean = sum(recent)/len(recent)
    if sign_changes >= max(2, len(diffs)//3) and spread > 0.15*mean:
        return "oscillatory"
    if spread < 0.2*mean:
        return "plateau"
    if x1 > x0*1.5:
        return "worsening"
    return "mixed / noisy"

def parse_qe_input_settings(text):
    keys = [
        "calculation","restart_mode","prefix","outdir","pseudo_dir","nat","ntyp","nbnd",
        "occupations","smearing","degauss","conv_thr","etot_conv_thr","forc_conv_thr",
        "electron_maxstep","diagonalization","mixing_beta","mixing_mode",
        "startingwfc","startingpot","max_seconds","ecutwfc","ecutrho"
    ]
    out = {}
    for key in keys:
        m = re.search(rf"(?im)^\s*{re.escape(key)}\s*=\s*([^,\n/]+)", text)
        if m:
            out[key] = m.group(1).strip().strip("'\"")
    return out

def output_core(text):
    iters=[]
    acc=[]
    energies=[]
    neg=[]
    for line in text.splitlines():
        m=re.search(r"iteration #\s*(\d+)", line, re.I)
        if m:
            iters.append(int(m.group(1)))
        m=re.search(r"estimated scf accuracy\s*<\s*("+FLOAT+r")\s*Ry", line, re.I)
        if m:
            acc.append(dfloat(m.group(1)))
        m=re.search(r"!\s+total energy\s*=\s*("+FLOAT+r")\s*Ry", line, re.I)
        if not m:
            m=re.search(r"(?<!estimated )\btotal energy\s*=\s*("+FLOAT+r")\s*Ry", line, re.I)
        if m:
            energies.append(dfloat(m.group(1)))
        m=re.search(r"negative rho.*?:\s*("+FLOAT+r")", line, re.I)
        if m:
            neg.append(abs(dfloat(m.group(1))))
    return {"iters":iters,"acc":acc,"energies":energies,"neg":neg}

def detect_file(path_arg):
    p = Path(path_arg)
    if p.is_file():
        return p
    if p.is_dir():
        outs = sorted(
            [x for x in p.glob("*.out") if x.is_file()],
            key=lambda x: x.stat().st_mtime,
            reverse=True
        )
        # Prefer QE output-looking files over Slurm wrapper outputs
        qeouts=[]
        for x in outs:
            try:
                t=x.read_text(errors="ignore")[:200000]
            except Exception:
                continue
            if "Program PWSCF" in t or "estimated scf accuracy" in t or "JOB DONE." in t:
                qeouts.append(x)
        if qeouts:
            return qeouts[0]
        if outs:
            return outs[0]
    raise SystemExit(f"No readable output file found for: {path_arg}")

def scf(path="."):
    p = detect_file(path)
    text = read(p)
    c = output_core(text)

    print("="*86)
    print("QESCF — RESEARCH-GRADE QUANTUM ESPRESSO SCF ANALYZER")
    print("="*86)
    print(f"File: {p.resolve()}")

    # Metadata
    patterns = {
        "QE version": r"Program PWSCF v\.([^\s]+)",
        "Atoms": r"number of atoms/cell\s*=\s*(\d+)",
        "Atomic species": r"number of atomic types\s*=\s*(\d+)",
        "Electrons": r"number of electrons\s*=\s*("+FLOAT+r")",
        "KS states": r"number of Kohn-Sham states\s*=\s*(\d+)",
        "k-points": r"number of k points\s*=\s*(\d+)",
        "ecutwfc": r"kinetic-energy cutoff\s*=\s*("+FLOAT+r")\s*Ry",
        "ecutrho": r"charge density cutoff\s*=\s*("+FLOAT+r")\s*Ry",
    }
    meta={}
    for key, pat in patterns.items():
        m = re.search(pat, text, re.I)
        if m:
            meta[key] = m.group(1)

    print("\nSYSTEM / NUMERICS")
    if meta:
        for k,v in meta.items():
            print(f"{k:<28}: {v}")
    else:
        print("No standard PWSCF metadata parsed.")

    # Output-side physics/numerics
    sm = re.search(r"number of k points=.*?(Marzari-Vanderbilt|Methfessel-Paxton|Gaussian|cold) smearing.*?width \(Ry\)=\s*("+FLOAT+r")", text, re.I)
    if sm:
        print(f"{'Smearing':<28}: {sm.group(1)}")
        print(f"{'Smearing width':<28}: {sm.group(2)} Ry")

    # Pseudopotential traces
    pseudo = []
    for line in text.splitlines():
        if re.search(r"\bUPF\b|PseudoPot\.|pseudopotential|read from file", line, re.I):
            s=line.strip()
            if s and s not in pseudo:
                pseudo.append(s)
    if pseudo:
        print("\nPSEUDOPOTENTIAL TRACE")
        for line in pseudo[:20]:
            print(" ", line)

    # Convergence
    print("\nSCF CONVERGENCE")
    conv_hits = re.findall(r"convergence has been achieved in\s+(\d+)\s+iterations", text, re.I)
    explicit_notconv = bool(re.search(r"convergence NOT achieved", text, re.I))
    wall_stop = bool(re.search(r"Maximum CPU time exceeded", text, re.I))
    stopped = bool(re.search(r"Calculation stopped", text, re.I))
    jobdone = "JOB DONE." in text
    err = re.search(r"Error in routine\s+([^( \n]+)", text)

    if c["acc"]:
        best=min(c["acc"])
        ibest=c["acc"].index(best)+1
        print(f"Residual samples           : {len(c['acc'])}")
        print(f"First estimated accuracy   : {c['acc'][0]:.10e} Ry")
        print(f"Best estimated accuracy    : {best:.10e} Ry  [sample {ibest}]")
        print(f"Last estimated accuracy    : {c['acc'][-1]:.10e} Ry")
        print(f"Recent residual behaviour  : {trend(c['acc'])}")
        if len(c["acc"]) >= 10:
            recent=c["acc"][-10:]
            print(f"Last-10 min / max          : {min(recent):.10e} / {max(recent):.10e} Ry")
            if best > 0:
                print(f"Last / best ratio          : {c['acc'][-1]/best:.3f}")
    else:
        print("Estimated accuracy          : UNKNOWN")

    if c["iters"]:
        print(f"Highest iteration number   : {max(c['iters'])}")

    if conv_hits:
        print(f"QE convergence statements  : {len(conv_hits)}")
        print(f"Last converged cycle        : {conv_hits[-1]} iterations")

    if c["energies"]:
        print(f"Energy samples             : {len(c['energies'])}")
        print(f"Last total energy          : {c['energies'][-1]:.12f} Ry")
        if len(c["energies"]) >= 2:
            de=c["energies"][-1]-c["energies"][-2]
            print(f"Last ΔE                    : {de:.6e} Ry")
        if len(c["energies"]) >= 5:
            last5=c["energies"][-5:]
            spread=max(last5)-min(last5)
            print(f"Last-5 energy spread       : {spread:.6e} Ry")

    # Negative rho and diagonalization warnings
    print("\nSCIENTIFIC SANITY")
    if c["neg"]:
        print(f"negative rho min / max     : {min(c['neg']):.3e} / {max(c['neg']):.3e}")
    else:
        print("negative rho               : not reported")

    eigwarn=[]
    for m in re.finditer(r"(\d+)\s+eigenvalues? not converged", text, re.I):
        eigwarn.append(int(m.group(1)))
    if eigwarn:
        print(f"Eigenvector warning lines  : {len(eigwarn)}")
        print(f"Max unconverged eigenvals  : {max(eigwarn)}")
    else:
        print("Eigenvector warnings       : none parsed")

    charge_wrong = bool(re.search(r"charge is wrong", text, re.I))
    many_bands = bool(re.search(r"too many bands are not converged", text, re.I))

    # Performance
    timing = last_or(re.findall(r"(?m)^.*PWSCF.*WALL.*$", text))
    if timing:
        print("\nPERFORMANCE")
        print(timing.strip())

    # Termination / restart
    print("\nTERMINATION / RESTART EVIDENCE")
    status_line("JOB DONE marker", "YES" if jobdone else "NO")
    status_line("Wall-time limit reached", "YES" if wall_stop else "NO")
    status_line("Calculation stopped marker", "YES" if stopped else "NO")
    status_line("Explicit non-convergence", "YES" if explicit_notconv else "NO")
    if err:
        status_line("QE runtime error", "YES", f"Error in routine {err.group(1)}")
    else:
        status_line("QE runtime error", "NO")

    # Derive assessment
    print("\nASSESSMENT")
    execution = "FAIL" if err else ("PASS" if jobdone else "UNKNOWN")
    status_line("Execution integrity", execution)

    if err:
        electronic = "FAIL"
        detail = "runtime error"
    elif explicit_notconv or wall_stop or stopped:
        electronic = "NOT CONVERGED"
        detail = "explicit stop/non-convergence evidence"
    elif conv_hits:
        electronic = "PASS"
        detail = "QE explicitly reports convergence"
    elif c["acc"]:
        electronic = "UNPROVEN"
        detail = "residual history exists but no explicit convergence statement"
    else:
        electronic = "UNKNOWN"
        detail = "insufficient SCF evidence"
    status_line("Electronic convergence", electronic, detail)

    if c["acc"]:
        status_line("Residual behaviour", "INFO", trend(c["acc"]))

    if c["neg"]:
        mx=max(c["neg"])
        nrho_status = "WARNING" if mx >= 0.1 else "INFO"
        status_line("Negative-rho check", nrho_status, f"max={mx:.3e}")

    if eigwarn or many_bands:
        status_line("Diagonalization sanity", "WARNING",
                    "unconverged eigenvectors/bands reported")
    else:
        status_line("Diagonalization sanity", "PASS")

    if charge_wrong:
        status_line("Charge consistency", "WARNING", "QE reported 'charge is wrong'")
    else:
        status_line("Charge consistency", "PASS")

    print("\nRESEARCH DECISION")
    if err:
        decision = "FAIL — runtime error. Do not use this result."
    elif explicit_notconv or wall_stop or stopped:
        decision = "NOT CONVERGED — do not use as a final SCF result."
    elif conv_hits:
        decision = "SCF CONVERGED — verify numerical settings and physical sanity before production use."
    elif c["acc"]:
        decision = "INCOMPLETE / UNPROVEN — convergence is not explicitly established."
    else:
        decision = "UNKNOWN — insufficient SCF evidence in this file."
    print(decision)

    print("\nNEXT USEFUL COMMANDS")
    print("  qehistory .          # inspect repeated runs/restarts")
    print("  qecompare A.out B.out")
    print("  qecheck .            # planned: input/pseudo/k-grid/restart integrity")
    print("="*86)

def relax(path="."):
    p=detect_file(path)
    text=read(p)
    print("="*72)
    print("QERELAX — BASIC RELAXATION ANALYZER (v0.2 placeholder)")
    print("="*72)
    print(f"File: {p.resolve()}")
    energies=[dfloat(x) for x in re.findall(r"!\s+total energy\s*=\s*("+FLOAT+r")\s*Ry", text)]
    forces=[dfloat(x) for x in re.findall(r"Total force\s*=\s*("+FLOAT+r")", text)]
    scfs=[int(x) for x in re.findall(r"convergence has been achieved in\s+(\d+)\s+iterations", text)]
    if energies:
        print(f"Ionic/energy samples       : {len(energies)}")
        print(f"Last energy                : {energies[-1]:.12f} Ry")
    if forces:
        print(f"Force samples              : {len(forces)}")
        print(f"Last total force           : {forces[-1]:.6e}")
        print(f"Best total force           : {min(forces):.6e}")
    if scfs:
        print(f"SCF cycles parsed          : {len(scfs)}")
        print(f"Last SCF iterations        : {scfs[-1]}")
    if re.search(r"bfgs converged", text, re.I):
        print("Relaxation status          : CONVERGED")
    elif re.search(r"Maximum CPU time exceeded|Calculation stopped", text, re.I):
        print("Relaxation status          : STOPPED / INCOMPLETE")
    else:
        print("Relaxation status          : UNPROVEN")
    print("\nNote: qerelax becomes the next research-grade module in v0.3.")

def history(path="."):
    p=Path(path)
    files=[]
    if p.is_file():
        files=[p]
    else:
        files=sorted(p.glob("*.out"), key=lambda x:x.stat().st_mtime)
    print("="*80)
    print("QEHISTORY — OUTPUT HISTORY")
    print("="*80)
    for f in files:
        try: t=read(f)
        except: continue
        if "Program PWSCF" not in t and "estimated scf accuracy" not in t and "JOB DONE." not in t:
            continue
        c=output_core(t)
        conv="CONV" if re.search(r"convergence has been achieved in",t,re.I) else ""
        stop="STOP" if re.search(r"Maximum CPU time exceeded|Calculation stopped",t,re.I) else ""
        err="ERR" if re.search(r"Error in routine",t,re.I) else ""
        best=min(c["acc"]) if c["acc"] else float("nan")
        last=c["acc"][-1] if c["acc"] else float("nan")
        print(f"{f.name:52s} best={best:.3e} last={last:.3e} {conv} {stop} {err}")

def compare(a,b):
    pa,pb=detect_file(a),detect_file(b)
    ta,tb=read(pa),read(pb)
    ca,cb=output_core(ta),output_core(tb)
    print("="*80)
    print("QECOMPARE")
    print("="*80)
    for label,ca0,cb0 in [
        ("Best residual", min(ca["acc"]) if ca["acc"] else None, min(cb["acc"]) if cb["acc"] else None),
        ("Last residual", ca["acc"][-1] if ca["acc"] else None, cb["acc"][-1] if cb["acc"] else None),
        ("Last energy", ca["energies"][-1] if ca["energies"] else None, cb["energies"][-1] if cb["energies"] else None),
    ]:
        print(f"{label:<20}: {ca0}    |    {cb0}")
    print(f"A: {pa}")
    print(f"B: {pb}")

def check(path="."):
    p=Path(path)
    print("="*80)
    print("QECHECK — BASIC DIRECTORY INTEGRITY")
    print("="*80)
    print(f"Path: {p.resolve()}")
    ins=list(p.glob("*.in")) if p.is_dir() else []
    outs=list(p.glob("*.out")) if p.is_dir() else []
    errs=list(p.glob("*.err")) if p.is_dir() else []
    print(f"Input files                : {len(ins)}")
    print(f"Output files               : {len(outs)}")
    print(f"Error files                : {len(errs)}")
    for f in ins[:10]:
        t=read(f)
        s=parse_qe_input_settings(t)
        print(f"\nINPUT: {f.name}")
        for k in ["calculation","restart_mode","prefix","outdir","pseudo_dir","nat","nbnd",
                  "occupations","smearing","degauss","conv_thr","mixing_beta","mixing_mode",
                  "startingwfc","startingpot","max_seconds"]:
            if k in s:
                print(f"  {k:<18} {s[k]}")
        # pseudo existence check
        pdir=s.get("pseudo_dir")
        if pdir:
            pp=Path(pdir)
            if pp.exists():
                print("  pseudo_dir exists   YES")
            else:
                print("  pseudo_dir exists   NO")
    print("\nNote: v0.3 will cross-check input ↔ save-dir ↔ Slurm ↔ output consistency.")

def main():
    if len(sys.argv) < 2:
        print("Usage: qetools <scf|relax|history|compare|check> [args]")
        return
    cmd=sys.argv[1]
    args=sys.argv[2:]
    if cmd=="scf":
        scf(args[0] if args else ".")
    elif cmd=="relax":
        relax(args[0] if args else ".")
    elif cmd=="history":
        history(args[0] if args else ".")
    elif cmd=="compare":
        if len(args)!=2:
            raise SystemExit("Usage: qetools compare A.out B.out")
        compare(args[0],args[1])
    elif cmd=="check":
        check(args[0] if args else ".")
    else:
        raise SystemExit(f"Unknown command: {cmd}")

if __name__=="__main__":
    main()
