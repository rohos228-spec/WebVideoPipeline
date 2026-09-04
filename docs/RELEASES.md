# Журнал релизов

Дата (UTC), тег, sha, образ по дайджесту — по одной строке на выкладку.
Пишется `scripts/release.sh`, читается `scripts/rollback.sh`. Строка
«ОТКАТ» — возврат на предыдущий дайджест.

Файл назван `.md`, а не `.log`, намеренно: `*.log` в `.gitignore`, а
журнал обязан быть в git — иначе откат через неделю нечем прочитать.

| дата | тег | sha | образ |
|---|---|---|---|
| `2026-09-04T02:29Z` | — | `2f631e0d3b1e6d90ab5d8dd1d1e1b02bd4a1e3f0` | `ghcr.io/multikco/video-pipeline@sha256:bc42c74e7ee0d8c82fc430d9113b10156fae2c4b69d7ec3df33c52c18d65788e` |
| `2026-09-04T14:05Z` | — | `72d0e9d0` | `ghcr.io/multikco/video-pipeline@sha256:5c6fc1695f37ed13b6008ee7321ac5f7095abe5bf8e6ad31a460346ddc2f27c5` |
