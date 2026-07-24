# Bundled engines

Optional native engines shipped beside the app (dev checkout or Windows installer).

| Folder | Purpose |
|--------|---------|
| [`dji_tsdk/`](dji_tsdk/README.md) | DJI Thermal SDK (`libdirp.dll`), ExifTool, optional MLP |

Photogrammetry engine: install **ODX** (`C:\ODX` or `OPENPVSCOPE_ODX_ROOT`).  
End users get ODX via **Full** OpenPVScope Setup. Developers: [`scripts/bootstrap_odx.ps1`](../scripts/bootstrap_odx.ps1).  
Docs: [docs/photogrammetry.md](../docs/photogrammetry.md)
