#!/usr/bin/env python3
"""
Hand-specified SVG generator for the CloudServe support pipeline diagram.

Every coordinate is chosen by hand to match src/pipeline.py and
src/route/router.py exactly (this is arithmetic scaffolding for grid
alignment, not an auto-layout library -- no graphviz/mermaid). Layout
deliberately keeps the "escalate" exits as short, parallel, non-crossing
arrows into a single collector box on the left, so the diagram reads
left-to-right for the happy path and top-to-bottom for the one branch
that matters most for the evaluation write-up (the forced-fallback path).

Run: python3 build_pipeline_diagram.py
Produces: pipeline_diagram.svg (in this directory)
"""

CHARCOAL = "#1f2430"
BOX_FILL = "#f5f6f8"
MUTED = "#5b6270"
ALERT_STROKE = "#b3261e"
ALERT_FILL = "#fdeceb"
SUCCESS_STROKE = "#1f7a4d"
SUCCESS_FILL = "#eaf7ef"

parts = []


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def box(x, y, w, h, title, sub=None, tag=None, stroke=CHARCOAL, fill=BOX_FILL,
        title_size=14, sub_size=10.5, sub2=None):
    r = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="7" '
         f'fill="{fill}" stroke="{stroke}" stroke-width="1.8"/>']
    cx = x + w / 2
    if sub:
        ty0 = y + h/2 - (14 if not sub2 else 20)
        r.append(f'<text x="{cx}" y="{ty0}" text-anchor="middle" '
                  f'font-family="Helvetica,Arial,sans-serif" font-weight="700" '
                  f'font-size="{title_size}" fill="{CHARCOAL}">{esc(title)}</text>')
        r.append(f'<text x="{cx}" y="{ty0 + 17}" text-anchor="middle" '
                  f'font-family="Helvetica,Arial,sans-serif" font-size="{sub_size}" '
                  f'fill="{MUTED}">{esc(sub)}</text>')
        if sub2:
            r.append(f'<text x="{cx}" y="{ty0 + 32}" text-anchor="middle" '
                      f'font-family="Helvetica,Arial,sans-serif" font-size="{sub_size}" '
                      f'fill="{MUTED}">{esc(sub2)}</text>')
    else:
        r.append(f'<text x="{cx}" y="{y + h/2 + 5}" text-anchor="middle" '
                  f'font-family="Helvetica,Arial,sans-serif" font-weight="700" '
                  f'font-size="{title_size}" fill="{CHARCOAL}">{esc(title)}</text>')
    if tag:
        tw = 7.2 * len(tag) + 16
        tx = x + w - tw - 6
        ty = y - 11
        r.append(f'<rect x="{tx}" y="{ty}" width="{tw}" height="18" rx="9" fill="{CHARCOAL}"/>')
        r.append(f'<text x="{tx + tw/2}" y="{ty + 13}" text-anchor="middle" '
                  f'font-family="Helvetica,Arial,sans-serif" font-size="10" '
                  f'fill="white">{esc(tag)}</text>')
    return "\n".join(r)


def diamond(cx, cy, w, h, lines, stroke=CHARCOAL, fill=BOX_FILL, size=12):
    pts = f"{cx},{cy-h/2} {cx+w/2},{cy} {cx},{cy+h/2} {cx-w/2},{cy}"
    r = [f'<polygon points="{pts}" fill="{fill}" stroke="{stroke}" stroke-width="1.8"/>']
    start_y = cy - (len(lines) - 1) * 8
    for i, ln in enumerate(lines):
        r.append(f'<text x="{cx}" y="{start_y + i*16 + 4}" text-anchor="middle" '
                  f'font-family="Helvetica,Arial,sans-serif" font-weight="600" font-size="{size}" '
                  f'fill="{CHARCOAL}">{esc(ln)}</text>')
    return "\n".join(r)


def hline(x1, y, x2, label=None, stroke=CHARCOAL, marker="arrow", label_above=True,
          label_size=10.5, sw=1.6):
    r = [f'<line x1="{x1}" y1="{y}" x2="{x2}" y2="{y}" stroke="{stroke}" stroke-width="{sw}" '
         f'marker-end="url(#{marker})"/>']
    if label:
        ly = y - 7 if label_above else y + 16
        r.append(f'<text x="{(x1+x2)/2}" y="{ly}" text-anchor="middle" '
                  f'font-family="Helvetica,Arial,sans-serif" font-size="{label_size}" '
                  f'fill="{stroke if stroke!=CHARCOAL else MUTED}">{esc(label)}</text>')
    return "\n".join(r)


def vline(x, y1, y2, label=None, stroke=CHARCOAL, marker="arrow", dash=None,
          label_size=10.5, sw=1.6, label_dx=14):
    d_attr = f' stroke-dasharray="{dash}"' if dash else ""
    r = [f'<line x1="{x}" y1="{y1}" x2="{x}" y2="{y2}" stroke="{stroke}" stroke-width="{sw}"'
         f'{d_attr} marker-end="url(#{marker})"/>']
    if label:
        r.append(f'<text x="{x+label_dx}" y="{(y1+y2)/2+4}" text-anchor="start" '
                  f'font-family="Helvetica,Arial,sans-serif" font-size="{label_size}" '
                  f'fill="{MUTED}">{esc(label)}</text>')
    return "\n".join(r)


def curve(x1, y1, x2, y2, c1, c2, label=None, stroke=CHARCOAL, marker="arrow",
          label_pos=0.5, label_dx=0, label_dy=-8, label_size=10.5, sw=1.6):
    path = f'M {x1} {y1} C {c1[0]} {c1[1]}, {c2[0]} {c2[1]}, {x2} {y2}'
    r = [f'<path d="{path}" fill="none" stroke="{stroke}" stroke-width="{sw}" '
         f'marker-end="url(#{marker})"/>']
    if label:
        mx = x1 + (x2 - x1) * label_pos + label_dx
        my = y1 + (y2 - y1) * label_pos + label_dy
        for i, ln in enumerate(label.split("\n")):
            r.append(f'<text x="{mx}" y="{my + i*12}" text-anchor="middle" '
                      f'font-family="Helvetica,Arial,sans-serif" font-size="{label_size}" '
                      f'fill="{stroke if stroke!=CHARCOAL else MUTED}">{esc(ln)}</text>')
    return "\n".join(r)


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
W, H = 1660, 1010

# Row 1: ingest -> classify -> retrieve, ending centered above the cascade
R1_Y, R1_H = 50, 68
TICKET   = (20,  R1_Y, 150, R1_H)
INGEST   = (210, R1_Y, 160, R1_H)
CLASSIFY = (410, R1_Y, 170, R1_H)
RETRIEVE = (620, R1_Y, 170, R1_H)   # center x = 705, aligns with cascade below

CASCADE_CX = 705
CH_W, CH_H = 300, 96
C1_Y, C2_Y, C3_Y, C4_Y = 235, 400, 565, 730
GAP_TOP = C1_Y - CH_H/2      # 187
GAP_BOT = C4_Y + CH_H/2      # 778

# Manifold (ESCALATE collector) sits to the LEFT of the cascade, same
# vertical extent, so all four "escalate" exits are short parallel
# horizontal arrows -- nothing crosses the continue-chain or the
# auto-respond exit on the right.
MANIFOLD = (60, GAP_TOP, 260, GAP_BOT - GAP_TOP)
HUMAN = (60, GAP_BOT + 60, 260, 90)

# Row 2 (auto-respond continuation), aligned with C4's centerline so the
# "continue" exit is one short straight arrow.
R2_H = 70
GENERATE = (1120, C4_Y - R2_H/2, 150, R2_H)
VALIDATE = (1320, C4_Y - R2_H/2, 150, R2_H)
SENT     = (1520, C4_Y - R2_H/2, 100, R2_H)

parts.append(f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" '
             f'role="img" aria-label="CloudServe support pipeline: a ticket moves through '
             f'ingest, classify and retrieve, then four routing checks that either send it to '
             f'a human agent (provider unavailable, an always-escalate intent, no grounding '
             f'passage, or low confidence) or auto-respond through generate and validate; '
             f'every stage writes to the decision log, and the provider-unavailable check is '
             f'marked as the branch that fired for 32 percent of the live evaluation run.">')

parts.append(f'''<defs>
  <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
    <path d="M 0 0 L 10 5 L 0 10 z" fill="{CHARCOAL}"/>
  </marker>
  <marker id="arrow-alert" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
    <path d="M 0 0 L 10 5 L 0 10 z" fill="{ALERT_STROKE}"/>
  </marker>
  <marker id="arrow-muted" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
    <path d="M 0 0 L 10 5 L 0 10 z" fill="{MUTED}"/>
  </marker>
</defs>''')

parts.append(f'<rect x="0" y="0" width="{W}" height="{H}" fill="white"/>')
parts.append(f'<text x="20" y="26" font-family="Helvetica,Arial,sans-serif" font-weight="700" '
             f'font-size="16" fill="{CHARCOAL}">CloudServe support pipeline: request flow and the routing decision</text>')

# --- Row 1 boxes ---
parts.append(box(*TICKET, "TICKET", "4 channels, normalized on entry"))
parts.append(box(*INGEST, "INGEST", "src/ingest/ingest.py"))
parts.append(box(*CLASSIFY, "CLASSIFY", "22 intents + confidence", tag="retry x3 (A11)"))
parts.append(box(*RETRIEVE, "RETRIEVE", "29-doc corpus, ranked passages"))

def right_mid(b): return b[0] + b[2], b[1] + b[3]/2
def left_mid(b): return b[0], b[1] + b[3]/2
def bottom_mid(b): return b[0] + b[2]/2, b[1] + b[3]
def top_mid(b): return b[0] + b[2]/2, b[1]

x, y = right_mid(TICKET); parts.append(hline(x, y, INGEST[0]))
x, y = right_mid(INGEST); parts.append(hline(x, y, CLASSIFY[0]))
x, y = right_mid(CLASSIFY); parts.append(hline(x, y, RETRIEVE[0], label="intent + confidence"))

# Retrieve -> down into cascade top
rx, ry = bottom_mid(RETRIEVE)
parts.append(vline(rx, ry, C1_Y - CH_H/2, label=None))
parts.append(f'<text x="{rx-16}" y="{ry+22}" text-anchor="end" '
             f'font-family="Helvetica,Arial,sans-serif" font-size="10.5" fill="{MUTED}">ranked passages,</text>')
parts.append(f'<text x="{rx-16}" y="{ry+36}" text-anchor="end" '
             f'font-family="Helvetica,Arial,sans-serif" font-size="10.5" fill="{MUTED}">or none</text>')

parts.append(f'<text x="30" y="{C1_Y - CH_H/2 - 14}" text-anchor="start" '
             f'font-family="Helvetica,Arial,sans-serif" font-weight="700" font-size="11.5" '
             f'fill="{MUTED}">ROUTE (src/route/router.py) -- deterministic, every branch logged</text>')

# --- Cascade diamonds ---
parts.append(diamond(CASCADE_CX, C1_Y, CH_W, CH_H,
                      ["Provider unavailable or", "response unparseable?"],
                      stroke=ALERT_STROKE, fill=ALERT_FILL))
parts.append(diamond(CASCADE_CX, C2_Y, CH_W, CH_H,
                      ["Always-escalate intent?"]))
parts.append(f'<text x="{CASCADE_CX}" y="{C2_Y + CH_H/2 + 20}" text-anchor="middle" '
             f'font-family="Helvetica,Arial,sans-serif" font-size="9.5" fill="{MUTED}">'
             f'compliance / security_incident / feature_request / unclear_request</text>')
parts.append(diamond(CASCADE_CX, C3_Y, CH_W, CH_H,
                      ["No passage above the 0.35", "relevance threshold?"]))
parts.append(diamond(CASCADE_CX, C4_Y, CH_W, CH_H,
                      ["Confidence below", "CONFIDENCE_THRESHOLD (0.80)?"]))

# continue-chain (top to bottom, straight line down the shared centerline)
parts.append(vline(CASCADE_CX, C1_Y + CH_H/2, C2_Y - CH_H/2, label="no"))
parts.append(vline(CASCADE_CX, C2_Y + CH_H/2, C3_Y - CH_H/2, label="no"))
parts.append(vline(CASCADE_CX, C3_Y + CH_H/2, C4_Y - CH_H/2, label="no (passage found)"))

# --- Manifold (ESCALATE collector) ---
parts.append(box(*MANIFOLD, "ESCALATE", "forced or low-confidence",
                  sub2="-> human agent, with the draft context (FR-11)"))

# escalate exits: short parallel horizontal arrows from each diamond's
# left vertex into the manifold's right edge, at that diamond's own height
manifold_right = MANIFOLD[0] + MANIFOLD[2]
for cy, lbl, stroke, marker in [
    (C1_Y, "yes -- provider/parse failure", ALERT_STROKE, "arrow-alert"),
    (C2_Y, "yes -- always-escalate class", CHARCOAL, "arrow"),
    (C3_Y, "yes -- nothing to ground in", CHARCOAL, "arrow"),
    (C4_Y, "yes -- below threshold", CHARCOAL, "arrow"),
]:
    parts.append(hline(CASCADE_CX - CH_W/2, cy, manifold_right, label=lbl, stroke=stroke, marker=marker))

# manifold -> human agent
mx, my = bottom_mid(MANIFOLD)
parts.append(vline(mx, my, HUMAN[1]))
parts.append(box(*HUMAN, "HUMAN AGENT", "Tier 2 queue", sub2="summary + sources + confidence gap"))

# c4 "no" (confidence sufficient) -> AUTO_RESPOND -> GENERATE (short, same centerline)
c4_right = CASCADE_CX + CH_W/2
gx, gy = left_mid(GENERATE)
parts.append(hline(c4_right, C4_Y, gx, label="no -- AUTO_RESPOND", label_above=True))

# Row 2: generate -> validate -> sent
parts.append(box(*GENERATE, "GENERATE", "grounded answer + citations", tag="retry x3 (A11)"))
parts.append(box(*VALIDATE, "VALIDATE", "5 blocking guardrails", tag="A7"))
parts.append(box(*SENT, "SENT", "to customer", stroke=SUCCESS_STROKE, fill=SUCCESS_FILL, title_size=13))

x, y = right_mid(GENERATE); parts.append(hline(x, y, VALIDATE[0], label="draft + citations"))
x, y = right_mid(VALIDATE); parts.append(hline(x, y, SENT[0], label="no guardrail hit"))

# VALIDATE blocked -> down, then left along the bottom gutter into HUMAN AGENT
vx, vy = bottom_mid(VALIDATE)
gutter_y = H - 70
parts.append(curve(vx, vy, HUMAN[0] + HUMAN[2], HUMAN[1] + HUMAN[3]/2,
                    c1=(vx, gutter_y), c2=(HUMAN[0] + HUMAN[2] + 40, gutter_y),
                    label="guardrail blocked -> escalate", label_pos=0.62, label_dy=14))

# --- Quota-exhaustion annotation, pointing at C1 ---
ann_x, ann_y, ann_w, ann_h = 900, C1_Y - 150, 420, 92
parts.append(f'<rect x="{ann_x}" y="{ann_y}" width="{ann_w}" height="{ann_h}" rx="6" '
             f'fill="{ALERT_FILL}" stroke="{ALERT_STROKE}" stroke-width="1.4"/>')
parts.append(f'<text x="{ann_x+14}" y="{ann_y+22}" font-family="Helvetica,Arial,sans-serif" '
             f'font-weight="700" font-size="12" fill="{ALERT_STROKE}">The branch that fired for 32.2% of the live run</text>')
parts.append(f'<text x="{ann_x+14}" y="{ann_y+41}" font-family="Helvetica,Arial,sans-serif" '
             f'font-size="11.5" fill="{ALERT_STROKE}">161 of 500 tickets forced through here -- Groq\'s</text>')
parts.append(f'<text x="{ann_x+14}" y="{ann_y+58}" font-family="Helvetica,Arial,sans-serif" '
             f'font-size="11.5" fill="{ALERT_STROKE}">daily quota was exhausted partway through the run,</text>')
parts.append(f'<text x="{ann_x+14}" y="{ann_y+75}" font-family="Helvetica,Arial,sans-serif" '
             f'font-size="11.5" fill="{ALERT_STROKE}">not a defect in the routing logic itself.</text>')
parts.append(curve(ann_x, ann_y + ann_h - 10, CASCADE_CX + 40, C1_Y - CH_H/2 - 2,
                    c1=(ann_x - 90, ann_y + ann_h + 20), c2=(CASCADE_CX + 120, C1_Y - CH_H/2 - 60),
                    stroke=ALERT_STROKE, marker="arrow-alert"))

# --- Decision log bar (cross-cutting), bottom ---
dl_y = H - 40
parts.append(f'<rect x="20" y="{dl_y}" width="{W-40}" height="30" rx="5" '
             f'fill="#eef0f3" stroke="{CHARCOAL}" stroke-width="1.2"/>')
parts.append(f'<text x="{W/2}" y="{dl_y+20}" text-anchor="middle" '
             f'font-family="Helvetica,Arial,sans-serif" font-size="12" fill="{CHARCOAL}">'
             f'decision_log.db -- one row per classify / retrieve / route / generate / validate decision, every run (FR-10)</text>')

for b in [CLASSIFY, RETRIEVE, GENERATE, VALIDATE]:
    cx = b[0] + b[2] / 2
    by = b[1] + b[3]
    parts.append(f'<line x1="{cx}" y1="{by}" x2="{cx}" y2="{dl_y}" stroke="{MUTED}" '
                 f'stroke-width="1" stroke-dasharray="3,3"/>')
# routing decision also logs; tick from the manifold's own bottom-log line, offset
# slightly right of the human-agent arrow so the two don't overlap
route_tick_x = MANIFOLD[0] + MANIFOLD[2]/2 + 40
parts.append(f'<line x1="{route_tick_x}" y1="{HUMAN[1]+HUMAN[3]}" x2="{route_tick_x}" y2="{dl_y}" '
             f'stroke="{MUTED}" stroke-width="1" stroke-dasharray="3,3"/>')

parts.append('</svg>')

svg = "\n".join(parts)
with open("pipeline_diagram.svg", "w") as f:
    f.write(svg)
print("wrote pipeline_diagram.svg,", len(svg), "bytes")
