"""Damped least squares, shared by the 2D and 3D constraint solvers.

A sketch and an assembly pose the same question in different variables: here
is a vector of residuals that should all be zero, and here is where we are
now - find the nearest state that satisfies them.  Levenberg-Marquardt is the
right tool for both, so it lives here once rather than in each solver.

The Jacobian is numeric on purpose.  Hand-written derivatives for every
constraint type would be faster, but they are also the classic place for a
solver to go quietly wrong, and these systems are small enough that the
finite-difference cost never shows.
"""

from __future__ import annotations

import math
from typing import Callable

import numpy as np

# how much a single step may move the state, relative to the state itself;
# without this a degenerate system can throw the geometry to infinity on the
# first iteration and never come back
STEP_LIMIT_FLOOR = 10.0


def jacobian(fn: Callable[[np.ndarray], np.ndarray], x: np.ndarray,
             r0: np.ndarray) -> np.ndarray:
    """Forward-difference Jacobian of ``fn`` at ``x``, given ``fn(x) == r0``."""
    n = len(x)
    m = len(r0)
    J = np.zeros((m, n))
    for i in range(n):
        h = 1e-6 * max(1.0, abs(float(x[i])))
        xp = x.copy()
        xp[i] += h
        rp = fn(xp)
        if len(rp) != m:
            continue
        J[:, i] = (rp - r0) / h
    return J


def minimise(fn: Callable[[np.ndarray], np.ndarray], x: np.ndarray,
             max_iter: int = 60) -> np.ndarray:
    """Levenberg-Marquardt least squares on the residual function ``fn``."""
    r = fn(x)
    if r.size == 0:
        return x
    n = len(x)
    err = float(r @ r)
    spent = 0

    # LM abandons a solve by letting its damping run away, and hands back a
    # point where every step it can still take is microscopic - short of the
    # answer rather than at it.  Resetting the damping and carrying on from
    # the same point is usually all that is needed, so the descent gets a few
    # fresh starts.  They share one iteration budget: a stall costs about
    # eight residual evaluations, not the whole allowance.
    for _attempt in range(5):
        lam = 1e-3
        started = err
        stalled = False

        while spent < max_iter and not stalled:
            spent += 1
            if math.sqrt(err / r.size) < 1e-12:
                return x

            J = jacobian(fn, x, r)
            JtJ = J.T @ J
            Jtr = J.T @ r

            # Damping is a flat lam*I, which assumes one unit of every
            # unknown counts for about as much as one unit of any other.
            # That is the caller's job to arrange - see the assembly
            # solver, which works in scaled variables for exactly this
            # reason - because weighting the damping by each column's own
            # sensitivity instead changes which solution a soft-anchored
            # drag settles on, and dragging has to stay predictable.
            stalled = True
            for _try in range(8):
                try:
                    step = np.linalg.solve(JtJ + lam * np.eye(n), -Jtr)
                except np.linalg.LinAlgError:
                    step = -np.linalg.pinv(JtJ + lam * np.eye(n)) @ Jtr
                norm = float(np.linalg.norm(step))
                limit = max(STEP_LIMIT_FLOOR,
                            0.5 * float(np.linalg.norm(x)) + STEP_LIMIT_FLOOR)
                if norm > limit:
                    step *= limit / norm
                xn = x + step
                rn = fn(xn)
                en = float(rn @ rn)
                if en < err:
                    x, r, err = xn, rn, en
                    lam = max(lam * 0.4, 1e-9)
                    stalled = False
                    break
                lam *= 6.0

        if spent >= max_iter or err >= started:
            break
    return x


def degrees_of_freedom(J: np.ndarray, n: int) -> int:
    """How many ways the system can still move, from the Jacobian's rank.

    The threshold is relative to the largest singular value rather than
    absolute.  A finite-difference Jacobian carries roughly the square root
    of machine epsilon in error, so a genuinely null direction shows up as a
    singular value near 1e-6, not near zero - an absolute cut-off counts it
    as rank and reports a free assembly as fully constrained.
    """
    if J.size == 0:
        return n
    try:
        singular = np.linalg.svd(J, compute_uv=False)
    except np.linalg.LinAlgError:
        return n
    if singular.size == 0:
        return n
    rank = int(np.count_nonzero(singular > float(singular[0]) * 1e-6))
    return max(0, n - rank)
