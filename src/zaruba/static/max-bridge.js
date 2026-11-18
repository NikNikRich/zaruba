/* Адаптер MAX Mini App (docs/09): системная кнопка «Назад», внешние ссылки,
   диплинки `?startapp=` и приветственное окно (§9.11). Единственный
   исполняемый JS сайта; подключается только при ZARUBA_MAX_MINIAPP=true.
   Вне MAX (`platform === null`) навигация и ссылки остаются обычными. */
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

  // Приветствие: первый визит и только без диплинка — ссылка на конкретный
  // раздел должна сразу вести к цели, а не в промежуточное окно.
  var welcome = document.getElementById('max-welcome-modal');
  var WELCOME_KEY = 'zaruba_max_welcome_v1';
  var seen = null;
  try {
    seen = window.localStorage.getItem(WELCOME_KEY);
  } catch (error) {
    // Приватный режим WebView: хранилище бросает. Окно показываем — это не
    // повод ломать страницу, — но флаг тогда не сохранится.
    seen = null;
  }
  if (welcome && !seen && !start) welcome.hidden = false;

  function closeWelcome() {
    try {
      window.localStorage.setItem(WELCOME_KEY, '1');
    } catch (error) {
      // Навигация важнее памяти: окно закрываем, даже если флаг не записался.
    }
    if (welcome) welcome.hidden = true;
  }

  document.addEventListener('click', function (event) {
    var target = event.target.closest ? event.target : null;
    if (!target) return;
    // Кнопки-ссылки перехода тоже несут `data-welcome-close`: флаг ставится
    // до перехода, и на следующей странице окно уже не появляется.
    if (target.closest('[data-welcome-close]')) {
      closeWelcome();
    } else if (target.closest('[data-welcome-open]') && welcome) {
      welcome.hidden = false;
    }
  });
})();
