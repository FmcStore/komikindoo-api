#!/usr/bin/env python3
"""
KomikIndo dalam format Sanka (drop-in untuk /comic/komikindo).

Tujuan: frontend yang sudah memakai Sanka cukup mengganti host URL, tanpa
mengubah parser. Bentuk respons (kunci, tipe, susunan) disamakan dengan
https://www.sankavollerei.web.id/comic/komikindo/...; isinya diambil LIVE
dari komikindo.ch (HTML + JSON API-nya sendiri).

Endpoint:
  GET /comic/komikindo                       katalog endpoint
  GET /comic/komikindo/latest/:page          rilis terbaru (40/halaman)
  GET /comic/komikindo/detail/:slug          detail + seluruh daftar chapter
  GET /comic/komikindo/chapter/:slug         baca chapter (daftar gambar)
  GET /comic/komikindo/library?page=1        katalog A-Z (30/halaman) + filter
  GET /comic/komikindo/genres                daftar genre
  GET /comic/komikindo/search/:query/:page   cari judul (20/halaman)

Semua tanpa penyimpanan: tidak ada file/DB, murni fetch saat request.
"""

import json
import re
import html as htmlmod
import urllib.error
import urllib.parse
import urllib.request
import concurrent.futures as cf

CREATOR = "Sanka Vollerei"
UPSTREAM = "https://komikindo.ch/"
API_BASE = UPSTREAM + "wp-json/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/json,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
    "Referer": UPSTREAM,
}
TIMEOUT = 45
WORKERS = 12

# pesan error persis seperti Sanka, supaya frontend yang membaca `message`
# tetap dapat teks yang sama.
MSG_DETAIL = "Error fetching komik detail from Komikindo"
MSG_CHAPTER = "Error fetching chapter from Komikindo"
MSG_LIST = "Error fetching komik list from Komikindo"


class SankaError(Exception):
    """Kesalahan yang sudah berbentuk respons Sanka."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


# --------------------------------------------------------------- http helpers

def _get(url, accept="text/html"):
    """GET url -> (status, body_text). Tidak melempar untuk HTTP error."""
    req = urllib.request.Request(url, headers=dict(HEADERS, Accept=accept))
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, r.read().decode("utf-8", "replace"), dict(r.headers)
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        return e.code, body, dict(e.headers or {})
    except Exception:                                             # noqa: BLE001
        return None, "", {}


def jget(path, params=None):
    """GET wp-json/<path> -> parsed JSON, atau None kalau gagal/kosong."""
    url = API_BASE + path.lstrip("/")
    if params:
        clean = {k: v for k, v in params.items() if v not in (None, "")}
        if clean:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(clean)
    status, body, _ = _get(url, "application/json")
    if status != 200 or not body.strip():
        return None
    try:
        return json.loads(body)
    except Exception:                                             # noqa: BLE001
        return None


def jget_many(specs):
    """specs: [(path, params)] -> [json|None] paralel (urutan dipertahankan)."""
    if not specs:
        return []
    out = [None] * len(specs)
    with cf.ThreadPoolExecutor(max_workers=min(WORKERS, max(1, len(specs)))) as ex:
        futs = {ex.submit(jget, p, q): i for i, (p, q) in enumerate(specs)}
        for f in cf.as_completed(futs):
            i = futs[f]
            try:
                out[i] = f.result()
            except Exception:                                     # noqa: BLE001
                out[i] = None
    return out


def hget_many(urls):
    out = [None] * len(urls)
    with cf.ThreadPoolExecutor(max_workers=min(WORKERS, max(1, len(urls)))) as ex:
        futs = {ex.submit(_get, u): i for i, u in enumerate(urls)}
        for f in cf.as_completed(futs):
            i = futs[f]
            try:
                status, body, _ = f.result()
                out[i] = body if status == 200 else None
            except Exception:                                     # noqa: BLE001
                out[i] = None
    return out


# ------------------------------------------------------------------ helpers

def clean(s):
    return re.sub(r"\s+", " ", htmlmod.unescape(s or "")).strip()


def items_of(j):
    if isinstance(j, list):
        return [i for i in j if isinstance(i, dict)]
    if isinstance(j, dict):
        return [i for i in (j.get("data") or []) if isinstance(i, dict)]
    return []


def id_of(url):
    m = re.search(r"[?&]id=(\d+)", str(url or ""))
    return int(m.group(1)) if m else None


def path_slug(url):
    """https://komikindo.ch/xxx-yyy-chapter-12/ -> xxx-yyy-chapter-12"""
    try:
        p = urllib.parse.urlsplit(url).path.strip("/")
    except Exception:                                             # noqa: BLE001
        return None
    return p.split("/")[-1] or None


def clean_slug(slug):
    """'508249-i-became-...' -> 'i-became-...' (komikindo pakai slug bab tanpa prefix angka)."""
    return re.sub(r"^\d+-", "", slug or "")


def norm_chapter_token(text):
    """'179 End' -> '179-end', '364.1' -> '364-1', '00' -> '00'."""
    t = re.sub(r"[^0-9a-zA-Z.]+", " ", str(text or "")).strip().lower()
    t = t.replace(".", "-")
    return re.sub(r"[\s]+", "-", t).strip("-")


def rating_of(v):
    if v is None:
        return "0"
    s = str(v).strip()
    if not s:
        return "0"
    try:
        return "%.2f" % float(s)
    except ValueError:
        return s


def color_of(v):
    return "Warna" if str(v) in ("1", "true", "True") else "Hitam"


_TITLE_FIX = {
    "boys-love": "Boys' Love", "girls-love": "Girls' Love", "sci-fi": "Sci-Fi",
    "shoujo-ai": "Shoujo Ai", "shounen-ai": "Shounen Ai",
    "slice-of-life": "Slice of Life", "post-apocalyptic": "Post-Apocalyptic",
    "time-travel": "Time Travel", "reverse-harem": "Reverse Harem",
    "video-games": "Video Games", "office-workers": "Office Workers",
    "traditional-games": "Traditional Games", "virtual-reality": "Virtual Reality",
    "sexual-violence": "Sexual Violence", "monster-girls": "Monster Girls",
    "gender-bender": "Gender Bender", "genderswap": "Genderswap",
    "gender-swap": "Gender Swap", "school-life": "School Life",
    "martial-arts": "Martial Arts", "magical-girls": "Magical Girls",
    "drama-supernatural": "Drama Supernatural", "sci_fi": "Sci-Fi",
}


def humanize(slug):
    s = (slug or "").strip().lower()
    if s in _TITLE_FIX:
        return _TITLE_FIX[s]
    return " ".join(w.capitalize() for w in re.split(r"[-_]+", s) if w)


# ------------------------------------------------- WP (slug & id) helpers

def wp_by_slug(slug):
    rows = jget("wp/v2/manga", {"slug": slug, "per_page": 1,
                                "_fields": "id,slug,title,link,modified"})
    if isinstance(rows, list) and rows:
        r = rows[0]
        return {"id": r.get("id"), "slug": r.get("slug"),
                "title": (r.get("title") or {}).get("rendered"),
                "link": r.get("link")}
    return None


def wp_resolve(slug):
    """Resolusi slug serba bisa.

    Komikindo menyimpan slug dengan prefix angka ('508249-judul'), sementara
    Sanka (dan tautan bab milik situs) memakai bentuk tanpa prefix
    ('judul'). Fungsi ini menerima kedua bentuk.
    """
    slug = (slug or "").strip().strip("/")
    if not slug:
        return None
    post = wp_by_slug(slug)
    if post:
        return post
    plain = clean_slug(slug)
    words = re.sub(r"[-_]+", " ", plain).strip()
    if not words:
        return None
    rows = jget("wp/v2/manga", {"search": words, "per_page": 20,
                               "_fields": "id,slug,title,link"})
    if not isinstance(rows, list):
        return None
    norm = re.sub(r"[^a-z0-9]", "", plain.lower())
    cand = None
    for r in rows:
        s = (r.get("slug") or "")
        if s.lower() == slug.lower() or clean_slug(s).lower() == plain.lower():
            cand = r
            break
        if norm and re.sub(r"[^a-z0-9]", "", clean_slug(s).lower()) == norm:
            cand = r
            break
    if cand is None and rows:
        cand = rows[0]
    if not cand:
        return None
    return {"id": cand.get("id"), "slug": cand.get("slug"),
            "title": (cand.get("title") or {}).get("rendered"),
            "link": cand.get("link")}


def wp_slug_map(ids):
    """[id] -> {id: slug} dalam satu request (include)."""
    ids = [str(i) for i in ids if i]
    if not ids:
        return {}
    rows = jget("wp/v2/manga", {"include": ",".join(ids), "per_page": 100,
                                "orderby": "include", "_fields": "id,slug"})
    out = {}
    if isinstance(rows, list):
        for r in rows:
            if r.get("id"):
                out[int(r["id"])] = r.get("slug")
    return out


def wp_total():
    """Jumlah post manga dari header X-WP-Total (untuk totalPages)."""
    status, body, hdrs = _get(API_BASE + "wp/v2/manga?per_page=1&_fields=id",
                              "application/json")
    for k, v in (hdrs or {}).items():
        if k.lower() == "x-wp-total":
            try:
                return int(v)
            except ValueError:
                pass
    return None


def catalog():
    return items_of(jget("apk/v2/list"))


# ---------------------------------------------------------- HTML parsers

def parse_manga_page(doc, series_slug):
    """Parse halaman /komik/<slug>/ -> info + chapter list + spoiler + mirip."""
    out = {"chapters": [], "spoilerImages": [], "similarManga": [],
           "info": {}, "title": None, "image": None, "rating": None,
           "votes": None, "description": None}
    if not doc:
        return out

    m = re.search(r"<h1[^>]*>(.*?)</h1>", doc, re.S)
    if m:
        out["title"] = clean(re.sub(r"<[^>]+>", " ", m.group(1)))
    m = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', doc)
    if not m:
        m = re.search(r'itemprop="image"[^>]+src="([^"]+)"', doc)
    if m:
        out["image"] = htmlmod.unescape(m.group(1))
    m = re.search(r'itemprop="ratingValue">\s*([\d.,]+)', doc)
    if m:
        out["rating"] = m.group(1).strip()
    m = re.search(r'votescount">\s*([^<]+)', doc)
    if m:
        out["votes"] = clean(m.group(1))

    # blok info <div class="spe">: <span><b>Label:</b> nilai</span>
    sp = re.search(r'<div class="spe">(.*?)</div>\s*</div>', doc, re.S)
    if sp:
        for lab, val in re.findall(r"<b>\s*([^<:]+):\s*</b>(.*?)</span>", sp.group(1), re.S):
            v = clean(re.sub(r"<[^>]+>", " ", val))
            # Sanka menulis pemisah tanpa spasi ganda: 'Magic, Monsters'
            v = re.sub(r"\s+,\s*", ", ", v).strip()
            out["info"][clean(lab).lower()] = v

    m = re.search(r'id="sinopsis".*?itemprop="description"[^>]*>(.*?)</div>',
                  doc, re.S)
    if not m:
        m = re.search(r'class="desc">.*?<p>(.*?)</p>', doc, re.S)
    if m:
        out["description"] = clean(re.sub(r"<[^>]+>", " ", m.group(1)))

    # daftar chapter lengkap: <span class="lchx"><a href title>Chapter N</a></span>
    #                      <span class="dt"><a href>waktu</a></span>
    for a in re.finditer(
            r'<span class="lchx">\s*<a href="([^"]+)"[^>]*?(?:title="([^"]*)")?[^>]*>(.*?)</a>'
            r'\s*</span>\s*<span class="dt"><a[^>]*>(.*?)</a>', doc, re.S):
        url, attr_title, inner, date = a.group(1), a.group(2), a.group(3), a.group(4)
        slug = path_slug(url)
        if not slug or "-chapter-" not in slug:
            continue
        title = clean(re.sub(r"<[^>]+>", " ", inner))
        if not title and attr_title:
            title = re.sub(r"^Komik\s*", "", clean(attr_title))
        out["chapters"].append({"title": title, "slug": slug,
                                "date": clean(date), "url": url})
    if not out["chapters"]:
        # fallback: semua anchor yang berpola chapter
        for a in re.finditer(r'<a href="(https://komikindo\.ch/[^"]*-chapter-[^"]+/)"[^>]*>(.*?)</a>',
                             doc, re.S):
            slug = path_slug(a.group(1))
            t = clean(re.sub(r"<[^>]+>", " ", a.group(2)))
            if slug and "-chapter-" in slug and t:
                out["chapters"].append({"title": t, "slug": slug,
                                        "date": None, "url": a.group(1)})
    # buang duplikat (mobile/desktop), pertahankan urutan (terbaru dulu)
    seen, uniq = set(), []
    for c in out["chapters"]:
        if c["slug"] in seen:
            continue
        seen.add(c["slug"])
        uniq.append(c)
    out["chapters"] = uniq

    # tab Spoiler -> gambar
    sp = re.search(r'<div class="spoiler" id="spoiler">(.*?)</div>\s*<div class="widget_senction',
                   doc, re.S)
    block = sp.group(1) if sp else ""
    out["spoilerImages"] = [htmlmod.unescape(u) for u in
                            re.findall(r"<img[^>]+src=['\"]([^'\"]+)['\"]", block)]

    # tab Mirip -> similarManga
    mi = re.search(r'id="mirip">(.*?)</div>\s*</div>\s*</section>', doc, re.S)
    block = mi.group(1) if mi else ""
    for li in re.findall(r"<li>(.*?)</li>", block, re.S)[:8]:
        href = re.search(r'<a class="series" href="([^"]+)"', li)
        img = re.search(r'<img[^>]+src="([^"]+)"', li)
        ttl = re.search(r'<h3>\s*<a[^>]*>(.*?)</a>', li, re.S)
        dsc = re.search(r'class="excerptmirip">(.*?)</div>', li, re.S)
        slug = path_slug(href.group(1)) if href else None
        if not slug:
            continue
        out["similarManga"].append({
            "title": clean(re.sub(r"<[^>]+>", " ", ttl.group(1))) if ttl else None,
            "slug": slug,
            "image": htmlmod.unescape(img.group(1)) if img else None,
            "description": clean(re.sub(r"<[^>]+>", " ", dsc.group(1))) if dsc else None,
        })
    return out


def parse_chapter_page(doc):
    """Parse halaman baca -> judul, id chapter, gambar, prev/next, slug manga."""
    out = {"title": None, "chapterId": None, "images": [],
           "prev": None, "next": None, "mangaSlug": None, "image": None}
    if not doc:
        return out
    m = re.search(r"<h1[^>]*>(.*?)</h1>", doc, re.S)
    if m:
        out["title"] = clean(re.sub(r"<[^>]+>", " ", m.group(1)))
    if not out["title"]:
        m = re.search(r"<title>(.*?)</title>", doc, re.S)
        if m:
            out["title"] = re.sub(r"\s*Bahasa Indonesia\s*-\s*KomikIndo\s*$", "",
                                  clean(m.group(1)))
    m = re.search(r'<article id="post-(\d+)"', doc)
    if m:
        out["chapterId"] = int(m.group(1))
    m = re.search(r'class="[^"]*category-([a-z0-9-]+)[^"]*"', doc)
    if m:
        out["mangaSlug"] = m.group(1)
    m = re.search(r'href="([^"]+)"[^>]*rel="prev"', doc)
    if m:
        out["prev"] = path_slug(m.group(1))
    m = re.search(r'href="([^"]+)"[^>]*rel="next"', doc)
    if m:
        out["next"] = path_slug(m.group(1))

    # area baca: <div class="chapter-image ..." id="Baca_Komik"> ... </div>
    i = doc.find('id="Baca_Komik"')
    area = doc[i:] if i >= 0 else doc
    # buang div iklan (kelas gulai_*/ads*) beserta isinya
    area = re.sub(r"<div class=\"(gulai_[^\"]*|ads?[^\"]*)\".*?</div>", " ", area, flags=re.S)
    urls = []
    for src in re.findall(r"<img[^>]+(?:data-src|src)=\"([^\"]+)\"", area):
        u = htmlmod.unescape(src)
        host = urllib.parse.urlsplit(u).netloc.lower()
        if not u.startswith("http"):
            continue
        if "blogger.googleusercontent" in host or "googleusercontent" in host:
            continue
        if host.endswith("komikindo.ch") or host.startswith("i0.wp.com") or host.startswith("i1.wp.com"):
            continue
        urls.append(u)
    seen, uniq = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    out["images"] = uniq
    return out


# ------------------------------------------------------------------ models

def populer_items(ids_authors=None):
    """Top-10 terpopuler -> bentuk komikPopuler Sanka."""
    pages = jget_many([("apk/v2/terpopuler/1", None), ("apk/v2/terpopuler/2", None)])
    rows = []
    for p in pages:
        rows.extend(items_of(p))
    rows = rows[:10]
    ids = [id_of(r.get("url")) for r in rows]
    slugmap = wp_slug_map([i for i in ids if i])
    details = jget_many([("apk/v2/manga/%d" % i, None) for i in ids if i]) \
        if ids_authors is None else []
    authors = {}
    for i, d in zip([i for i in ids if i], details):
        try:
            names = [a.get("name") for a in (d[0].get("author") or []) if a.get("name")]
        except Exception:                                         # noqa: BLE001
            names = []
        if names:
            authors[i] = ", ".join(names) if len(names) > 1 else names[0]
    out = []
    for n, r in enumerate(rows):
        sid = id_of(r.get("url"))
        auth = authors.get(sid)
        if not auth:
            d = r.get("data") or {}
            auth = r.get("author") or r.get("views")
        out.append({
            "rank": str(n + 1),
            "title": r.get("title"),
            "slug": slugmap.get(sid) or (str(sid) if sid else None),
            "author": auth,
            "rating": rating_of(r.get("score")),
            "image": r.get("img"),
        })
    return out


def latest_list_item(r, slug):
    """Item /apk/v2/latest -> bentuk komikList Sanka (punya chapters[])."""
    d = r.get("data") if isinstance(r.get("data"), dict) else {}
    chapters = []
    if d.get("chapter"):
        chapters.append({
            "title": "Chapter %s" % str(d.get("chapter")).strip(),
            "slug": "%s-chapter-%s" % (clean_slug(slug), norm_chapter_token(d.get("chapter"))),
            "date": d.get("time"),
        })
    return {
        "title": r.get("title"),
        "slug": slug,
        "image": r.get("img"),
        "type": r.get("type"),
        "color": color_of(r.get("colorized")),
        "chapters": chapters,
    }


def lib_item(r, slug):
    return {
        "title": r.get("title"),
        "rating": rating_of(r.get("score")),
        "slug": slug,
        "image": r.get("img"),
        "type": r.get("type"),
        "color": color_of(r.get("colorized")),
    }


def search_item(r, slug):
    return {
        "title": r.get("title"),
        "rating": rating_of(r.get("score")),
        "slug": slug,
        "image": r.get("img"),
        "type": r.get("type"),
    }


def pagination(page, total_pages):
    has_next = bool(total_pages) and page < total_pages
    return {"currentPage": page, "totalPages": total_pages,
            "hasNextPage": has_next, "nextPage": (page + 1) if has_next else None}


# ------------------------------------------------------------------ handlers

def h_root(env):
    return {
        "creator": CREATOR,
        "success": True,
        "status": "ok",
        "name": "KomikIndo REST API (format Sanka)",
        "version": "1.0.0",
        "base": UPSTREAM,
        "note": ("Drop-in untuk /comic/komikindo milik Sanka: ganti host URL, "
                 "bentuk respons tetap sama. Data live dari komikindo.ch, "
                 "tanpa penyimpanan. Base URL: host ini + /comic/komikindo. "
                 "Endpoint /api/* tetap ada kalau perlu, tapi TIDAK dipakai "
                 "frontend Sanka."),
        "endpoints": ENDPOINTS,
    }


def h_latest(env):
    page = int(env["vars"].get("page") or env["query"].get("page") or 1)
    if page < 1:
        raise SankaError(500, MSG_LIST)
    specs = [("apk/v2/latest/%d" % (4 * (page - 1) + k), None) for k in range(1, 5)]
    pages = jget_many(specs)
    rows = []
    for p in pages:
        rows.extend(items_of(p))
    if not rows:
        raise SankaError(500, MSG_LIST)
    ids = [id_of(r.get("url")) for r in rows]
    slugmap = wp_slug_map([i for i in ids if i])
    total = wp_total() or 9388
    total_pages = (total + 39) // 40
    return {
        "creator": CREATOR, "success": True,
        "pagination": pagination(page, total_pages),
        "komikList": [latest_list_item(r, slugmap.get(id_of(r.get("url"))))
                      for r in rows],
        "komikPopuler": populer_items(),
    }


def h_detail(env):
    slug = urllib.parse.unquote(env["vars"]["slug"]).strip().strip("/")
    post = wp_resolve(slug)
    if not post:
        raise SankaError(500, MSG_DETAIL)
    real_slug = post.get("slug") or slug
    link = post.get("link") or (UPSTREAM + "komik/%s/" % real_slug)
    docs = hget_many([link])
    page_html = docs[0]
    if not page_html:
        raise SankaError(500, MSG_DETAIL)
    parsed = parse_manga_page(page_html, real_slug)
    apk = jget("apk/v2/manga/%d" % post["id"])
    md = apk[0] if isinstance(apk, list) and apk and isinstance(apk[0], dict) else {}
    info = parsed["info"]

    def info_of(*keys, default=None):
        for k in keys:
            v = info.get(k)
            if v:
                return v
        return default

    author = info_of("pengarang") or \
        ", ".join([a.get("name") for a in (md.get("author") or []) if a.get("name")]) or None
    status = info_of("status") or md.get("status")
    genres = [{"name": g.get("name"),
               "slug": "/genres/" + re.sub(r"[^a-z0-9]+", "-",
                                           str(g.get("name") or "").lower()).strip("-")}
              for g in (md.get("genre") or []) if isinstance(g, dict)]
    chapters = [{"title": c["title"],
                 "slug": c["slug"],
                 "releaseTime": c.get("date")} for c in parsed["chapters"]]
    first = chapters[-1] if chapters else None
    latest = chapters[0] if chapters else None
    image = parsed["image"] or md.get("cover") or md.get("img")
    data = {
        "id": str(post["id"]),
        "title": parsed["title"] or post.get("title"),
        "image": image,
        "rating": parsed["rating"] or rating_of(md.get("score")),
        "votes": parsed["votes"] or "0 Orang",
        "detail": {
            "alternativeTitle": info_of("judul alternatif"),
            "status": status,
            "author": author,
            "illustrator": info_of("ilustrator"),
            "type": info_of("jenis komik", "tipe") or md.get("type"),
            "theme": info_of("tema"),
        },
        "genres": genres,
        "description": parsed["description"] or md.get("synopsis"),
        "firstChapter": {"title": first["title"], "slug": first["slug"]} if first else None,
        "latestChapter": {"title": latest["title"], "slug": latest["slug"]} if latest else None,
        "chapters": chapters,
        "spoilerImages": parsed["spoilerImages"],
        "similarManga": parsed["similarManga"],
    }
    return {"creator": CREATOR, "success": True, "data": data}


def h_chapter(env):
    slug = urllib.parse.unquote(env["vars"]["slug"]).strip().strip("/")
    if not slug:
        raise SankaError(500, MSG_CHAPTER)
    status, doc, _ = _get(UPSTREAM + slug + "/")
    if status != 200 or not doc:
        # fallback: resolusi lewat series (mis. slug tanpa prefix / format lain)
        m = re.match(r"^(.*)-chapter-(.+)$", slug)
        alt = None
        if m:
            post = wp_resolve(m.group(1))
            if post and post.get("link"):
                page = hget_many([post["link"]])[0]
                parsed = parse_manga_page(page, post.get("slug") or m.group(1))
                want = norm_chapter_token(m.group(2))
                for c in parsed["chapters"]:
                    rest = c["slug"].split("-chapter-", 1)[-1]
                    if norm_chapter_token(rest) == want:
                        alt = c["slug"]
                        break
        if not alt:
            raise SankaError(500, MSG_CHAPTER)
        status, doc, _ = _get(UPSTREAM + alt + "/")
        if status != 200 or not doc:
            raise SankaError(500, MSG_CHAPTER)
    ch = parse_chapter_page(doc)
    if not ch["images"] and not ch["title"]:
        raise SankaError(500, MSG_CHAPTER)
    manga_slug = ch["mangaSlug"]
    manga = parse_manga_page(hget_many([UPSTREAM + "komik/%s/" % manga_slug])[0],
                             manga_slug) if manga_slug else \
        {"title": None, "description": None, "chapters": [], "image": None}
    title = (ch["title"] or "").replace("Komik ", "", 1).strip()
    manga_title = (manga.get("title") or "").replace("Komik ", "", 1).strip()
    info_chapters = [{"title": c["title"], "slug": c["slug"]}
                     for c in (manga.get("chapters") or [])[:5]]
    data = {
        "id": str(ch["chapterId"]) if ch["chapterId"] else None,
        "title": ch["title"],
        "navigation": {"prev": ch["prev"], "next": ch["next"]},
        "allChapterSlug": manga_slug,
        "images": [{"id": i + 1, "url": u} for i, u in enumerate(ch["images"])],
        "thumbnail": {
            "url": manga.get("image"),
            "title": ("Komik %s" % manga_title) if manga_title else None,
        },
        "komikInfo": {
            "title": ("Komik %s Indo" % manga_title) if manga_title else None,
            "description": manga.get("description"),
            "chapters": info_chapters,
        },
    }
    return {"creator": CREATOR, "success": True, "data": data}


def h_library(env):
    page = int(env["vars"].get("page") or env["query"].get("page") or 1)
    if page < 1:
        raise SankaError(500, MSG_LIST)
    allowed = ("genre", "type", "demographic", "theme")
    filters = {k: v for k, v in env["query"].items() if k in allowed and v}
    search = (env["query"].get("search") or "").strip()
    rows = []
    if search:
        specs = [("apk/v2/search", {"s": search, "paged": p}) for p in (page,)]
        for j in jget_many(specs):
            rows.extend(items_of(j))
    else:
        first = 3 * (page - 1) + 1
        specs = [("apk/v2/filter", dict(filters, paged=first + k)) for k in range(3)]
        for j in jget_many(specs):
            rows.extend(items_of(j))
    if not rows:
        raise SankaError(500, MSG_LIST)
    ids = [id_of(r.get("url")) for r in rows]
    slugmap = wp_slug_map([i for i in ids if i])
    total = wp_total() or 9388
    total_pages = (total + 29) // 30
    filt = dict(filters)
    if search:
        filt["search"] = search
    return {
        "creator": CREATOR, "success": True,
        "pagination": pagination(page, total_pages),
        "filters": filt,
        "komikList": [lib_item(r, slugmap.get(id_of(r.get("url")))) for r in rows],
        "komikPopuler": populer_items(),
    }


def h_genres(env):
    simple = str(env["query"].get("simple") or "") in ("1", "true", "yes")
    terms = {}
    if not simple:
        pages = jget_many([("wp/v2/manga", {"per_page": 100, "page": p,
                                            "_fields": "id,class_list",
                                            "orderby": "id", "order": "asc"})
                           for p in range(1, 100)])
        kinds = ("genres", "demographic", "theme", "content")
        for j in pages:
            for post in items_of(j):
                for cls in (post.get("class_list") or []):
                    for kind in kinds:
                        pre = kind + "-"
                        if cls.startswith(pre) and len(cls) > len(pre):
                            terms.setdefault(kind, set()).add(cls[len(pre):])
    else:
        for it in catalog():
            for g in (it.get("genre") or []):
                name = g.get("name") if isinstance(g, dict) else str(g)
                if name:
                    terms.setdefault("genres", set()).add(
                        re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"))
    rows = []
    for kind in ("genres", "demographic", "content", "theme"):
        for slug in sorted(terms.get(kind) or []):
            rows.append({"name": humanize(slug), "value": slug})
    if not rows:
        raise SankaError(500, MSG_LIST)
    return {"creator": CREATOR, "success": True, "genres": rows}


def h_search(env):
    query = urllib.parse.unquote(env["vars"].get("query") or
                                 env["query"].get("q") or "").strip()
    page = int(env["vars"].get("page") or env["query"].get("page") or 1)
    if not query:
        raise SankaError(400, "Parameter query wajib diisi")
    if page < 1:
        page = 1
    specs = [("apk/v2/search", {"s": query, "paged": 2 * page - 1}),
             ("apk/v2/search", {"s": query, "paged": 2 * page})]
    rows = []
    for j in jget_many(specs):
        rows.extend(items_of(j))
    ids = [id_of(r.get("url")) for r in rows]
    slugmap = wp_slug_map([i for i in ids if i])
    has_next = len(rows) >= 20
    return {
        "creator": CREATOR, "success": True,
        "query": query,
        "pagination": {"currentPage": page, "hasNextPage": has_next,
                       "nextPage": (page + 1) if has_next else None},
        "komikList": [search_item(r, slugmap.get(id_of(r.get("url")))) for r in rows],
    }


ENDPOINTS = [
    {"method": "GET", "path": "/comic/komikindo", "desc": "Katalog endpoint"},
    {"method": "GET", "path": "/comic/komikindo/latest/:page", "desc": "Komik terbaru (40 per halaman)"},
    {"method": "GET", "path": "/comic/komikindo/detail/:slug", "desc": "Detail komik + daftar chapter"},
    {"method": "GET", "path": "/comic/komikindo/chapter/:slug", "desc": "Baca chapter (daftar gambar)"},
    {"method": "GET", "path": "/comic/komikindo/library?page=1", "desc": "Daftar komik A-Z (30 per halaman) + genre/type/search"},
    {"method": "GET", "path": "/comic/komikindo/genres", "desc": "Daftar genre"},
    {"method": "GET", "path": "/comic/komikindo/search/:query/:page", "desc": "Cari komik (20 per halaman)"},
]

ROUTES = [
    r"^/comic/komikindo$",
    r"^/comic/komikindo/latest$",
    r"^/comic/komikindo/latest/(?P<page>\d+)$",
    r"^/comic/komikindo/detail/(?P<slug>.+)$",
    r"^/comic/komikindo/chapter/(?P<slug>.+)$",
    r"^/comic/komikindo/library$",
    r"^/comic/komikindo/library/(?P<page>\d+)$",
    r"^/comic/komikindo/genres$",
    r"^/comic/komikindo/search/(?P<query>[^/]+)$",
    r"^/comic/komikindo/search/(?P<query>[^/]+)/(?P<page>\d+)$",
]
HANDLERS = [h_root, h_latest, h_latest, h_detail, h_chapter, h_library, h_library,
            h_genres, h_search, h_search]
