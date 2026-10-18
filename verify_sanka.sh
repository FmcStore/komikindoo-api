#!/usr/bin/env bash
# Verifikasi cepat: semua endpoint format Sanka di host publik.
B="${1:-https://komikindo.aoxi.my.id}"
D="--doh-url https://cloudflare-dns.com/dns-query"
paths=(
  "/"
  "/comic/komikindo"
  "/comic/komikindo/latest/1"
  "/comic/komikindo/latest"
  "/comic/komikindo/detail/blue-lock"
  "/comic/komikindo/chapter/blue-lock-chapter-364-1"
  "/comic/komikindo/library?page=1"
  "/comic/komikindo/library/2"
  "/comic/komikindo/library?genre=action&page=1"
  "/comic/komikindo/library?type=Manhwa&page=1"
  "/comic/komikindo/library?search=solo&page=1"
  "/comic/komikindo/genres"
  "/comic/komikindo/search/naruto/1"
  "/comic/komikindo/search/naruto/2"
)
for p in "${paths[@]}"; do
  printf '%-52s ' "$p"
  code=$(curl -sS -m 300 $D -o /tmp/vr.json -w '%{http_code}' "$B$p")
  info=$(python3 - <<'PY'
import json
try:
    j = json.load(open('/tmp/vr.json'))
except Exception as e:
    print('parse error', e); raise SystemExit
if 'komikList' in j:
    print('komikList=%d  contoh=%s' % (len(j['komikList']), (j['komikList'][0]['title'][:38] if j['komikList'] else '-')))
elif 'genres' in j:
    print('genres=%d' % len(j['genres']))
elif 'data' in j:
    print('data.title=%s' % ((j['data'].get('title') or '')[:44]))
elif 'endpoints' in j:
    print('katalog endpoint (%d entri)' % len(j['endpoints']))
else:
    print(str(j)[:70])
PY
)
  printf '%s  %s\n' "$code" "$info"
done
