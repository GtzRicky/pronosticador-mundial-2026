# Public Bundle Notes

Este directorio existe para documentar el modo publicable del proyecto.

El flujo recomendado es:

1. Mantener el código y la documentación en el repo público.
2. Mantener `.env`, cache y telemetría de consumo fuera del repo.
3. Exportar un bundle offline sólo cuando tengas claro que puedes redistribuir los datos.

Comandos útiles:

```bash
python scripts/09_export_public_bundle.py
python scripts/10_import_public_bundle.py --bundle-path outputs/bundles/quiniela_public_bundle.zip
python scripts/11_public_bundle_status.py
```

El bundle exportado:

- conserva la base necesaria para predicción offline
- elimina `api_cache`
- elimina `api_usage`
- no incluye ninguna API key

- puede incluir predicciones, alineaciones oficiales y alineaciones estimadas
  guardadas en `lineup_estimates`

Antes de compartir un bundle:

- confirma que no expones secretos locales
- confirma que no incluyes telemetria de uso
- revisa si la redistribucion de datos derivados o enriquecidos cumple el
  acuerdo aplicable de API-Football y cualquier licencia adicional de origen

Por defecto, los bundles se generan en `outputs/bundles/` y no se versionan.
