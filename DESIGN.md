---
version: 1
name: Continuum-design-system
description: A credit-rating interface that reads as an instrument panel rather than an institution. **Monochrome by constraint** — every colour on the surface is taken from the logo mark: cool near-black ground (`#0A0F13`), slate hairlines (`#24313A`), and a single teal voltage, **Trace** (`#6FD3C7`). The defining rule is that **colour is data**. Teal carries the healthy end of the severity scale by luminance and weight — a deep outline that recedes — and the bottom two bands break to a warm solid that advances. Warm lands on perhaps two rows of twelve, so it stays an alarm rather than a texture. Three type roles in deliberate tension — Archivo grotesque for headings (instrument labelling), Newsreader serif for explanatory prose (the register of the rating agencies this product argues with), IBM Plex Mono for every score, interval, hash and address. Panels are hairline-bordered with a soft shadow, section headings are small wide-tracked mono caps, and digits are tabular everywhere they align. Dark-only by commitment, not omission.

colors:
  ground: "#0A0F13"
  surface: "#101820"
  surface-raised: "#16212A"
  hairline: "#24313A"
  hairline-soft: "#1A242C"
  text: "#E6EFF1"
  muted: "#8FA5AC"
  dim: "#62767F"
  trace: "#6FD3C7"
  trace-soft: "rgba(111, 211, 199, 0.10)"
  trace-line: "rgba(111, 211, 199, 0.32)"
  band: "rgba(111, 211, 199, 0.24)"
  severity-strong: "#2F6F68"
  severity-good: "#45A79B"
  severity-watch: "#6FD3C7"
  severity-weak: "#D9734E"
  severity-bad: "#E2503A"
  severity-neutral: "#5C7079"

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
  panel: 12px
  control: 8px
  chip: 6px

shadow:
  panel: "0 1px 1px rgba(0,0,0,.5), 0 8px 24px rgba(0,0,0,.24)"
---

# Continuum — DESIGN.md

## 1. Visual Theme & Atmosphere

An **instrument panel**, not an institution. Credit rating agencies look like law firms — navy, serif, gold, authority asserted through gravitas. Continuum's argument is the opposite: that a rating should be a live measurement you can check, not a pronouncement you must accept. The interface has to carry that argument.

So the surface reads as monitoring equipment. A cool near-black ground, hairline-bordered panels, small wide-tracked mono labels, and one luminous teal line doing the actual work. The reference points are an oscilloscope and a seismograph, not a dashboard template.

The atmosphere is **quiet by default and loud only where it must be**. Most of the screen is neutral chrome; the eye is drawn to the trace and to any grade chip bright enough to lift off its panel. That contrast is the whole design — if everything shouts, the severity ramp stops meaning anything.

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

| Token | Hex | Grades | Chip treatment |
|---|---|---|---|
| `severity-strong` | `#2F6F68` | AAA–A | Deep teal outline, recedes |
| `severity-good` | `#45A79B` | A-–BBB- | Teal outline |
| `severity-watch` | `#6FD3C7` | BB–B | Teal, tinted fill |
| `severity-weak` | `#D9734E` | B-–CCC | **Solid clay**, ground-coloured text |
| `severity-bad` | `#E2503A` | CC–D | **Solid vermilion**, bold |

**Two axes, and a deliberate break.** Weight carries the teal range: quiet hairline → outline →
tinted fill. Luminance alone was tried and failed — five steps in one hue put BB-, BBB- and B
within a few percent of each other and the roster stopped being scannable, because hue had been
doing that work.

The bottom two bands then break to warm. A fully monochrome version was built and the cost was
real: red-for-bad is a strong convention in credit, and a failing borrower rendered as *merely
bright* rather than *wrong*. Reserving the hue change for `weak` and `bad` recovers the convention
without diluting the identity — **warm appears on about two rows of twelve, so it means something
when it does.** A palette whose alarm colour is common has no alarm colour.

The vermilion is pulled toward the teal's temperature rather than taken off a stock ramp, so the
two read as one system rather than a brand with a warning bolted on.

**Text on the warm fills is the ground colour, and this is measured rather than assumed:** `#0A0F13`
gives 5.97:1 on `weak` and 4.99:1 on `bad`, past AA for normal text. Light text was tried and fails
— 2.96:1 on `weak`, below even the 3.0 that bold type is allowed.

> **On this product, colour is data.** A reader learns within seconds that teal is ordinary and
> warm is not. Spending either on decoration — a teal divider, a warm icon that means nothing —
> destroys the only thing the palette is for.

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

**Grade chip** — mono 600, 6px radius, 2px 8px padding. Outlined and tinted in teal for `strong` / `good` / `watch`; solid warm with `ground` text for `weak` / `bad`, the two bands a lender has to act on. The large variant leads the borrower detail header.

**Badge** — 1px border, no fill, mono, ~11px. `ok` borders `trace`, `warn` borders `severity-watch`, `on` borders `severity-bad`, `off` borders `hairline` with `dim` text. Badges state facts (`attested`, `held`, `capped`), never decorate.

**Data table** — mono `dim` headers with 0.09em tracking, hairline row rules, numeric columns right-aligned and tabular. Wide tables scroll inside their own container.

**Sparkline** — 62×18, 1.4px stroke, coloured by the borrower's **grade tone**, never by direction of travel. A binary rising/falling colour paints every mild drift full vermilion, which in a declining portfolio means the whole roster reads as critical.

**Score chart** — teal trace over a `band` fill, filled dots for published scores and hollow for held-back ones, dashed grade-band gridlines. Scales via `viewBox`; never scrolls horizontally. A score history is read as a *shape*, and a shape you must scroll to see is not being read.

**Callout** — 1px `hairline` border with a 3px left border carrying the meaning: `trace` for informational, `severity-bad` for errors. Never `severity-watch` — a note true of every row must not render as a warning about one.

## 5. Layout Principles

### Spacing

An 8px scale — `4 / 8 / 12 / 16 / 24 / 32 / 48` — and nothing off it. Panels were previously spaced
on a flat 16px gap regardless of content, so a three-row table and a full explainability trail read
identically and the page became an undifferentiated stack. The gap *between* sections is now larger
than the gap *within* them, which is what makes groups legible as groups.

Content is capped at 1180px. Beyond that the tables stretch into unreadable line lengths and the
page stops feeling composed.

### Structure

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
- Use the accent decoratively — it is data, and a teal divider teaches the reader to ignore teal
- Apply the loudest colour to the most common state
- Colour a trend line by direction rather than severity
- Use `severity-watch` for chrome (this was a real bug: the trust banner read as a per-borrower warning)
- Introduce a third hue. The palette is the mark's teal plus one warm break reserved for the bottom two grades. If something else needs emphasis it needs weight, luminance or space — not colour
- Add gradients, glows or glass
- Let the page body scroll sideways

## 8. Responsive Behavior

**≤ 900px** — the columns stack. The roster becomes a bounded scrollable strip capped at `42vh`, so borrower switching stays a thumb-reach away without pushing the detail pane below twelve rows. Headline stats lose their `margin-left: auto` and become their own row.

**≤ 560px** — stats become a 2×2 grid rather than an unevenly wrapping flex line. Padding tightens to 12–14px; the large grade chip drops to 22px.

Tables always scroll inside `overflow-x: auto`. Charts scale via `viewBox` with `height: auto`, and their labels scale back up under 700px so the axis stays readable.

## 9. Agent Prompt Guide

When building UI for this project:

> Use the Continuum design system. Dark instrument-panel aesthetic on `#0A0F13`. **The palette is the logo mark's — ink `#0A0F13`, slate `#24313A`, teal `#6FD3C7` — plus exactly one warm break (`#D9734E` clay, `#E2503A` vermilion) reserved for the bottom two grade bands. Introduce no other hue.** Severity escalates by weight through the teal range (deep outline → outline → tinted fill) and then by hue for `weak` and `bad`, which are solid with ground-coloured text. Never use either family decoratively. Archivo for headings, Newsreader for explanatory prose, IBM Plex Mono for all data and for section headings (10.5px uppercase, 0.14em tracking, `#697C86`). Panels: `#121A20` on `#24313A` hairlines, 10px radius, one soft shadow, no gradients. Tabular numerals wherever digits align. Every interactive element gets a visible focus ring.

**The one rule to carry into any change:** on this product colour is data. Before adding a colour, ask what fact it encodes. If the answer is "it looks better", use hierarchy, spacing or weight instead.
