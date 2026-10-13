"""Equation specs: safe expression handling and cross-checks against the source.

equations.yaml (authored by Claude from the source, then validated here):

    consts:
      g: {value: 9.81, unit: m/s2}
    equations:
      - name: stopping_sight_distance
        unit: p112                       # citation unit the equation appears in
        expr: "0.278*V*t + V**2/(254*(a/9.81 + G))"
        vars:  {V: {unit: km/h, min: 30, max: 140}, t: {unit: s}, a: {unit: m/s2}, G: {unit: ""}}
        result: {unit: m}
        source_text: "SSD = 0.278Vt + V2/(254((a/9.81) ± G))"      # as printed
        implicit_literals: []            # literals in expr that are not printed (e.g. 0.5 for sqrt)
        ignore_source_numbers: ["3.1"]   # printed numbers that are not part of the maths (equation no.)
        examples:
          - inputs: {V: 100, t: 2.5, a: 3.4, G: 0}
            expected: 185.0
            decimals: 0                  # or tol_rel: 0.01
            unit: p113                   # where the worked example is printed (default: equation unit)
            assumed: [G]                 # inputs that are the author's choice, not printed in the source
"""
from __future__ import annotations

import ast
import builtins
import keyword
import math
import re

import yaml

from .model import numbers, squeeze, cmp_text

FUNCS = {"sqrt": math.sqrt, "log": math.log, "log10": math.log10, "exp": math.exp, "sin": math.sin, "cos": math.cos,
         "tan": math.tan, "asin": math.asin, "acos": math.acos, "atan": math.atan, "atan2": math.atan2,
         "radians": math.radians, "degrees": math.degrees, "abs": abs, "min": min, "max": max}
CONSTS = {"pi": math.pi, "e": math.e}
BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod, ast.FloorDiv)
MAX_LEN = 500


class ExprError(Exception):
    pass


def parse_expr(expr: str):
    """Validate ``expr`` against the whitelist. Returns (code, names, literals)."""
    if not isinstance(expr, str) or not expr.strip():
        raise ExprError("empty expression")
    if len(expr) > MAX_LEN:
        raise ExprError(f"expression longer than {MAX_LEN} characters")
    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except SyntaxError as e:
        raise ExprError(f"syntax error: {e.msg}") from e
    names, lits = set(), []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Expression, ast.Load, ast.UAdd, ast.USub) + BINOPS):
            continue
        if isinstance(node, ast.BinOp):
            if isinstance(node.op, ast.Pow) and (isinstance(node.right, ast.BinOp) and isinstance(node.right.op, ast.Pow)):
                raise ExprError("chained powers a**b**c are not allowed; use parentheses with a literal exponent")
            continue
        if isinstance(node, ast.UnaryOp):
            continue
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise ExprError(f"only numeric literals are allowed, found {node.value!r}")
            if abs(node.value) > 1e12:
                raise ExprError("literal too large")
            lits.append(float(node.value))
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in FUNCS or node.keywords:
                raise ExprError("only plain calls to " + ", ".join(sorted(FUNCS)) + " are allowed")
        else:
            raise ExprError(f"disallowed syntax: {type(node).__name__}")
    callnames = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call)}
    return compile(tree, "<equation>", "eval"), names - callnames, lits


def evaluate(expr: str, values: dict) -> float:
    code, _, _ = parse_expr(expr)
    ns = {**CONSTS, **FUNCS, **values}
    return float(eval(code, {"__builtins__": {}}, ns))  # noqa: S307 - AST whitelisted above


def load_spec(path):
    with open(path, encoding="utf-8") as f:
        spec = yaml.safe_load(f) or {}
    if not isinstance(spec, dict):
        raise ValueError("equations.yaml must be a mapping with 'equations' (and optional 'consts')")
    return spec


def _floats(counter) -> set:
    out = set()
    for t in counter:
        try:
            out.add(float(t))
        except ValueError:
            pass
    return out


def _near(x, pool, rel=1e-9) -> bool:
    return any(abs(x - p) <= rel * max(1.0, abs(x)) for p in pool)


def check(spec: dict, unit_text: dict, add):
    """Validate every equation. ``add(code, severity, unit, detail, tid)`` registers a flag.

    Returns the list of equations (normalised dicts) for emission.
    """
    consts = spec.get("consts") or {}
    out, seen = [], set()
    for k, c in consts.items():
        if not str(k).isidentifier() or keyword.iskeyword(k) or k in FUNCS or k in CONSTS:
            add("EQ_BAD_NAME", "BLOCK", "", f"constant name '{k}' is not a valid/safe identifier", f"const:{k}")
    for eq in spec.get("equations") or []:
        name = str(eq.get("name", ""))
        unit = str(eq.get("unit", ""))
        tid = f"eq:{name}"
        bad = lambda code, detail, sev="BLOCK": add(code, sev, unit, f"{name}: {detail}", tid)  # noqa: E731
        ok = True
        if not name.isidentifier() or keyword.iskeyword(name) or name in dir(builtins) or name in FUNCS or name in seen:
            bad("EQ_BAD_NAME", "name must be a unique valid Python identifier (not a builtin/keyword)")
            ok = False
        seen.add(name)
        vars_ = eq.get("vars") or {}
        for v in vars_:
            if not str(v).isidentifier() or keyword.iskeyword(v) or v in FUNCS or v in CONSTS or v in consts:
                bad("EQ_BAD_NAME", f"variable '{v}' is not a safe/unique identifier")
                ok = False
        try:
            _, names, lits = parse_expr(eq.get("expr", ""))
        except ExprError as e:
            bad("EQ_BAD_EXPR", str(e))
            out.append({**eq, "status": "unverified"})
            continue
        unknown = names - set(vars_) - set(consts) - set(CONSTS)
        if unknown:
            bad("EQ_BAD_EXPR", "undeclared names in expr: " + ", ".join(sorted(unknown)))
            ok = False
        unused = set(vars_) - names
        if unused:
            bad("EQ_BAD_EXPR", "declared but unused variables: " + ", ".join(sorted(unused)))
            ok = False
        text = unit_text.get(unit)
        if text is None:
            bad("EQ_UNIT_MISSING", f"unit '{unit}' does not exist in the converted reference")
            out.append({**eq, "status": "unverified"})
            continue
        src = str(eq.get("source_text", ""))
        if not src.strip():
            bad("EQ_NO_SOURCE_TEXT", "source_text (the equation exactly as printed) is required")
            ok = False
        else:
            missing = numbers(src) - numbers(text)
            if missing:
                bad("EQ_SOURCE_NUMBER_NOT_FOUND",
                    "numbers in source_text that are not in the accepted text of " + unit + ": " + ", ".join(sorted(missing.elements())))
                ok = False
            if squeeze(src) not in squeeze(text):
                bad("EQ_SOURCE_NOT_VERBATIM",
                    "source_text is not found verbatim in the extracted text (equations are often mangled in PDFs). "
                    "Confirm it visually against the page; use two blind transcriptions for scanned sources.", "WARN")
        # literals in the expression must be exactly what is printed
        src_nums = _floats(numbers(src))
        const_vals = {float(c.get("value")) for c in consts.values() if isinstance(c, dict) and "value" in c}
        implicit = {float(x) for x in eq.get("implicit_literals") or []}
        ignore = {float(x) for x in eq.get("ignore_source_numbers") or []}
        extra_lit = {x for x in lits if not _near(x, src_nums | implicit | const_vals)}
        extra_src = {x for x in src_nums if not _near(x, set(lits) | ignore | const_vals)}
        if extra_lit or extra_src:
            parts = []
            if extra_lit:
                parts.append("literals in expr not printed in source_text: " + ", ".join(f"{x:g}" for x in sorted(extra_lit)))
            if extra_src:
                parts.append("numbers printed in source_text but missing from expr: " + ", ".join(f"{x:g}" for x in sorted(extra_src)))
            bad("EQ_LITERAL_MISMATCH", "; ".join(parts) + ". Fix the expr, or declare implicit_literals / ignore_source_numbers.")
            ok = False
        # worked examples
        exs = eq.get("examples") or []
        if not exs:
            bad("EQ_UNTESTED", "no worked example from the source; the generated function cannot be tested", "WARN")
        for i, ex in enumerate(exs, 1):
            inputs = ex.get("inputs") or {}
            if set(inputs) != set(vars_):
                bad("EQ_EXAMPLE_INPUTS", f"example {i}: inputs must be exactly {sorted(vars_)}")
                ok = False
                continue
            if ex.get("expected") is None or ("decimals" not in ex and "tol_rel" not in ex):
                bad("EQ_EXAMPLE_TOL", f"example {i}: give 'expected' and either 'decimals' (as printed) or 'tol_rel'")
                ok = False
                continue
            try:
                got = evaluate(eq["expr"], {**{k: float(c["value"]) for k, c in consts.items()}, **{k: float(v) for k, v in inputs.items()}})
            except Exception as e:  # noqa: BLE001
                bad("EQ_EXAMPLE_ERROR", f"example {i}: {type(e).__name__}: {e}")
                ok = False
                continue
            exp = float(ex["expected"])
            tol = 0.5 * 10 ** (-int(ex["decimals"])) + 1e-9 if "decimals" in ex else abs(exp) * float(ex["tol_rel"]) + 1e-12
            if not abs(got - exp) <= tol:
                bad("EQ_EXAMPLE_FAIL", f"example {i}: expression gives {got:.6g}, source says {exp:g} (tolerance {tol:.3g}). "
                    "Either the expr/constants are wrong or the printed example is.")
                ok = False
            ex_text = unit_text.get(str(ex.get("unit", unit)))
            if ex_text is None:
                bad("EQ_UNIT_MISSING", f"example {i}: unit '{ex.get('unit')}' not found")
                ok = False
                continue
            pool = _floats(numbers(ex_text))
            need = {k: float(v) for k, v in inputs.items() if k not in (ex.get("assumed") or [])}
            miss = [f"{k}={v:g}" for k, v in need.items() if not _near(v, pool)]
            if not _near(exp, pool):
                miss.append(f"expected={exp:g}")
            if miss:
                bad("EQ_EXAMPLE_NOT_IN_SOURCE", f"example {i}: values not printed in {ex.get('unit', unit)}: {', '.join(miss)} "
                    "(declare author-chosen inputs under 'assumed')")
                ok = False
            for k, spec_v in vars_.items():
                v = float(inputs[k])
                lo, hi = (spec_v or {}).get("min"), (spec_v or {}).get("max")
                if (lo is not None and v < lo) or (hi is not None and v > hi):
                    bad("EQ_EXAMPLE_RANGE", f"example {i}: {k}={v:g} outside declared range [{lo}, {hi}]", "WARN")
        for v, d in vars_.items():
            if not (d or {}).get("unit") and (d or {}).get("unit") != "":
                bad("EQ_NO_UNITS", f"variable '{v}' has no unit (use unit: \"\" for dimensionless)", "WARN")
        out.append({**eq, "consts_used": sorted(names & set(consts))})
    return out
