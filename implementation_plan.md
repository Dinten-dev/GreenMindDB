# GreenMind SEO Optimization Implementation Plan

This plan details the implementation of the requested SEO strategy, transforming the Next.js frontend into a high-authority, optimized, and low-carbon platform.

## User Review Required
> [!IMPORTANT]
> - By switching to **Dark Mode by default** (#000000), the entire aesthetic of the dashboard will change from "Light Liquid Glassmorphism" to a deep, dark tech aesthetic.
> - We will use "Galaxyadvisors AG" as the organization for the Schema.org LocalBusiness markup. 
> - Are you okay with adding placeholder blog posts for the 5 headlines, or would you like me to focus solely on the technical architecture so you can fill them in later?

## Proposed Changes

---

### Core Structure & Local SEO
#### [MODIFY] [layout.tsx](file:///Users/traver/Library/Mobile%20Documents/com~apple~CloudDocs/FHNW/Projekte/GreenMind.nosync/GreenMindDB/frontend/src/app/layout.tsx)
- Embed the `<script type="application/ld+json">` with `Organization` and local Switzerland address data to boost Swiss Domain Authority.
- Integrate a new `Footer` component.

#### [NEW] [Footer.tsx](file:///Users/traver/Library/Mobile%20Documents/com~apple~CloudDocs/FHNW/Projekte/GreenMind.nosync/GreenMindDB/frontend/src/components/Footer.tsx)
- Create a global footer.
- Add the "Hetzner Green Hosting" Badge (Low Carbon Webdesign / Trust).
- Add links to the new Blog and Methodology pages.

---

### UI & Technical Audit (Low Carbon)
#### [MODIFY] [globals.css](file:///Users/traver/Library/Mobile%20Documents/com~apple~CloudDocs/FHNW/Projekte/GreenMind.nosync/GreenMindDB/frontend/src/app/globals.css)
- **Dark Mode by Default:** Swap base HIG tokens (`--color-bg`, `--color-text-primary`) to dark variants (`#000000` background).
- Update the Dashboard "Light Liquid Glassmorphism" tags to Dark surfaces (`rgba(20,20,20, 0.72)`) for energy efficiency on OLED screens.
- Enforce CSS-level aspect-ratios to prevent Cumulative Layout Shifts (CLS).

---

### E-E-A-T & Content Clusters
#### [NEW] [methodology/page.tsx](file:///Users/traver/Library/Mobile%20Documents/com~apple~CloudDocs/FHNW/Projekte/GreenMind.nosync/GreenMindDB/frontend/src/app/methodology/page.tsx)
- Dedicated "Wie wir messen" page explaining the ESP32 to Cloud pipeline. Builds immense algorithmic Trust (Expertise).

#### [NEW] [blog/page.tsx](file:///Users/traver/Library/Mobile%20Documents/com~apple~CloudDocs/FHNW/Projekte/GreenMind.nosync/GreenMindDB/frontend/src/app/blog/page.tsx)
- Blog index aggregating the localized Swiss / Green-mind content clusters.

#### [NEW] [blog/[slug]/page.tsx](file:///Users/traver/Library/Mobile%20Documents/com~apple~CloudDocs/FHNW/Projekte/GreenMind.nosync/GreenMindDB/frontend/src/app/blog/[slug]/page.tsx)
- The blog post template including an integrated **Author Box** (Trust & Experience) and fast loading structure (LCP optimization).
- Populate the first dummy post: *"Was wir hörten, als wir unseren Tomaten in der Schweiz zuhörten"*.

## Open Questions
- Do you want to supply an exact physical address for the FHNW/Galaxyadvisors campus for the Local SEO snippet, or should I use a generic Switzerland placeholder?

## Verification Plan
### Automated Tests
- Run `npm run build` locally in the frontend folder to ensure Next.js static generation succeeds.
- Check for styling and routing compilation errors.
### Manual Verification
- After approval, I will deploy the changes to your Hetzner Production server via the `deploy_production.sh` script to verify the new Dark Mode, the Footer badge, and the Blog routes live.
