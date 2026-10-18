#!/usr/bin/env python3
"""Diff bentuk JSON API kita vs Sanka asli untuk /comic/komikindo.

Membandingkan NAMA KEY **dan TIPE** secara rekursif tiap endpoint (bukan cuma
HTTP 200). Slug tiap sisi diambil dari /latest/1 sisi itu sendiri supaya detail
& chapter dibandingkan pada komik yang sepadan, lalu satu komik yang sama
(slug identik) dibandingkan nilainya untuk lihat beda isi.

    python3 parity.py
    python3 parity.py --ours https://komikindo.aoxi.my.id/comic/komikindo
"""
import argparse
import json
import subprocess
import sys

SANKA = "https://www.sankavollerei.web.id/comic/komikindo"
DOH = "https://cloudflare-dns.com/dns-query"


def get(url, t=240):
    out = subprocess.run(["curl", "-s", "-m", str(t), "--doh-url", DOH, url],
                         capture_output=True, text=True).stdout
    try:
        return json.loads(out)
    except Exception as e:                                        # noqa: BLE001
        return {"__parse_error__": str(e), "__raw__": out[:160]}


def shape(o):
    """Struktur + tipe. None -> 'null' (sengaja dibedakan dari tipe lain)."""
    if isinstance(o, dict):
        return {k: shape(v) for k, v in o.items()}
    if isinstance(o, list):
        return ["list", shape(o[0])] if o else "list[]"
    if o is None:
        return "null"
    return type(o).__name__


def typediff(a, b, path=""):
    """Beda key DAN beda tipe, rekursif."""
    out = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            sub = "%s.%s" % (path, k) if path else k
            if k not in a:
                out.append("    KITA LEBIH : %s" % sub)
            elif k not in b:
                out.append("    SANKA SAJA : %s" % sub)
            else:
                out.extend(typediff(a[k], b[k], sub))
        return out
    if isinstance(a, list) and isinstance(b, list):
        if a and b:
            out.extend(typediff(a[1], b[1], path + "[]"))
        elif bool(a) != bool(b):
            out.append("    ISI BEDA : %s (satu sisi array kosong)" % path)
        return out
    if isinstance(a, str) and isinstance(b, str) and a != b:
        out.append("    TIPE BEDA : %s (sanka=%s kita=%s)" % (path, a, b))
    return out


def cmp(label, sanka_url, ours_url):
    s, o = get(sanka_url), get(ours_url)
    diff = typediff(shape(s), shape(o))
    print("[%s] %s" % ("BEDA" if diff else "SAMA", label))
    for line in diff:
        print(line)
    return not diff


def dig(o, path):
    cur = o
    for p in path.split("."):
        if isinstance(cur, list):
            cur = cur[0] if cur else None
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ours", default="http://127.0.0.1:8797/comic/komikindo")
    a = ap.parse_args()
    S, O = SANKA, a.ours.rstrip("/")
    ok = [0]
    total = [0]

    def run(label, sp, op):
        total[0] += 1
        ok[0] += 1 if cmp(label, S + sp, O + op) else 0

    run("root", "", "")
    run("latest/1", "/latest/1", "/latest/1")
    run("latest/2", "/latest/2", "/latest/2")
    run("genres", "/genres", "/genres")
    run("library?page=1", "/library?page=1", "/library?page=1")
    run("library?page=2", "/library?page=2", "/library?page=2")
    run("library?genre=action", "/library?genre=action&page=1",
        "/library?genre=action&page=1")
    run("search/naruto/1", "/search/naruto/1", "/search/naruto/1")
    run("search/solo/2", "/search/solo/2", "/search/solo/2")

    ss = (get(S + "/latest/1").get("komikList") or [{}])[0].get("slug")
    os_ = (get(O + "/latest/1").get("komikList") or [{}])[0].get("slug")
    run("detail (sanka=%s kita=%s)" % (ss, os_), "/detail/%s" % ss,
        "/detail/%s" % os_)

    sd = (get("%s/detail/%s" % (S, ss)).get("data") or {}).get("latestChapter") or {}
    od = (get("%s/detail/%s" % (O, os_)).get("data") or {}).get("latestChapter") or {}
    run("chapter (sanka=%s kita=%s)" % (sd.get("slug"), od.get("slug")),
        "/chapter/%s" % sd.get("slug"), "/chapter/%s" % od.get("slug"))

    # jumlah item per halaman (typediff tidak menangkap ini)
    print("\n== jumlah item per halaman ==")
    for label, path in (("latest/1", "/latest/1"), ("latest/2", "/latest/2"),
                        ("library?page=1", "/library?page=1"),
                        ("search/naruto/1", "/search/naruto/1")):
        js = (get(S + path).get("komikList") or [])
        jo = (get(O + path).get("komikList") or [])
        flag = "ok" if len(js) == len(jo) else "BEDA"
        print("   %-14s sanka=%-3d kita=%-3d  %s" % (label, len(js), len(jo), flag))

    # isi satu komik yang sama di dua sisi (slug identik)
    print("\n== isi komik yang sama (slug identik) ==")
    for cand in ("blue-lock", "student-jo-eun", "solo-leveling", "one-piece",
                 "the-wicked-little-princess"):
        ds, do = get("%s/detail/%s" % (S, cand)), get("%s/detail/%s" % (O, cand))
        if not ds.get("success") or not do.get("success"):
            continue
        print("   slug: %s" % cand)
        for path, label in (("data.title", "title"),
                            ("data.rating", "rating"),
                            ("data.votes", "votes"),
                            ("data.detail.status", "status"),
                            ("data.detail.author", "author"),
                            ("data.genres", "genres"),
                            ("data.latestChapter.slug", "latestChapter.slug")):
            vs, vo = dig(ds, path), dig(do, path)
            if path == "data.genres":
                vs = [g.get("name") for g in (vs or [])]
                vo = [g.get("name") for g in (vo or [])]
            same = vs == vo
            print("      %-20s %s | sanka=%r kita=%r"
                  % (label, "sama" if same else "beda", vs, vo))
        cs, co = dig(ds, "data.chapters"), dig(do, "data.chapters")
        print("      %-20s sanka=%d chapter, kita=%d chapter"
              % ("jumlah chapter", len(cs or []), len(co or [])))
        break

    print("\n%d/%d endpoint identik dengan Sanka (key + tipe)" % (ok[0], total[0]))
    return 0 if ok[0] == total[0] else 1


if __name__ == "__main__":
    sys.exit(main())
