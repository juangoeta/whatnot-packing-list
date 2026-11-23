# Placeholders in the landing page

Every `{{TOKEN}}` below is a decision Juan has not made yet. They are styled on the page as yellow dashed
chips (and dashed outlines on links) so they cannot ship by accident. Find them all with:

    grep -rn '{{' site/

Replace the token everywhere it appears (both pages), then run the grep again until it prints nothing.

| token | where it appears | what to put there |
|---|---|---|
| `{{PRICE_PER_SHOW}}` | `index.html` pricing card "Per show", FAQ "What does it cost?" | The price of one show credit, e.g. `$15`. |
| `{{PRICE_MONTHLY}}` | `index.html` pricing card "Monthly", FAQ "What does it cost?" | The monthly price, e.g. `$39`. |
| `{{SHOWS_PER_MONTH}}` | `index.html` pricing card "Monthly" (3 places), FAQ "What does it cost?" | How many show credits the monthly plan includes, e.g. `4`. |
| `{{CHECKOUT_URL_SHOW}}` | `index.html` "Buy one show" button `href` | Hosted checkout link for one show credit (Stripe Payment Link or Lemon Squeezy). Nothing card-related on our side. |
| `{{CHECKOUT_URL_MONTHLY}}` | `index.html` "Start monthly" button `href` | Hosted checkout link for the monthly subscription. |
| `{{SITE_DOMAIN}}` | `index.html` footer | The public domain the site is served from, e.g. `whogotwhat.app`. |
| `{{SUPPORT_EMAIL}}` | `index.html` FAQ "wrong order", contact block (text and `mailto:`); `guide/index.html` settings tip and footer | The email sellers write to. Same address as the Chrome Web Store listing's support contact. |
| `{{PRIVACY_URL}}` | `index.html` FAQ "What leaves my computer?", footer | URL of the privacy policy. The text is in `extension/store/listing.md`; host it as `site/privacy.html` or similar and the Web Store listing can point at the same URL. |
| `{{WEBSTORE_URL}}` | `index.html` install step 1; `guide/index.html` step 1 | The Chrome Web Store listing URL once E02's submission is published. |
| `{{UNPACKED_ZIP_URL}}` | `index.html` install step 1 (temporary block); `guide/index.html` step 1 (temporary block) | Where the extension zip is downloadable while the Web Store listing is pending (build it with the `zip` command in README "Publishing"; a GitHub release asset works). Delete both "Temporary" blocks once the Web Store link is live. |
| `{{TURNAROUND_SMALL}}` | `index.html` "How it works" step 3, FAQ "How long"; `guide/index.html` step 3 | Turnaround promise for shows up to about 150 orders, e.g. `the same day`. |
| `{{TURNAROUND_LARGE}}` | same places as above | Turnaround promise for bigger shows, e.g. `by the next morning`. |
| `{{CODE_WITHIN}}` | `index.html` pricing intro; `guide/index.html` settings tip | Hours within which the access code is emailed after a paid order (manual `clients.py add --email` until a webhook exists), e.g. `12`. |

Also not on the page, Juan's call:
- The service address is baked into the extension (0.1.14), so the code email carries the code only. Today it is the Tailscale Funnel address in README; moving to a custom domain or a server means an extension update.
- A refund or redo policy. The FAQ promises a corrected list, nothing about money back.
- The evidence screenshot (`img/evidence-desktop.jpg`, `img/evidence-phone.jpg`) is from the Oct 5 show with buyer names replaced and the host's face pixelated. If Juan has the host's consent, retake it from a show where the product alone is on camera.
