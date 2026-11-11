#!/usr/bin/env python3
"""Upload các segment MP4 đã chốt từ /records lên rclone remote (mặc định Google Drive).

Chạy nền trong container stream (xem entrypoint.sh, UPLOAD_ENABLED=1). Nguyên tắc:

- Chỉ upload file "ổn": mtime cũ hơn UPLOAD_AFTER_SECONDS (file đang ghi còn được ffmpeg ghi tiếp).
- rclone moveto: chỉ xoá file local sau khi copy + verify checksum thành công; lỗi mạng thì file
  giữ nguyên, vòng sau thử lại (rclone tự retry thấp cấp bên trong).
- Điều tiết băng thông theo người xem thật: hỏi mediamtx API (127.0.0.1:9996) — có reader type
  "hls" nghĩa là có người đang xem web → giới hạn theo UPLOAD_BWLIMIT_WATCHING; không thì
  UPLOAD_BWLIMIT_IDLE. Không hỏi được API → coi như đang có người xem (an toàn là trên hết).
  (Container không thấy được NIC của host, và băng thông giữa máy chủ↔đầu ghi↔người xem mới là
  thứ cần bảo vệ, nên tín hiệu "người xem" chính xác hơn đo utilization.)

File trên Drive: <UPLOAD_REMOTE>/ch<N>/YYYY-MM-DD_HH-MM.mp4. Không tự xoá trên Drive — giữ làm lưu trữ.
"""

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

MEDIAMTX_API = "http://127.0.0.1:9996/v3/paths/list"
RECORD_MOUNT = Path(os.environ.get("RECORD_MOUNT", "/records"))
RCLONE_CONFIG = os.environ.get("RCLONE_CONFIG", "/config/rclone.conf")
REMOTE_BASE = os.environ.get("UPLOAD_REMOTE", "gdrive:hik-connect-records")
BW_WATCHING = os.environ.get("UPLOAD_BWLIMIT_WATCHING", "2M")
BW_IDLE = os.environ.get("UPLOAD_BWLIMIT_IDLE", "off")
UPLOAD_AFTER_SECONDS = int(os.environ.get("UPLOAD_AFTER_SECONDS", "120"))
SCAN_EVERY_SECONDS = int(os.environ.get("UPLOAD_SCAN_SECONDS", "60"))


def log(msg: str) -> None:
    print(f"[upload] {msg}", file=sys.stderr, flush=True)


def someone_watching() -> bool:
    try:
        with urllib.request.urlopen(MEDIAMTX_API, timeout=5) as res:
            items = json.load(res).get("items", [])
        return any(r.get("type") == "hls" for p in items for r in p.get("readers", []))
    except Exception:
        return True  # không kiểm tra được → thận trọng, áp dụng bwlimit


def upload_one(src: Path) -> bool:
    dest = f"{REMOTE_BASE}/{src.parent.name}/{src.name}"
    cmd = ["rclone", "moveto", str(src), dest,
           "--config", RCLONE_CONFIG,
           "--transfers", "1", "--checkers", "4", "--retries", "3",
           "--bwlimit", BW_WATCHING if someone_watching() else BW_IDLE]
    started = time.monotonic()
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log(f"{src.name}: lỗi ({result.stderr.strip().splitlines()[-1] if result.stderr else result.returncode})")
        return False
    log(f"{src.parent.name}/{src.name}: đã upload sau {time.monotonic() - started:.0f}s "
        f"(bwlimit={'người xem' if someone_watching() else 'rảnh'})")
    return True


def main() -> None:
    if shutil.which("rclone") is None:
        log("không có rclone trong image, tắt")
        return
    log(f"bật: {RECORD_MOUNT} → {REMOTE_BASE}, "
        f"bwlimit người xem={BW_WATCHING}/rảnh={BW_IDLE}, ổn sau {UPLOAD_AFTER_SECONDS}s")

    while True:
        if Path(RCLONE_CONFIG).is_file():
            candidates = []
            for ch_dir in sorted(RECORD_MOUNT.glob("ch*")):
                for f in ch_dir.glob("*.mp4"):
                    try:
                        if time.time() - f.stat().st_mtime > UPLOAD_AFTER_SECONDS:
                            candidates.append(f)
                    except OSError:
                        pass
            for f in sorted(candidates, key=lambda p: p.stat().st_mtime):
                if not upload_one(f):
                    break  # remote lỗi thì dừng vòng này, thử lại lần quét sau
        time.sleep(SCAN_EVERY_SECONDS)


if __name__ == "__main__":
    main()
