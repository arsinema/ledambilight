"""Вкладка «Интеграции»: Spotify, Telegram и Windows-уведомления."""

from __future__ import annotations

from PyQt5.QtWidgets import (
    QCheckBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QVBoxLayout, QWidget,
)


class IntegrationsWidget(QWidget):
    def __init__(self, config, handlers=None, on_change=None, parent=None):
        super().__init__(parent)
        self.config = config
        self.handlers = handlers or {}
        self.on_change = on_change
        self._build()

    def _spotify(self):
        return self.config.setdefault("integrations", {}).setdefault("spotify", {})

    def _telegram(self):
        return self.config.setdefault("integrations", {}).setdefault("telegram", {})

    def _windows(self):
        return self.config.setdefault("integrations", {}).setdefault(
            "windows_notifications", {}
        )

    def _build(self):
        root = QVBoxLayout(self)
        intro = QLabel(
            "Интеграции необязательны. Токены хранятся в Windows Credential "
            "Manager (DPAPI) и не попадают в config.json."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        spotify = QGroupBox("Spotify Premium")
        sf = QFormLayout(spotify)
        self.spotify_client_id = QLineEdit(self._spotify().get("client_id", ""))
        self.spotify_client_id.setPlaceholderText("Client ID из developer.spotify.com")
        self.spotify_client_id.editingFinished.connect(self._on_spotify_id)
        sf.addRow("Client ID", self.spotify_client_id)
        redirect = QLabel(self._spotify().get(
            "redirect_uri", "http://127.0.0.1:8888/callback"
        ))
        redirect.setTextInteractionFlags(redirect.textInteractionFlags() | 1)
        sf.addRow("Redirect URI", redirect)
        self.spotify_status = QLabel("Не подключён")
        sf.addRow("Статус", self.spotify_status)
        row = QHBoxLayout()
        self.spotify_connect = QPushButton("Подключить Spotify")
        self.spotify_connect.clicked.connect(self._on_spotify_connect)
        row.addWidget(self.spotify_connect)
        self.spotify_logout = QPushButton("Отключить")
        self.spotify_logout.clicked.connect(self._on_spotify_logout)
        row.addWidget(self.spotify_logout)
        sf.addRow(row)
        controls = QHBoxLayout()
        for label, key in (("⏮", "previous"), ("⏯", "toggle"), ("⏭", "next")):
            button = QPushButton(label)
            button.clicked.connect(lambda _=False, k=key: self._spotify_control(k))
            controls.addWidget(button)
        sf.addRow("Управление", controls)
        root.addWidget(spotify)

        telegram = QGroupBox("Telegram")
        tf = QFormLayout(telegram)
        self.telegram_token = QLineEdit()
        self.telegram_token.setEchoMode(QLineEdit.Password)
        self.telegram_token.setPlaceholderText("токен от @BotFather")
        tf.addRow("Токен", self.telegram_token)
        self.telegram_chat = QLineEdit(self._telegram().get("chat_id", ""))
        self.telegram_chat.setPlaceholderText("твой chat_id")
        self.telegram_chat.editingFinished.connect(self._on_telegram_config)
        tf.addRow("Chat ID", self.telegram_chat)
        self.telegram_commands = QCheckBox("Разрешить команды /status, /off, /scene")
        self.telegram_commands.setChecked(
            bool(self._telegram().get("commands", True))
        )
        self.telegram_commands.stateChanged.connect(self._on_telegram_config)
        tf.addRow(self.telegram_commands)
        self.telegram_notify = QCheckBox("Присылать уведомления в Telegram")
        self.telegram_notify.setChecked(
            bool(self._telegram().get("notifications", True))
        )
        self.telegram_notify.stateChanged.connect(self._on_telegram_config)
        tf.addRow(self.telegram_notify)
        self.telegram_status = QLabel("Не подключён")
        tf.addRow("Статус", self.telegram_status)
        row = QHBoxLayout()
        self.telegram_start = QPushButton("Запустить бота")
        self.telegram_start.clicked.connect(self._on_telegram_start)
        row.addWidget(self.telegram_start)
        self.telegram_stop = QPushButton("Остановить")
        self.telegram_stop.clicked.connect(self._on_telegram_stop)
        row.addWidget(self.telegram_stop)
        test = QPushButton("Проверить")
        test.clicked.connect(lambda: self._call("telegram_test"))
        row.addWidget(test)
        tf.addRow(row)
        root.addWidget(telegram)

        windows = QGroupBox("Уведомления Windows")
        wf = QFormLayout(windows)
        self.windows_enabled = QCheckBox(
            "Реагировать на входящие уведомления Telegram/Discord/почты"
        )
        self.windows_enabled.setChecked(
            bool(self._windows().get("enabled", False))
        )
        self.windows_enabled.stateChanged.connect(self._on_windows)
        wf.addRow(self.windows_enabled)
        self.windows_status = QLabel(
            "winsdk для любых приложений или Telegram Desktop по заголовку"
        )
        wf.addRow("Статус", self.windows_status)
        test_windows = QPushButton("Тестовая вспышка")
        test_windows.clicked.connect(lambda: self._call("test_notification"))
        wf.addRow(test_windows)
        root.addWidget(windows)
        root.addStretch()

    # ------------------------------------------------------------------

    def _call(self, name, *args):
        handler = self.handlers.get(name)
        if callable(handler):
            try:
                return handler(*args)
            except Exception as exc:
                self.status_error(str(exc))
        return None

    def _changed(self):
        if callable(self.on_change):
            self.on_change()

    def _on_spotify_id(self):
        self._spotify()["client_id"] = self.spotify_client_id.text().strip()
        self._changed()

    def _on_spotify_connect(self):
        self._spotify()["client_id"] = self.spotify_client_id.text().strip()
        self._changed()
        self._call("spotify_connect", self.spotify_client_id.text().strip())

    def _on_spotify_logout(self):
        self._call("spotify_logout")

    def _spotify_control(self, key):
        self._call("spotify_control", key)

    def _on_telegram_config(self, *_):
        self._telegram()["chat_id"] = self.telegram_chat.text().strip()
        self._telegram()["commands"] = bool(self.telegram_commands.isChecked())
        self._telegram()["notifications"] = bool(self.telegram_notify.isChecked())
        self._changed()

    def _on_telegram_start(self):
        token = self.telegram_token.text().strip()
        if token:
            self.telegram_token.clear()
        self._on_telegram_config()
        self._telegram()["enabled"] = True
        self._changed()
        self._call("telegram_start", token, self.telegram_chat.text().strip())

    def _on_telegram_stop(self):
        self._telegram()["enabled"] = False
        self._changed()
        self._call("telegram_stop")

    def _on_windows(self, state):
        self._windows()["enabled"] = bool(state)
        self._changed()
        self._call("windows_notifications", bool(state))

    def set_spotify_status(self, text):
        self.spotify_status.setText(text)

    def set_telegram_status(self, text):
        self.telegram_status.setText(text)

    def set_windows_status(self, text):
        self.windows_status.setText(text)

    def status_error(self, text):
        self.spotify_status.setText(str(text)[:160])
