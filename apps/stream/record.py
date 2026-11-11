#!/usr/bin/env python3
"""Ghi hình MP4 liên tục trong khung giờ cấu hình (mặc định 06:00-20:00, khung 10 phút).

Đọc RTSP nội bộ mediamtx (rtsp://127.0.0.1:8554/ch<N>): với mediamtx on-demand, kết nối
đọc chính nó kích hoạt pull_stream từ đầu ghi, nên worker ghi hình chỉ là "người xem" —
web vẫn dùng chung một phiên SDK. -c copy không encode lại, CPU ~0. File
YYYY-MM-DD_HH-MM.mp4 (giờ địa phương theo RECORD_TZ) nằm trong RECORD_DIR/ch<N>/.

Ngoài khung giờ hoặc kênh biến mất khỏi discover thì dừng ffmpeg (SIGINT để chốt segment);
ffmpeg chết (mạng rớt, đầu ghi offline) thì thử lại sau RETRY_SECONDS. RECORD_KEEP_DAYS > 0
xoá file cũ hơn vậy mỗi giờ. Chạy nền trong container stream (xem entrypoint.sh).
"""

import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

RTSP_BASE = "rtsp://127.0.0.1:8554"
DISCOVERY_URL = "http://127.0.0.1:9000/channels"
REFETCH_CHANNELS_SECONDS = 300
RETRY_SECONDS = 30
CLEANUP_EVERY_SECONDS = 3600

os.environ["TZ"] = os.environ.get("RECORD_TZ", "Asia/Ho_Chi_Minh")
time.tzset()  # ffmpeg -strftime đặt tên file theo TZ này


def log(msg: str) -> None:
    print(f"[record] {msg}", file=sys.stderr, flush=True)


def hhmm(name: str, fallback: dtime) -> dtime:
    try:
        h, m = os.environ[name].strip().split(":")
        return dtime(int(h), int(m))
    except (KeyError, ValueError):
        return fallback


TZ = ZoneInfo(os.environ["TZ"])
START = hhmm("RECORD_START", dtime(6, 0))
END = hhmm("RECORD_END", dtime(20, 0))
# Điểm mount trong container (compose bind ${RECORD_DIR} trên host vào /records)
OUT_DIR = Path(os.environ.get("RECORD_MOUNT", "/records"))
SEGMENT_SECONDS = int(os.environ.get("RECORD_SEGMENT_SECONDS", "600"))
KEEP_DAYS = int(os.environ.get("RECORD_KEEP_DAYS", "7"))


def in_window(now: datetime) -> bool:
    t = now.time()
    if START <= END:
        return START <= t < END
    return t >= START or t < END  # khung qua đêm (vd 22:00-06:00)


def channels() -> list[int]:
    """Kênh cần ghi: ghi đè bằng RECORD_CHANNELS ("42" hoặc "42,33"), rỗng = theo discover."""
    raw = os.environ.get("RECORD_CHANNELS", "").strip()
    if raw:
        return [int(c) for c in raw.split(",") if c.strip()]
    try:
        with urllib.request.urlopen(DISCOVERY_URL, timeout=10) as res:
            return [int(c["channel"]) for c in json.load(res)["channels"]]
    except Exception as err:
        log(f"không lấy được /channels: {err}")
        return []


def start_ffmpeg(channel: int) -> subprocess.Popen:
    out_dir = OUT_DIR / f"ch{channel}"
    out_dir.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen(
        ["ffmpeg", "-hide_banner", "-loglevel", "warning",
         "-rtsp_transport", "tcp", "-i", f"{RTSP_BASE}/ch{channel}",
         "-c", "copy", "-f", "segment", "-segment_time", str(SEGMENT_SECONDS),
         "-reset_timestamps", "1", "-strftime", "1",
         "-movflags", "+frag_keyframe+empty_moov",  # segment còn nguyên kể cả bị kill
         str(out_dir / "%Y-%m-%d_%H-%M.mp4")],
    )


def stop(proc: subprocess.Popen, channel: int) -> None:
    if proc.poll() is None:
        proc.send_signal(signal.SIGINT)  # ffmpeg chốt segment hiện tại rồi thoát
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def cleanup_old() -> None:
    if KEEP_DAYS <= 0:
        return
    cutoff = time.time() - KEEP_DAYS * 86400
    for f in OUT_DIR.rglob("*.mp4"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                log(f"xoá file cũ hơn {KEEP_DAYS} ngày: {f.relative_to(OUT_DIR)}")
        except OSError:
            pass


def main() -> None:
    workers: dict[int, subprocess.Popen] = {}
    last_try: dict[int, float] = {}
    chans: list[int] = []
    last_fetch = 0.0
    last_cleanup = 0.0
    log(f"bật: {START:%H:%M}-{END:%H:%M} {os.environ['TZ']}, segment {SEGMENT_SECONDS}s, "
        f"giữ {KEEP_DAYS or '∞'} ngày, ghi vào {OUT_DIR}")

    while True:
        now = datetime.now(TZ)
        if now.timestamp() - last_fetch >= REFETCH_CHANNELS_SECONDS:
            chans = channels()
            last_fetch = now.timestamp()
            if chans:
                log(f"kênh cần ghi: {chans}")

        if in_window(now):
            for ch in chans:
                if workers.get(ch) is not None and workers[ch].poll() is None:
                    continue
                if now.timestamp() - last_try.get(ch, 0) < RETRY_SECONDS:
                    continue
                if ch in workers:
                    log(f"ch{ch}: ffmpeg thoát mã {workers[ch].returncode}, thử lại")
                last_try[ch] = now.timestamp()
                workers[ch] = start_ffmpeg(ch)
                log(f"ch{ch}: bắt đầu ghi")
            for ch in [c for c in workers if c not in chans]:
                stop(workers.pop(ch), ch)
                log(f"ch{ch}: kênh không còn trong danh sách, dừng")
            if now.timestamp() - last_cleanup >= CLEANUP_EVERY_SECONDS:
                cleanup_old()
                last_cleanup = now.timestamp()
        elif workers:
            for ch, proc in workers.items():
                stop(proc, ch)
                log(f"ch{ch}: ngoài khung giờ, dừng")
            workers.clear()

        time.sleep(5)


if __name__ == "__main__":
    main()
