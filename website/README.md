# TruckBuddy website

Static marketing page for TruckBuddy. No build step: open `index.html` in a browser, or serve the folder with any static host (`python3 -m http.server -d website`).

The look comes from the app itself:

- Colors are the tokens in `frontend/src/theme.ts` (ink `#1C1C1E`, route blue `#007AFF`, category colors, Buddy's beige disc `#F7F2EA`). Light only, like the app.
- Type is Plus Jakarta Sans, the app's font, with Space Mono for truck specs.
- The phone in the hero, the pins, the copilot sheet, the driving banner and the truck profile are rebuilt in HTML/CSS from the app's components (`CopilotSheet`, `Pin`, `NavBanner`, `TopBar`, `truck-profile`).
- `assets/buddy-body.webp` and `assets/buddy-eyes.webp` are `frontend/assets/images/avatar.png` split into two layers (eyes removed from the body and kept on their own), so the page can move the eyes toward the cursor and make Buddy blink.

The "Coming soon" store buttons are placeholders until the App Store and Google Play links exist.

## Pages

- `/` (`index.html`): the landing page.
- `/privacy/`: the privacy policy, copied word for word from the app store submission draft (the app's consent screen links `https://truckbuddy.io/privacy`).
- `/support/`: contact email and common questions, for the App Store and Google Play listings.

Each page is a folder with an `index.html`, so `/privacy` and `/support` work on any static host without rewrite rules.
