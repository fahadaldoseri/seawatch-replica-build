#!/usr/bin/env python3
"""يبني نسخة محلية ثابتة من موقع منشور (لقطة للاطلاع أو العرض بلا خادم).

الاستخدام:
  python3 tools/build_replica.py --source <رابط الموقع> --out <مجلد الإخراج> [--zip]
  python3 tools/build_replica.py --verify <مجلد نسخة موجودة>

الخطوات:
  1. زحف الصفحات والأصول على النطاق نفسه (روابط HTML وsrcset وurl() في CSS).
  2. جلب الإصدارات المذكورة في reports/en/index.json (الأرشيف يُبنى منها بالجافاسكربت،
     فلا يلتقطها الزحف وحده).
  3. تحويل المسارات المطلقة (/...) إلى نسبية حتى يعمل المجلد من أي موقع، بما فيها
     مسارات الجافاسكربت الحرفية مثل '/reports/'.
  4. فحص الروابط المحلية كلها (--verify أو تلقائيًا بعد البناء).

رموز الخروج (على قاعدة CLAUDE.md رقم 1):
  0  النسخة كاملة بلا نقص.
  3  منقوصة: الأسباب مكتوبة في manifest.json، والتقرير يُفصح عنها.
  1  فاشلة: تعذّر جلب الصفحة الرئيسية أو فهرس الإصدارات، أو وُجدت روابط محلية مكسورة.

لا يحفظ هذا السكربت أسرارًا، ولا يرسل شيئًا إلى أي خدمة غير الموقع المصدر.
"""
import argparse
import datetime as dt
import json
import os
import re
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

USER_AGENT = "seawatch-replica/1.0"
ASSET_RE = re.compile(r"^[A-Za-z0-9_./-]+\.(html|json|xml|css|js|png|jpe?g|svg|woff2?|ico|webp|pdf)$")
JS_PATH_RE = re.compile(r"""(['"])(/(?:reports|assets)/[A-Za-z0-9_./-]+)\1""")
CSS_URL_RE = re.compile(r"""url\(\s*['"]?([^'")\s]+)""")
REWRITE_ATTR_RE = re.compile(r"""((?:href|src|data-go|poster)=["'])/(?!/)([^"']*)""")


def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(), r.headers.get("Content-Type", "")


class RefParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.refs = []

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        for key in ("href", "src", "poster", "data-go"):
            if d.get(key):
                self.refs.append(d[key])
        if tag == "link" and d.get("imagesrcset"):
            self.refs += [part.strip().split(" ")[0] for part in d["imagesrcset"].split(",") if part.strip()]
        if tag == "meta" and d.get("property") == "og:image" and d.get("content"):
            self.refs.append(d["content"])


def local_path(out_dir, url_path):
    if url_path.endswith("/") or url_path == "":
        url_path += "index.html"
    return os.path.join(out_dir, url_path.lstrip("/"))


def extract_refs(content_type, text):
    refs = []
    if "html" in content_type:
        p = RefParser()
        p.feed(text)
        refs += p.refs
        refs += [m.group(2) for m in JS_PATH_RE.finditer(text)]
    elif "css" in content_type or text.lstrip().startswith("@font-face") or "url(" in text:
        refs += CSS_URL_RE.findall(text)
    return refs


def crawl(source, out_dir, missing):
    origin = urllib.parse.urlsplit(source).netloc
    seen, queue, saved = set(), [source], []
    while queue:
        url, _ = urllib.parse.urldefrag(queue.pop(0))
        if url in seen:
            continue
        seen.add(url)
        sp = urllib.parse.urlsplit(url)
        if sp.netloc != origin:
            continue
        try:
            data, ctype = fetch(url)
        except (urllib.error.URLError, OSError) as e:
            missing.append({"url": url, "reason": str(e)[:160]})
            continue
        path = local_path(out_dir, sp.path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        saved.append(path)
        text = data.decode("utf-8", "ignore")
        for ref in extract_refs(ctype, text):
            ref = ref.strip()
            if ref.startswith(("mailto:", "tel:", "data:", "javascript:", "#")):
                continue
            nxt = urllib.parse.urljoin(url, ref)
            nsp = urllib.parse.urlsplit(nxt)
            if nsp.netloc == origin and ASSET_RE.match(nsp.path.lstrip("/") or "x") and nxt not in seen:
                queue.append(nxt)
    return saved


def fetch_archive_dates(source, out_dir, missing):
    """يُرجع قائمة التواريخ من reports/en/index.json، ويحفظ الملف."""
    index_url = urllib.parse.urljoin(source, "/reports/en/index.json")
    try:
        data, _ = fetch(index_url)
        obj = json.loads(data.decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as e:
        missing.append({"url": index_url, "reason": str(e)[:160]})
        return None
    path = local_path(out_dir, "/reports/en/index.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return [d for d in obj.get("dates", []) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d)]


def fetch_dated_pages(source, out_dir, dates, missing):
    saved = []
    for d in dates:
        for rel in (f"/reports/{d}.html", f"/reports/en/{d}.html"):
            url = urllib.parse.urljoin(source, rel)
            path = local_path(out_dir, rel)
            if os.path.exists(path):
                continue
            try:
                data, _ = fetch(url)
            except (urllib.error.URLError, OSError) as e:
                missing.append({"url": url, "reason": str(e)[:160]})
                continue
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(data)
            saved.append(path)
    return saved


def rewrite_to_relative(out_dir):
    """يحوّل المسارات المطلقة إلى نسبية بحسب عمق الملف، في HTML وCSS وJS المضمّن."""
    changed_files = 0
    for d, _, files in os.walk(out_dir):
        for name in files:
            if not name.endswith((".html", ".css", ".js")):
                continue
            path = os.path.join(d, name)
            prefix = os.path.relpath(out_dir, d)
            prefix = "" if prefix == "." else prefix + "/"
            with open(path, encoding="utf-8", errors="ignore") as f:
                text = f.read()
            new = REWRITE_ATTR_RE.sub(lambda m: m.group(1) + prefix + m.group(2), text)
            new = JS_PATH_RE.sub(lambda m: m.group(1) + prefix + m.group(2).lstrip("/") + m.group(1), new)
            if name.endswith(".css"):
                new = re.sub(r"""url\(\s*(['"]?)/(?!/)""", lambda m: "url(" + m.group(1) + prefix, new)
            if new != text:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(new)
                changed_files += 1
    return changed_files


def check_local_links(out_dir):
    """يُرجع الروابط المحلية المكسورة (ملفات HTML وCSS فقط)."""
    broken = []
    ref_re = re.compile(r"""(?:href|src|data-go|poster)=["']([^"'#?]+)""")
    for d, _, files in os.walk(out_dir):
        for name in files:
            if not name.endswith((".html", ".css")):
                continue
            path = os.path.join(d, name)
            with open(path, encoding="utf-8", errors="ignore") as f:
                text = f.read()
            targets = ref_re.findall(text) + CSS_URL_RE.findall(text) if name.endswith(".html") else CSS_URL_RE.findall(text)
            for t in targets:
                t = t.strip()
                if not t or t.startswith(("http:", "https:", "mailto:", "tel:", "data:", "javascript:", "//")):
                    continue
                if "'+" in t or '"+' in t or "{" in t:
                    continue  # قالب جافاسكربت وليس مسارًا
                target = os.path.normpath(os.path.join(d, t))
                if not os.path.exists(target) and not os.path.exists(os.path.join(target, "index.html")):
                    broken.append({"in": os.path.relpath(path, out_dir), "to": t})
    return broken


# ---------- التوحيد البصري (يُطبَّق بعد التحويل النسبي، ويمكن تعطيله بـ--no-brand) ----------
BRAND_NAME_OLD = "مركز الوعي البحري — مملكة البحرين"
BRAND_NAME_NEW = "مركز الوعي بالمجال البحري — مملكة البحرين"
BRAND_EN_TITLE_RE = re.compile(r"Daily Maritime Security Report — ([0-9-]+) \(English edition\)")
BRAND_BODY_FONT = '"IBM Plex Sans Arabic","Noto Kufi Arabic","Segoe UI",Tahoma,system-ui,sans-serif'
BRAND_BODY_STACKS = [
    '"IBM Plex Sans Arabic",system-ui,Tahoma,sans-serif',
    '"IBM Plex Sans Arabic","Noto Kufi Arabic",sans-serif',
    '"IBM Plex Sans Arabic","Noto Kufi Arabic","Segoe UI",Tahoma,system-ui,sans-serif',
]


def neutral_mark(size):
    """علامة محايدة بلا ختم ولا نص دولة، بألوان الموقع."""
    from PIL import Image, ImageDraw
    S = size * 4
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    bg, acc, mid = (12, 22, 38, 255), (34, 195, 182, 255), (77, 157, 255, 255)
    d.ellipse([0, 0, S - 1, S - 1], fill=bg)
    d.ellipse([S * 0.06, S * 0.06, S * 0.94, S * 0.94], outline=acc, width=max(2, S // 40))
    cx, cy = S / 2, S / 2
    for r in (0.30, 0.20):
        d.ellipse([cx - S * r, cy - S * r, cx + S * r, cy + S * r], outline=mid, width=max(1, S // 90))
    d.line([S * 0.18, cy + S * 0.14, S * 0.82, cy + S * 0.14], fill=acc, width=max(2, S // 50))
    d.ellipse([cx - S * 0.05, cy - S * 0.12, cx + S * 0.05, cy + S * 0.02], fill=acc)
    return im.resize((size, size), Image.LANCZOS)


# هوية مستقلة: كل روابط نطاق الأصل تُوجَّه إلى نطاق النسخة، وصورة المشاركة تُستبدل بصورة محايدة
BRAND_HOST_OLD = "seawatch-live.pages.dev"
BRAND_HOST_NEW = "seawatch-replica.pages.dev"  # غيّره إن تغيّر نطاق النسخة


def neutral_og(size=(1200, 630)):
    """صورة مشاركة محايدة بلا ختم ولا نص دولة: خلفية داكنة وعلامة الموقع في الوسط."""
    from PIL import Image, ImageDraw
    w, h = size
    im = Image.new("RGB", (w, h), (7, 12, 22))
    d = ImageDraw.Draw(im)
    for y in range(h):
        t = y / h
        d.line([(0, y), (w, y)], fill=(int(7 + 6 * t), int(12 + 14 * t), int(22 + 20 * t)))
    mark = neutral_mark(360).convert("RGBA")
    im.paste(mark, ((w - 360) // 2, (h - 360) // 2), mark)
    return im


def brand_normalize(out_dir):
    """يوحّد الاسم والعنوان الإنجليزي ومكدّس الخط ولون الخلفية، ويستبدل الشعار الشبيه بالختم."""
    stats = {"name": 0, "en_title": 0, "font": 0, "bg": 0, "logo": 0, "host": 0, "og": 0}
    for d, _, files in os.walk(out_dir):
        for name in files:
            if not name.endswith((".html", ".css", ".xml", ".json", ".js")):
                continue
            path = os.path.join(d, name)
            with open(path, encoding="utf-8", errors="ignore") as f:
                text = f.read()
            new = text
            stats["name"] += new.count(BRAND_NAME_OLD)
            # تباين التحذير في الوضع النهاري: #a86b00 يعطي 4.40 على الأبيض، و#945c00 يعطي 5.54 (WCAG AA)
            stats["warn"] = stats.get("warn", 0) + new.count("--warn:#a86b00")
            new = new.replace("--warn:#a86b00", "--warn:#945c00")
            new = new.replace(BRAND_NAME_OLD, BRAND_NAME_NEW)
            new, n = BRAND_EN_TITLE_RE.subn(r"Maritime Domain Awareness Center — Daily Maritime Security Report, \1", new)
            stats["en_title"] += n
            for v in BRAND_BODY_STACKS:
                k = new.count("font-family:" + v)
                stats["font"] += k
                new = new.replace("font-family:" + v, "font-family:" + BRAND_BODY_FONT)
            if path == os.path.join(out_dir, "index.html"):
                stats["bg"] += new.count("--bg:#040810") + new.count("--sn-bg:#040810")
                new = new.replace("--bg:#040810", "--bg:#050a12").replace("--sn-bg:#040810", "--sn-bg:#050a12")
            hc = new.count(BRAND_HOST_OLD)
            stats["host"] = stats.get("host", 0) + hc
            new = new.replace(BRAND_HOST_OLD, BRAND_HOST_NEW)
            if new != text:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(new)
    assets = os.path.join(out_dir, "assets")
    if os.path.isdir(assets):
        if os.path.exists(os.path.join(assets, "og-1200x630.png")):
            neutral_og().save(os.path.join(assets, "og-1200x630.png"))
            stats["og"] = 1
        for name, size in (("logo-header.png", 80), ("icon-32.png", 32), ("icon-180.png", 180), ("icon-512.png", 512)):
            if os.path.exists(os.path.join(assets, name)):
                neutral_mark(size).save(os.path.join(assets, name))
                stats["logo"] += 1
    return stats


def build(args):
    out_root = os.path.abspath(args.out)
    out_dir = os.path.join(out_root, "seawatch-replica")
    if os.path.exists(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir)
    source = args.source.rstrip("/") + "/"
    missing = []

    home_url = urllib.parse.urljoin(source, "")
    try:
        fetch(home_url)
    except (urllib.error.URLError, OSError) as e:
        print(f"فشل: تعذّر جلب الصفحة الرئيسية {home_url}: {e}", file=sys.stderr)
        return 1

    crawled = crawl(home_url, out_dir, missing)
    dates = fetch_archive_dates(source, out_dir, missing)
    if dates is None:
        print("فشل: تعذّر جلب فهرس الإصدارات، والأرشيف سيكون فارغًا.", file=sys.stderr)
        return 1
    dated = fetch_dated_pages(source, out_dir, dates, missing)
    rewritten = rewrite_to_relative(out_dir)
    brand = brand_normalize(out_dir) if not args.no_brand else None
    broken = check_local_links(out_dir)

    files = [os.path.join(d, n) for d, _, ns in os.walk(out_dir) for n in ns]
    manifest = {
        "source": args.source,
        "built_at_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "files": len(files),
        "crawled": len(crawled),
        "dated_pages": len(dated),
        "archive_dates": dates,
        "files_rewritten_to_relative": rewritten,
        "brand_applied": brand,
        "missing": missing,
        "broken_local_links": broken,
    }
    if not args.no_brand:
        manifest.pop("source", None)  # هوية مستقلة: لا يُكتب عنوان الأصل في النسخة المنشورة
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    if args.zip:
        base = os.path.join(out_root, "seawatch-replica")
        if os.path.exists(base + ".zip"):
            os.remove(base + ".zip")
        shutil.make_archive(base, "zip", out_root, "seawatch-replica")

    print(f"الملفات: {len(files)} | الزحف: {len(crawled)} | الإصدارات: {len(dated)} | "
          f"مكتوبة نسبيًا: {rewritten} | منقوص: {len(missing)} | روابط مكسورة: {len(broken)}")
    for m in missing:
        print(f"  نقص: {m['url']} ({m['reason']})")
    for b in broken:
        print(f"  رابط مكسور: {b['in']} -> {b['to']}")
    if broken:
        return 1
    if missing:
        return 3
    return 0


def verify(out_dir):
    broken = check_local_links(out_dir)
    for b in broken:
        print(f"رابط مكسور: {b['in']} -> {b['to']}")
    print(f"الروابط المكسورة: {len(broken)}")
    return 1 if broken else 0


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source", help="رابط الموقع المصدر")
    p.add_argument("--out", default="replica-out", help="مجلد الإخراج")
    p.add_argument("--zip", action="store_true", help="إنشاء أرشيف zip بجانب المجلد")
    p.add_argument("--no-brand", action="store_true", help="تخطي التوحيد البصري والشعار المحايد")
    p.add_argument("--verify", metavar="DIR", help="فحص الروابط المحلية لنسخة موجودة فقط")
    args = p.parse_args()
    if args.verify:
        return verify(args.verify)
    if not args.source:
        p.error("--source مطلوب")
    return build(args)


if __name__ == "__main__":
    sys.exit(main())
