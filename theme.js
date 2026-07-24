(function () {
  var STORAGE_KEY = 'spiyd-theme';

  function applyTheme(theme) {
    var root = document.documentElement;
    root.classList.remove('dark', 'light');
    root.classList.add(theme);
    localStorage.setItem(STORAGE_KEY, theme);
  }

  window.spiydTheme = {
    get: function () {
      return document.documentElement.classList.contains('light') ? 'light' : 'dark';
    },
    set: applyTheme,
    toggle: function () {
      applyTheme(this.get() === 'light' ? 'dark' : 'light');
    }
  };

  applyTheme(localStorage.getItem(STORAGE_KEY) || 'dark');
})();
