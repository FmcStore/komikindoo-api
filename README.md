# KomikIndo REST API

REST API dari **komikindo.ch**. **Mode live: tidak menyimpan apa pun.**
Tidak ada SQLite, tidak ada file cache, tidak ada data di disk. Setiap request
langsung menembak sumbernya (JSON API + HTML komikindo.ch), lalu dijawab JSON.
Hentikan proses = tidak ada sisa data.

Ada **dua lapis** endpoint di server yang sama:

1. `/comic/komikindo/*` — **format Sanka** (drop-in). INI yang dipakai
   frontend: base URL-nya `https://komikindo.aoxi.my.id` (atau host ini),
   path-nya sama persis dengan Sanka.
2. `/api/*` — API asli proyek ini (envelope `{ok, mode, data, source}`).
   Opsional, tidak dipakai frontend Sanka. Ada buat kebutuhan lain saja.

## Base URL (tinggal ganti host)

```
https://www.sankavollerei.web.id  ->  https://komikindo.aoxi.my.id
```

Path dan struktur tetap sama, jadi nol perubahan di sisi web:

| Sanka | Di sini |
|---|---|
| `.../comic/komikindo/latest/1` | `https://komikindo.aoxi.my.id/comic/komikindo/latest/1` |
| `.../comic/komikindo/detail/one-piece` | `https://komikindo.aoxi.my.id/comic/komikindo/detail/one-piece` |
| `.../comic/komikindo/chapter/one-piece-chapter-1` | `https://komikindo.aoxi.my.id/comic/komikindo/chapter/one-piece-chapter-1` |
| `.../comic/komikindo/library?page=1` | `https://komikindo.aoxi.my.id/comic/komikindo/library?page=1` |
| `.../comic/komikindo/genres` | `https://komikindo.aoxi.my.id/comic/komikindo/genres` |
| `.../comic/komikindo/search/naruto/1` | `https://komikindo.aoxi.my.id/comic/komikindo/search/naruto/1` |

Domain root (`/`) juga menampilkan katalog format Sanka, jadi tidak ada yang
mengarah ke `/api`. Penanda cepat: `bash verify_sanka.sh` (14 cek ke host publik).

## Struktur

- `server.py` — server utama (stdlib `http.server`, threaded). Port :8797.
- `sanka.py` — modul endpoint format Sanka + parser HTML. Diimpor server.py.
- `parity.py` — diff bentuk JSON kita vs Sanka asli (rekursif: key & tipe).
- `test.sh` — smoke test 48 cek (`bash test.sh [base_url]`).
- `named-config.yml` — config cloudflared tunnel `komikindo.aoxi.my.id`.
- `*.py.bak` — versi lama (offline/SQLite), tidak dipakai, boleh dihapus.

## Jalankan

```bash
cd /root/komikindo-api
python3 server.py                              # 127.0.0.1:8797
bash test.sh                                   # 48 cek ke lokal
bash test.sh https://komikindo.aoxi.my.id      # 48 cek lewat internet
python3 parity.py                              # bandingkan bentuk vs Sanka asli
```

## Endpoint format Sanka (drop-in)

Ganti `https://www.sankavollerei.web.id` dengan `https://komikindo.aoxi.my.id`.
Path-nya persis sama:

- `GET /comic/komikindo/latest/:page` — komik terbaru, 40 per halaman
  (`/latest` tanpa angka = halaman 1)
- `GET /comic/komikindo/detail/:slug` — detail + seluruh daftar chapter
- `GET /comic/komikindo/chapter/:slug` — baca chapter (daftar URL gambar)
- `GET /comic/komikindo/library?page=1` — katalog A-Z, 30 per halaman
  (filter: `genre`, `type`, `demographic`, `theme`, `search`; `/library/2`
  juga jalan)
- `GET /comic/komikindo/genres` — daftar genre
- `GET /comic/komikindo/search/:query/:page` — cari judul, 20 per halaman
  (`/search/solo` tanpa angka = halaman 1)
- `GET /comic/komikindo` — katalog endpoint (juga di domain root `/`)

Hasil `python3 parity.py`: **10/11 endpoint identik** dengan Sanka asli
(diff nama key **dan tipe**, rekursif): latest/1, latest/2, genres,
library?page=1, library?page=2, library?genre=action, search/naruto/1,
search/solo/2, detail, chapter. Jumlah item per halaman juga sama
(latest 40, library 30, search 20 di halaman penuh). Yang ke-11, `root`,
sengaja beda: Sanka membalas 403 pesan "Plana AI Detector" di root, di sini
root justru memberi katalog endpoint.

Uji isi pada komik yang sama (slug `blue-lock`, dua sisi): `rating`, `votes`,
`status`, `author`, `genres`, `latestChapter.slug`, dan jumlah chapter (368)
**sama persis**. Tiga beda yang diketahui dan disengaja:

1. `root` (lihat di atas).
2. `data.title` di detail/chapter: Sanka menyimpan spasi baris HTML mentah
   (`"Komik\n              Blue Lock"`), di sini dinormalkan jadi
   `"Komik Blue Lock"`. Isi sama, cuma rapi.
3. `data.detail.theme` bisa berisi teks di sini sementara Sanka `null`
   (contoh `one-piece`), karena scrape Sanka-nya yang basi. Tipe tetap
   string-atau-null, jadi tidak mengubah parser.

Slug yang diterima dua bentuk: slug asli WP komikindo (`508249-judul`) maupun
bentuk tanpa prefix angka (`judul`) yang dipakai Sanka dan dipakai URL chapter
di situsnya.

## Endpoint `/api` (API asli proyek ini)

Katalog lengkap: `GET /api` (atau `GET /`, isinya JSON sama).

- `/api/health` — cek server + keterjangkauan upstream
- `/api/latest?page=1` — rilis terbaru (10 per halaman)
- `/api/terpopuler?page=1` — terpopuler (5 per halaman)
- `/api/rekomendasi?page=1` — rekomendasi (5 per halaman)
- `/api/colorized?color=1&page=1` — judul berwarna
- `/api/type/{Manga|Manhua|Manhwa}?page=1` — per tipe
- `/api/terms/{genres|demographic|theme|content|type|status}` — nilai term + jumlah judul
- `/api/term/{kind}/{value}?page=1` — judul per term
- `/api/list?page=1&per_page=24&type=&genre=&status=&q=&sort=score` — katalog penuh
- `/api/search?q=` — cari judul (upstream pakai parameter `s`)
- `/api/filter?genre=&type=&demographic=&theme=&paged=1`
- `/api/series/{id|slug}` — detail + seluruh daftar chapter
- `/api/series/{id|slug}/chapters?page=1&per_page=100`
- `/api/series/{id|slug}/chapters/{number}` — mis. `/1.1`
- `/api/chapter/{chapterId}` — isi chapter + prev/next
- `/api/chapter/{chapterId}/images` — hanya array URL gambar + rekap host
- `/api/random` — satu series acak + detailnya
- `/api/wp/manga?search=&per_page=` — passthrough pencarian WP (slug -> id)

Envelope balasan:

```json
{ "ok": true, "mode": "live", "cached": false, "storage": "none",
  "elapsedMs": 712,
  "source": ["https://komikindo.ch/wp-json/apk/v2/chapter/450204"],
  "data": { } }
```

`source` berisi URL upstream yang benar-benar di-fetch pada request itu
(bukan tebakan), jadi kelihatan kalau satu request memanggil beberapa endpoint.

## Sumber data (hasil probe, bukan asumsi)

- `/wp-json/apk/v2/...` — API milik situs: `list` (9rb+ judul, 1 request),
  `latest/{p}` (10/hal), `terpopuler/{p}` (5/hal), `rekomendasi/{p}`,
  `colorized/{0|1}/{p}` (10/hal), `type/{Tipe}/{p}` (5/hal), `manga/{id}`
  (detail + seluruh chapter), `chapter/{id}` (gambar), `search`, `filter`.
- `/wp-json/wp/v2/manga?id=` — id **sama** dengan id di `apk/v2`; dipakai untuk
  memetakan id -> slug (`?include=`) dan hitung total post (`X-WP-Total`).
- `/wp-json/apk/v2/search` membaca parameter **`s`** (standar WP) + `paged`.
  Param `q`/`query`/`title`/`keyword` **diabaikan** upstream (balas daftar
  default) — ini pernah jadi bug di versi lama.
- `/wp-json/apk/v2/filter` membaca `genre`, `type`, `demographic`, `theme`,
  `paged`. `status`/`order` tidak berpengaruh.
- Halaman manga `/komik/<slug>/` memuat: blok info (Judul Alternatif, Status,
  Pengarang, Ilustrator, Grafis/Konten/Tema), rating + votes, sinopsis, tab
  **Spoiler** (gambar), tab **Mirip** (rekomendasi), dan `#chapter_list`
  lengkap dengan tanggal tiap chapter.
- Halaman chapter `/<slug-chapter-N>/` memuat `<article id="post-<chapterId>">`,
  area baca `#Baca_Komik` (gambar), `rel="prev"` / `rel="next"`, dan kelas
  `category-<manga-slug>`.
- Gambar di area baca muncul dari host yang berganti-ganti (`.lol`, `.lat`,
  `.pics`, `master.komikcdn.me`). Iklan (`blogger.googleusercontent`) dan
  div `gulai_*` dibuang, jadi `images[]` bersih.

## Yang disimpan

Tidak ada. `data/` hanya berisi log stdout/stderr dari systemd
(`server.log`, `tunnel.log`, `named-tunnel.log`). Tidak ada `.db`/cache.
Tidak ada cache in-memory juga: tiap request fetch ulang.

## systemd (user)

- `komikindo-api.service` — server :8797
- `komikindo-named-tunnel.service` — tunnel tetap **https://komikindo.aoxi.my.id**
- `komikindo-tunnel.service` — quick tunnel lama (dibiarkan jalan, tidak dihapus)

## Catatan teknis

- Endpoint berat (memang, karena live): `/api/list` ~7 s (9rb judul dalam 1
  request), `/comic/komikindo/genres` ~10 s (menghimpun seluruh term dari
  94 halaman `wp/v2/manga`; `?simple=1` memakai cara cepat 1 request),
  `latest`/`library` ~5-6 s (paralel: 4 halaman latest/filter + peta slug +
  komikPopuler). Sisanya < 2 s.
- Upstream membalas **HTTP 200 + body kosong** untuk manga id yang tidak ada;
  itu dipetakan jadi 404, bukan 502.
- Error di rute format Sanka dibalas persis seperti Sanka: `500` +
  `{"creator","success":false,"message":"Error fetching komik detail from Komikindo"}`.
- Rute `/comic/*` yang tidak dikenal dibalas `403` + pesan "Plana AI Detector",
  sama seperti Sanka.
- `cloudflared` quick tunnel wajib diberi `--config` (file kosong) supaya tidak
  membaca `~/.cloudflared/config.yml` milik layanan lain; tunnel bernama pakai
  config sendiri (`named-config.yml`).
