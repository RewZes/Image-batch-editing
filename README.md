# Image-batch-editing (RenderBatch)

A Windows app for post-processing renders and photos in batches: AI denoise and upscale, mood / relight
grading, Corona `.cxr` LightMix, color matching, fog and light rays, looks and presets — on any DirectX 12
GPU (DirectML) or the CPU.

## Install

1. Download [`installer/RenderBatch_Setup_3.9.0.exe`](installer/RenderBatch_Setup_3.9.0.exe) and double-click it.
   Windows may warn that the publisher is unknown (the app isn't code-signed): *More info → Run anyway*.
2. Choose **Install on this PC** (Start menu + desktop shortcut, uninstall from Windows Settings › Apps) or
   **Portable** (everything in one folder you pick).
3. The installer downloads the Python packages and models (about 250 MB) from the [`data`](data) folder of
   this repository and checks every file before unpacking it. If RenderBatch is already on the PC, it offers to
   copy them from there instead.

On first start the app downloads the remaining default models from their authors' sites and converts them
for the app (see `MODELS.txt`).

## Update

Download the update zip from [`installer`](installer) and either leave it in your Downloads folder (the app
offers it when it starts), drop it onto the app window, or use *Settings › Updates › Install update…*.

## What's in this repository

| Path | What it is |
|---|---|
| `app/` | the app (Python, PySide6, ONNX Runtime) |
| `data/` | `RenderBatch_*.rbdata`: Python runtime packages, the bundled models and the optional model converter (PyTorch), split into parts under 30 MB. The installer and the app download them from here. |
| `installer/` | the Windows installer and the latest update zip |
| `tools/` | build scripts: installer (NSIS), data packs, looks, launcher |
| `README.txt` (`_RU`, `_RO`) | the user guide, also in Russian and Romanian |
| `MODELS.txt` | the default models, their sources and licenses |

Default models come from their original authors (SCUNet — Kai Zhang et al.; 2x NomosUni SPAN — Philip
Hofmann; 4x-UltraSharp — Kim2091). Check each model's license before commercial use; 4x-UltraSharp is listed
as non-commercial.
