// Show/hide button on every password field (owner request 10/19/26: you couldn't
// see which password autofill or paste had put in). Progressive enhancement —
// without JS the fields stay plain password inputs. Included by base.html and by
// each standalone auth page (login, register, reset).
(function () {
  var EYE = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12z"/><circle cx="12" cy="12" r="3"/></svg>';
  var EYE_OFF = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 19c-7 0-11-7-11-7a18.45 18.45 0 0 1 5.06-5.94"/><path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 7 11 7a18.5 18.5 0 0 1-2.16 3.19"/><path d="M14.12 14.12a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>';
  var hiders = [];

  function enhance(input) {
    if (input.dataset.revealReady) return;
    input.dataset.revealReady = '1';
    // While revealed it's a text field: keep the keyboard from changing or learning it.
    input.setAttribute('autocapitalize', 'none');
    input.setAttribute('autocorrect', 'off');
    input.setAttribute('spellcheck', 'false');

    // Moving a node cancels the browser's pending autofocus and drops focus.
    var refocus = document.activeElement === input || input.hasAttribute('autofocus');
    var wrap = document.createElement('div');
    wrap.className = 'password-field';
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);
    if (refocus) input.focus();

    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'password-toggle';
    // A fixed label; aria-pressed carries the on/off state.
    btn.setAttribute('aria-label', 'Show password');
    btn.title = 'Show password';
    if (input.id) btn.setAttribute('aria-controls', input.id);
    wrap.appendChild(btn);

    function show(visible) {
      input.type = visible ? 'text' : 'password';
      btn.setAttribute('aria-pressed', visible ? 'true' : 'false');
      btn.innerHTML = visible ? EYE_OFF : EYE;
    }
    show(false);
    hiders.push(function () { show(false); });

    // A pointer tap keeps focus (and the phone keyboard) on the field; keyboard
    // users stay on the button.
    btn.addEventListener('mousedown', function (e) { e.preventDefault(); });
    btn.addEventListener('click', function () { show(input.type === 'password'); });
    // Hide again before submitting, so the browser's password manager still
    // sees a password field when it decides what to save.
    if (input.form) input.form.addEventListener('submit', function () { show(false); });
  }

  function hideAll() { hiders.forEach(function (h) { h(); }); }
  // Don't leave a password showing in the app switcher or on a page restored by Back.
  document.addEventListener('visibilitychange', function () { if (document.hidden) hideAll(); });
  window.addEventListener('pagehide', hideAll);

  function init() {
    document.querySelectorAll('input[type="password"]').forEach(enhance);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
