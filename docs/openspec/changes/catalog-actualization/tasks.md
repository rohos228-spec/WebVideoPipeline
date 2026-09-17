# Tasks: catalog-actualization

- [x] A.1 Живой `/api/v1/models` снят (картинки 2×2K, видео 1×720p/8с).
- [x] B.1 Бэкенд-опции: убраны lite/4K/1080p; дефолты `gpt_image_2_vip` /
      `veo_3_1_lite`; graceful-алиасы в `outsee_http` оставлены как страховка.
- [x] B.2 `media_prices.json`: +`outsee:gpt-image-2-vip` (null, цену вписывает
      владелец), −блок `grsai:*`.
- [x] B.3 Фронт-каталоги (`node-model-catalog`, `outsee-catalog`: только записи
      со `studioId` без бэкенда) + STUDIO_VERSION 518 + сборка зеленая.
- [x] C.1 Тесты: billing/db_v2/parity/studio_agent/vibecode + весь затронутый
      набор (178) зелено; `tsc`, `ruff`, `mypy`, `policy_check` чисто.
- [ ] C.2 Живая проверка владельцем (пикер, дефолты, dry_run) + команда на коммит.
