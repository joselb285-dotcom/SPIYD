// Banner de instalación PWA — se muestra una sola vez por dispositivo, después del login.
(function () {
  var KEY = 'spiyd_pwa_install_seen';
  var isStandalone = window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;
  if (isStandalone) return;
  if (localStorage.getItem(KEY)) return;

  var isIOS = /iphone|ipad|ipod/i.test(navigator.userAgent) && !window.MSStream;
  var deferredPrompt = null;

  function dismiss(el) {
    localStorage.setItem(KEY, '1');
    if (el && el.parentNode) el.remove();
  }

  function buildBanner(iosMode, onInstall) {
    var el = document.createElement('div');
    el.id = 'pwaInstallBanner';
    el.style.cssText = 'position:fixed;left:50%;bottom:18px;transform:translateX(-50%);z-index:99999;' +
      'display:flex;align-items:center;gap:10px;background:#12141c;border:1px solid rgba(249,115,22,.4);' +
      'border-radius:12px;padding:10px 14px;box-shadow:0 8px 30px rgba(0,0,0,.5);' +
      'font-family:Inter,system-ui,sans-serif;color:#fff;max-width:92vw;';
    el.innerHTML =
      '<span style="font-size:20px;line-height:1">🔥</span>' +
      '<span style="font-size:13px;flex:1;min-width:0">' +
      (iosMode ? 'Instalá SPIYD: tocá compartir <b>⬆</b> y luego "Agregar a inicio"'
                : 'Instalá SPIYD en este dispositivo para acceso rápido') +
      '</span>' +
      (iosMode ? '' : '<button id="pwaInstallBtn" style="background:#f97316;color:#fff;border:none;' +
        'border-radius:8px;padding:7px 14px;font-size:13px;font-weight:700;cursor:pointer;white-space:nowrap;">Instalar</button>') +
      '<button id="pwaInstallClose" style="background:transparent;border:none;color:rgba(255,255,255,.5);' +
        'font-size:16px;cursor:pointer;padding:0 2px;line-height:1;">✕</button>';
    document.body.appendChild(el);
    document.getElementById('pwaInstallClose').onclick = function () { dismiss(el); };
    if (!iosMode) {
      document.getElementById('pwaInstallBtn').onclick = function () { onInstall(el); };
    }
    return el;
  }

  if (isIOS) {
    // Safari iOS no dispara beforeinstallprompt: mostramos instrucciones manuales.
    buildBanner(true, null);
    return;
  }

  window.addEventListener('beforeinstallprompt', function (e) {
    e.preventDefault();
    deferredPrompt = e;
    buildBanner(false, function (el) {
      dismiss(el);
      if (!deferredPrompt) return;
      deferredPrompt.prompt();
      deferredPrompt.userChoice.finally(function () { deferredPrompt = null; });
    });
  });

  window.addEventListener('appinstalled', function () {
    dismiss(document.getElementById('pwaInstallBanner'));
  });
})();
