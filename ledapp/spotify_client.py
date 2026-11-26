"""Spotify Premium: OAuth PKCE, текущий трек, палитра обложки и управление."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import secrets as random_secrets
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import secrets

log = logging.getLogger(__name__)

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API = "https://api.spotify.com/v1"
SCOPES = (
    "user-read-playback-state user-modify-playback-state "
    "user-read-currently-playing"
)
DEFAULT_REDIRECT = "http://127.0.0.1:8888/callback"


def _b64(data):
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def make_pkce():
    verifier = _b64(random_secrets.token_bytes(64))
    challenge = _b64(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def palette_from_image(data, limit=6):
    """Возвращает [(r,g,b), ...] — самые заметные цвета обложки."""
    if not data:
        return []
    try:
        from PIL import Image

        image = Image.open(io.BytesIO(data)).convert("RGB")
        image = image.resize((64, 64))
        quant = image.quantize(colors=max(2, min(10, limit)), method=Image.Quantize.MEDIANCUT)
        palette = quant.getpalette() or []
        counts = quant.getcolors() or []
        result = []
        for count, index in sorted(counts, reverse=True)[:limit]:
            r = palette[index * 3] if index * 3 < len(palette) else 0
            g = palette[index * 3 + 1] if index * 3 + 1 < len(palette) else 0
            b = palette[index * 3 + 2] if index * 3 + 2 < len(palette) else 0
            result.append((int(r), int(g), int(b)))
        return result or [(40, 80, 160)]
    except Exception:
        return []


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.rstrip("/") != "/callback":
            self.send_response(404)
            self.end_headers()
            return
        query = urllib.parse.parse_qs(parsed.query)
        code = (query.get("code") or [""])[0]
        state = (query.get("state") or [""])[0]
        error = (query.get("error") or [""])[0]
        app = getattr(self.server, "spotify_app", None)
        if app is not None:
            app._callback_code = code
            app._callback_state = state
            app._callback_error = error
            app._callback_event.set()
        body = (
            "<html><body style='background:#121316;color:#e8e8ec;font-family:Segoe UI'>"
            "<h2>LED Ambilight</h2><p>"
            + ("Готово. Можно закрыть вкладку." if code else "Не удалось войти в Spotify.")
            + "</p></body></html>"
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class SpotifyClient:
    def __init__(self, client_id="", redirect_uri=DEFAULT_REDIRECT,
                 on_track=None, on_status=None):
        self.client_id = str(client_id or "").strip()
        self.redirect_uri = str(redirect_uri or DEFAULT_REDIRECT).strip()
        self.on_track = on_track
        self.on_status = on_status
        self._tokens = {}
        self._thread = None
        self._running = False
        self._stop = threading.Event()
        self._generation = 0
        self._callback_code = ""
        self._callback_state = ""
        self._callback_error = ""
        self._callback_event = threading.Event()
        self.current = {}
        self.palette = []
        self.last_error = ""
        self._last_art_url = ""
        self._last_art_data = b""
        self._load_tokens()

    # ------------------------------------------------------------------

    def _load_tokens(self):
        raw = secrets.get_secret("spotify_tokens")
        try:
            self._tokens = json.loads(raw) if raw else {}
        except ValueError:
            self._tokens = {}

    def _save_tokens(self):
        secrets.set_secret("spotify_tokens", json.dumps(self._tokens, ensure_ascii=False))

    def configured(self):
        return bool(self.client_id and self._tokens.get("refresh_token"))

    def authorize(self, timeout=180):
        """Блокирующий OAuth; вызывать из фонового потока."""
        if not self.client_id:
            self.last_error = "не указан Spotify Client ID"
            return False
        verifier, challenge = make_pkce()
        state = random_secrets.token_urlsafe(16)
        parsed = urllib.parse.urlparse(self.redirect_uri)
        port = parsed.port or 8888
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), _CallbackHandler)
        except OSError as exc:
            self.last_error = f"порт {port} занят: {exc}"
            return False
        server.spotify_app = self
        self._callback_event.clear()
        self._callback_code = ""
        self._callback_state = ""
        self._callback_error = ""
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "scope": SCOPES,
            "state": state,
            "code_challenge_method": "S256",
            "code_challenge": challenge,
        }
        webbrowser.open(AUTH_URL + "?" + urllib.parse.urlencode(params))
        ok = self._callback_event.wait(max(10, timeout))
        try:
            server.shutdown()
            server.server_close()
        except Exception:
            pass
        if not ok or not self._callback_code:
            self.last_error = self._callback_error or "OAuth не завершён"
            return False
        if self._callback_state != state:
            self.last_error = "OAuth state не совпал"
            return False
        return self._exchange_code(self._callback_code, verifier)

    def _post_token(self, payload):
        data = urllib.parse.urlencode(payload).encode("utf-8")
        request = urllib.request.Request(
            TOKEN_URL, data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            result = json.loads(response.read().decode("utf-8"))
        if "access_token" not in result:
            raise RuntimeError(result.get("error_description") or "Spotify token error")
        result["expires_at"] = time.time() + int(result.get("expires_in", 3600))
        return result

    def _exchange_code(self, code, verifier):
        try:
            result = self._post_token({
                "grant_type": "authorization_code", "code": code,
                "redirect_uri": self.redirect_uri, "client_id": self.client_id,
                "code_verifier": verifier,
            })
            self._tokens = result
            self._save_tokens()
            self._status("connected", "")
            return True
        except Exception as exc:
            self.last_error = str(exc)
            self._status("error", str(exc))
            return False

    def _access_token(self):
        token = self._tokens.get("access_token")
        if token and float(self._tokens.get("expires_at", 0)) > time.time() + 60:
            return token
        refresh = self._tokens.get("refresh_token")
        if not refresh:
            return ""
        try:
            self._tokens.update(self._post_token({
                "grant_type": "refresh_token", "refresh_token": refresh,
                "client_id": self.client_id,
            }))
            if not self._tokens.get("refresh_token"):
                self._tokens["refresh_token"] = refresh
            self._save_tokens()
            return self._tokens.get("access_token", "")
        except Exception as exc:
            self.last_error = str(exc)
            self._status("error", str(exc))
            return ""

    def _api(self, method, path, params=None, timeout=15):
        token = self._access_token()
        if not token:
            raise RuntimeError("Spotify не авторизован")
        url = API + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        headers = {"Authorization": f"Bearer {token}"}
        data = None
        if method in ("PUT", "POST"):
            data = b""
            headers["Content-Length"] = "0"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
        return json.loads(body) if body else {}

    # ------------------------------------------------------------------

    def start(self):
        if self._running:
            return True
        if not self.configured():
            self.last_error = "Spotify не подключён"
            return False
        self._running = True
        self._stop.clear()
        self._generation += 1
        self._thread = threading.Thread(
            target=self._loop, args=(self._generation,), daemon=True,
            name="spotify",
        )
        self._thread.start()
        return True

    def stop(self):
        self._running = False
        self._stop.set()
        self._generation += 1
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=3)

    @property
    def running(self):
        return self._running

    def _loop(self, generation):
        while self._running and generation == self._generation:
            try:
                self.poll()
                self.last_error = ""
            except Exception as exc:
                self.last_error = str(exc)
                log.warning("Spotify poll: %s", exc)
                self._status("error", str(exc))
            self._stop.wait(2.0)
        self._running = False
        self._status("stopped", "")

    def poll(self):
        payload = self._api("GET", "/me/player", params={"market": "from_token"})
        if not payload or not payload.get("item"):
            if self.current:
                self.current = {}
                self.palette = []
                if callable(self.on_track):
                    self.on_track({})
            return
        item = payload.get("item") or {}
        artists = ", ".join(
            str(artist.get("name", "")) for artist in item.get("artists") or []
        )
        images = (item.get("album") or {}).get("images") or []
        art_url = images[0].get("url", "") if images else ""
        track = {
            "id": item.get("id", ""),
            "name": item.get("name", ""),
            "artists": artists,
            "album": (item.get("album") or {}).get("name", ""),
            "duration_ms": item.get("duration_ms", 0),
            "progress_ms": payload.get("progress_ms", 0),
            "playing": bool(payload.get("is_playing")),
            "art_url": art_url,
        }
        old = self.current or {}
        changed = (
            old.get("id") != track.get("id")
            or bool(old.get("playing")) != bool(track.get("playing"))
        )
        self.current = track
        if art_url != self._last_art_url:
            self._last_art_url = art_url
            self._last_art_data = self._download(art_url) if art_url else b""
            self.palette = palette_from_image(self._last_art_data) if self._last_art_data else []
        if changed and callable(self.on_track):
            self.on_track(track)
        self._status("connected", "")

    def _download(self, url):
        try:
            with urllib.request.urlopen(url, timeout=15) as response:
                return response.read(2 * 1024 * 1024)
        except Exception:
            return b""

    def _status(self, state, message):
        if callable(self.on_status):
            try:
                self.on_status(state, message)
            except Exception:
                pass

    # ------------------------------------------------------------------

    def play(self):
        return self._control("PUT", "/me/player/play")

    def pause(self):
        return self._control("PUT", "/me/player/pause")

    def next(self):
        return self._control("POST", "/me/player/next")

    def previous(self):
        return self._control("POST", "/me/player/previous")

    def _control(self, method, path):
        try:
            self._api(method, path)
            self.poll()
            return True
        except Exception as exc:
            self.last_error = str(exc)
            log.warning("Spotify control: %s", exc)
            return False

    def logout(self):
        self.stop()
        self._tokens = {}
        secrets.delete_secret("spotify_tokens")
        self.current = {}
        self.palette = []
