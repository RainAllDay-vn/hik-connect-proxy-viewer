# hik-connect-proxy-viewer-stream

Stream proxy for Hikvision recorders (NVR/DVR) whose account only works through **Hik-Connect / the SDK
port** and is refused on RTSP (`401`). It logs in with Hikvision's `HCNetSDK`, pulls the channel on demand,
remuxes the MPEG-PS output with ffmpeg (no re-encoding) and serves it as HLS through mediamtx.

Part of [hik-connect-proxy-viewer](https://github.com/phuthuycoding/hik-connect-proxy-viewer): pair it with
`phuthuycoding/hik-connect-proxy-viewer-web` for the password-protected web app, or install both with one
Helm chart: `oci://ghcr.io/phuthuycoding/charts/hik-connect-proxy-viewer`.

## What is inside

- `mediamtx` (PID 1) with `runOnDemand` on any path `ch<N>`: the first viewer of `/ch7/index.m3u8` triggers
  a recorder login for channel 7, later viewers share it, the login is dropped when nobody watches.
- `pull_stream.py`: HCNetSDK real-play callback -> ffmpeg `-f mpeg -c copy` -> RTSP publish to mediamtx.
- `discover.py`: probes every channel, keeps the ones the account may view (with the recorder's channel
  names) and serves them on `:9000/channels`.
- `record.py`: optional continuous MP4 recorder (`-c copy`, 10-min segments) for a time window, e.g.
  06:00–20:00; reading the internal RTSP path counts as a viewer, so it triggers the on-demand pull.
  Files land in `RECORD_DIR/ch<N>/YYYY-MM-DD_HH-MM.mp4`; `RECORD_KEEP_DAYS` prunes old files.
- `upload.py`: optional rclone uploader for finished segments; throttles itself while someone is
  watching (mediamtx API) and moves the local file only after the upload verifies.
- `get_sdk.sh`: Hikvision's SDK is **not** in this image. It is downloaded into `/sdk` at first start from
  hikvision.com (or from `HCNETSDK_ZIP_URL`). Mount a volume on `/sdk` to keep it.

## Usage

```bash
docker run -d --name hik-stream \
  -e HIK_DOMAIN=recorder.example.net -e HIK_SDK_PORT=8000 \
  -e HIK_USER=USER -e HIK_PASS=PASS \
  -e MTX_PUBLISH_BASE=rtsp://puller:puller-secret@127.0.0.1:8554 \
  -v hik_sdk:/sdk \
  -v $PWD/mediamtx.yml:/mediamtx.yml:ro \
  -p 127.0.0.1:8888:8888 -p 127.0.0.1:9000:9000 \
  phuthuycoding/hik-connect-proxy-viewer-stream:latest
```

`mediamtx.yml` is in the repo under `chart/files/`. Ports: `8888` HLS (`/ch<N>/index.m3u8`), `9000` channel
list (`/channels`). Never run two replicas against the same account: each channel is one SDK session.

| Env | Default | |
|---|---|---|
| `HIK_DOMAIN` / `HIK_HOST` | | recorder DDNS hostname or IP |
| `HIK_SDK_PORT` | `8000` | SDK port as mapped by the school |
| `HIK_USER`, `HIK_PASS` | | recorder account |
| `HIK_SUB` | `0` | `1` = sub stream |
| `MTX_PUBLISH_BASE` | | RTSP publish base, must match `authInternalUsers` in mediamtx.yml |
| `HCNETSDK_ZIP_URL` | | self-hosted SDK zip; empty = hikvision.com |
| `DISCOVER_REFRESH_SECONDS` | `21600` | re-probe channels |
| `RECORD_ENABLED` | `0` | `1` = continuous recording (see `record.py`) |
| `RECORD_CHANNELS` | *(all discovered)* | channels to record, e.g. `42` or `42,33` |
| `RECORD_START` / `RECORD_END` | `06:00` / `20:00` | recording window in `RECORD_TZ` |
| `RECORD_TZ` | `Asia/Ho_Chi_Minh` | timezone for the window and file names |
| `RECORD_SEGMENT_SECONDS` | `600` | segment length |
| `RECORD_KEEP_DAYS` | `7` | delete files older than this; `0` = keep forever |
| `RECORD_DIR` | `/records` | mount a host directory here for the MP4 segments |
| `UPLOAD_ENABLED` | `0` | `1` = upload finished segments via rclone (`upload.py`) |
| `UPLOAD_REMOTE` | `gdrive:hik-connect-records` | rclone remote/path base; files go to `<base>/ch<N>/` |
| `UPLOAD_BWLIMIT_WATCHING` | `2M` | upload bandwidth limit while a viewer is watching |
| `UPLOAD_BWLIMIT_IDLE` | `off` | upload bandwidth limit when nobody is watching |
| `UPLOAD_AFTER_SECONDS` | `120` | only upload files whose mtime is older than this |
| `RCLONE_CONFIG` | `/config/rclone.conf` | mount the host rclone config dir on `/config` |

Platform: `linux/amd64` only (the SDK is x86_64). Tags: `latest`, `sha-<commit>`, `X.Y.Z`.

Runs fine as non-root (`--user 10001:10001`; `/sdk` is world-writable in the image). Prefer that on shared
Kubernetes nodes: mediamtx needs one inotify instance per process and Linux counts them per UID, so a root
container on a busy node can hit `couldn't initialize inotify: too many open files`.

## Disclaimer

For personal use with accounts you are authorized to use. Not affiliated with Hikvision; the Hikvision SDK
is proprietary and downloaded by you under Hikvision's terms. MIT licensed, provided as is.
