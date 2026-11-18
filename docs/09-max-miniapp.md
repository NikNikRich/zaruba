# 09. Mini App в MAX и бесплатный GitHub Pages

> Ответвление `feat/max-miniapp`: тот же сайт открывается внутри мессенджера
> MAX как мини-приложение. Инфраструктура — статика на GitHub Pages, 0 ₽.
> Спецификация MAX: <https://dev.max.ru/docs/webapps/introduction/> и
> <https://dev.max.ru/docs/webapps/bridge>.

## 9.1. Что меняет флаг `ZARUBA_MAX_MINIAPP=true`

| Что | Без флага | С флагом |
|---|---|---|
| MAX Bridge | не подключается | `https://st.max.ru/js/max-web-app.js` в конце `body`, затем `static/max-bridge.js` |
| CSP | `script-src 'self'`, `frame-ancestors 'none'` | `script-src 'self' https://st.max.ru`, `frame-ancestors 'self' https://max.ru https://*.max.ru` |
| `X-Frame-Options` | `DENY` | не отправляется: иначе десктопный клиент MAX не встроит страницу |
| Шапка | логотип «ЗАРУБА · Тюмень» и меню | логотип скрыт (нативный заголовок MAX уже называет бота), в меню добавлена «Главная» |
| Кнопка «Назад» | нет | `BackButton.hide()` на главной, `show()` на внутренних страницах, нажатие — `history.back()` |
| Внешние ссылки | обычный переход | `target="_blank"` перехватывается и открывается через `WebApp.openLink()` |

Вне MAX (`window.WebApp.platform === null`) адаптер не делает ничего: обычный
сайт остаётся обычным сайтом.

## 9.2. Локальная проверка

```powershell
$env:ZARUBA_MAX_MINIAPP = "true"
python -m uvicorn zaruba.main:app --port 8000
# в ответе: script-src ... https://st.max.ru, X-Frame-Options отсутствует,
# в шапке нет логотипа
```

Сборка ровно того, что уедет на Pages:

```powershell
$env:ZARUBA_ENV = "prod"
$env:ZARUBA_MAX_MINIAPP = "true"
python scripts/build_max_dist.py
python -m http.server 8001 --directory dist   # проверка относительных ссылок
```

Отдельно проверяется подпись `?startapp=`: открыть
`http://127.0.0.1:8001/?startapp=schedule` — адаптер перейдёт на
`schedule.html`, только если файл существует.

## 9.3. Включить GitHub Pages (5 шагов)

1. Ветка `feat/max-miniapp` запушена в GitHub; файл `.github/workflows/pages-max.yml` в ней есть.
2. Репозиторий → **Settings** → **Pages** → **Source**: выбрать **GitHub Actions**
   (не «Deploy from a branch»: тогда Pages возьмёт корень репозитория, а не `dist/`).
3. Репозиторий → **Actions** → workflow **pages-max** → **Run workflow**, ветка
   `feat/max-miniapp`. Пуш в эту ветку запускает его сам.
4. Дождаться зелёного job `deploy`; адрес будет в выводе job и в
   **Settings → Pages**: `https://<владелец>.github.io/<репозиторий>/`.
5. Открыть адрес в браузере: страницы и CSS должны работать без сервера.

Секреты `ZARUBA_SHEET_ID` и `ZARUBA_SHEETS_MAP` — те же, что у `sync-sheets.yml`
(`08-deploy-and-sheets.md`). Если `ZARUBA_SHEETS_MAP` не задан, сборка идёт на
данных репозитория и пишет `::notice::`, а не падает.

## 9.4. Ссылка на мини-приложение в MAX

1. Открыть <https://business.max.ru/self> → **Чат-боты** → **Перейти**.
2. Выбрать бота → **⋮** → **Настройки**.
3. Вставить адрес из шага 9.3 в поле URL мини-приложения.
4. Выбрать вид кнопки (**Открыть**, **Старт**, **Играть** или без названия) →
   **Сохранить**.

Требования MAX к адресу: только `https://`, не длиннее 1024 символов, латиница,
цифры, точка и дефис, без пробелов. Адрес `github.io` подходит; свой домен не
нужен. Если репозиторий переименован, адрес в настройках бота надо обновить —
иначе кнопка ведёт на 404.

## 9.5. Диплинки `?startapp=`

Формат: `https://max.ru/<bot>?startapp=<значение>`, значение — до 512 символов.
MAX передаёт его мини-приложению в `window.WebApp.initDataUnsafe.start_param`.
Адаптер понимает значение как slug страницы: `[a-z0-9-]`, 1–40 символов,
переход только на существующий `<slug>.html` статической сборки.

| Диплинк | Куда попадёт пользователь |
|---|---|
| `https://max.ru/<bot>?startapp=schedule` | `schedule.html` — расписание |
| `https://max.ru/<bot>?startapp=participants` | `participants.html` — каталог участников |
| `https://max.ru/<bot>?startapp=results` | `results.html` — результаты |
| `https://max.ru/<bot>` | главная, `start_param` пуст |

Доступные slug — это имена страниц из `scripts/build_static_preview.py`
(`SEED_PAGES`): `event`, `schedule`, `performances`, `participants`, `studios`,
`judges`, `results`, `tickets`, `venue`, `faq`, `media`, `privacy`, `about`,
`rules`, `takedown`, `glossary`, `history`, `ratings`, `search`.

Не передавайте в `startapp` конфиденциальные данные открытым текстом — только
одноразовые токены или идентификаторы сессии (требование MAX). Редирект
работает в статической сборке; на локальном FastAPI-стенде `<slug>.html` нет,
поэтому диплинк просто открывает главную.

## 9.6. Обновление и после слияния в `main`

- По расписанию (`*/30 * * * *`) и по пушу workflow забирает таблицы, гоняет
  `zaruba.content check`, `pytest`, собирает `dist/` и публикует Pages.
- GitHub запускает `schedule` только из workflow-файла в **ветке по умолчанию**.
  До слияния `pages-max.yml` в `main` расписание молчит: работают пуш в
  `feat/max-miniapp` и кнопка **Run workflow**.
- После слияния добавьте `main` в `on.push.branches`, чтобы Pages обновлялся и
  от коммитов в основную ветку.
- Коммитов workflow не делает: данные таблиц живут в опубликованном `dist/`, а
  не в истории репозитория.

## 9.7. Ограничения, о которых нужно помнить

- GitHub Pages отдаёт файлы как есть и **не присылает заголовки** из
  `middleware.py`: CSP и `X-Frame-Options` действуют только на FastAPI-стенде.
  Для Mini App это не проблема (страница и так внутри MAX), но проверять
  политику надо на `uvicorn`, а не на Pages.
- Копия на `github.io` закрыта от индексации файлом `robots.txt` с
  `Disallow: /`: `canonical` в статике срезается, а JSON-LD сохраняет адреса
  `testserver` — пусть робот лучше не видит эту копию вовсе.
- Поиск и фильтры — динамика FastAPI: в статике форма открывает каталог без
  параметров.
- `dist/` в `.gitignore`: публикуется артефакт сборки, а не папка в git.
