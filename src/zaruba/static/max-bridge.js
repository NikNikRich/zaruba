/* Адаптер MAX Mini App (docs/09): системная кнопка «Назад», внешние ссылки и
   диплинки `?startapp=`. Единственный исполняемый JS сайта; подключается
   только при ZARUBA_MAX_MINIAPP=true. Вне MAX (`platform === null`) не делает
   ничего: ссылки и переходы остаются обычными. */
(function () {
  'use strict';
  var app = window.WebApp;
  var inside = Boolean(app && app.platform);
  var home = /(^|\/)(index\.html)?$/.test(location.pathname);

  if (inside) {
    app.ready();
    if (home) {
      app.BackButton.hide();
    } else {
      app.BackButton.show();
      app.BackButton.onClick(function () { history.back(); });
    }
  }

  // Внешние ссылки: MAX открывает их своим браузером, обычный переход
  // остаётся запасным вариантом вне мессенджера.
  document.addEventListener('click', function (event) {
    var link = event.target.closest ? event.target.closest('a[target="_blank"]') : null;
    if (!inside || !link || !/^https?:/i.test(link.href)) return;
    event.preventDefault();
    app.openLink(link.href);
  }, true);

  // Диплинк `?startapp=`: страница сборки, в том числе вложенная (`participants/ivan-petrov`).
  var start = inside && app.initDataUnsafe && app.initDataUnsafe.start_param;
  if (inside && home && /^[a-z0-9][a-z0-9/-]{0,63}$/.test(start || '')) {
    var page = start + '.html';
    fetch(page, { method: 'HEAD' }).then(function (response) {
      if (response.ok) location.replace(page);
    }).catch(function () {});
  }
})();
