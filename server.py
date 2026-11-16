#!/usr/bin/env python3
"""LionFlix Cortes - le lista M3U ou DNS (Xtream), corta filmes/series e gera capas.
Requisitos: Python 3.8+ e ffmpeg/ffprobe no PATH. Sem pacotes extras.
Uso: python3 server.py   ->  abra http://localhost:8000
"""
import json, os, re, subprocess, threading, uuid, zipfile, urllib.request, urllib.parse, queue, shutil, mimetypes
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path

BASE = Path(__file__).parent.resolve()
OUT = BASE / "cortes"
OUT.mkdir(exist_ok=True)
(OUT / "_zips").mkdir(exist_ok=True)
LOGO = BASE / "logo.png"          # coloque aqui o logo do LionFlix (opcional)
WORKERS = int(os.environ.get("WORKERS", "3"))   # cortes simultaneos
UA = "Mozilla/5.0 LionFlixCortes"

LIB = []          # biblioteca carregada
JOBS = {}         # id -> job
ORDER = []
Q = queue.Queue()
LOCK = threading.Lock()


# ---------- utilidades ----------
def safe(s):
    s = re.sub(r"[^\w\- ]", "", s or "", flags=re.UNICODE).strip()
    return (s[:80] or "sem_nome").replace(" ", "_")


def http_get(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def norm_base(h):
    h = h.strip().rstrip("/")
    if not h.startswith("http"):
        h = "http://" + h
    return h


def xt_api(base, u, p, action):
    q = f"{base}/player_api.php?username={urllib.parse.quote(u)}&password={urllib.parse.quote(p)}&action={action}"
    return json.loads(http_get(q, 120))


def dur(src):
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(src)],
                           capture_output=True, text=True, timeout=90)
        return float(r.stdout.strip())
    except Exception:
        return 0.0


# ---------- leitura da lista ----------
def kind_of(name, group, url):
    u, g, n = url.lower(), group.lower(), name.lower()
    if "/series/" in u:
        return "serie"
    if "/movie/" in u:
        return "filme"
    if re.search(r"s\d{1,2}\s*e\d{1,3}", n):
        return "serie"
    if any(k in g for k in ("serie", "série", "novela", "anime", "temporada")):
        return "serie"
    if any(k in g for k in ("filme", "movie", "vod", "cine", "lancamento", "lançamento")):
        return "filme"
    if re.search(r"\.(mp4|mkv|avi|mov)(\?|$)", u):
        return "filme"
    return "canal"


def parse_m3u(text):
    items, cur = [], None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#EXTINF"):
            attrs = dict(re.findall(r'([\w-]+)="([^"]*)"', line))
            cur = {"name": line.rsplit(",", 1)[-1].strip(), "group": attrs.get("group-title", ""),
                   "logo": attrs.get("tvg-logo", "")}
        elif line and not line.startswith("#") and cur:
            cur["url"] = line
            cur["kind"] = kind_of(cur["name"], cur["group"], line)
            if cur["kind"] != "canal":
                items.append(cur)
            cur = None
    return items


def load_xtream(host, user, pw):
    base = norm_base(host)
    items = []
    cats = {}
    try:
        for c in xt_api(base, user, pw, "get_vod_categories"):
            cats[str(c["category_id"])] = c["category_name"]
    except Exception:
        pass
    for v in xt_api(base, user, pw, "get_vod_streams") or []:
        ext = v.get("container_extension") or "mp4"
        items.append({"name": v.get("name", ""), "kind": "filme", "logo": v.get("stream_icon") or "",
                      "group": cats.get(str(v.get("category_id")), ""),
                      "url": f"{base}/movie/{user}/{pw}/{v['stream_id']}.{ext}"})
    scats = {}
    try:
        for c in xt_api(base, user, pw, "get_series_categories"):
            scats[str(c["category_id"])] = c["category_name"]
    except Exception:
        pass
    for s in xt_api(base, user, pw, "get_series") or []:
        items.append({"name": s.get("name", ""), "kind": "serie", "logo": s.get("cover") or "",
                      "group": scats.get(str(s.get("category_id")), ""),
                      "xt": {"base": base, "u": user, "p": pw, "id": s["series_id"]}})
    return items


def expand_series(it):
    x = it["xt"]
    info = xt_api(x["base"], x["u"], x["p"], f"get_series_info&series_id={x['id']}")
    eps = info.get("episodes") or {}
    pairs = []
    if isinstance(eps, dict):
        for season, lst in eps.items():
            for e in lst:
                pairs.append((int(season) if str(season).isdigit() else 0, e))
    else:
        for i, lst in enumerate(eps, 1):
            for e in lst:
                pairs.append((i, e))
    out = []
    for season, e in pairs:
        ext = e.get("container_extension") or "mp4"
        num = int(e.get("episode_num") or 0)
        out.append({"name": f"{it['name']} S{season:02d}E{num:02d}", "season": season, "ep": num,
                    "url": f"{x['base']}/series/{x['u']}/{x['p']}/{e['id']}.{ext}"})
    return out


# ---------- corte + capa ----------
FONTS = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
         "C:/Windows/Fonts/arialbd.ttf", "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"]


def find_font():
    for f in FONTS:
        if os.path.exists(f):
            return f.replace(":", "\\:")
    return None


def make_cover(clip, out, n):
    d = dur(clip)
    t = max(0.5, d * 0.4)
    base_vf = "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720"
    font = find_font()
    txt = f"drawtext=fontfile='{font}':text='PARTE {n}':fontsize=64:fontcolor=white:box=1:boxcolor=0x000000AA:boxborderw=14:x=30:y=H-th-40" if font else None

    def run(with_text):
        if LOGO.exists():
            fc = f"[0:v]{base_vf}[b];[1:v]scale=200:-1[l];[b][l]overlay=W-w-24:24"
            if with_text and txt:
                fc += "," + txt
            cmd = ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(clip), "-i", str(LOGO),
                   "-filter_complex", fc, "-frames:v", "1", "-q:v", "2", str(out)]
        else:
            vf = base_vf + ("," + txt if with_text and txt else "")
            cmd = ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(clip),
                   "-vf", vf, "-frames:v", "1", "-q:v", "2", str(out)]
        return subprocess.run(cmd, capture_output=True).returncode == 0 and Path(out).exists()

    if not run(True):
        run(False)


def cut(job, outdir):
    N = job["seconds"]
    total = dur(job["url"])
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-nostats", "-progress", "pipe:1"]
    if job["url"].startswith("http"):
        cmd += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"]
    cmd += ["-i", job["url"], "-map", "0:v:0", "-map", "0:a:0?"]
    if job["precise"]:
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "aac", "-b:a", "128k",
                "-force_key_frames", f"expr:gte(t,n_forced*{N})"]
    else:
        cmd += ["-c", "copy", "-avoid_negative_ts", "make_zero"]
    cmd += ["-f", "segment", "-segment_time", str(N), "-reset_timestamps", "1", "-segment_format", "mp4",
            "-segment_format_options", "movflags=+faststart", str(outdir / "corte_%03d.mp4")]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    err = []
    for line in p.stdout:
        line = line.strip()
        m = re.match(r"out_time_(?:us|ms)=(\d+)", line)
        if m and total:
            job["progress"] = min(90, int(int(m.group(1)) / 1e6 / total * 90))
        elif "=" not in line and line:
            err.append(line)
    p.wait()
    files = sorted(outdir.glob("corte_*.mp4"))
    if p.returncode != 0 and not files:
        raise RuntimeError(" ".join(err[-3:]) or "ffmpeg falhou")
    return files


def run_job(j):
    if j.get("xt"):
        j["status"] = "lendo episódios"
        eps = expand_series(j["item"])
        for e in eps:
            add_job({"name": e["name"], "url": e["url"], "kind": "serie", "series": j["name"]},
                    j["seconds"], j["precise"])
        j["status"] = "feito"
        j["progress"] = 100
        j["note"] = f"{len(eps)} episódios enviados para a fila"
        return
    j["status"] = "cortando"
    parts = [safe(j.get("series") or j["name"])]
    if j.get("series"):
        parts.append(safe(j["name"]))
    outdir = OUT.joinpath(*parts)
    outdir.mkdir(parents=True, exist_ok=True)
    j["rel"] = "/".join(parts)
    files = cut(j, outdir)
    j["status"] = "gerando capas"
    clips = []
    for i, f in enumerate(files, 1):
        cov = f.with_suffix(".jpg")
        make_cover(f, cov, i)
        clips.append({"video": f"{j['rel']}/{f.name}", "cover": f"{j['rel']}/{cov.name}" if cov.exists() else ""})
        j["progress"] = 90 + int(10 * i / len(files))
    j["clips"] = clips
    j["progress"] = 100
    j["status"] = "feito"


def worker():
    while True:
        j = Q.get()
        try:
            run_job(j)
        except Exception as e:
            j["status"] = "erro"
            j["error"] = str(e)[:300]
        Q.task_done()


def add_job(it, seconds, precise):
    j = {"id": uuid.uuid4().hex[:8], "name": it["name"], "kind": it.get("kind", ""), "seconds": seconds,
         "precise": precise, "status": "na fila", "progress": 0, "clips": [], "rel": "", "error": "", "note": ""}
    if it.get("url"):
        j["url"] = it["url"]
    if it.get("xt"):
        j["xt"] = it["xt"]
        j["item"] = it
    if it.get("series"):
        j["series"] = it["series"]
    with LOCK:
        JOBS[j["id"]] = j
        ORDER.append(j["id"])
    Q.put(j)
    return j


# ---------- servidor HTTP ----------
class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send_json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def send_file(self, path, name=None):
        path = Path(path)
        if not path.is_file():
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(str(path))[0] or "application/octet-stream")
        self.send_header("Content-Length", str(path.stat().st_size))
        if name:
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
        self.end_headers()
        with open(path, "rb") as f:
            shutil.copyfileobj(f, self.wfile)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/":
            return self.send_file(BASE / "index.html")
        if u.path == "/logo.png":
            return self.send_file(LOGO)
        if u.path == "/api/jobs":
            with LOCK:
                data = [{k: v for k, v in JOBS[i].items() if k not in ("url", "xt", "item")} for i in ORDER]
            return self.send_json(data)
        if u.path.startswith("/files/"):
            p = (OUT / urllib.parse.unquote(u.path[7:])).resolve()
            if OUT not in p.parents:
                return self.send_error(403)
            return self.send_file(p)
        if u.path == "/api/zip":
            jid = urllib.parse.parse_qs(u.query).get("id", [""])[0]
            j = JOBS.get(jid)
            if not j or not j.get("rel"):
                return self.send_error(404)
            folder = OUT / j["rel"]
            zp = OUT / "_zips" / f"{jid}.zip"
            with zipfile.ZipFile(zp, "w", zipfile.ZIP_STORED) as z:
                for f in sorted(folder.iterdir()):
                    z.write(f, f.name)
            return self.send_file(zp, f"{safe(j['name'])}.zip")
        self.send_error(404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        try:
            if self.path == "/api/load":
                if body.get("mode") == "xtream":
                    items = load_xtream(body["host"], body["user"], body["pass"])
                else:
                    text = body.get("text") or http_get(body["url"], 180)
                    items = parse_m3u(text)
                with LOCK:
                    LIB.clear()
                    for i, it in enumerate(items):
                        it["i"] = i
                        LIB.append(it)
                view = [{"i": it["i"], "name": it["name"], "kind": it["kind"], "group": it["group"],
                         "logo": it["logo"]} for it in LIB]
                return self.send_json({"items": view})
            if self.path == "/api/start":
                secs = int(body["seconds"])
                if secs not in (30, 60, 180, 300, 600):
                    return self.send_json({"error": "tamanho inválido"}, 400)
                c = 0
                for i in body["ids"]:
                    if 0 <= i < len(LIB):
                        add_job(LIB[i], secs, bool(body.get("precise")))
                        c += 1
                return self.send_json({"queued": c})
            if self.path == "/api/clear":
                with LOCK:
                    for i in [i for i in ORDER if JOBS[i]["status"] in ("feito", "erro")]:
                        ORDER.remove(i)
                        JOBS.pop(i)
                return self.send_json({"ok": True})
        except Exception as e:
            return self.send_json({"error": str(e)[:300]}, 500)
        self.send_error(404)


if __name__ == "__main__":
    if not shutil.which("ffmpeg"):
        print("ATENCAO: ffmpeg nao encontrado no PATH. Instale em https://ffmpeg.org")
    for _ in range(WORKERS):
        threading.Thread(target=worker, daemon=True).start()
    print("LionFlix Cortes rodando em http://localhost:8000")
    ThreadingHTTPServer(("0.0.0.0", 8000), H).serve_forever()
