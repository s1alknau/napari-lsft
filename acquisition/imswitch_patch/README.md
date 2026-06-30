# ImSwitch patch: `setLaserGalvo` API endpoint

ImSwitch can drive the UC2 light-sheet galvo (`ESP32LEDLaserManager.setGalvo`),
but only from the **GUI** (`HoliSheetController.toggleLightsheet`). There is no
REST endpoint, so a script cannot activate the sheet at measurement start.

This patch adds one `@APIExport` to `LaserController`:

```
GET /LaserController/setLaserGalvo?laserName=635%20Laser&frequency=10&amplitude=1
```

- `frequency=10` (or any >0) starts the galvo sweep that forms the sheet
- `frequency=0` stops it
- manual GUI control (Holo widget) is unchanged — this only *adds* an API path

[`acquire_lsft.py`](../acquire_lsft.py) calls it automatically (galvo on before
rotation, off after); disable with `--no-auto-galvo`.

## Apply

Run with the **same Python/env that runs ImSwitch** (here: `imswitch21`):

```bash
python add_galvo_endpoint.py            # auto-locates the installed imswitch
# or target a file explicitly:
python add_galvo_endpoint.py --path ".../imswitch/imcontrol/controller/controllers/LaserController.py"
```

Then **restart ImSwitch** so the new endpoint is registered.

- Idempotent — re-running does nothing if already applied.
- Writes a `LaserController.py.bak` backup next to the file.
- `--revert` removes the endpoint again (restores the file exactly).

## Note

This edits the installed ImSwitch package, so a future ImSwitch upgrade/reinstall
will drop it — just re-run the applier afterwards. The change is also a sensible
upstream contribution (galvo control over the API), if you want to PR it.
