RENDERBATCH 3.9.0
===============
Batch post-processing for renders: denoise, mood / relight grading, color matching, upscale, sharpen.

NEW IN 3.9 - EVERYTHING DOWNLOADS BY ITSELF
  The installer is all you need: the Python packages and models (about 250 MB) are downloaded from the
    RenderBatch repository (github.com/RewZes/image-batch-editing) and checked before they're unpacked.
    A RenderBatch already on this PC is still offered first (quicker, keeps your settings).
  Default models: SCUNet denoise (faithful / GAN), SPAN 2x NomosUni and 4x-UltraSharp. The first time the app
    runs it downloads the missing ones from their authors' sites and converts them for the app (the
    converter is fetched once, about 120 MB). If a link fails you get a list with each model's page and the
    folder to put it in.
  The Denoise / Upscale dropdowns always list the default models, with a small "default" tag. Ones that aren't
    downloaded have a download icon: pick one and it downloads (progress shows in the list).
  4x-UltraSharp is listed by its author as non-commercial: check its page before using it for client work.

NEW IN 3.8 - INSTALLER AND ONE-CLICK UPDATES
  RenderBatch_Setup_3.8.0.exe: double-click, choose
    Install on this PC - into your user folder (no admin rights), Start menu + desktop shortcut, and an
      uninstaller in Windows Settings > Apps;
    Portable - everything in one folder you pick (also a USB drive), nothing written outside it.
  The setup takes the Python packages and models from the RenderBatch_*.rbdata files next to it (or in
    Downloads), or copies them, with your settings, presets and looks, from a RenderBatch folder that is
    already on this PC - nothing to download again.
  Uninstall: Windows Settings > Apps > RenderBatch (or Uninstall.exe in the folder). It asks whether to keep
    your settings, presets, looks, LUTs and models.
  Updates: Settings > Updates > Install update..., or drop the update .zip onto the window, or just leave it
    in Downloads - RenderBatch offers it when it starts, installs it and restarts. Files unpacked by the app
    don't get Windows' "downloaded from the internet" warning. Extracting the zip by hand still works.

NEW IN 3.7
  3.7.3: switching the language in Settings now also translates card titles with "&" (Texture & grain)
    and tooltips made of several parts.
  3.7.2: clicking into a number box selects the whole number (not the unit after it, like "×"), so you
    can type the new value straight away. LightMix reacts much faster: changing one light only adds that
    light's difference instead of mixing every pass again, and Corona's tone mapping runs on all CPU
    cores (on a 4K image with 20 lights: about 0.5 s instead of 4-10 s).
  .cxr files open about twice as fast (the light passes are read 4-5x faster).
  The Looks / Presets button shows the one you picked; with the preview or the strip of thumbnails
    selected, the Up / Down arrow keys step to the previous / next look (or preset).
  "Reset all" and the GPU badge moved to the bottom bar, right of "Activity log".
  Looks / Presets menus start with "None". Right-click the Looks button: no look (only the look
    settings go back to default). Right-click the Presets button: every setting back to default.

NEW IN 3.6 - LOOKS
  LUT: with two or more .cube files in the luts folder (subfolders too) the LUT box is a dropdown of
    them; Browse still picks any other file.
  Two buttons at the top right, Looks and Presets:
    Looks change only how the image looks: mood sliders, LUT, clarity / grain and Atmosphere. Picking a
      look replaces the previous look but keeps denoise, upscale, sharpen, output and folders.
    Presets keep every setting.
  13 built-in looks: Cinematic Teal & Orange, Cinematic Warm Drama, Retro 70s Film, Professional
    Photography, Perfect Golden Hour, Serene Night, Bright Scandinavian, Luxury Warm Interior, Dark &
    Moody, Fine Art Black & White, Soft Pastel Morning, Warm Film Stock, Nordic Cool Daylight
    (hover one in the menu for a description). Each has its own color grade (LUT); fine-tune it with the
    sliders afterwards.
  "Save look..." saves into the looks folder, "Save preset..." into the presets folder; both appear in the
    menu. "Load from another file..." opens either kind.

NEW IN 3.5
  Resolution / upscale card:
    "Upscale multiple times" + a number: runs the upscale step that many times in a row, each time by
      the Scale factor (2x three times = 8x), e.g. a wallpaper texture for print. The file name gets
      _upscaled_x<times>. Only in Resize: Scale factor. Results too big for the PC's memory are refused.
    "Also save a version without upscaling": saves <name>_final_no-upscale next to the result, and the
      upscaled file gets the upscaler's name (<name>_final_<model>), so you can compare upscalers.
  Presets: the Presets button lists every preset in the app's presets folder; click one to use it.
    "Save preset..." only asks for a name. Copy .json presets into that folder to share them.

NEW IN 3.4
  Changing the language (Settings) now happens in place: nothing reloads, the preview, zoom, region and
    a running batch stay as they are, and the Settings window changes language while it's open.
  The mouse wheel scrolls the strip of thumbnails left / right.

NEW IN 3.3 - FASTER
  Batch: while the GPU works on one image, the next one is read and the previous one is finished and
    saved at the same time, and the color grade / fog / saving use all the CPU threads set in Settings.
  Thumbnails are kept in a small cache (cache\thumbs, safe to delete), so folders reopen instantly;
    new ones are made several at a time.
  The images next to the one you're viewing are read ahead, so the arrow keys / clicking the strip
    switch almost instantly. Faster start-up and smoother preview updates.
  Tip: Settings > GPU usage limit at 100% makes batches fastest (lower keeps the laptop cooler).

NEW IN 3.2 - ATMOSPHERE (new card under Mood / relight)
  Time of day: As rendered / Day / Dusk / Night. .cxr files are relit through their LightMix passes
    (lights named Sun..., Environment / Sky / HDRI..., everything else counts as interior lights);
    other images are relit with a grade that keeps lamps lit. Amount, Interior lights, Sky & sun,
    Lights warmth fine-tune it.
  Fog / haze: Density, Starts at, Ground fog, Glow around lights, Fog color (white box = automatic).
  Light rays: Intensity, Length, Light comes from (angle), Spread (parallel sun beams ... fanning
    out from a lamp), Only from brighter than; "Pick on image" sets the point the rays come from.
    For .cxr files "Rays from" can be one LightMix light (e.g. Sun).
  Fog and rays need depth: a Corona ZDepth pass (CGeometry_ZDepth) in the .cxr is used when present,
    otherwise the AI depth model (Depth Anything V2 Small, 99 MB, free): press "Download" in the card
    once, or copy depth_anything_v2_small.onnx into models\depth by hand. Without either, a rough
    guess is used.

NEW IN 3.1
  Region (R): draw a box on the preview; denoise / upscale / sharpen / Detail check run only inside it
    at any zoom. Drag edges to resize, inside to move, x to remove.
  Cancel button next to the progress bar stops everything that's running.
  Resolution: Scale factor is the default; greyed fields show what they work out to.
  Every number box has small arrows: click, hold, or drag up / down; right-click resets.

NEW IN 3.0
  Language changes instantly (no restart): the window rebuilds in the new language.
  Drop images / .cxr files on the preview (or on the strip of thumbnails): they're copied into the
    input folder and shown. With no input folder yet, their own folder becomes the input folder.
  Drop an image on "Match colors" to use it as the reference.
  Strip of thumbnails: drag to reorder (Ctrl / Shift-click to select and move several at once);
    "Reset order" goes back to the automatic order. The batch processes images in the strip's order.
  "Process selected" next to "Process folder" processes only the selected thumbnails.
  .cxr files you already opened reopen instantly (their light passes stay in memory, up to a quarter
    of your RAM).

START
  Double-click RenderBatch.exe. Nothing needs to be installed. Windows 10 (1809+) or Windows 11.
  Keep all folders (runtime, app, models, luts) next to RenderBatch.exe.

LANGUAGE
  Settings > Language: English, Русский (Russian), Română (Romanian). The app restarts to switch.

THE WINDOW
  Left:    settings in cards. Click a card title to fold it. The switch on the right of a card turns
           that step on or off (Mood, Match colors, Denoise, Resolution / upscale, Sharpen).
           The batch panel is pinned at the bottom: Process / Pause / Cancel, progress and time left.
  Center:  the preview. Pick the image in the toolbar, the filmstrip, or with the arrow keys.
  Filmstrip: images already processed (found in the output folder) come first with a green frame;
           the one being processed now has an orange frame; red means it failed (see the log).
  Top:     "Reset all" puts every setting back to default (folders are kept) - you get an Undo button.
  Bottom:  the activity bar shows what is running, with a progress bar. "Activity log" opens the log.

PREVIEW
  Update preview (toolbar button, or F5) applies waiting changes and renders every enabled step
  (also denoise and upscale) on the area you're zoomed into. It lights up when changes are waiting.

  Denoise and Resolution / upscale each have a "Preview" choice inside their card:
    Interactive  the AI renders the zoomed-in area only when you move, zoom or resize the view
    Manual       nothing runs until you press Update preview or Detail check (default)
  The denoised / upscaled area stays on screen while you change quick settings (mood, exposure,
  sharpen, clarity, grain...): only those are re-applied on top of it, in a moment. It is replaced
  only when you move/zoom/resize the view or press Update preview. Changing a denoise / upscale
  setting keeps the old result on screen and lights up Update preview.
  Mood, color match and sharpen: Settings > "Mood, color match and sharpen"
    Automatic    the preview follows every slider move (default)
    Manual       changes wait for Update preview
  The "after" label shows which steps are rendered on screen, e.g. "AFTER · +UPSCALE +SHARPEN".
  Denoise / upscale / sharpen work on fine detail, so they are shown when you zoom in.

  Scroll: zoom at the mouse · Drag: move · Drag the round handle: move the split
  Double-click: fit / 100% · F: fit · 1: 100%
  Zooming into the Split view switches to two synchronized panes, before and after.

SETTINGS WORTH KNOWING
  The mouse wheel never changes a dropdown, number box or slider by accident: over them it just
  scrolls the panel. Click one first if you want to use the wheel on it.
  Numbers: the slider covers the usual range; click the number box to type any value beyond it
  (hover the box to see the limits).
  Match colors: "Choose any image..." lets you use a reference that isn't in the batch folder.
  "Use the AI upscaler only when enlarging at least": e.g. 1.30x means an image made 1.3 times
  bigger or more goes through the AI upscaler; smaller changes use a normal resize.
  Sharpen "Protect smooth areas": detail weaker than this isn't sharpened, so grain stays calm.

TEXTURE & GRAIN (switch on the card to use it)
  Clarity         brings back local contrast (depth) that denoising and upscaling flatten.
                  Updates live at any zoom.
  Grain           photographic grain that follows the light, like a real camera:
                    Digital sensor: most in shadows and midtones, none in blown highlights
                    Film: most in the midtones, fading toward black and white
                  Size is in pixels of the final image; Color mixes mono and colored speckles.
                  Every file gets its own grain pattern, the same every time you process it,
                  so batches are consistent and re-runs are identical. Zoom to 100% to judge it:
                  the zoomed preview shows exactly the grain the batch will produce.
  Grain source    the built-in grain (recommended), or a 1x model from models\grain.

CORONA .CXR FILES - LIGHTMIX
  Put your Corona .cxr renders in the input folder (you can mix them with PNG/JPG/TIF).
  Every .cxr is shown exactly as Corona shows it: its LightMix and its Post settings (exposure, white
  balance, tint, contrast, saturation, highlight compression, ACES, filmic, vignette, curves, LUT) are
  read from the file and reproduced (matched against Corona Image Editor to well under 1/255).
  Before = the file as saved in Corona.  After = your light changes + everything else in the app.

  The "💡 LightMix" button (toolbar) appears when the folder has .cxr files; it opens the panel on the right.
    Individual lights / Group lights   switch at the top of the panel
    Each light:  checkbox = on/off · Intensity × (multiplies the value saved in each file) · color box
    Color box:   click = color picker with a color-temperature slider · drag it onto another light's
                 color box = copy the color · right-click = back to the color saved in the file ("file")
    A change applies to the light with that name in EVERY file, so the whole batch stays consistent.
  Individual lights and Group lights are two separate setups with their own values; the one selected is
  the one you see and the one that is processed.
  Colors: a white box = the color saved in each file. The color picker has temperature, hue, saturation and
    brightness sliders, hex / RGB and the colors already used; the image updates while you pick
    (Cancel puts the old color back).
  Groups: double-click a group's name to rename it.
  Groups (e.g. one per room): "+ Create group" at the bottom of the panel, name it and tick its lights,
    or drag a light by its ⠿ handle onto a group. A group has its own on/off, intensity (multiplies its
    lights) and color (replaces theirs). In "Group lights" the grouped lights are hidden from the list;
    "Individual lights" shows every light (tagged with its group). Groups survive Reset all.
    ⋯ on a group: rename, change its lights, or Ungroup.
  Use each file's Post settings: off = plain LightMix without Corona's tone mapping.
  Light passes: As saved / Always denoised / Never (raw).
  Save… / Load… keep a lighting setup with its groups (e.g. "Evening") to reuse on other projects.

  LUTs used in Corona's Post: put the .cube file next to the .cxr files or in the app's luts folder.
  Not reproduced: Corona bloom & glare. Photographic exposure is approximated.
  Only .cxr files saved with ZIP compression (Corona's default) or no compression can be read.

SHOW: IMAGES / CXR (bottom right)
  Untick one to hide those files from the filmstrip AND from the batch (e.g. process only the .cxr).
  Hiding CXR also hides the LightMix panel; its lights and groups are kept.

INTERFACE SIZE
  Settings > Interface size: Compact (default) or Comfortable (larger). Spacing fully updates after a restart.

SIDEBAR AND OUTPUT
  Drag a section by its title to move it up or down. Settings > Sidebar shows / hides sections.
  The order and visibility are remembered, also after Reset all.
  Output format, bits and name suffix are in Settings > Output. 32-bit = floating-point TIFF.
  "Skip images already processed" is in the Batch panel.
  Sliders: click anywhere on a slider to jump there (and keep dragging).
  Right-click a slider (or its number or name) to put it back to its default value.

ERRORS
  When something is wrong, the setting that causes it gets a red outline, its card opens and the
  message is shown right there. Fix it and the outline disappears.

MODELS
  models\denoise   denoising models (1x)
  models\upscale   upscaling models (2x, 4x ...)
  models\grain     optional grain / texture models (1x)
  Each dropdown only lists its own folder.

  Drop models in any of these formats into those folders, even while the app is open:
    .onnx                      used as they are
    .pth .safetensors .pt .ckpt   converted automatically (activity bar shows progress, usually
                               under a minute). The converted copy is checked against the original;
                               only then is the original deleted.
  A model placed in the wrong folder (e.g. a 4x upscaler in denoise) is moved to the right one.
  If a file can't be converted (broken download, or not a denoise / upscale model) it is left
  untouched, you get a message, and it isn't retried unless the file changes.
  Good source: openmodeldb.info

SETTINGS (top right)
  Themes, GPU usage limit (keeps the PC responsive during a batch), CPU threads, preview quality,
  Live AI, and buttons that open the model / LUT folders.

IF SOMETHING GOES WRONG
  If the app shows an error at start, it writes crash.log in this folder. Send that file for help.
