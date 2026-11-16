# Autobirder Web

A small web app for tagging animal photos. It scans a folder (including subfolders) for jpg/jpeg/png images and classifies them with [BioCLIP](https://github.com/Imageomics/pybioclip). It shows the top 10 species guesses per image. You pick one, edit the fields it will write, and save. The app then moves on to the next image.
It will probably also work for plants as the model has training data for it, but i have not tested that as i am a bird photographer. 

## Run

```bash
./run.sh "/path/to/photos"   # builds the image with podman and starts it
```

Then open <http://localhost:8000>.

- The photo folder is mounted at `/data` and **modified in place**. Tags are overwritten on save.
- Classification results are cached in the `autobirder_cache` volume, and model files in `bioclip_cache`.
- The first start downloads about 4 GB of model files. Loading the model needs roughly 5 GB of free RAM and takes about a minute. Each image takes a few seconds.

## What gets written

| Field | Where |
|---|---|
| Description | EXIF `ImageDescription` |
| Keywords | EXIF `XPKeywords` (Windows tags), XMP `dc:subject`, IPTC `Keywords` |
| Comment | EXIF `UserComment` |

PNG files only get XMP. Files are replaced in place and their modification time is preserved.

## Keyboard

Press `?` in the app for the full list.

| Key | Action |
|---|---|
| `1`–`9`, `0` | Pick guess 1–10 |
| `↑` / `↓` | Move the guess selection |
| `Enter` | Save and go to the next image |
| `S` | Skip (writes nothing) |
| `←` / `→`, `K` / `J` | Previous / next image |
| `N` | None of these (custom entry) |
| `E` or `/` | Edit fields |
| `R` / `U` | Reset to the guess / load existing tags |
| `H` | Hide finished images |
| `Ctrl+Enter` | Save while typing |

## Keyword autocomplete

Tags you add yourself (for example locations) are remembered when you save and suggested while you type in the keyword field. Suggestions also appear when you focus the empty field, most used first. Species and genus names that came from the guesses are not remembered. In the keyword field, `↑`/`↓` select a suggestion, `Enter` or `Tab` adds it, and `Shift+Delete` forgets it, for example after a typo. The list is stored in the `keywords` table of `autobirder.sqlite` in the cache volume (`/cache` in the container).

## Configuration

Environment variables (set with `-e` in `podman run`):

| Variable | Default | Purpose |
|---|---|---|
| `DATA_DIR` | `/data` | Photo root |
| `CACHE_DIR` | `/cache` | Result cache and previews |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` | Accepted `Host` headers |

## Security

There is **no authentication**. `run.sh` publishes the port on `127.0.0.1` only. To use it from another machine, put an authenticating reverse proxy in front and add its hostname to `ALLOWED_HOSTS`.

## Tests

```bash
docker run --rm -v "$(pwd)/tests:/app/tests:ro" autobirder-web:latest \
  sh -c "pip install -q pytest httpx && python -m pytest -q"
```

## License

Autobirder Web is licensed under the [GNU General Public License v3.0 or later](LICENSE) (GPL-3.0-or-later).

**Why GPL-3.0?** The app depends on [`pyexiv2`](https://pypi.org/project/pyexiv2/), which wraps the Exiv2 library and is GPL-licensed. The container image bundles it, so distributing the image means distributing GPL software. Licensing the whole project under GPL-3.0 keeps the source and the image consistent and avoids any doubt about license compatibility. Other options, such as Apache-2.0 or replacing `pyexiv2`, were considered.

### Third-party components

| Component | License |
|---|---|
| fastapi | MIT |
| uvicorn | BSD-3-Clause |
| pybioclip | MIT |
| pillow | MIT-CMU (HPND) |
| piexif | MIT |
| torch, torchvision | BSD-3-Clause |
| pyexiv2 / Exiv2 | GPL-3.0 / GPL-2.0-or-later |
| BioCLIP model weights (downloaded at first start, not shipped in the image) | MIT |

The BioCLIP model was trained on TreeOfLife-10M, which is made up of images under various licenses. The dataset is not part of this project.
