// Settings form, shared by options.html and welcome.html (same element ids). Guarded so both pages also render
// from file:// (design checks) where chrome.storage does not exist.
const $ = id => document.getElementById(id);
const ext = () => { try { return !!(chrome.storage && chrome.storage.sync); } catch (e) { return false; } };

// The service address is baked in (DEFAULT, a declared host in the manifest). The field under 'Advanced' is an
// override for self-hosted or test receivers: saving one asks Chrome for exactly that origin
// (optional_host_permissions). The prompt must come from a click, so the request happens inside the Save
// handler before anything is stored. An empty field, or the default itself, means no override.
const DEFAULT = 'https://your-receiver.example.com/show';
const originPattern = endpoint => { try { return new URL(endpoint).origin + '/*'; } catch (e) { return null; } };
const perms = () => { try { return chrome.permissions || null; } catch (e) { return null; } };
const hasOrigin = pattern => new Promise(res => { const p = perms(); if (!p || !pattern) return res(false); p.contains({ origins: [pattern] }, ok => res(!!ok)); });
const askOrigin = pattern => new Promise(res => { const p = perms(); if (!p) return res(true); p.request({ origins: [pattern] }, ok => { void chrome.runtime.lastError; res(!!ok); }); });
const dropOrigin = pattern => new Promise(res => { const p = perms(); if (!p || !pattern) return res(); p.remove({ origins: [pattern] }, () => { void chrome.runtime.lastError; res(); }); });
const stored = () => new Promise(res => { if (!ext()) return res({}); chrome.storage.sync.get(['endpoint', 'token'], v => res(v || {})); });

const say = (t, cls) => { $('msg').textContent = t; $('msg').className = cls || 'saved'; };
const DONE = 'Connected. Open Seller Hub › Shipments and click Send show for packing list.';

(async () => {
  const v = await stored();
  $('endpoint').value = v.endpoint || ''; $('token').value = v.token || '';
  const pat = originPattern(v.endpoint || '');
  if (pat && !(await hasOrigin(pat))) { $('adv').open = true; say('Chrome has not yet allowed this address. Click Save and choose Allow.', 'warn'); }
})();

$('save').onclick = async () => {
  let endpoint = $('endpoint').value.trim(); const token = $('token').value.trim();
  if (!token) { say('Paste the access code from your welcome email.', 'warn'); $('token').focus(); return; }
  if (endpoint === DEFAULT) endpoint = '';
  const want = endpoint ? originPattern(endpoint) : null;
  if (endpoint && !/^https?:\/\//.test(endpoint)) { $('adv').open = true; say('The address must start with https://', 'warn'); return; }
  if (endpoint && !want) { $('adv').open = true; say('That address is not a valid URL.', 'warn'); return; }
  if (!ext()) { say(DONE, 'saved'); return; }   // file:// preview
  // Ask first, before any other await: the request only works while the click's user activation is fresh.
  // Chrome answers at once, without a prompt, when the origin is already allowed.
  if (want && !(await askOrigin(want))) { say('Not saved: Chrome needs permission to send to ' + want.slice(0, -2) + '. Click Save again and choose Allow.', 'warn'); return; }
  const prev = originPattern((await stored()).endpoint || '');
  chrome.storage.sync.set({ endpoint, token }, async () => {
    if (prev && prev !== want) await dropOrigin(prev);   // keep only the override in use, none when back on the service
    say(DONE, 'saved');
  });
};
