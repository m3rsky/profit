```markdown
---
name: Firma Kubiak
colors:
  primary: "#1a5276"
  secondary: "#1a6e8a"
  tertiary: "#c0392b"
  neutral: "#f5f5f5"
  error: "#c0392b"
  success: "#27ae60"
typography:
  h1:
    fontFamily: sans-serif
    fontSize: 2.5rem
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: "-0.01em"
  body-md:
    fontFamily: sans-serif
    fontSize: 1rem
    fontWeight: 400
    lineHeight: 1.6
  label-caps:
    fontFamily: sans-serif
    fontSize: 0.75rem
rounded:
  sm: 2px
  md: 4px
  lg: 8px
spacing:
  sm: 8px
  md: 16px
  lg: 32px
---

## Overview

Firma Kubiak is a Polish industrial manufacturer of electrical enclosures and distribution boards, presenting a professional, trustworthy B2B identity rooted in technical competence. The design emphasises clarity, heritage (since 1995), and product confidence through a structured, no-frills layout with a cool navy-teal palette and bold numeric statistics.

## Colors

- **Primary (`#1a5276`)**: Deep navy blue — used for headings, navigation active states, icon outlines, and key statistic numbers. Communicates industrial reliability and technical authority.
- **Secondary (`#1a6e8a`)**: Mid teal-blue — used for body link text, icon illustration strokes, and supporting text accents. Provides visual cohesion between informational elements.
- **Tertiary (`#c0392b`)**: Red — used sparingly for the "Polski Produkt" badge accent and any error/alert states. Signals national pride and urgency.
- **Neutral (`#f5f5f5`)**: Light grey — the dominant background for the hero statistics section and alternating content panels. Keeps the interface airy and content-focused.
- **Error (`#c0392b`)**: Matches tertiary red; used for validation feedback and critical notices.
- **Success (`#27ae60`)**: Not prominently visible in the viewport but inferred for form confirmations and positive states.

## Typography

The type system is clean and functional, relying on a system sans-serif stack throughout. No custom webfonts were declared in the scraped content.

- **H1**: Large, bold hero headings (e.g. "Doświadczenie od 1995 roku") set at approximately 2.5rem with tight line-height. Used for slide titles and primary section calls-to-action.
- **Body-md**: Standard paragraph and navigation text at 1rem/1.6 line-height. Used for descriptive copy, menu items, and sub-labels.
- **Label-caps**: Small-caps style uppercase labels at ~0.75rem, used for statistic descriptors below numeric figures (e.g. "ROK ROZPOCZĘCIA DZIAŁALNOŚCI", "LICZBA PRACOWNIKÓW"). These provide scannable data context in the statistics grid.

## Layout

The layout follows a conventional full-width corporate structure:

- **Grid**: The hero section uses a two-column split — left for text/CTA, right for a 3×2 icon statistics grid. Below the fold, product cards are displayed in a 4-column carousel/slider row.
- **Spacing**: Generous vertical rhythm with approximately 32px between major sections and 16px internal padding within cards and stat blocks. Icon grid cells maintain ~16px gutters.
- **Border radius**: Minimal rounding throughout — buttons use ~4px radius, cards are nearly square-cornered (2–4px). The "Kreator" CTA button has a visible rectangular border with ~4px radius.
- **Philosophy**: Functional over decorative. The layout prioritises product scannability and navigation clarity suited to a B2B industrial audience. Content density is moderate; white space is used strategically to let numerical stats breathe.

## Components

### Navigation Bar
A sticky top navigation with the Kubiak logo on the left, horizontal menu links centred (with dropdown indicators for "Oferta" and "Realizacje"), and utility elements on the far right (language flags, search, cart badge, Polski Produkt seal, Kreator CTA). The active link "Start" is highlighted in the primary navy colour; other links are in dark grey. Dropdowns are multi-level.

### Hero Carousel / Slider
Full-width banner with left-side text (H1 + body copy + ghost button) and right-side icon statistics grid. Carousel dots are visible at the bottom. Navigation arrows (chevrons) flank the sides. Statistics icons use thin line-art style in the primary teal-navy palette.

### Statistics Grid
A 3×2 grid of icon + number + label units displayed on the neutral background. Numbers are bold, large (~2rem), and rendered in the primary navy. Labels are uppercase, tracking-wide, small caps at ~0.75rem. Icons are consistent teal outlines.

### "Zobacz więcej" (CTA) Button
Outlined ghost button with a rectangular border, ~4px radius, dark text, and no fill. On hover it likely inverts. Used consistently as a secondary action throughout the page.

### "Kreator" Button
Outlined button in the header with a visible border and label "Kreator" — serves as a primary tool shortcut. Slightly bolder than the ghost CTA buttons.

### Product Card Carousel
Horizontal scrolling strip of product thumbnail cards. Each card shows a photo with a bold label below. Cards have minimal borders and slight shadow on hover. Labels are bold, sentence-case.

### Cart / Query Badge
Top-right header badge displaying "W zapytaniu: 0, suma: 0 zł" — styled as a dark teal pill/button for high visibility, serving the B2B RFQ workflow.

### Cookie Banner
Full-width bottom-fixed banner in light grey with inline text, a link to the privacy policy, and a "Rozumiem" (Understood) accept button styled as a filled secondary button.

### Social Media Side Panel
Fixed right-side vertical strip with circular icon buttons for Facebook, Instagram, and Google Maps. These are always-visible floating anchors.
```