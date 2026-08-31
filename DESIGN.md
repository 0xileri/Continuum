---
version: 1
name: Continuum-design-system
description: A credit-rating interface that reads as an instrument panel rather than an institution. Cool near-black ground (`#0A0F13`) with a single teal voltage — **Trace** (`#6FD3C7`) — spent exclusively on the score line, its confidence band, and interactive chrome. The defining rule is that **colour is data**: a warm ramp from muted ochre through clay to vermilion encodes borrower severity, saturation rising with badness, and is never used decoratively. Three type roles in deliberate tension — Archivo grotesque for headings (instrument labelling), Newsreader serif for explanatory prose (the register of the rating agencies this product argues with), IBM Plex Mono for every score, interval, hash and address. Panels are hairline-bordered with a soft shadow, section headings are small wide-tracked mono caps, and digits are tabular everywhere they align. Dark-only by commitment, not omission.

colors:
  ground: "#0A0F13"
  surface: "#121A20"
  surface-raised: "#18222A"
  hairline: "#24313A"
  hairline-soft: "#1B252C"
  text: "#E4EDF0"
  muted: "#93A5AE"
  dim: "#697C86"
  trace: "#6FD3C7"
  trace-soft: "rgba(111, 211, 199, 0.13)"
  trace-line: "rgba(111, 211, 199, 0.38)"
  band: "rgba(111, 211, 199, 0.26)"
  severity-strong: "#5FC9A8"
  severity-good: "#6FD3C7"
  severity-watch: "#C9A46A"
  severity-weak: "#D0805A"
  severity-bad: "#E2603F"
  severity-neutral: "#7C8D96"

typography:
  display-lg:
    fontFamily: "Archivo, -apple-system, 'Segoe UI', Roboto, sans-serif"
    fontSize: 25px
    fontWeight: 600
    lineHeight: 1.1
    letterSpacing: -0.75px
  display-md:
    fontFamily: "Archivo, sans-serif"
    fontSize: 20px
    fontWeight: 600
    lineHeight: 1.1
    letterSpacing: -0.7px
  section-label:
    fontFamily: "'IBM Plex Mono', ui-monospace, monospace"
    fontSize: 10.5px
    fontWeight: 600
    lineHeight: 1.5
    letterSpacing: 1.47px
    textTransform: uppercase
  body:
    fontFamily: "Archivo, sans-serif"
    fontSize: 14px
    fontWeight: 400
    lineHeight: 1.55
  prose:
    fontFamily: "Newsreader, Georgia, 'Times New Roman', serif"
    fontSize: 13.5px
    fontWeight: 300
    lineHeight: 1.55
  data:
    fontFamily: "'IBM Plex Mono', ui-monospace, monospace"
    fontSize: 12px
    fontWeight: 400
    lineHeight: 1.5
    fontVariantNumeric: tabular-nums
  stat-label:
    fontFamily: "'IBM Plex Mono', monospace"
    fontSize: 9.5px
    letterSpacing: 1.24px
    textTransform: uppercase

radii:
  panel: 10px
  control: 7px
  chip: 5px

shadow:
  panel: "0 1px 2px rgba(0,0,0,.45), 0 10px 28px rgba(0,0,0,.28)"
---

# Continuum — DESIGN.md

## 1. Visual Theme & Atmosphere

An **instrument panel**, not an institution. Credit rating agencies look like law firms — navy, serif, gold, authority asserted through gravitas. Continuum's argument is the opposite: that a rating should be a live measurement you can check, not a pronouncement you must accept. The interface has to carry that argument.

So the surface reads as monitoring equipment. A cool near-black ground, hairline-bordered panels, small wide-tracked mono labels, and one luminous teal line doing the actual work. The reference points are an oscilloscope and a seismograph, not a dashboard template.

The atmosphere is **quiet by default and loud only where it must be**. Most of the screen is neutral chrome; the eye is drawn to the trace, the grade chip, and anything coloured warm. That contrast is the whole design — if everything shouts, the severity ramp stops meaning anything.

## 2. Color Palette & Roles

### Ground and chrome

| Token | Hex | Role |
|---|---|---|
| `ground` | `#0A0F13` | Page background. Cool near-black — a pure grey reads as unconsidered beside a teal accent. |
| `surface` | `#121A20` | Panels, cards, table rows |
| `surface-raised` | `#18222A` | Hover states, nested surfaces |
| `hairline` | `#24313A` | Panel borders, table rules |
| `hairline-soft` | `#1B252C` | Internal dividers |
| `text` | `#E4EDF0` | Primary text |
| `muted` | `#93A5AE` | Secondary text, prose |
| `dim` | `#697C86` | Labels, captions, de-emphasised data |

### The single accent

`trace` `#6FD3C7` is the brand voltage and is spent on exactly three things: **the score line**, **the confidence band around it**, and **interactive chrome** (links, focus rings, selected rows, badges that mean "present/verified"). Nothing else may use it. An accent spent everywhere is not an accent.

`band` is `trace` at 26% — the same density as the confidence band in the logo mark, so the identity and the thing it depicts are drawn identically.

### Severity — the load-bearing rule

The warm ramp encodes **borrower severity**, and it is the reason this palette exists:

| Token | Hex | Grades | Meaning |
|---|---|---|---|
| `severity-strong` | `#5FC9A8` | AAA–A | Healthy |
| `severity-good` | `#6FD3C7` | A-–BBB- | Sound |
| `severity-watch` | `#C9A46A` | BB–B | Watch |
| `severity-weak` | `#D0805A` | B-–CCC | Weak |
| `severity-bad` | `#E2603F` | CC–D | Distressed |

**Saturation rises with severity, and this is not an aesthetic preference.** `watch` covers the modal band in a distressed portfolio. When it was a saturated alarm-amber the interface shouted about its most ordinary borrowers and had nothing louder left for the ones actually failing. The loudest colour must mark the worst case, never the most common one.

> **On this product, colour is data.** A reader learns that warm means deteriorating within seconds. Spending these hues on decoration — a warm section divider, an amber icon, an orange button — destroys the only thing the palette is for.

## 3. Typography Rules

Three faces, three jobs, chosen for the tension between them.

**Archivo (600 / 400)** — headings, the wordmark, borrower names, headline numbers. A grotesque with enough width to read as instrument labelling rather than a startup landing page. Display sizes take tight negative tracking (−0.03em); body sits at 400.

**Newsreader (300)** — explanatory prose: panel notes, the trust banner, anything arguing rather than reporting. The serif is deliberate. It borrows the register of the institutions this product is arguing with, and the contrast against the mono data is what makes the page feel considered rather than templated.

**IBM Plex Mono (400 / 500 / 600)** — every score, interval, basis point, hash, address, feed name and enum value. Also every section heading, at 10.5px uppercase with 0.14em tracking.

### Non-negotiables

- `font-variant-numeric: tabular-nums` anywhere digits align in columns. Without it the roster's numbers jitter as values update and the table reads as unstable.
- Section headings are **mono, small, wide-tracked, uppercase, `dim`**. They name a panel and get out of the way — this is a scanned surface, not a read one.
- Running prose stays near 65 characters.
- Raw enum values (`scheduled_daily`, `event_document`) are shown in mono beside their human label, never replaced by it. The engine publishes the enum; hiding it undoes the point of publishing it.

## 4. Component Stylings

**Panel** — `surface` fill, 1px `hairline` border, 10px radius, 18–20px padding, soft shadow. Heading is a mono section label; an optional `note` in Newsreader sits under it.

**Grade chip** — mono 700, 5px radius, 1px 7px padding, filled with the severity tone, text in `ground`. The large variant (26px) leads the borrower detail header.

**Badge** — 1px border, no fill, mono, ~11px. `ok` borders `trace`, `warn` borders `severity-watch`, `on` borders `severity-bad`, `off` borders `hairline` with `dim` text. Badges state facts (`attested`, `held`, `capped`), never decorate.

**Data table** — mono `dim` headers with 0.09em tracking, hairline row rules, numeric columns right-aligned and tabular. Wide tables scroll inside their own container.

**Sparkline** — 62×18, 1.4px stroke, coloured by the borrower's **grade tone**, never by direction of travel. A binary rising/falling colour paints every mild drift full vermilion, which in a declining portfolio means the whole roster reads as critical.

**Score chart** — teal trace over a `band` fill, filled dots for published scores and hollow for held-back ones, dashed grade-band gridlines. Scales via `viewBox`; never scrolls horizontally. A score history is read as a *shape*, and a shape you must scroll to see is not being read.

**Callout** — 1px `hairline` border with a 3px left border carrying the meaning: `trace` for informational, `severity-bad` for errors. Never `severity-watch` — a note true of every row must not render as a warning about one.

## 5. Layout Principles

Two columns on desktop: a fixed **330px** roster beside a fluid detail pane. The roster is sticky and independently scrollable; the detail pane holds stacked panels with a 16px gap.

Panels are ordered by **narrative**, not by data structure: what the score is → why it moved → what evidence supports it → what it costs. A reader should be able to stop at any point and have a complete, if shallower, answer.

Spacing comes from flex/grid `gap`, never per-element margins that silently collapse or double.

## 6. Depth & Elevation

Almost flat. Depth comes from **hairline borders and a single soft shadow**, not from stacked elevations. One shadow token, used on panels only:

```
0 1px 2px rgba(0,0,0,.45), 0 10px 28px rgba(0,0,0,.28)
```

Hover raises `surface` → `surface-raised`. Selection uses `trace-soft` fill with a `trace-line` border. No gradients, no glows, no glass. The only luminous element on the page is the trace itself, and it earns that by being the data.

## 7. Do's and Don'ts

**Do**
- Reserve `trace` for the score line, the band, and interactive chrome
- Let severity colour come from the grade, so a chip and its sparkline can never disagree
- Use tabular numerals wherever digits align
- Show the raw enum beside its human label
- Give every interactive element a visible focus ring — the roster is a list of buttons
- Let wide content scroll inside its own container

**Don't**
- Use severity colours decoratively — they are data, and a warm divider teaches the reader to ignore warm
- Apply the loudest colour to the most common state
- Colour a trend line by direction rather than severity
- Use `severity-watch` for chrome (this was a real bug: the trust banner read as a per-borrower warning)
- Introduce a second accent — if something needs emphasis and cannot use `trace`, it needs hierarchy, not hue
- Add gradients, glows or glass
- Let the page body scroll sideways

## 8. Responsive Behavior

**≤ 900px** — the columns stack. The roster becomes a bounded scrollable strip capped at `42vh`, so borrower switching stays a thumb-reach away without pushing the detail pane below twelve rows. Headline stats lose their `margin-left: auto` and become their own row.

**≤ 560px** — stats become a 2×2 grid rather than an unevenly wrapping flex line. Padding tightens to 12–14px; the large grade chip drops to 22px.

Tables always scroll inside `overflow-x: auto`. Charts scale via `viewBox` with `height: auto`, and their labels scale back up under 700px so the axis stays readable.

## 9. Agent Prompt Guide

When building UI for this project:

> Use the Continuum design system. Dark instrument-panel aesthetic on `#0A0F13`. **Teal `#6FD3C7` is the only accent** — use it for the score trace, its confidence band, and interactive chrome, nothing else. Warm colours (`#C9A46A` ochre → `#D0805A` clay → `#E2603F` vermilion) are **semantic only**: they encode borrower severity, saturation rising with badness, and must never be used decoratively. Archivo for headings, Newsreader for explanatory prose, IBM Plex Mono for all data and for section headings (10.5px uppercase, 0.14em tracking, `#697C86`). Panels: `#121A20` on `#24313A` hairlines, 10px radius, one soft shadow, no gradients. Tabular numerals wherever digits align. Every interactive element gets a visible focus ring.

**The one rule to carry into any change:** on this product colour is data. Before adding a colour, ask what fact it encodes. If the answer is "it looks better", use hierarchy, spacing or weight instead.
