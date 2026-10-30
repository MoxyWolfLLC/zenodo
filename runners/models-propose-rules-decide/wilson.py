"""Whole-count examples for the Section 9 acceptance gate: a level clears a bar when the one-sided 95% Wilson
lower bound of k correct out of n automatic writes reaches it. Run: python3 wilson.py [bar]  (default 0.90)."""
import math
import sys

Z = 1.6448536  # one-sided 95%


def lower(k, n, z=Z):
    p = k / n
    return (p + z * z / (2 * n) - z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / (1 + z * z / n)


def smallest(p, bar):
    """Smallest n, with k = round(p * n) whole successes within 0.002 of p, whose bound clears the bar."""
    return next((round(p * n), n) for n in range(1, 100000)
                if abs(round(p * n) / n - p) < 0.002 and lower(round(p * n), n) >= bar)


if __name__ == "__main__":
    bar = float(sys.argv[1]) if len(sys.argv) > 1 else 0.90
    for p in (1.00, 0.95, 0.93):
        k, n = smallest(p, bar)
        print(f"observed about {p:.2f}: {k} of {n} -> bound {lower(k, n):.4f}; "
              f"{k - 1} of {n} -> {lower(k - 1, n):.4f} (bar {bar})")
    assert smallest(1.0, 0.90) == (25, 25) and smallest(0.95, 0.90) == (95, 100) and smallest(0.93, 0.90) == (231, 248)
