"""
Пульт с телефона: маленький HTTP-сервер внутри приложения.

Телефон в той же Wi-Fi сети открывает http://<ip-компьютера>:8765 и получает
страницу с кнопками. Никаких приложений ставить не нужно.

Важно: сервер слушает локальную сеть, поэтому любой в этой сети может
управлять подсветкой. Для домашней сети это нормально, для общей — выключи
галочку в настройках.
"""

import json
import logging
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

log = logging.getLogger(__name__)

DEFAULT_PORT = 8765

PAGE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>LED Ambilight</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin: 0; padding: 16px; background: #16171a; color: #e8e8ec;
         font-family: -apple-system, Segoe UI, Roboto, sans-serif; }
  h1 { font-size: 18px; margin: 0 0 4px; }
  .sub { color: #8a8b93; font-size: 13px; margin-bottom: 18px; }
  .row { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 10px; }
  button { border: 1px solid #3a3c42; background: #232428; color: #e8e8ec;
           border-radius: 12px; padding: 16px 10px; font-size: 15px; cursor: pointer; }
  button:active { background: #2f3138; }
  button.primary { background: #2fa84f; border-color: #2fa84f; color: #fff; font-weight: 600; }
  button.danger { background: #3a2a2a; border-color: #6b3838; color: #f0b4b4; }
  .full { grid-column: 1 / -1; }
  .status { display: flex; gap: 8px; align-items: center; margin-bottom: 16px;
            background: #1e2024; border: 1px solid #2e3036; border-radius: 12px; padding: 12px 14px; }
  .dot { width: 10px; height: 10px; border-radius: 50%; background: #6b6c72; }
  .dot.on { background: #2fa84f; }
  .kv { color: #9a9ba3; font-size: 13px; }
</style>
</head>
<body>
  <h1>LED Ambilight</h1>
  <div class="sub">Пульт с телефона</div>

  <div class="status">
    <div class="dot" id="dot"></div>
    <div>
      <div id="device">—</div>
      <div class="kv" id="meta">—</div>
    </div>
  </div>

  <div class="row">
    <button class="primary full" id="toggle">Запустить подсветку</button>
  </div>

  <div class="row">
    <button id="minus">Яркость −</button>
    <button id="plus">Яркость +</button>
  </div>

  <div class="row">
    <button id="s-cinema">Кино</button>
    <button id="s-game">Игра</button>
  </div>
  <div class="row">
    <button id="s-night">Ночь</button>
    <button id="s-day">Яркий день</button>
  </div>

  <div class="row">
    <button id="a-off">Без звука</button>
    <button id="a-spec">Спектр</button>
  </div>

  <div class="row">
    <button class="danger full" id="off">Погасить</button>
  </div>

<script>
async function post(path, body) {
  try {
    await fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json'},
                       body: JSON.stringify(body || {})});
  } catch (e) { /* телефон мог отвалиться от сети */ }
  setTimeout(refresh, 120);
}

async function refresh() {
  try {
    const r = await fetch('/api/state');
    const s = await r.json();
    document.getElementById('dot').className = 'dot' + (s.connected ? ' on' : '');
    document.getElementById('device').textContent = s.device || 'Устройство не подключено';
    document.getElementById('meta').textContent =
      (s.capturing ? 'подсветка включена' : 'подсветка выключена') +
      ' · яркость ' + s.brightness + '%' +
      (s.scene ? ' · ' + s.scene : '');
    const t = document.getElementById('toggle');
    t.textContent = s.capturing ? 'Остановить подсветку' : 'Запустить подсветку';
  } catch (e) { /* нет связи */ }
}

document.getElementById('toggle').onclick = () => post('/api/toggle');
document.getElementById('off').onclick = () => post('/api/blackout');
document.getElementById('plus').onclick = () => post('/api/brightness', {delta: 5});
document.getElementById('minus').onclick = () => post('/api/brightness', {delta: -5});
document.getElementById('s-cinema').onclick = () => post('/api/scene', {name: 'Кино'});
document.getElementById('s-game').onclick = () => post('/api/scene', {name: 'Игра'});
document.getElementById('s-night').onclick = () => post('/api/scene', {name: 'Ночь'});
document.getElementById('s-day').onclick = () => post('/api/scene', {name: 'Яркий день'});
document.getElementById('a-off').onclick = () => post('/api/audio', {enabled: false});
document.getElementById('a-spec').onclick = () => post('/api/audio', {enabled: true, mode: 'spectrum'});

refresh();
setInterval(refresh, 3000);
</script>
</body>
</html>
"""


class _Handler(BaseHTTPRequestHandler):
    server_version = "LedAmbilight"

    # доступ к владельцу сервера
    @property
    def app(self):
        return self.server.app  # type: ignore[attr-defined]

    def log_message(self, *args):
        pass  # не засоряем лог каждым запросом

    def log_request(self, code="-", size="-"):
        """Пишем только первые запросы - по ним видно, дошёл ли телефон."""
        counter = self.app.note_request(self.client_address[0], self.path, code)
        if counter is not None and counter <= 30:
            log.info("Пульт: %s %s -> %s", self.client_address[0], self.path, code)

    def _send(self, code, body, content_type="application/json"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send(200, PAGE, "text/html")
        elif path == "/api/state":
            self._send(200, json.dumps(self.app.state_snapshot(), ensure_ascii=False))
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            payload = {}

        handler = self.app.handle_action(path, payload)
        if handler is None:
            self._send(404, json.dumps({"error": "not found"}))
            return
        self._send(200, json.dumps({"ok": True}, ensure_ascii=False))


class RemoteServer:
    """HTTP-пульт. Все действия уходят в GUI через очередь, чтобы не трогать
    виджеты из чужого потока."""

    def __init__(self, submit, state_snapshot, handle_action, port=DEFAULT_PORT):
        self._submit = submit
        self._state_snapshot = state_snapshot
        self._handle_action = handle_action
        self.port = int(port)
        self._httpd = None
        self._thread = None
        self.request_count = 0
        self.last_client = ""

    def note_request(self, client, path, code):
        """Считает входящие запросы (для диагностики «телефон не подключается»)."""
        self.request_count += 1
        if client and client != self.last_client:
            self.last_client = client
            log.info("Пульт: подключился клиент %s", client)
        return self.request_count

    # ------------------------------------------------------------------

    def state_snapshot(self):
        try:
            return self._state_snapshot()
        except Exception:
            log.debug("Не удалось получить состояние для пульта", exc_info=True)
            return {}

    def handle_action(self, path, payload):
        """Возвращает обработчик (вызывается в потоке сервера) или None."""
        try:
            return self._handle_action(path, payload)
        except Exception:
            log.debug("Ошибка обработки запроса пульта", exc_info=True)
            return None

    # ------------------------------------------------------------------

    @property
    def is_running(self):
        return self._httpd is not None

    def start(self):
        if self._httpd is not None:
            return True
        try:
            httpd = ThreadingHTTPServer(("0.0.0.0", self.port), _Handler)
        except OSError as exc:
            log.warning("Не удалось запустить пульт на порту %s: %s", self.port, exc)
            return False
        httpd.daemon_threads = True
        httpd.app = self  # type: ignore[attr-defined]
        self._httpd = httpd
        self._thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        self._thread.start()
        log.info("Пульт с телефона: http://%s:%s", self.local_ip(), self.port)
        return True

    def stop(self):
        if self._httpd is None:
            return
        try:
            self._httpd.shutdown()
            self._httpd.server_close()
        except Exception:
            log.debug("Ошибка остановки пульта", exc_info=True)
        self._httpd = None
        self._thread = None

    # ------------------------------------------------------------------

    @staticmethod
    def local_ip():
        """Адрес компьютера в локальной сети (для ссылки с телефона)."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
        except OSError:
            return "127.0.0.1"
        finally:
            sock.close()

    def url(self):
        return f"http://{self.local_ip()}:{self.port}"
