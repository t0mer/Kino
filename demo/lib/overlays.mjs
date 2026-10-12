// Demo overlays injected into every page of the recording (see ~/.claude/guidelines/video-demos.md):
// a cursor dot (headless Chromium draws none), a bottom-left caption box and the Kino mascot as a
// narrating avatar in the bottom-right corner. All three live in one init script (`overlayScript`).
//
// Page API:  window.__caption(text)          show a caption ('' clears it)
//            window.__avatar.speak(env, at)  animate the mouth from a 20 fps 0..1 envelope, starting
//                                            `at` seconds into it; ring glows while speaking
//            window.__avatar.idle()          stop speaking (idle ring, mouth closed)
//            window.__avatar.hide(bool)      hide/show the avatar (it also hides itself while a toast
//                                            occupies the bottom-right corner)
// The top page's <main> gets extra end padding so the avatar sits in empty space, never on content.
// Inside iframes (the phone mock-up) only the cursor runs, forwarding its position to the parent.

/** Cursor dot: 18px, 26px while the button is down. Runs in every frame. */
function cursor() {
  if (window.top !== window) {
    // Inside the phone iframe: drive the parent's cursor, mapped through the iframe's scale.
    const fwd = (e, down) => {
      try {
        const r = window.frameElement.getBoundingClientRect();
        const k = r.width / window.innerWidth;
        window.parent.__cursorTo(r.left + e.clientX * k, r.top + e.clientY * k, down);
      } catch { /* detached */ }
    };
    window.addEventListener('mousemove', (e) => fwd(e), true);
    window.addEventListener('mousedown', (e) => fwd(e, true), true);
    window.addEventListener('mouseup', (e) => fwd(e, false), true);
    return;
  }
  const mk = () => {
    if (document.getElementById('__cur')) return document.getElementById('__cur');
    const st = document.createElement('style');
    st.textContent = `#__cur{position:fixed;left:-40px;top:-40px;width:18px;height:18px;margin:-9px 0 0 -9px;border-radius:50%;
      background:rgba(225,29,72,.85);border:2px solid #fff;box-shadow:0 0 6px rgba(0,0,0,.4);z-index:2147483647;
      pointer-events:none;transition:width .12s,height .12s,margin .12s;box-sizing:border-box}
      #__cur.down{width:26px;height:26px;margin:-13px 0 0 -13px}`;
    document.documentElement.appendChild(st);
    const c = document.createElement('div');
    c.id = '__cur';
    document.documentElement.appendChild(c);
    return c;
  };
  window.__cursorTo = (x, y, down) => {
    const c = mk();
    c.style.left = `${x}px`;
    c.style.top = `${y}px`;
    if (down !== undefined) c.classList.toggle('down', down);
  };
  window.addEventListener('mousemove', (e) => window.__cursorTo(e.clientX, e.clientY), true);
  window.addEventListener('mousedown', () => mk().classList.add('down'), true);
  window.addEventListener('mouseup', () => mk().classList.remove('down'), true);
  document.addEventListener('DOMContentLoaded', mk);
}

/** Caption: bottom-left navy box, ends left of the avatar, at most two lines. */
function caption() {
  if (window.top !== window) return;
  const mk = () => {
    let k = document.getElementById('__cap');
    if (k) return k;
    const st = document.createElement('style');
    st.textContent = `#__cap{position:fixed;left:24px;bottom:20px;max-width:1056px;box-sizing:border-box;
      background:rgba(15,23,42,.94);color:#fff;font:600 18px/1.4 Inter,system-ui,-apple-system,"Segoe UI",sans-serif;
      padding:14px 18px;border-radius:8px;box-shadow:0 4px 18px rgba(0,0,0,.28);z-index:2147483646;pointer-events:none;
      display:none;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;overflow:hidden;text-align:start}
      #__cap[hidden]{display:none}`;
    document.documentElement.appendChild(st);
    k = document.createElement('div');
    k.id = '__cap';
    k.dir = 'auto';
    k.hidden = true;
    document.documentElement.appendChild(k);
    return k;
  };
  window.__caption = (t) => {
    const k = mk();
    k.textContent = t || '';
    k.hidden = !t;
  };
  document.addEventListener('DOMContentLoaded', mk);
}

/** The Kino mascot (web/public/favicon.svg) as a talking, blinking narrator. */
function avatar() {
  if (window.top !== window) return;
  const SIZE = 145;
  let env = [], t0 = 0, speaking = false, raf = 0, forced = false;
  const mk = () => {
    let a = document.getElementById('__ava');
    if (a) return a;
    const st = document.createElement('style');
    st.textContent = `#__ava{position:fixed;right:20px;bottom:20px;width:${SIZE}px;height:${SIZE}px;border-radius:50%;
      background:#1c1917;z-index:2147483645;pointer-events:none;box-sizing:border-box;border:3px solid rgba(245,158,11,.35);
      box-shadow:0 6px 20px rgba(0,0,0,.35);transition:opacity .25s,box-shadow .2s,border-color .2s}
      #__ava.talk{border-color:rgba(251,191,36,.95);box-shadow:0 0 0 4px rgba(245,158,11,.25),0 0 26px 6px rgba(245,158,11,.55),0 6px 20px rgba(0,0,0,.35)}
      #__ava.gone{opacity:0}
      #__ava svg{width:100%;height:100%;display:block}
      #__ava .lid{transform-origin:72.5px 62px;transition:transform .08s}
      #__ava.blink .lid{transform:scaleY(.08)}
      #__ava .ring{transition:stroke .2s}
      #__ava.talk .ring{stroke:#fbbf24}
      main{padding-inline-end:190px !important}`; /* keep page content out of the avatar's column */
    document.documentElement.appendChild(st);
    a = document.createElement('div');
    a.id = '__ava';
    a.setAttribute('aria-hidden', 'true');
    // 145x145 box: eye (ring + pupil) slightly above the centre, mouth pill below it.
    a.innerHTML = `<svg viewBox="0 0 145 145" xmlns="http://www.w3.org/2000/svg">
      <g class="lid"><circle class="ring" cx="72.5" cy="62" r="34" fill="none" stroke="#f59e0b" stroke-width="10"/>
      <circle cx="72.5" cy="62" r="14" fill="#f59e0b"/></g>
      <rect class="mouth" x="58.5" y="110" width="28" height="5" rx="2.5" fill="#f59e0b"/></svg>`;
    document.documentElement.appendChild(a);
    // Blink every 3-5 s.
    const blink = () => {
      a.classList.add('blink');
      setTimeout(() => a.classList.remove('blink'), 140);
      setTimeout(blink, 3000 + Math.random() * 2000);
    };
    setTimeout(blink, 1800);
    // Step aside while a toast (sonner, bottom-right) is on screen.
    const toastCheck = () => a.classList.toggle('gone', forced || !!document.querySelector('[data-sonner-toast]'));
    new MutationObserver(toastCheck).observe(document.documentElement, { childList: true, subtree: true });
    return a;
  };
  const mouth = (v) => {
    const m = mk().querySelector('.mouth');
    const h = 5 + Math.max(0, Math.min(1, v)) * 15;
    m.setAttribute('height', h.toFixed(1));
    m.setAttribute('y', (112.5 - h / 2).toFixed(1));
    m.setAttribute('width', (28 - v * 6).toFixed(1));
    m.setAttribute('x', (72.5 - (28 - v * 6) / 2).toFixed(1));
  };
  const tick = () => {
    if (!speaking) return;
    const i = Math.floor(((performance.now() - t0) / 1000) * 20);
    if (i >= env.length) { window.__avatar.idle(); return; }
    mouth(env[i]);
    raf = requestAnimationFrame(tick);
  };
  window.__avatar = {
    speak(e, at = 0) {
      env = e || [];
      t0 = performance.now() - at * 1000;
      speaking = true;
      mk().classList.add('talk');
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(tick);
    },
    idle() {
      speaking = false;
      cancelAnimationFrame(raf);
      mk().classList.remove('talk');
      mouth(0);
    },
    hide(on) {
      forced = !!on;
      mk().classList.toggle('gone', forced);
    },
  };
  document.addEventListener('DOMContentLoaded', mk);
}

/** One init script for `context.addInitScript({ content })`. */
export const overlayScript = `(${cursor})();(${caption})();(${avatar})();`;

/**
 * Phone mock-up: the app at a real 390px viewport inside a scaled iframe, centred above the caption.
 * Run with page.evaluate(showPhone, url). The page behind is dimmed.
 */
export function showPhone(url) {
  const W = 390, H = 720, K = 0.78;
  const wrap = document.createElement('div');
  wrap.id = '__phone';
  wrap.style.cssText = 'position:fixed;inset:0;z-index:2147483600;background:rgba(10,10,12,.55);opacity:0;transition:opacity .35s';
  const bezel = document.createElement('div');
  bezel.style.cssText = `position:absolute;left:50%;top:18px;width:${W * K + 20}px;height:${H * K + 20}px;margin-left:-${(W * K + 20) / 2}px;
    border-radius:34px;background:#0c0a09;box-shadow:0 18px 50px rgba(0,0,0,.55);padding:10px;box-sizing:border-box;overflow:hidden`;
  const f = document.createElement('iframe');
  f.src = url;
  f.title = 'Kino on a phone';
  f.style.cssText = `width:${W}px;height:${H}px;border:0;border-radius:26px;transform:scale(${K});transform-origin:0 0;background:#fff`;
  bezel.appendChild(f);
  wrap.appendChild(bezel);
  document.body.appendChild(wrap);
  requestAnimationFrame(() => { wrap.style.opacity = '1'; });
}

export function hidePhone() {
  const w = document.getElementById('__phone');
  if (!w) return;
  w.style.opacity = '0';
  setTimeout(() => w.remove(), 400);
}
