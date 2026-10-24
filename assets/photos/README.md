# Исходники фото

Сюда кладут исходные снимки — большие файлы, в git они не попадают
(`.gitignore`). Готовые превью скрипт пишет в `src/zaruba/static/img/`,
и уже они уезжают в репозиторий и в образ.

```
assets/photos/
  history/          кадры выпусков       → 4:3, 960×720, WebP
  people/           исходники портретов: официальные фото и афиши
  people/portrait/  кропы 3:4 по лицу    → 480×640, WebP
```

Порядок:

```powershell
# 1. посмотреть, что будет сделано, ничего не записывая
.venv\Scripts\python.exe tools\prepare_photos.py --check

# 2. разложить по форматам (Pillow нужен только этому скрипту)
.venv\Scripts\python.exe -m pip install pillow
.venv\Scripts\python.exe tools\prepare_photos.py

# 3. один файл с явным slug и пресетом
.venv\Scripts\python.exe tools\prepare_photos.py --preset portrait --slug fara assets/photos/people/IMG_0421.jpg
```

Скрипт печатает готовый фрагмент YAML: его вставляют в `content/photos.yaml`
(архив) или в карточку человека в `content/editions/*.yaml`. Поля `alt` и
`author` обязательны всегда, а основание публикации — одно из двух:

```
use: own        # кадр наш или есть разрешение автора: нужен license
use: citation   # цитирование по п. 1 ст. 1274 ГК РФ: нужны purpose и источник
```

Для цитаты подсказка печатается флагом `--rights citation --purpose info`
(`science`, `education`, `culture` — остальные законные цели). Без цели, автора
или источника загрузка контента падает, и это намеренно: «нашлось в интернете»
основанием не считается. Даже законная цитата не отменяет `/takedown`: просьбу
убрать снимок выполняем за 48 часов.

Эффекты для сцены — `--fx dance`: неон, световые ленты, пыль, виньетка и
зерно. Лицо не меняется, меняются только свет и цвет; EXIF всё так же
вырезается.

```powershell
.venv\Scripts\python.exe tools\prepare_photos.py --fx dance --slug fara assets\photos\people\IMG_0421.jpg
```

Если своих снимков нет, постеры 3:4 для жюри рисует `tools/avatars.py`
(силуэт в свете, дым, частицы). Это иллюстрации: в YAML они подписаны
`photo_nature: illustration`, на карточке стоит бейдж «иллюстрация».

Портреты жюри и ведущего выпуска 2026 — кропы 3:4 по лицу, а не афиши целиком:
в карточке шириной 160 px лицо с афиши занимает меньше четверти кадра, и человека
не узнать. Источники двух видов:

* официальные фото со страниц организаций — `burtsev.jpg` (MDC NRG,
  nrg-style.ru) и `mc-geo.jpg` (PROТАНЦЫ CAMP, protancycamp.com);
* афиши сообщества «ЗАРУБА72» — `fara.jpg`, `gubanova.jpg`, `bonchinche.jpg`.

Рамки вокруг головы заданы в `tools/crop_portraits.py` (словарь `FRAMES`: сверху
и снизу запас, чтобы макушка и подбородок остались в кадре), кропы — 3:4 без
ретуши и апскейла больше чем в два раза:

```powershell
# 1. проверить рамки: 3:4 и в пределах исходника
.venv\Scripts\python.exe tools\crop_portraits.py --check

# 2. вырезать портреты в assets/photos/people/portrait/<slug>.jpg
.venv\Scripts\python.exe tools\crop_portraits.py

# 3. привести к пресету portrait (480×640, WebP); пресет берётся
#    из имени папки people — отдельный --preset не нужен
.venv\Scripts\python.exe tools\prepare_photos.py assets/photos/people/portrait
```

В YAML у такой карточки `photo: img/people/<slug>.webp`, `photo_use: citation`
с `photo_purpose: info` и `photo_source_url` на публикацию: у Бурцева и Гео —
страница организации, у остальных — конкретный пост (`vk.com/wall-185685166_223`
и т. д.). `photo_author` — публикатор: «сообщество «ЗАРУБА72»», «Московский
танцевальный центр MDC NRG», «PROТАНЦЫ CAMP».

Прогон `prepare_photos.py` без аргументов папку `people/` пропускает: там
исходники, а на сайт идут только кропы из `people/portrait/`.

Имя файла — это slug: строчные латинские буквы, цифры и дефис. `IMG_0421`
не подойдёт, `fara` — подойдёт.

Метаданные (EXIF, GPS, ICC) вырезаются: на публичной странице им не место.
