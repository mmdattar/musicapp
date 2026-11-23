import json
import os
import re
import time
from datetime import datetime, timezone
from urllib.parse import quote
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

DATA_FILE = "songs.json"
MAX_TRACKS_TOTAL = 100
MAX_NEW_PER_CATEGORY = 6
MAX_TRACKS_PER_ALBUM = 2

HEADERS = {
    "User-Agent": "CalmWaveApp/1.0 (Royalty-Free Audio Aggregator)"
}

# کوئری‌های هدفمند آثار دارای مجوز Creative Commons و Public Domain در Internet Archive
CATEGORY_QUERIES = [
    {
        "category": "lofi",
        "query": 'mediatype:audio AND (licenseurl:*creativecommons* OR licenseurl:*publicdomain*) AND (subject:"lofi" OR subject:"lo-fi" OR title:"lofi" OR title:"lo-fi" OR subject:"chillhop")',
        "fallback_cover": "https://images.unsplash.com/photo-1518609878373-06d740f60d8b?auto=format&fit=crop&w=600&q=80"
    },
    {
        "category": "sleep",
        "query": 'mediatype:audio AND (licenseurl:*creativecommons* OR licenseurl:*publicdomain*) AND (subject:"sleep" OR title:"sleep" OR subject:"ambient" OR title:"deep sleep")',
        "fallback_cover": "https://images.unsplash.com/photo-1507525428034-b723cf961d3e?auto=format&fit=crop&w=600&q=80"
    },
    {
        "category": "relax",
        "query": 'mediatype:audio AND (licenseurl:*creativecommons* OR licenseurl:*publicdomain*) AND (subject:"meditation" OR subject:"relaxing" OR title:"relaxation" OR title:"calm")',
        "fallback_cover": "https://images.unsplash.com/photo-1506126613408-eca07ce68773?auto=format&fit=crop&w=600&q=80"
    },
    {
        "category": "piano",
        "query": 'mediatype:audio AND (licenseurl:*creativecommons* OR licenseurl:*publicdomain*) AND (subject:"piano" OR title:"piano") AND (subject:"instrumental" OR subject:"solo" OR subject:"classical" OR subject:"ambient")',
        "fallback_cover": "https://images.unsplash.com/photo-1520523839897-bd0b52f945a0?auto=format&fit=crop&w=600&q=80"
    },
    {
        "category": "nature",
        "query": 'mediatype:audio AND (licenseurl:*creativecommons* OR licenseurl:*publicdomain*) AND (subject:"rain" OR title:"rain" OR title:"ocean waves" OR title:"forest sounds" OR subject:"nature sounds")',
        "fallback_cover": "https://images.unsplash.com/photo-1441974231531-c6227db76b6e?auto=format&fit=crop&w=600&q=80"
    }
]


def translate_to_fa(text):
    if not text or not text.strip():
        return ""
    text = text.strip()[:200]
    try:
        res = requests.get(
            "https://translate.googleapis.com/translate_a/single",
            params={"client": "gtx", "sl": "en", "tl": "fa", "dt": "t", "q": text},
            headers=HEADERS,
            timeout=10
        )
        if res.status_code == 200:
            data = res.json()
            out = "".join([part[0] for part in data[0] if part[0]]).strip()
            if out:
                return out
    except Exception:
        pass
    return text


def clean_track_name(raw_name, fallback_title):
    name = os.path.basename(raw_name)
    name = re.sub(r'\.mp3$', '', name, flags=re.IGNORECASE)
    name = re.sub(r'^\d+[\s._-]+', '', name)
    name = re.sub(r'[_]+', ' ', name)
    name = re.sub(r'\s+', ' ', name).strip()
    if len(name) < 3 or name.isdigit():
        return fallback_title
    return name[:90]


def parse_duration_seconds(length_val):
    if not length_val:
        return 0
    s = str(length_val).strip()
    if ":" in s:
        parts = s.split(":")
        try:
            nums = [float(p) for p in parts]
            if len(nums) == 2:
                return int(nums[0] * 60 + nums[1])
            if len(nums) == 3:
                return int(nums[0] * 3600 + nums[1] * 60 + nums[2])
        except ValueError:
            return 0
    try:
        return int(float(s))
    except ValueError:
        return 0


def format_duration(sec):
    if sec <= 0:
        return "03:30"
    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def format_license(url):
    u = (url or "").lower()
    if "publicdomain" in u or "/zero/" in u:
        return "Public Domain / CC0"
    if "creativecommons.org" in u:
        return "Creative Commons (CC)"
    return "Royalty-Free (Archive.org)"


def fetch_category_tracks(cfg, existing_urls):
    found = []
    cat = cfg["category"]
    params = {
        "q": cfg["query"],
        "fl[]": ["identifier", "title", "creator", "licenseurl"],
        "sort[]": "downloads desc",
        "rows": 14,
        "page": 1,
        "output": "json"
    }

    try:
        res = requests.get("https://archive.org/advancedsearch.php", params=params, headers=HEADERS, timeout=15)
        if res.status_code != 200:
            return found
        docs = res.json().get("response", {}).get("docs", [])
    except Exception as e:
        print(f"Search error ({cat}): {e}")
        return found

    for doc in docs:
        if len(found) >= MAX_NEW_PER_CATEGORY:
            break

        identifier = doc.get("identifier")
        if not identifier:
            continue

        album_title = doc.get("title", "Relaxing Track")
        if isinstance(album_title, list):
            album_title = album_title[0]
        creator = doc.get("creator", "Independent Artist")
        if isinstance(creator, list):
            creator = ", ".join(str(c) for c in creator[:2])
        license_label = format_license(doc.get("licenseurl", ""))

        try:
            meta_res = requests.get(f"https://archive.org/metadata/{identifier}", headers=HEADERS, timeout=12)
            if meta_res.status_code != 200:
                continue
            files = meta_res.json().get("files", [])
        except Exception:
            continue

        cover_url = f"https://archive.org/services/img/{identifier}"
        added_from_album = 0

        for f in files:
            if len(found) >= MAX_NEW_PER_CATEGORY or added_from_album >= MAX_TRACKS_PER_ALBUM:
                break

            fname = f.get("name", "")
            if not fname.lower().endswith(".mp3"):
                continue
            if "_64kb.mp3" in fname.lower():
                continue

            size_bytes = int(f.get("size") or 0)
            if size_bytes and (size_bytes < 400_000 or size_bytes > 35_000_000):
                continue

            dur_sec = parse_duration_seconds(f.get("length"))
            if dur_sec and (dur_sec < 60 or dur_sec > 2700):
                continue

            audio_url = f"https://archive.org/download/{identifier}/{quote(fname, safe='/')}"
            if audio_url in existing_urls:
                continue

            raw_title = f.get("title") or clean_track_name(fname, str(album_title))
            title_en = str(raw_title).strip()[:90]
            title_fa = translate_to_fa(title_en)

            found.append({
                "title": title_fa,
                "title_en": title_en,
                "artist": str(creator).strip()[:70] or "Independent Artist",
                "category": cat,
                "duration": format_duration(dur_sec),
                "duration_sec": dur_sec,
                "audio_url": audio_url,
                "cover": cover_url,
                "fallback_cover": cfg["fallback_cover"],
                "license": license_label,
                "source_url": f"https://archive.org/details/{identifier}",
                "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            })
            existing_urls.add(audio_url)
            added_from_album += 1
            time.sleep(0.25)

    return found


def main():
    existing_tracks = []
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, list):
                    existing_tracks = loaded
        except Exception:
            existing_tracks = []

    existing_urls = {item.get("audio_url") for item in existing_tracks if item.get("audio_url")}
    max_track_id = max([item.get("track_id", 1000) for item in existing_tracks], default=1000)

    new_tracks = []
    for cfg in CATEGORY_QUERIES:
        tracks = fetch_category_tracks(cfg, existing_urls)
        new_tracks.extend(tracks)

    new_tracks.reverse()
    for item in new_tracks:
        max_track_id += 1
        item["track_id"] = max_track_id
    new_tracks.reverse()

    updated = (new_tracks + existing_tracks)[:MAX_TRACKS_TOTAL]

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(updated, f, ensure_ascii=False, indent=2)

    print(f"Saved {len(updated)} tracks (added {len(new_tracks)} new). Latest ID: {max_track_id}")


if __name__ == "__main__":
    main()
