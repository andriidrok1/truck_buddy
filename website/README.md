# Truck Buddy website

Static marketing page for Truck Buddy. No build step: open `index.html` in a browser, or serve the folder with any static host (`python3 -m http.server -d website`).

The look comes from the app itself:

- Colors are the tokens in `frontend/src/theme.ts` (ink `#1C1C1E`, route blue `#007AFF`, category colors, Buddy's beige disc `#F7F2EA`). Light only, like the app.
- Type is Plus Jakarta Sans, the app's font, with Space Mono for truck specs.
- The phone in the hero, the pins, the copilot sheet, the driving banner and the truck profile are rebuilt in HTML/CSS from the app's components (`CopilotSheet`, `Pin`, `NavBanner`, `TopBar`, `truck-profile`).
- `assets/buddy.webp` is `frontend/assets/images/avatar.png`, compressed.

The "Coming soon" store buttons are placeholders until the App Store and Google Play links exist.
