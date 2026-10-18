#!/usr/bin/env bash
# Smoke test untuk KomikIndo REST API (mode LIVE, tanpa penyimpanan).
#   ./test.sh [base_url]
# Exit non-zero kalau ada yang gagal. Tidak butuh jq.
set -u
B="${1:-http://127.0.0.1:8797}"
pass=0
fail=0

say() { printf '  %-4s %s\n' "$1" "$2"; }

get() {
  BODY=$(curl -sS -m 300 -o - -w '\n%{http_code}' "$B$1" 2>/dev/null)
  CODE="${BODY##*$'\n'}"
  BODY="${BODY%$'\n'*}"
}

# expect <label> <path> <code> [grep pattern]
expect() {
  get "$2"
  if [ "$CODE" = "$3" ] && { [ -z "${4:-}" ] || printf '%s' "$BODY" | grep -qF "$4"; }; then
    say PASS "$1"; pass=$((pass+1))
  else
    say FAIL "$1 (code=$CODE, wanted=$3${4:+, pattern=$4})"
    printf '       %s\n' "$(printf '%s' "$BODY" | head -c 220)"
    fail=$((fail+1))
  fi
}

# expect_not <label> <path> <code> <pattern>
expect_not() {
  get "$2"
  if [ "$CODE" = "$3" ] && ! printf '%s' "$BODY" | grep -qF "$4"; then
    say PASS "$1"; pass=$((pass+1))
  else
    say FAIL "$1 (code=$CODE, pola tak diinginkan=$4 ada)"
    fail=$((fail+1))
  fi
}

jqget() { printf '%s' "$1" | sed -n "s/^[[:space:]]*\"$2\": \\(.*\\)$/\\1/p" | head -1 | tr -d '",'; }

# id series dari https://kmkindo.click/?page=manga&id=450201
urlid() { printf '%s' "$1" | sed -n 's/.*[?&]id=\([0-9]\{1,\}\).*/\1/p' | head -1; }

echo "== inti =="
expect "GET /api (katalog endpoint)" /api           200 '"endpoints"'
expect "GET /api/health"             /api/health    200 '"storage": "none"'
expect "GET / (root = katalog JSON)" /              200 '"endpoints"'

echo "== daftar live =="
expect "GET /api/latest"        /api/latest              200 '"items"'
expect "GET /api/terpopuler"    /api/terpopuler          200 '"items"'
expect "GET /api/rekomendasi"   /api/rekomendasi         200 '"items"'
expect "GET /api/colorized"     '/api/colorized?paged=1' 200 '"items"'
expect "GET /api/colorized color=0" '/api/colorized?color=0&page=1' 200 '"items"'
expect "GET /api/type/Manhwa"   '/api/type/Manhwa?page=1' 200 '"Manhwa"'
expect "GET /api/type invalid -> 400" '/api/type/Anime'  400 'type harus'
expect "GET /api/terms/genres"  /api/terms/genres        200 '"count"'
expect "GET /api/genres (alias)" /api/genres             200 '"kind": "genres"'
expect "GET /api/term/genres/action" '/api/term/genres/action?page=1' 200 '"items"'
expect "GET /api/wp/manga"      '/api/wp/manga?search=solo&per_page=5' 200 '"slug"'
expect "GET /api/list paginasi" '/api/list?per_page=3&page=1' 200 '"totalPages"'

echo "== pencarian (upstream pakai parameter 's') =="
expect "GET /api/search?q=solo" '/api/search?q=solo' 200 '"items"'
Q=$(jqget "$BODY" 'query')
if [ "$Q" = "solo" ]; then say PASS "search echo query"; pass=$((pass+1));
else say FAIL "search echo query ($Q)"; fail=$((fail+1)); fi
# hasil harus relevan: ada judul memuat 'Solo'
if printf '%s' "$BODY" | grep -qi 'solo'; then say PASS "search hasil relevan"; pass=$((pass+1));
else say FAIL "search hasil tidak relevan (kata 'solo' tak ada di judul)"; fail=$((fail+1)); fi
# halaman 1 vs 2 harus beda
B1="$BODY"; expect "GET /api/search?q=solo&page=2" '/api/search?q=solo&page=2' 200 '"items"'
if [ "$B1" != "$BODY" ]; then say PASS "search paging beda isi"; pass=$((pass+1));
else say FAIL "search paging sama isi"; fail=$((fail+1)); fi

echo "== filter =="
expect "GET /api/filter genre"  '/api/filter?genre=fantasy&paged=1' 200 '"items"'
expect "GET /api/filter genre 2" '/api/filter?genre=fantasy&paged=2' 200 '"items"'

echo "== alur series -> chapter -> gambar =="
expect "GET /api/latest (ambil id)" /api/latest 200 '"items"'
SID=$(urlid "$BODY")
if [ -n "$SID" ]; then
  expect "GET /api/series/{id}" "/api/series/$SID" 200 '"chapters"'
  CN=$(jqget "$BODY" 'number')
  CID=$(jqget "$BODY" 'chapterId')
  expect "GET /api/series/{id}/chapters" "/api/series/$SID/chapters?per_page=5" 200 '"items"'
  if [ -n "$CID" ]; then
    expect "GET /api/chapter/{id}"        "/api/chapter/$CID"         200 '"images"'
    expect "GET /api/chapter/{id}/images" "/api/chapter/$CID/images"  200 '"hosts"'
  else
    say FAIL "chapterId tidak ada di series $SID"; fail=$((fail+1))
  fi
  if [ -n "$CN" ]; then
    expect "GET /api/series/{id}/chapters/{n}" "/api/series/$SID/chapters/$CN" 200 '"imageCount"'
  fi
else
  say FAIL "/api/latest tidak memberi id series"; fail=$((fail+1))
fi

echo "== random + resolusi slug (live) =="
expect "GET /api/random"        /api/random               200 '"seriesId"'
expect "GET /api/series/{slug}" '/api/series/solo-leveling' 200 '"seriesId"'

echo "== error handling =="
expect "404 series ngawur"      /api/series/999999999 404 '"ok": false'
expect "404 chapter non-angka"  /api/chapter/abc      404 '"ok": false'
expect "400 search tanpa q"     /api/search           400 'wajib diisi'
expect "400 term kind ngawur"   /api/terms/ngawur     400 'kind harus'
expect "404 route ngawur"       /api/ngawur           404 'tidak ada'

echo "== format Sanka (/comic/komikindo, drop-in) =="
expect "GET /comic/komikindo (root)"   /comic/komikindo          200 '"endpoints"'
expect "GET /comic/komikindo/latest/1" /comic/komikindo/latest/1 200 '"komikList"'
expect "latest punya komikPopuler"     /comic/komikindo/latest/1 200 '"komikPopuler"'
expect "GET /comic/komikindo/genres"   /comic/komikindo/genres   200 '"value"'
expect "GET /comic/komikindo/library"  '/comic/komikindo/library?page=1' 200 '"komikList"'
expect "GET /comic/komikindo/search"   /comic/komikindo/search/solo/1    200 '"query"'
SS=$(printf '%s' "$BODY" | sed -n 's/.*"slug": "\([^"]*\)".*/\1/p' | head -1)
expect "GET /comic/komikindo/latest (ambil slug)" /comic/komikindo/latest/1 200 '"slug"'
if [ -n "$SS" ]; then
  expect "GET /comic/komikindo/detail/{slug}" "/comic/komikindo/detail/$SS" 200 '"similarManga"'
  CS=$(printf '%s' "$BODY" | sed -n 's/.*"latestChapter": {.*//; s/.*"slug": "\([^"]*\)".*/\1/p' | head -1)
  CS=$(printf '%s' "$BODY" | grep -o '"slug": "[^"]*chapter[^"]*"' | head -1 | sed 's/.*: "//; s/"//')
  if [ -n "$CS" ]; then
    expect "GET /comic/komikindo/chapter/{slug}" "/comic/komikindo/chapter/$CS" 200 '"komikInfo"'
    expect "chapter punya images" "/comic/komikindo/chapter/$CS" 200 '"images"'
  else
    say FAIL "slug chapter tidak ditemukan di detail $SS"; fail=$((fail+1))
  fi
else
  say FAIL "/comic/komikindo/latest tidak memberi slug"; fail=$((fail+1))
fi
expect "500 detail slug ngawur (pesan Sanka)" /comic/komikindo/detail/zzz-tidak-ada 500 'Error fetching komik detail'
expect "500 chapter slug ngawur (pesan Sanka)" /comic/komikindo/chapter/zzz-tidak-ada-chapter-1 500 'Error fetching chapter'
expect "403 route ngawur di /comic" /comic/komikindo/ngawur 403 'Plana AI Detector'

echo
echo "pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
