# Как выпустить новую версию

Короткий чек-лист, чтобы релиз не разъехался с исходниками и тегом.

## 1. Поднять версию

1. В `ledapp/__init__.py` поменять `__version__` (например, `1.0.2`).
2. В `CHANGELOG.md` добавить раздел `## [1.0.2] — ГГГГ-ММ-ДД` и описать
   изменения. Ссылки внизу файла (`[1.0.2]: ...`) тоже добавить.
3. Убедиться, что в `README.md` не осталось упоминаний старой версии
   (версия в окне берётся из `__version__` автоматически).

## 2. Проверить локально

```powershell
python selftest.py
python build_exe.py
(Get-Item dist\LEDAmbilight.exe).VersionInfo |
    Format-List ProductName, ProductVersion, FileVersion, FileDescription
```

`ProductVersion` должен совпасть с `__version__`. Запусти exe, подключи ленту,
включи захват, проверь превью и кнопку «Выход».

## 3. Закоммитить и поставить тег

```powershell
git add -A
git commit -m "Версия 1.0.2: ..."
git push origin master

git tag -a v1.0.2 -m "LED Ambilight 1.0.2"
git push origin v1.0.2
```

Тег должен указывать на тот же коммит, что и `master`. CI
(`.github/workflows/ci.yml`) на этом коммите прогонит самотест и соберёт exe;
зелёная галочка — обязательное условие для релиза.

## 4. Собрать релизные файлы

Локальная сборка (рекомендуется: она уже проверена на живой ленте):

```powershell
python build_exe.py              # dist\LEDAmbilight.exe
python build_exe.py --portable   # dist\LEDAmbilight-<версия>-portable.zip
python build_installer.py        # dist\installer\LEDAmbilight-<версия>-setup.exe
Get-FileHash dist\LEDAmbilight.exe -Algorithm SHA256
Get-FileHash dist\LEDAmbilight-*-portable.zip -Algorithm SHA256
Get-FileHash dist\installer\LEDAmbilight-*-setup.exe -Algorithm SHA256
```

## 5. Создать релиз на GitHub

```powershell
$ver = "1.0.2"
gh release create "v$ver" `
    "dist\LEDAmbilight.exe" `
    "dist\LEDAmbilight-$ver-portable.zip" `
    "dist\installer\LEDAmbilight-$ver-setup.exe" `
    --title "LED Ambilight $ver" `
    --notes-file "release-notes-$ver.md" `
    --target master
```

В заметках релиза укажи: что исправлено, что нового, требования, порядок
установки (SmartScreen), SHA-256 всех файлов.

## 6. Проверить релиз

```powershell
gh release view "v$ver" -R arsinema/ledambilight --json tagName,assets
```

- размер ассетов совпадает с локальными файлами;
- `digest` ассетов совпадает с `Get-FileHash`;
- ссылка `https://github.com/arsinema/ledambilight/releases/latest` открывается
  и ведёт на новый релиз;
- скачанный exe и распакованный zip запускаются на чистой машине
  (или хотя бы из другой папки).

## Если CI упал

1. Открыть лог job в GitHub Actions.
2. Воспроизвести локально на той же версии Python (CI использует 3.12).
3. Починить, закоммитить; тег пересоздать только если релиз ещё не опубликован:

```powershell
git tag -d v1.0.2
git push origin :refs/tags/v1.0.2
```

Опубликованный релиз с тем же тегом перезаписывать нельзя — выпускай 1.0.3.
