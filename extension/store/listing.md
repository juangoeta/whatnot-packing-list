# Chrome Web Store listing

Everything the Developer Dashboard asks for, ready to paste. Images in this folder:
`screenshot-1280x800.png` (store screenshot, a mock of the Shipments page with placeholder buyers; replace with a
real screenshot only after blurring buyer names) and `tile-440x280.png` (small promo tile). The 128 px icon is
`extension/icons/icon128.png`.

Build the upload: from the repo root, `zip -r whatnot-packing-extension-<version>.zip extension -x '*/store/*'`
(the version is in `extension/manifest.json`).

## TODO (Juan)
- [ ] Register the developer account ($5 once) at https://chrome.google.com/webstore/devconsole
- [ ] Decide the public service address sellers will use (currently the Tailscale Funnel on this Mac). It goes in
      the "Setup" paragraph below and in the privacy policy's "sent to" sentence.
- [ ] Read and approve the texts below before pasting them; change the support email if needed.
- [ ] Host the privacy policy text at a public URL (a GitHub Pages or landing-page URL works; the store requires a link).

## Name
Whatnot Packing List Sender

## Summary (132 characters max)
One click on your Whatnot Shipments page sends the show link and order list to your packing-list service.

## Description
Turn a Whatnot live show into a packing list without screenshots or DevTools.

Open Seller Hub > Shipments, pick the show, click "Send show for packing list". The extension reads the
orders on the page (buyer, order number, listing, price, package, receipt timestamp), the package list
(tracking number, carrier) and the link to the show recording, and sends them to the Who Got What
packing-list service. The service builds the list; nothing large is downloaded to your computer.

What it checks before sending
- The page is filtered to one show and to Unfulfilled orders (it tells you if not).
- Every order the packages should hold is on the page; it never sends a short list.
- Shows with more than 250 packages are read page by page.

Setup
1. Install; the welcome page opens.
2. Paste the access code you were given and click Save.

Permissions
- whatnot.com: to add the button to the Shipments page and read the orders shown there.
- The Who Got What service address: where the order list is sent.
- storage: to remember the access code.
- A different service address (Advanced, self-hosted receivers only): requested only when you save one.

This extension is not affiliated with Whatnot.

## Category
Productivity (or Shopping)

## Language
English

## Privacy practices (Developer Dashboard, "Privacy" tab)

Single purpose: send the order list and recording link of one Whatnot show from the Shipments page to the
Who Got What packing-list service.

Permission justifications
- `storage`: stores the access code the user types in Settings (and an override address, if any).
- Host `https://www.whatnot.com/*`: injects the "Send show for packing list" card into the Shipments page and
  reads the orders and packages shown there through the page's own data request.
- Host `https://your-receiver.example.com/*`: the Who Got What service the order list is posted to.
- Optional host `https://*/*` (plus localhost for testing): a self-hosted receiver can be at any address. The
  extension requests exactly the origin of the address saved under Advanced, at the moment it is saved, and
  drops it when the address changes or is cleared.
- Remote code: none. All code ships in the package.

Data usage disclosure (check these boxes)
- Personally identifiable information: yes (buyer usernames and order numbers of the seller's own orders).
- Website content: yes (the order table of the seller's Shipments page).
- Not sold to third parties, not used for purposes unrelated to the single purpose, not used for
  creditworthiness or lending.

## Privacy policy text (host at a public URL and link it in the listing)

Whatnot Packing List Sender sends order data only to the Who Got What service (or to an address you set yourself).

What it reads: on your own Whatnot Seller Hub Shipments page, the orders of the selected show (buyer
username, order number, listing title, price, package id, receipt timestamp), the package list (tracking
number, carrier) and the signed link to the show recording.

Where it goes: only to the Who Got What service (or the one address you typed under Advanced in Settings),
together with the access code you typed, and only when you click "Send show for packing list". The
extension never contacts any other server, never reads other pages, and never sends anything on its own.

What it stores: the access code (and an override address, if you set one), in Chrome's extension storage (synced with your
Chrome profile if sync is on). Nothing else is stored.

What the service does with the data depends on who runs it; the extension's author runs one at
TODO(Juan): public service address, which keeps the order list and the show recording for as long as the
packing list is being prepared and uses them for nothing else.

Contact: TODO(Juan): support email.
