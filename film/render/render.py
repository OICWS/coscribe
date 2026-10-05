"""Render the film frame by frame with headless Chromium and encode it with ffmpeg.

Usage:
  python3 render/render.py --lang zh                       # full film -> out/film_zh.mp4 (muxes audio/out/mix_zh.m4a if present)
  python3 render/render.py --lang zh --start 8 --end 30    # a section
  python3 render/render.py --lang zh --stills 3 9.5 21     # PNG stills for review -> out/stills/
  python3 render/render.py --lang zh --contact 2           # one still every 2 s, tiled into a contact sheet

The page is a pure function of time (F.seek(t)), so every frame is exact and
reproducible regardless of how long each screenshot takes.
"""
import argparse
import functools
import http.server
import os
import subprocess
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

FILM = Path(__file__).resolve().parent.parent
CHROMIUM = os.environ.get("FILM_CHROMIUM", "/opt/pw-browsers/chromium")


def serve():
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    handler = functools.partial(Quiet, directory=str(FILM))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def open_page(pw, port, lang, subs=True):
    browser = pw.chromium.launch(executable_path=CHROMIUM, args=["--font-render-hinting=none", "--disable-gpu-vsync"])
    page = browser.new_page(viewport={"width": 1920, "height": 1080}, device_scale_factor=1)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: m.type == "error" and errors.append(m.text))
    page.goto(f"http://127.0.0.1:{port}/index.html?render=1&lang={lang}&subs={1 if subs else 0}")
    page.wait_for_function("window.renderFrame !== undefined")
    page.evaluate("document.fonts.ready")
    # warm every scene once so lazily-decoded fonts/images are ready before frame 0
    page.evaluate("async () => { for (let t = 0; t < window.filmDuration; t += 1) await window.renderFrame(t); await document.fonts.ready; }")
    return browser, page, errors


def shot(page, t):
    page.evaluate(f"window.renderFrame({t})")
    return page.screenshot(type="png", clip={"x": 0, "y": 0, "width": 1920, "height": 1080})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", default="zh")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--start", type=float, default=0)
    ap.add_argument("--end", type=float)
    ap.add_argument("--stills", type=float, nargs="*")
    ap.add_argument("--contact", type=float, help="contact sheet: one still every N seconds")
    ap.add_argument("--no-subs", action="store_true")
    ap.add_argument("--out")
    args = ap.parse_args()

    outdir = FILM / "out"
    outdir.mkdir(exist_ok=True)
    srv = serve()
    with sync_playwright() as pw:
        browser, page, errors = open_page(pw, srv.server_port, args.lang, not args.no_subs)
        dur = page.evaluate("window.filmDuration")
        end = min(args.end or dur, dur)

        if args.stills is not None or args.contact:
            sd = outdir / "stills"
            sd.mkdir(exist_ok=True)
            times = args.stills or []
            if args.contact:
                times = [round(args.start + i * args.contact, 2) for i in range(int((end - args.start) / args.contact) + 1)]
            paths = []
            for t in times:
                p = sd / f"{args.lang}_{t:07.2f}.png"
                p.write_bytes(shot(page, t))
                paths.append(p)
            if args.contact and paths:
                sheet = args.out or str(outdir / f"contact_{args.lang}_{args.start:g}-{end:g}.jpg")
                cols = 4
                rows = (len(paths) + cols - 1) // cols
                inputs = sum((["-i", str(p)] for p in paths), [])
                pads = len(paths)
                filt = "".join(f"[{i}:v]scale=480:-1,drawtext=text='{times[i]:.1f}':x=8:y=8:fontsize=20:fontcolor=white:box=1:boxcolor=black@0.5[v{i}];" for i in range(pads))
                layout = "|".join(f"{(i % cols) * 480}_{(i // cols) * 270}" for i in range(pads))
                filt += "".join(f"[v{i}]" for i in range(pads)) + f"xstack=inputs={pads}:layout={layout}:fill=black"
                if pads == 1:
                    filt = "[0:v]scale=480:-1[v]"; 
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", filt, "-frames:v", "1", "-q:v", "3", sheet], check=True)
                print(sheet)
            else:
                print("\n".join(map(str, paths)))
        else:
            out = args.out or str(outdir / (f"film_{args.lang}.mp4" if args.start == 0 and end >= dur else f"part_{args.lang}_{args.start:g}-{end:g}.mp4"))
            audio = FILM / "audio" / "out" / f"mix_{args.lang}.m4a"
            cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(args.fps), "-i", "-"]
            if audio.exists():
                cmd += ["-ss", str(args.start), "-t", str(end - args.start), "-i", str(audio), "-c:a", "aac", "-b:a", "256k", "-shortest"]
            cmd += ["-c:v", "libx264", "-preset", "slow", "-crf", "16", "-pix_fmt", "yuv420p", "-movflags", "+faststart", out]
            enc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
            n = int(round((end - args.start) * args.fps))
            for i in range(n):
                enc.stdin.write(shot(page, args.start + i / args.fps))
                if i % (args.fps * 5) == 0:
                    print(f"  {args.start + i / args.fps:6.1f}s / {end:.1f}s", flush=True)
            enc.stdin.close()
            enc.wait()
            print(out)
        if errors:
            print("PAGE ERRORS:\n" + "\n".join(errors[:20]))
        browser.close()
    srv.shutdown()


if __name__ == "__main__":
    main()
