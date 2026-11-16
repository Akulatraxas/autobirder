# Autobirder Web

A small web app for tagging animal photos. It scans a folder (including subfolders) for jpg/jpeg/png images and classifies them with [BioCLIP](https://github.com/Imageomics/pybioclip). It shows the top 10 species guesses per image. You pick one, edit the fields it will write, and save. The app then moves on to the next image.
Intitially this was just a small cli tool that helped me not getting mad with tagging my pictures.

It will probably also work for plants as the model has training data for it, but I have not tested that as I am a bird photographer.

> [!WARNING]
> **Photos are modified in place.** Saving writes tags directly to the image files and overwrites existing values in those fields. **Make a backup** (or test on a copy) before pointing it at your photo library.

## Quick start (Docker Compose)

Prebuilt `linux/amd64` images are published to GitHub Container Registry (`ghcr.io/akulatraxas/autobirder`).

1. Clone the repository (or download [`docker-compose.yml`](docker-compose.yml) and [`.env.example`](.env.example)):

   ```bash
   cp .env.example .env
   ```

2. Edit `.env` and set `PHOTOS_DIR` to your photo folder (and `PUID`/`PGID` if `id -u` / `id -g` is not `1000`).
3. Start the container:

   ```bash
   docker compose up -d
   ```

4. Open <http://localhost:8000>.

- Classification results and thumbnails are cached in the `autobirder_cache` volume, and model weights in `bioclip_cache`.
- Stop with `docker compose down`.

## Build and run locally (`run.sh`)

```bash
./run.sh /path/to/photos
```

`run.sh` builds the image locally and starts it, auto-detecting `podman` or `docker` (podman first). Override the engine or host port with environment variables:

```bash
CONTAINER_ENGINE=docker PORT=8080 ./run.sh /path/to/photos
```

Then open <http://localhost:8000> (or your chosen port).

## Requirements & performance

- **Architecture:** `linux/amd64` (CPU-only PyTorch, no GPU required).
- **First run:** downloads roughly 4 GB of BioCLIP model files into `bioclip_cache`.
- **System Requirements:** loading the model needs roughly 5 GB of free RAM and takes about a minute on startup/first classification. Each image takes a few seconds on CPU.

## What gets written

| Field | Where |
|---|---|
| Description | EXIF `ImageDescription` |
| Keywords | EXIF `XPKeywords` (Windows tags), XMP `dc:subject`, IPTC `Keywords` |
| Comment | EXIF `UserComment` |

PNG files only get XMP. Files are replaced and their modification time is preserved.

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

### Container environment variables

| Variable | Default | Purpose |
|---|---|---|
| `DATA_DIR` | `/data` | Photo root inside the container |
| `CACHE_DIR` | `/cache` | Result cache and previews |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` | Accepted `Host` headers (keep `localhost,127.0.0.1` for the healthcheck) |

### Docker Compose (`.env`) variables

See [`.env.example`](.env.example) for defaults: `PHOTOS_DIR`, `PORT`, `PUID`, `PGID`, `ALLOWED_HOSTS`, `AUTOBIRDER_IMAGE`.

### `run.sh` environment variables

| Variable | Default | Purpose |
|---|---|---|
| `PORT` | `8000` | Host port bound on `127.0.0.1` |
| `CONTAINER_ENGINE` | auto (`podman`, then `docker`) | Force `"podman"` or `"docker"` |
| `RUN_AS_ROOT` | `0` | Set `RUN_AS_ROOT=1` to pass `--user 0:0` (troubleshooting only) |

## Security

There is **no authentication**. Both `docker-compose.yml` and `run.sh` publish the port on `127.0.0.1` only. To use it from another machine, put an authenticating reverse proxy in front and add its hostname to `ALLOWED_HOSTS`.

## Troubleshooting

- **Permission denied when saving tags:** Make sure `PUID` and `PGID` in `.env` match your host user (`id -u` and `id -g`). On special mounts where a non-root container user still cannot write even on writable shares (for example an SMB share mounted in WSL), you can run as root as a last resort:
  - Compose: set `PUID=0` and `PGID=0` in `.env`
  - `run.sh`: `RUN_AS_ROOT=1 ./run.sh /path/to/photos`
- **Rootless `podman-compose` file ownership:** If modified files appear owned by a subuid, uncomment `userns_mode: "keep-id"` in `docker-compose.yml`.
- **First image stays on "classifying" for a while:** On the very first run, the container is downloading ~4 GB of model weights and loading them into RAM. Check `docker compose logs -f` to follow progress.
- **400 Bad Request (`Invalid Host header`):** Add the hostname you access the app with to `ALLOWED_HOSTS`.

## Tests

```bash
docker run --rm -v "$(pwd)/tests:/app/tests:ro" autobirder-web:latest \
  sh -c "pip install -q pytest httpx && python -m pytest -q"
```

## License

Autobirder Web is licensed under the [GNU General Public License v3.0 or later](LICENSE) (GPL-3.0-or-later).

**Why GPL-3.0?** The app depends on [`pyexiv2`](https://pypi.org/project/pyexiv2/), which wraps the Exiv2 library and is GPL-licensed. It is what writes the XMP and IPTC tags. The container image bundles it, so distributing the image means distributing GPL software. Licensing the whole project under GPL-3.0 keeps the source and the image consistent and avoids any doubt about license compatibility. Other options, such as Apache-2.0 or replacing `pyexiv2`, were considered.

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
