"""Media authenticity checks (Phase 11).

Decides whether an uploaded photo or video looks authentic, using
deterministic, explainable forensics - no black box:

- hash match: the file on disk must still match the digest recorded at
  upload time (tamper check)
- duplicate reuse: the exact same file was already submitted for another
  incident/delivery (copy-paste "proof")
- EXIF camera metadata: real phone photos carry make/model/timestamp;
  screenshots and generated images usually do not
- Error Level Analysis: a saved-then-pasted region compresses differently
  from the rest of the picture, which betrays most edits
- container sniffing for video (mp4/webm magic bytes), with ffprobe
  metadata when the tool is installed

The verdict is evidence FOR a human decision, never the decision itself.
"""
import hashlib
import json
import os
import shutil
import subprocess

from PIL import Image, ImageChops

SEVERITY_START = 100
PENALTY = {
    "hash_mismatch": 100,     # instant likely_fake
    "duplicate": 65,          # the same file re-used as "new" proof
    "unknown_format": 70,     # not a recognisable photo/video at all
    "suspicious_ela": 30,
    "no_exif": 30,            # screenshots/edited images, weak but real
    "tiny_image": 20,
    "video_container_only": 10,
}


def _ela(image_path: str) -> dict:
    """Error Level Analysis: recompress and measure the difference."""
    try:
        img = Image.open(image_path).convert("RGB")
        tmp = image_path + ".ela.jpg"
        img.save(tmp, "JPEG", quality=90)
        recompressed = Image.open(tmp).convert("RGB")
        diff = ImageChops.difference(img, recompressed)
        hist = diff.convert("L").histogram()
        total = sum(hist) or 1
        mean = sum(i * c for i, c in enumerate(hist)) / total
        os.remove(tmp)
        return {"ela_mean": round(mean, 2),
                "ela_max": max(i for i, c in enumerate(hist) if c)}
    except Exception:
        return {"ela_mean": None, "ela_max": None}


def _sniff_media(path: str) -> str:
    """Return 'image' or 'video' from magic bytes, not the filename."""
    with open(path, "rb") as f:
        head = f.read(16)
    if head[:3] == b"\xff\xd8\xff":
        return "image"       # JPEG
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "image"       # PNG
    if head[4:8] == b"ftyp":
        return "video"       # MP4/MOV family
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "video"       # WebM/MKV
    return "unknown"


def _video_reasons(path: str) -> tuple[list, int]:
    reasons = []
    penalty = 0
    if shutil.which("ffprobe"):
        try:
            out = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries",
                 "format=duration,size", "-of", "default=nw=1", path],
                capture_output=True, text=True, timeout=15)
            if out.returncode == 0:
                reasons.append({"flag": "video_metadata", "severity": "info",
                                "detail": "container parsed: "
                                          + " ".join(out.stdout.split())})
                return reasons, penalty
        except Exception:
            pass
    reasons.append({"flag": "video_container_only", "severity": "info",
                    "detail": "container signature is valid, but no "
                              "ffprobe is installed: frames were not "
                              "analysed. Treat as unverified, not proof."})
    return reasons, PENALTY["video_container_only"]


def _image_reasons(path: str, digest: str, conn, media_kind: str,
                   ref_id: int) -> tuple[list, int, dict]:
    reasons, penalty, extras = [], 0, {}

    with open(path, "rb") as f:
        content = f.read()
    if hashlib.sha256(content).hexdigest() != digest:
        reasons.append({"flag": "hash_mismatch", "severity": "block",
                        "detail": "the file changed after upload - its "
                                  "fingerprint no longer matches the one "
                                  "recorded at upload time"})
        return reasons, PENALTY["hash_mismatch"], extras

    # duplicate reuse: the same bytes already used anywhere else
    others = conn.execute(
        "SELECT 'evidence' AS src, id FROM incident_evidence "
        "WHERE sha256 = %s UNION ALL "
        "SELECT 'delivery' AS src, id FROM assignments "
        "WHERE proof_sha256 = %s",
        (digest, digest)).fetchall()
    mine = (media_kind, ref_id)
    seen_elsewhere = [(r["src"], r["id"]) for r in others
                      if (r["src"], r["id"]) != mine]
    if seen_elsewhere:
        reasons.append({"flag": "duplicate", "severity": "high",
                        "detail": "this exact file was already submitted "
                                  f"for {seen_elsewhere[0][0]} #{seen_elsewhere[0][1]}"})
        penalty += PENALTY["duplicate"]

    kind = _sniff_media(path)
    if kind == "unknown":
        reasons.append({"flag": "unknown_format", "severity": "high",
                        "detail": "file is not a recognisable photo or "
                                  "video format"})
        penalty += PENALTY["unknown_format"]
        return reasons, penalty, extras
    if kind == "video":
        vreasons, vpenalty = _video_reasons(path)
        reasons += vreasons
        penalty += vpenalty
        return reasons, penalty, extras

    try:
        img = Image.open(path)
        width, height = img.size
        extras["dimensions"] = f"{width}x{height}"
        if width < 200 or height < 200:
            reasons.append({"flag": "tiny_image", "severity": "medium",
                            "detail": f"only {width}x{height} pixels - too "
                                      "small to show any real scene"})
            penalty += PENALTY["tiny_image"]
        exif = img.getexif()
        make = exif.get(271)
        model = exif.get(272)
        when = exif.get(306)
        if make or model or when:
            reasons.append({"flag": "camera_exif", "severity": "info",
                            "detail": f"camera metadata present: "
                                      f"{make or '?'} {model or '?'}"
                                      + (f", taken {when}" if when else "")})
        else:
            reasons.append({"flag": "no_exif", "severity": "medium",
                            "detail": "no camera metadata: typical of "
                                      "screenshots, edited or generated "
                                      "images, not of an original photo"})
            penalty += PENALTY["no_exif"]
    except Exception:
        reasons.append({"flag": "unknown_format", "severity": "high",
                        "detail": "file could not be opened as an image"})
        penalty += PENALTY["unknown_format"]
        return reasons, penalty, extras

    ela = _ela(path)
    extras.update(ela)
    if ela["ela_mean"] is not None and ela["ela_mean"] > 8.0:
        reasons.append({"flag": "suspicious_ela", "severity": "high",
                        "detail": "error levels are unusually high and "
                                  "uneven: the image was most likely "
                                  "edited or heavily recompressed"})
        penalty += PENALTY["suspicious_ela"]
    return reasons, penalty, extras


def _verdict_for(penalty: int, hard_block: bool) -> tuple[str, int]:
    score = max(0, SEVERITY_START - penalty)
    if hard_block or score < 40:
        return "likely_fake", score
    if score < 75:
        return "suspicious", score
    return "clean", score


def check_media(conn, media_kind: str, ref_id: int, path: str,
                digest: str, filename: str | None) -> dict:
    """Analyse one media file, store and return the verdict report."""
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    reasons, penalty, extras = _image_reasons(
        path, digest, conn, media_kind, ref_id)
    hard = any(r["flag"] == "hash_mismatch" for r in reasons)
    if any(r["flag"] == "video_container_only" for r in reasons) and \
            not any(r["severity"] in ("high", "block", "medium")
                    for r in reasons):
        verdict = "unverified"
        score = SEVERITY_START - penalty
    else:
        verdict, score = _verdict_for(penalty, hard)

    row = conn.execute(
        "INSERT INTO media_checks (media_kind, ref_id, filename, sha256, "
        "verdict, score, reasons) VALUES (%s, %s, %s, %s, %s, %s, %s) "
        "RETURNING id, checked_at",
        (media_kind, ref_id, filename, digest, verdict, score,
         json.dumps([dict(r, **({"dimensions": extras.get("dimensions")}
                                if r["flag"] == "camera_exif"
                                and extras.get("dimensions") else {}))
                     for r in reasons])),
    ).fetchone()
    return {"id": row["id"], "media_kind": media_kind, "ref_id": ref_id,
            "filename": filename, "sha256": digest[:16] + "...",
            "verdict": verdict, "score": score, "reasons": reasons,
            "checked_at": row["checked_at"].isoformat()}


def latest_check(conn, media_kind: str, ref_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM media_checks WHERE media_kind = %s AND ref_id = %s "
        "ORDER BY id DESC LIMIT 1", (media_kind, ref_id)).fetchone()
    return dict(row) if row else None
