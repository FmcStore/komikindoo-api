#!/usr/bin/env python3
"""
KomikIndo REST API - MODE LIVE (tanpa penyimpanan apa pun).

Tidak ada SQLite, tidak ada cache, tidak ada file data. Setiap request
diteruskan langsung ke JSON API milik komikindo.ch
(komikindo.ch/wp-json/apk/v2/...) dan hasilnya dikembalikan dalam envelope
JSON yang seragam. Hentikan proses = tidak ada sisa data.

Catatan penting soal sumber (hasil probe, bukan asumsi):
  - /apk/v2/search  membaca parameter `s` (standar WP) + `paged`.
                    Param q/query/title/keyword DIABAIKAN upstream.
  - /apk/v2/filter  membaca `genre`, `type`, `demographic`, `theme`, `paged`.
                    `status`/`order` tidak berpengaruh (tetap diteruskan).
  - /apk/v2/list    seluruh katalog (~9.1rb judul) dalam 1 request (~7 s).

    python3 server.py [--port 8797] [--host 127.0.0.1]
"""

import argparse
import json
import os
import re
import random
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = "3.0.0"
UPSTREAM = "https://komikindo.ch/"
API_BASE = UPSTREAM + "wp-json/"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
    "Referer": UPSTREAM,
}

TIMEOUT = 60
RETRIES = 2
TYPES = ("Manga", "Manhua", "Manhwa")
TERM_KINDS = ("genres", "demographic", "theme", "content", "type", "status")


class ApiError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


# Jejak URL upstream yang benar-benar di-fetch pada request yang sedang jalan.
# Dipakai untuk mengisi field "source" dengan jujur (tanpa menebak route).
_call_log = threading.local()


def _record(url):
    paths = getattr(_call_log, "paths", None)
    if paths is None:
        paths = _call_log.paths = []
    paths.append(url)
    return paths


def _take_calls():
    paths = getattr(_call_log, "paths", None) or []
    _call_log.paths = []
    return list(paths)


# ------------------------------------------------------------------ upstream

def fetch(path, params=None, retries=RETRIES):
    """GET wp-json/<path> dan kembalikan (status, json).

    Status non-200 yang masih berisi JSON dianggap jawaban sah dan diteruskan;
    kegagalan jaringan/timeout dicoba ulang lalu jadi ApiError 502.
    """
    url = API_BASE + path.lstrip("/")
    if params:
        clean = {k: v for k, v in params.items() if v not in (None, "")}
        if clean:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(clean)
    _record(url)
    last = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers=HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                raw = r.read()
                status = r.status
            if not raw.strip():
                # Upstream membalas 200 + body kosong untuk id yang tidak ada
                # (mis. apk/v2/manga/<id ngawur>). Itu "tidak ada", bukan error.
                return status, None
            try:
                return status, json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return status, None
        except urllib.error.HTTPError as e:
            body = e.read()
            try:
                return e.code, json.loads(body.decode("utf-8"))
            except Exception:
                return e.code, None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
            if attempt < retries:
                time.sleep(0.6 * (attempt + 1))
    raise ApiError(502, "upstream %s tidak bisa dihubungi: %s" % (path, last))


def fetch_ok(path, params=None, retries=RETRIES):
    """fetch() yang mewajibkan 200 + payload; selain itu ApiError."""
    status, j = fetch(path, params, retries=retries)
    if status != 200:
        msg = j.get("message") if isinstance(j, dict) else None
        raise ApiError(status, msg or "upstream %s -> HTTP %d" % (path, status))
    if j is None:
        raise ApiError(404, "upstream %s kosong (data tidak ada)" % path)
    return j


def fetch_maybe(path, params=None, retries=RETRIES):
    """200 (termasuk body kosong -> None) dianggap sah; HTTP error -> ApiError."""
    status, j = fetch(path, params, retries=retries)
    if status != 200:
        msg = j.get("message") if isinstance(j, dict) else None
        raise ApiError(status, msg or "upstream %s -> HTTP %d" % (path, status))
    return j


def catalog():
    """Katalog penuh dari upstream (list). Berat: ~7 s, 9rb+ judul."""
    return items_of(fetch_ok("apk/v2/list"))


# ------------------------------------------------------------------- helpers

def dedup(seq):
    seen, out = set(), []
    for u in seq or []:
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def chapter_id_of(url):
    """Ambil id dari https://kmkindo.click/?page=chapter&id=158405"""
    if not url:
        return None
    m = re.search(r"[?&]id=(\d+)", str(url))
    return int(m.group(1)) if m else None


def int_arg(env, name, default=None, minimum=None, maximum=None):
    """Baca angka dari path var dulu, lalu query string."""
    raw = env["vars"].get(name)
    if raw is None:
        raw = env["query"].get(name)
    if raw is None or raw == "":
        if default is None and minimum is not None:
            raise ApiError(400, "parameter %r wajib diisi" % name)
        return default
    if not re.fullmatch(r"-?\d+", raw):
        raise ApiError(400, "parameter %r harus angka, dapat %r" % (name, raw))
    v = int(raw)
    if minimum is not None and v < minimum:
        raise ApiError(400, "parameter %r minimal %d" % (name, minimum))
    if maximum is not None and v > maximum:
        raise ApiError(400, "parameter %r maksimal %d" % (name, maximum))
    return v


def norm_chapter(c):
    if not isinstance(c, dict):
        return c
    cid = chapter_id_of(c.get("url"))
    return {
        "number": c.get("chapter"),
        "chapterId": cid,
        "api": ("/api/chapter/%d" % cid) if cid else None,
        "download": c.get("download") or None,
    }


def norm_list_item(it):
    """Item list upstream -> bentuk konsisten (daftar chapter ikut dinormalkan)."""
    if not isinstance(it, dict):
        return it
    out = dict(it)
    for key in ("data", "chapter_list"):
        if isinstance(it.get(key), list):
            out["chapters"] = [norm_chapter(c) for c in it[key]]
            del out[key]
    return out


def _as_items(j):
    if isinstance(j, list):
        return j
    if isinstance(j, dict):
        return j.get("data") or []
    return []


def items_of(j):
    return _as_items(j)


def paginate(items, page, per):
    total = len(items)
    start = (page - 1) * per
    return {
        "items": items[start:start + per],
        "pagination": {"page": page, "perPage": per, "total": total,
                       "totalPages": (total + per - 1) // per if per else 0,
                       "hasNext": start + per < total},
    }


# ------------------------------------------------------------------- handler

def h_root(env):
    return {
        "name": "KomikIndo REST API",
        "version": VERSION,
        "mode": "live",
        "storage": "none (setiap request fetch langsung ke upstream)",
        "upstream": UPSTREAM,
        "upstreamApi": API_BASE + "apk/v2/",
        "note": ("Tidak ada SQLite / cache / file data. Respons tergantung "
                 "upstream. Parameter pencarian upstream: search pakai 's', "
                 "filter pakai genre/type/demographic/theme/paged."),
        "endpoints": ENDPOINTS,
        "sankaFormat": {
            "note": ("Bentuk Sanka (drop-in /comic/komikindo): "
                     "ganti host https://www.sankavollerei.web.id dengan host "
                     "di sini, response tetap sama."),
            "base": "/comic/komikindo",
            "endpoints": SANKA_ENDPOINTS,
        },
    }


def h_health(env):
    t0 = time.time()
    try:
        fetch_ok("apk/v2/latest/1", retries=0)
        up = {"reachable": True, "ms": int((time.time() - t0) * 1000)}
    except ApiError as e:
        up = {"reachable": False, "error": e.message}
    return {"status": "ok" if up["reachable"] else "degraded",
            "mode": "live", "upstream": up, "storage": "none",
            "version": VERSION}


def h_latest(env):
    page = int_arg(env, "page", 1, minimum=1)
    items = _as_items(fetch_maybe("apk/v2/latest/%d" % page))
    return {"page": page, "total": len(items),
            "items": [norm_list_item(i) for i in items]}


def h_terpopuler(env):
    page = int_arg(env, "page", 1, minimum=1)
    items = _as_items(fetch_maybe("apk/v2/terpopuler/%d" % page))
    return {"page": page, "total": len(items),
            "items": [norm_list_item(i) for i in items]}


def h_rekomendasi(env):
    page = int_arg(env, "page", 1, minimum=1)
    items = _as_items(fetch_maybe("apk/v2/rekomendasi/%d" % page))
    return {"page": page, "total": len(items),
            "items": [norm_list_item(i) for i in items]}


def h_colorized(env):
    paged = int_arg(env, "paged", None)
    if paged is None:
        paged = int_arg(env, "page", 1, minimum=1)
    color = int_arg(env, "color", 1, minimum=0, maximum=1)
    items = _as_items(fetch_maybe("apk/v2/colorized/%d/%d" % (color, paged)))
    return {"colorized": color, "paged": paged, "page": paged,
            "total": len(items), "items": [norm_list_item(i) for i in items]}


def h_type(env):
    raw = env["vars"].get("type") or env["query"].get("type") or ""
    key = raw.strip().lower()
    m = {"manga": "Manga", "manhua": "Manhua", "manhwa": "Manhwa"}.get(key)
    if not m:
        raise ApiError(400, "type harus salah satu dari %s, dapat %r"
                       % (", ".join(TYPES), raw))
    page = int_arg(env, "page", 1, minimum=1)
    items = _as_items(fetch_maybe("apk/v2/type/%s/%d" % (m, page)))
    return {"type": m, "page": page, "total": len(items),
            "items": [norm_list_item(i) for i in items]}


def h_list(env):
    """Katalog penuh dalam SATU request upstream (9rb+ judul). Berat, live."""
    items = catalog()
    total = len(items)
    q = (env["query"].get("q") or "").strip().lower()
    type_ = (env["query"].get("type") or "").strip().lower()
    genre = (env["query"].get("genre") or "").strip().lower()
    status = (env["query"].get("status") or "").strip().lower()
    if type_:
        items = [i for i in items if (i.get("type") or "").lower() == type_]
    if status:
        items = [i for i in items if (i.get("status") or "").lower() == status]
    if genre:
        items = [i for i in items
                 if genre in [str(g).lower() for g in (i.get("genre") or [])]]
    if q:
        items = [i for i in items if q in (i.get("title") or "").lower()]
    sort = (env["query"].get("sort") or "").strip().lower()
    if sort == "score":
        def num(v):
            try:
                return float(v)
            except (TypeError, ValueError):
                return 0.0
        items = sorted(items, key=lambda i: num(i.get("score")), reverse=True)
    page = int_arg(env, "page", 1, minimum=1)
    per = int_arg(env, "per_page", 24, minimum=1, maximum=500)
    out = paginate([norm_list_item(i) for i in items], page, per)
    out["pagination"]["totalUpstream"] = total
    out["pagination"]["filters"] = {"q": q or None, "type": type_ or None,
                                    "genre": genre or None,
                                    "status": status or None,
                                    "sort": sort or None}
    return out


def h_search(env):
    """Cari judul. Upstream membaca parameter `s` (bukan q)."""
    q = (env["query"].get("q") or env["query"].get("s") or "").strip()
    if not q:
        raise ApiError(400, "parameter 'q' wajib diisi")
    page = int_arg(env, "page", 1, minimum=1)
    per = int_arg(env, "per_page", 0, minimum=0, maximum=500)
    items = [norm_list_item(i) for i in
             _as_items(fetch_ok("apk/v2/search", {"s": q, "paged": page}))]
    out = {"query": q, "page": page, "total": len(items), "items": items}
    if per:
        out = paginate(items, 1, per)
        out.update({"query": q, "page": page})
    return out


def h_filter(env):
    allowed = ("genre", "demographic", "theme", "type", "status", "order",
               "title", "chapter")
    params = {k: v for k, v in env["query"].items() if k in allowed}
    params["paged"] = int_arg(env, "paged", None) or int_arg(env, "page", 1, minimum=1)
    items = [norm_list_item(i) for i in _as_items(fetch_ok("apk/v2/filter", params))]
    return {"filters": params, "total": len(items), "items": items}


def h_terms(env):
    """Daftar nilai sebuah jenis term, dihitung dari katalog penuh (live)."""
    kind = (env["vars"].get("kind") or env["query"].get("kind") or "genres").lower()
    if kind not in TERM_KINDS:
        raise ApiError(400, "kind harus salah satu dari %s, dapat %r"
                       % (", ".join(TERM_KINDS), kind))
    items = catalog()
    bucket = {}
    if kind == "type":
        for i in items:
            t = i.get("type")
            if t:
                bucket[t] = bucket.get(t, 0) + 1
    elif kind == "status":
        for i in items:
            s = i.get("status")
            if s:
                bucket[s] = bucket.get(s, 0) + 1
    else:
        key = {"genres": "genre", "demographic": "demographic",
               "theme": "theme", "content": "content"}[kind]
        for i in items:
            for g in (i.get(key) or []):
                name = g.get("name") if isinstance(g, dict) else str(g)
                if not name:
                    continue
                slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
                bucket.setdefault(slug, {"name": name, "count": 0})["count"] += 1
    if kind in ("type", "status"):
        rows = [{"slug": re.sub(r"[^a-z0-9]+", "-", k.lower()).strip("-"),
                 "name": k, "count": v}
                for k, v in sorted(bucket.items(), key=lambda kv: -kv[1])]
    else:
        rows = [dict(v, slug=s) for s, v in
                sorted(bucket.items(), key=lambda kv: -kv[1]["count"])]
    return {"kind": kind, "total": len(rows), "catalogSize": len(items),
            "items": rows}


def h_term_result(env):
    kind = env["vars"].get("kind") or env["query"].get("kind") or "genres"
    value = env["vars"].get("value") or env["query"].get("value") or ""
    if kind.lower() not in TERM_KINDS:
        raise ApiError(400, "kind harus salah satu dari %s, dapat %r"
                       % (", ".join(TERM_KINDS), kind))
    if not value:
        raise ApiError(400, "value wajib diisi")
    if not re.fullmatch(r"[a-zA-Z0-9-]+", value):
        raise ApiError(400, "value hanya huruf/angka/strip, dapat %r" % value)
    page = int_arg(env, "page", 1, minimum=1)
    items = [norm_list_item(i) for i in _as_items(
        fetch_maybe("apk/v2/termresult/%s/%s/%d" % (kind.lower(), value, page)))]
    return {"kind": kind.lower(), "value": value, "page": page,
            "total": len(items), "items": items}


def _series_detail(series_id):
    j = fetch_maybe("apk/v2/manga/%d" % series_id)
    if not isinstance(j, list) or not j:
        raise ApiError(404, "series %s tidak ada di upstream" % series_id)
    d = dict(j[0])
    chapters = [norm_chapter(c) for c in (d.pop("data", None) or [])]
    return {
        "seriesId": series_id,
        "title": d.get("title"),
        "type": d.get("type"),
        "status": d.get("status"),
        "score": d.get("score"),
        "author": d.get("author"),
        "released": d.get("released"),
        "cover": d.get("cover"),
        "img": d.get("img"),
        "genre": d.get("genre"),
        "demographic": d.get("demographic"),
        "theme": d.get("theme"),
        "content": d.get("content"),
        "synopsis": d.get("synopsis"),
        "chapterCount": len(chapters),
        "latestChapter": chapters[0] if chapters else None,
        "firstChapter": chapters[-1] if chapters else None,
        "chapters": chapters,
    }


def resolve_slug(slug):
    """slug -> series id, murni live (wp/v2/manga?search=)."""
    words = urllib.parse.unquote(slug).replace("-", " ").strip()
    if not words:
        raise ApiError(400, "slug kosong")
    j = fetch_ok("wp/v2/manga", {"search": words, "per_page": 20})
    rows = j if isinstance(j, list) else []
    want = urllib.parse.unquote(slug).strip().lower()
    for r in rows:
        if (r.get("slug") or "").lower() == want:
            return r.get("id"), r.get("slug")
    squash = want.replace("-", "")
    for r in rows:
        if (r.get("slug") or "").lower().replace("-", "") == squash:
            return r.get("id"), r.get("slug")
    if rows:
        return rows[0].get("id"), rows[0].get("slug")
    raise ApiError(404, "slug %r tidak ditemukan di upstream" % slug)


def _series_key(env):
    key = env["vars"]["key"]
    if re.fullmatch(r"\d+", key):
        return int(key), None
    env["source"].append("live:wp/v2/manga?search= (resolusi slug)")
    sid, real = resolve_slug(key)
    if not sid:
        raise ApiError(404, "slug %r tidak bisa diresolusi" % key)
    return int(sid), real


def h_series(env):
    key = env["vars"]["key"]
    sid, real = _series_key(env)
    out = _series_detail(sid)
    if real:
        out["slug"] = real
        out["requestedSlug"] = key
    return out


def h_series_chapters(env):
    key = env["vars"]["key"]
    sid, real = _series_key(env)
    d = _series_detail(sid)
    if real:
        d["slug"] = real
    per = int_arg(env, "per_page", 100, minimum=1, maximum=1000)
    page = int_arg(env, "page", 1, minimum=1)
    ch = d.get("chapters") or []
    total = len(ch)
    start = (page - 1) * per
    return {"seriesId": d["seriesId"], "title": d.get("title"),
            "slug": d.get("slug"), "chapterCount": total,
            "items": ch[start:start + per],
            "pagination": {"page": page, "perPage": per, "total": total,
                           "totalPages": (total + per - 1) // per,
                           "hasNext": start + per < total}}


def _chapter_payload(cid):
    j = fetch_maybe("apk/v2/chapter/%d" % cid)
    if not isinstance(j, dict):
        raise ApiError(404, "chapter %s tidak ada di upstream" % cid)
    imgs = dedup(j.get("image") or [])
    hosts = {}
    for u in imgs:
        try:
            h = urllib.parse.urlsplit(u).netloc
        except ValueError:
            continue
        if h:
            hosts[h] = hosts.get(h, 0) + 1
    nxt, prv = j.get("next"), j.get("prev")
    return {
        "chapterId": cid,
        "title": j.get("title"),
        "number": str(j.get("chapter")) if j.get("chapter") is not None else None,
        "imageCount": len(imgs),
        "images": imgs,
        "hosts": hosts,
        "thumb": j.get("thumb"),
        "prev": {"chapterId": chapter_id_of(prv), "api": "/api/chapter/%s" % chapter_id_of(prv),
                 "url": prv} if prv else None,
        "next": {"chapterId": chapter_id_of(nxt), "api": "/api/chapter/%s" % chapter_id_of(nxt),
                 "url": nxt} if nxt else None,
        "webUrl": j.get("link"),
    }


def h_chapter(env):
    return _chapter_payload(int_arg(env, "cid", minimum=1))


def h_chapter_images(env):
    cid = int_arg(env, "cid", minimum=1)
    d = _chapter_payload(cid)
    return {"chapterId": cid, "count": d["imageCount"],
            "hosts": d["hosts"], "images": d["images"]}


def h_series_chapter_by_number(env):
    key = env["vars"]["key"]
    raw = urllib.parse.unquote(env["vars"]["number"]).strip()
    number = raw.lower()
    sid, _ = _series_key(env)
    d = _series_detail(int(sid))
    ch = d.get("chapters") or []
    hit = next((c for c in ch
                if str(c.get("number") or "").strip().lower() == number), None)
    if hit is None:
        hit = next((c for c in ch if str(c.get("number") or "")
                    .strip().lower().startswith(number)), None)
    if hit is None or not hit.get("chapterId"):
        raise ApiError(404, "chapter nomor %r tidak ada di %s" % (raw, d.get("title")))
    return _chapter_payload(hit["chapterId"])


def h_random(env):
    items = catalog()
    if not items:
        raise ApiError(502, "katalog upstream kosong")
    pick = random.choice(items)
    sid = chapter_id_of(pick.get("url"))
    out = _series_detail(int(sid))
    out["randomPick"] = norm_list_item(pick)
    return out


def h_wp_manga(env):
    """Passthrough wp/v2/manga (pencarian slug / id WP)."""
    allowed = ("search", "slug", "page", "per_page", "orderby", "order")
    params = {k: v for k, v in env["query"].items() if k in allowed}
    rows = fetch_ok("wp/v2/manga", params)
    rows = rows if isinstance(rows, list) else []
    return {"total": len(rows),
            "items": [{"id": r.get("id"), "slug": r.get("slug"),
                       "title": (r.get("title") or {}).get("rendered"),
                       "link": r.get("link"), "date": r.get("date"),
                       "modified": r.get("modified")} for r in rows]}


ENDPOINTS = [
    {"method": "GET", "path": "/api", "desc": "Katalog endpoint (identik dengan /)"},
    {"method": "GET", "path": "/api/health", "desc": "Cek server + keterjangkauan upstream"},
    {"method": "GET", "path": "/api/latest?page=1", "desc": "Rilis terbaru (10 per halaman)"},
    {"method": "GET", "path": "/api/terpopuler?page=1", "desc": "Terpopuler (5 per halaman)"},
    {"method": "GET", "path": "/api/rekomendasi?page=1", "desc": "Rekomendasi (5 per halaman)"},
    {"method": "GET", "path": "/api/colorized?color=1&page=1", "desc": "Judul berwarna (10 per halaman)"},
    {"method": "GET", "path": "/api/type/{Manga|Manhua|Manhwa}?page=1", "desc": "Per tipe (5 per halaman)"},
    {"method": "GET", "path": "/api/terms/{genres|demographic|theme|content|type|status}", "desc": "Daftar nilai term + jumlah judul (hitung dari katalog)"},
    {"method": "GET", "path": "/api/term/{kind}/{value}?page=1", "desc": "Judul per term, mis. /api/term/genres/action?page=2"},
    {"method": "GET", "path": "/api/list?page=1&per_page=24&type=&genre=&status=&q=&sort=score", "desc": "Katalog penuh (1 fetch upstream), bisa difilter + dipaginasi"},
    {"method": "GET", "path": "/api/search?q=&page=1&per_page=", "desc": "Cari judul (upstream pakai parameter s)"},
    {"method": "GET", "path": "/api/filter?genre=&type=&demographic=&theme=&paged=1", "desc": "Filter lanjutan"},
    {"method": "GET", "path": "/api/series/{id|slug}", "desc": "Detail series + SELURUH daftar chapter"},
    {"method": "GET", "path": "/api/series/{id|slug}/chapters?page=1&per_page=100", "desc": "Daftar chapter (paginasi)"},
    {"method": "GET", "path": "/api/series/{id|slug}/chapters/{number}", "desc": "Chapter by nomor, mis. /1.1"},
    {"method": "GET", "path": "/api/chapter/{chapterId}", "desc": "Isi chapter: daftar URL gambar + prev/next"},
    {"method": "GET", "path": "/api/chapter/{chapterId}/images", "desc": "Hanya array URL gambar + host"},
    {"method": "GET", "path": "/api/random", "desc": "Satu series acak + detailnya"},
    {"method": "GET", "path": "/api/wp/manga?search=&per_page=", "desc": "Passthrough pencarian WP (slug -> id)"},
]

ROUTES = [
    r"^/api$",
    r"^/api/health$",
    r"^/api/latest$",
    r"^/api/terpopuler$",
    r"^/api/rekomendasi$",
    r"^/api/colorized$",
    r"^/api/colorized/(?P<paged>\d+)$",
    r"^/api/type$",
    r"^/api/type/(?P<type>[A-Za-z]+)$",
    r"^/api/terms$",
    r"^/api/terms/(?P<kind>[A-Za-z]+)$",
    r"^/api/genres$",
    r"^/api/term/(?P<kind>[A-Za-z]+)/(?P<value>[^/]+)$",
    r"^/api/termresult/(?P<kind>[A-Za-z]+)/(?P<value>[^/]+)/(?P<page>\d+)$",
    r"^/api/list$",
    r"^/api/search$",
    r"^/api/filter$",
    r"^/api/random$",
    r"^/api/wp/manga$",
    r"^/api/series/(?P<key>[^/]+)$",
    r"^/api/series/(?P<key>[^/]+)/chapters$",
    r"^/api/series/(?P<key>[^/]+)/chapters/(?P<number>[^/]+)$",
    r"^/api/chapter/(?P<cid>\d+)$",
    r"^/api/chapter/(?P<cid>\d+)/images$",
]
HANDLERS = [
    h_root, h_health, h_latest, h_terpopuler, h_rekomendasi, h_colorized,
    h_colorized, h_type, h_type, h_terms, h_terms, h_terms, h_term_result,
    h_term_result, h_list, h_search, h_filter, h_random, h_wp_manga,
    h_series, h_series_chapters, h_series_chapter_by_number, h_chapter,
    h_chapter_images,
]
COMPILED = [(re.compile(p), fn) for p, fn in zip(ROUTES, HANDLERS)]

# ---------------------------------------------------- format Sanka (drop-in)
# /comic/komikindo/* menjawab dengan bentuk milik Sanka apa adanya (tanpa
# envelope {ok,mode,data}), supaya frontend yang sudah pakai Sanka tinggal
# mengganti host. Lihat sanka.py.
try:
    import sanka
    RAW_ROUTES = [(re.compile(p), fn) for p, fn in
                  zip(sanka.ROUTES, sanka.HANDLERS)]
    SANKA_ENDPOINTS = sanka.ENDPOINTS
    SankaError = sanka.SankaError
    CREATOR = sanka.CREATOR
except Exception as _e:                                          # noqa: BLE001
    RAW_ROUTES, SANKA_ENDPOINTS = [], []
    CREATOR = "Sanka Vollerei"

    class SankaError(Exception):
        def __init__(self, code, message):
            super().__init__(message)
            self.code, self.message = code, message

    sys.stderr.write("sanka.py tidak bisa dimuat: %s\n" % _e)


# -------------------------------------------------------------------- server

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "KomikIndoLive/" + VERSION

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), fmt % args))
        sys.stderr.flush()

    def _send(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_raw(self, code, payload):
        """Kirim badan JSON apa adanya (dipakai rute format Sanka)."""
        self._send(code, payload)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        t0 = time.time()
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path.rstrip("/") or "/"
        q = urllib.parse.parse_qs(parsed.query, keep_blank_values=False)
        env = {"params": q, "query": {k: v[0] for k, v in q.items()},
               "vars": {}, "source": []}
        _take_calls()          # reset jejak upstream untuk request ini

        # Domain root: tampilkan katalog format Sanka (base URL yang dipakai
        # frontend adalah <host>/comic/komikindo). /api/* tetap hidup.
        if path == "/" and SANKA_ENDPOINTS:
            try:
                self._send_raw(200, sanka.h_root(env))
            except Exception:                                     # noqa: BLE001
                pass
            return
        if path == "/":
            path = "/api"

        # ---- rute format Sanka (/comic/komikindo/*) didahulukan ----
        raw_fn = None
        raw_m = None
        for rx, fn in RAW_ROUTES:
            raw_m = rx.match(path)
            if raw_m:
                raw_fn = fn
                break
        if raw_fn is not None:
            env["vars"] = raw_m.groupdict()
            try:
                body = raw_fn(env)
                self._send_raw(200, body)
            except SankaError as e:
                self._send_raw(e.code, {"creator": CREATOR,
                                        "success": False,
                                        "message": e.message})
            except Exception as e:  # noqa: BLE001
                self._send_raw(500, {"creator": CREATOR, "success": False,
                                     "message": "%s: %s" % (type(e).__name__, e)})
            return

        if path.startswith("/comic/"):
            # Sanka menjawab 403 + pesan AI detector untuk endpoint yang salah.
            self._send_raw(403, {"status": "Plana AI Detector",
                                 "creator": CREATOR,
                                 "message": ("Mungkin Kamu Salah Endpoint..."
                                             "harap baca baik baik ya dokumentasinya")})
            return

        fn, m = None, None
        for rx, handler in COMPILED:
            m = rx.match(path)
            if m:
                fn = handler
                break
        if fn is None:
            self._send(404, {"ok": False, "mode": "live", "error": {
                "code": 404, "message": "route %r tidak ada. lihat GET /api" % path}})
            return
        env["vars"] = m.groupdict()

        try:
            data = fn(env)
            self._send(200, {"ok": True, "mode": "live", "cached": False,
                             "storage": "none",
                             "elapsedMs": int((time.time() - t0) * 1000),
                             "source": env["source"] or _take_calls(),
                             "data": data})
        except ApiError as e:
            self._send(e.code, {"ok": False, "mode": "live",
                                "elapsedMs": int((time.time() - t0) * 1000),
                                "error": {"code": e.code, "message": e.message}})
        except Exception as e:  # noqa: BLE001 - jangan biarkan server mati
            self._send(500, {"ok": False, "mode": "live",
                             "error": {"code": 500, "message": "%s: %s" % (
                                 type(e).__name__, e)}})


def main():
    ap = argparse.ArgumentParser(description="KomikIndo REST API (live fetch)")
    ap.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8797")))
    a = ap.parse_args()
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    srv.daemon_threads = True
    print("KomikIndo REST API %s (LIVE, tanpa penyimpanan) di http://%s:%d"
          % (VERSION, a.host, a.port))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
