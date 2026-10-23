"""Reliability diagram for the README, from results/ft_metrics.json -> docs/reliability.svg. Standard library only.

Mean predicted probability vs observed hallucination rate per 0.1-wide bin, raw scores and Platt-calibrated, against
the diagonal of perfect calibration. Colors are the validated two-slot categorical pair (blue, orange), each with
light and dark steps; the SVG switches on prefers-color-scheme. Hovering a point shows its bin, n and values.
"""

import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
W, H = 640, 440
LEFT, RIGHT, TOP, BOTTOM = 64, 140, 48, 56  # right margin holds the direct labels
PW, PH = W - LEFT - RIGHT, H - TOP - BOTTOM

STYLE = """
.surface { fill: #fcfcfb; } .ink { fill: #0b0b0b; } .ink2 { fill: #52514e; } .muted { fill: #898781; }
.grid { stroke: #e1e0d9; } .axis { stroke: #c3c2b7; } .diag { stroke: #898781; }
.s1 { stroke: #2a78d6; } .s1f { fill: #2a78d6; } .s2 { stroke: #eb6834; } .s2f { fill: #eb6834; }
.ring { stroke: #fcfcfb; }
@media (prefers-color-scheme: dark) {
  .surface { fill: #1a1a19; } .ink { fill: #f0efec; } .ink2 { fill: #c3c2b7; }
  .grid { stroke: #2c2c2a; } .axis { stroke: #383835; }
  .s1 { stroke: #3987e5; } .s1f { fill: #3987e5; } .s2 { stroke: #d95926; } .s2f { fill: #d95926; }
  .ring { stroke: #1a1a19; }
}
text { font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }
"""


def x(v: float) -> float:
    return LEFT + v * PW


def y(v: float) -> float:
    return TOP + (1 - v) * PH


def series(bins: list[dict], cls: str, name: str) -> list[str]:
    pts = [(b["mean_score"], b["hallucinated_rate"], b) for b in bins]
    path = " ".join(f"{'M' if i == 0 else 'L'}{x(p):.1f},{y(r):.1f}" for i, (p, r, _) in enumerate(pts))
    out = [f'<path d="{path}" class="{cls}" fill="none" stroke-width="2" stroke-linejoin="round"/>']
    for p, r, b in pts:
        out.append(f'<g><title>{name}, bin {b["bin"]}: mean score {p:.3f}, observed rate {r:.3f}, n={b["n"]:,}</title>'
                   f'<circle cx="{x(p):.1f}" cy="{y(r):.1f}" r="12" fill="transparent"/>'
                   f'<circle cx="{x(p):.1f}" cy="{y(r):.1f}" r="4.5" class="{cls}f ring" stroke-width="2"/></g>')
    return out


def main() -> None:
    cal = json.loads((ROOT / "results" / "ft_metrics.json").read_text())["calibration_test"]
    raw, platt = cal["raw"], cal["platt_from_val"]
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" '
             f'aria-labelledby="t d">',
             '<title id="t">Reliability diagram, fine-tuned detector on RAGTruth test</title>',
             f'<desc id="d">Observed hallucination rate against mean predicted probability in ten bins. Raw scores '
             f'(ECE {raw["ece"]}) sit below the diagonal at high scores; Platt-calibrated scores (ECE {platt["ece"]}) '
             f'track it closely.</desc>',
             f"<style>{STYLE}</style>", f'<rect width="{W}" height="{H}" class="surface"/>',
             f'<text x="{LEFT}" y="22" class="ink" font-size="15" font-weight="600">Calibration on RAGTruth test '
             f'(18,965 sentences)</text>',
             f'<text x="{LEFT}" y="39" class="ink2" font-size="12">Points above the diagonal: model under-predicts; '
             f'below: over-confident</text>']
    for t in (0, 0.2, 0.4, 0.6, 0.8, 1.0):
        parts += [f'<line x1="{x(0)}" x2="{x(1)}" y1="{y(t):.1f}" y2="{y(t):.1f}" class="grid" stroke-width="1"/>',
                  f'<text x="{x(0) - 8}" y="{y(t) + 4:.1f}" class="muted" font-size="11" text-anchor="end">{t:.1f}</text>',
                  f'<text x="{x(t):.1f}" y="{y(0) + 18}" class="muted" font-size="11" text-anchor="middle">{t:.1f}</text>']
    parts += [f'<line x1="{x(0)}" x2="{x(1)}" y1="{y(0)}" y2="{y(0)}" class="axis" stroke-width="1"/>',
              f'<line x1="{x(0)}" x2="{x(1)}" y1="{y(0)}" y2="{y(1)}" class="diag" stroke-width="1.5" '
              f'stroke-dasharray="4 4"/>',
              f'<text x="{x(0.62):.1f}" y="{y(0.68):.1f}" class="muted" font-size="11" '
              f'transform="rotate(-{math.degrees(math.atan2(PH, PW)):.1f} {x(0.62):.1f} {y(0.68):.1f})">perfect calibration</text>',
              f'<text x="{LEFT + PW / 2}" y="{H - 14}" class="ink2" font-size="12" text-anchor="middle">'
              f'Mean predicted probability (bin)</text>',
              f'<text x="18" y="{TOP + PH / 2}" class="ink2" font-size="12" text-anchor="middle" '
              f'transform="rotate(-90 18 {TOP + PH / 2})">Observed hallucination rate</text>']
    parts += series(raw["bins"], "s1", "Raw score")
    parts += series(platt["bins"], "s2", "Platt-calibrated")
    # legend (always, two series) doubles as the direct labels at the right edge
    for i, (cls, name, ece) in enumerate((("s1", "Raw score", raw["ece"]), ("s2", "Platt-calibrated", platt["ece"]))):
        ly = TOP + 16 + i * 40
        parts += [f'<line x1="{W - RIGHT + 16}" x2="{W - RIGHT + 36}" y1="{ly}" y2="{ly}" class="{cls}" stroke-width="2"/>',
                  f'<circle cx="{W - RIGHT + 26}" cy="{ly}" r="4.5" class="{cls}f ring" stroke-width="2"/>',
                  f'<text x="{W - RIGHT + 42}" y="{ly + 4}" class="ink" font-size="12">{name}</text>',
                  f'<text x="{W - RIGHT + 42}" y="{ly + 20}" class="ink2" font-size="11">ECE {ece:.3f}</text>']
    parts.append("</svg>")
    out = ROOT / "docs" / "reliability.svg"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(parts) + "\n")
    print(f"wrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
