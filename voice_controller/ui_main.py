from __future__ import annotations

import sys
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QMenu,
    QStyle,
    QSystemTrayIcon,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
import yaml

from voice_controller.config import load_app_config_with_env
from voice_controller.main import _setup_logging
from voice_controller.pipeline import VoicePipeline, warn_if_unsupported_platform


class Bridge(QObject):
    state = Signal(str)
    transcript = Signal(str)
    error = Signal(str)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("AI Voice Controller (PySide6)")
        self.resize(820, 520)

        self._pipeline: VoicePipeline | None = None
        self._thread: threading.Thread | None = None
        self._exiting = False
        self._recognition_enabled = False
        self._bridge = Bridge()
        self._bridge.state.connect(self._on_state)
        self._bridge.transcript.connect(self._on_transcript)
        self._bridge.error.connect(self._on_error)

        root = QWidget(self)
        self.setCentralWidget(root)
        v = QVBoxLayout(root)

        g = QGridLayout()
        v.addLayout(g)

        g.addWidget(QLabel("配置文件"), 0, 0)
        self.config_edit = QLineEdit(str(Path("config.yaml").resolve()))
        g.addWidget(self.config_edit, 0, 1)
        btn_browse = QPushButton("浏览")
        btn_browse.clicked.connect(self._browse_config)
        g.addWidget(btn_browse, 0, 2)

        g.addWidget(QLabel("ASR Provider"), 1, 0)
        self.provider_combo = QComboBox()
        g.addWidget(self.provider_combo, 1, 1, 1, 2)

        g.addWidget(QLabel("投递模式"), 2, 0)
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["paste_and_send", "paste_only", "review"])
        g.addWidget(self.mode_combo, 2, 1, 1, 2)

        self.auto_send_chk = QCheckBox("自动发送回车（Enter）")
        g.addWidget(self.auto_send_chk, 3, 0, 1, 3)

        g.addWidget(QLabel("窗口白名单"), 4, 0)
        self.whitelist_edit = QLineEdit("")
        self.whitelist_edit.setPlaceholderText("逗号分隔，例如：Cursor, Notepad++")
        g.addWidget(self.whitelist_edit, 4, 1, 1, 2)

        self.minimize_to_tray_chk = QCheckBox("关闭窗口时最小化到托盘")
        self.minimize_to_tray_chk.setChecked(True)
        g.addWidget(self.minimize_to_tray_chk, 5, 0, 1, 3)

        self.default_enable_chk = QCheckBox("程序启动后默认启用识别（下次启动生效）")
        self.default_enable_chk.setToolTip("该选项写入配置文件，影响下一次点击“启动监听”后的初始识别状态。")
        self.default_enable_chk.setChecked(True)
        g.addWidget(self.default_enable_chk, 6, 0, 1, 3)

        self.state_label = QLabel("状态: idle")
        g.addWidget(self.state_label, 7, 0, 1, 3)

        h = QHBoxLayout()
        v.addLayout(h)
        self.btn_start = QPushButton("启动监听")
        self.btn_start.setToolTip("启动热键监听与语音管线。")
        self.btn_start.clicked.connect(self._start)
        self.btn_stop = QPushButton("停止监听")
        self.btn_stop.setToolTip("停止热键监听与语音管线。")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._stop)
        self.btn_toggle_recognition = QPushButton("启用识别")
        self.btn_toggle_recognition.setToolTip("在监听运行期间，临时启用/禁用识别。")
        self.btn_toggle_recognition.setEnabled(False)
        self.btn_toggle_recognition.clicked.connect(self._toggle_recognition)
        self.btn_save = QPushButton("保存配置")
        self.btn_save.clicked.connect(self._save_config_changes)
        h.addWidget(self.btn_save)
        h.addWidget(self.btn_toggle_recognition)
        h.addWidget(self.btn_start)
        h.addWidget(self.btn_stop)
        h.addStretch(1)

        v.addWidget(QLabel("最近识别文本"))
        self.transcript_box = QTextEdit()
        self.transcript_box.setReadOnly(True)
        v.addWidget(self.transcript_box, 1)

        v.addWidget(QLabel("错误"))
        self.error_box = QTextEdit()
        self.error_box.setReadOnly(True)
        v.addWidget(self.error_box, 1)

        self._init_tray()

    def _init_tray(self) -> None:
        self.tray = QSystemTrayIcon(self)
        icon = self.style().standardIcon(QStyle.StandardPixmap.SP_MediaVolume)
        self.tray.setIcon(icon)
        self.tray.setToolTip("AI Voice Controller")

        menu = QMenu()
        action_show = QAction("显示窗口", self)
        action_show.triggered.connect(self._show_from_tray)
        action_start = QAction("启动监听", self)
        action_start.triggered.connect(self._start)
        action_stop = QAction("停止监听", self)
        action_stop.triggered.connect(self._stop)
        action_exit = QAction("退出", self)
        action_exit.triggered.connect(self._exit_app)
        menu.addAction(action_show)
        menu.addAction(action_start)
        menu.addAction(action_stop)
        menu.addSeparator()
        menu.addAction(action_exit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

    def _browse_config(self) -> None:
        p, _ = QFileDialog.getOpenFileName(self, "选择配置文件", str(Path.cwd()), "YAML (*.yaml *.yml)")
        if p:
            self.config_edit.setText(p)
            self._load_config_for_ui()

    def _load_config_for_ui(self) -> None:
        cfg_path = Path(self.config_edit.text().strip())
        if not cfg_path.exists():
            return
        try:
            data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            asr = data.get("asr") or {}
            providers = asr.get("providers") or {}
            active = str(asr.get("active_provider") or "")

            self.provider_combo.clear()
            if isinstance(providers, dict):
                keys = [str(k) for k in providers.keys()]
                self.provider_combo.addItems(keys)
                if active and active in keys:
                    self.provider_combo.setCurrentText(active)

            delivery = data.get("delivery") or {}
            mode = str(delivery.get("mode") or "paste_and_send")
            idx = self.mode_combo.findText(mode)
            if idx >= 0:
                self.mode_combo.setCurrentIndex(idx)
            self.auto_send_chk.setChecked(bool(delivery.get("auto_send_enter", True)))
            wl = delivery.get("window_whitelist") or []
            if isinstance(wl, list):
                self.whitelist_edit.setText(", ".join(str(x) for x in wl if str(x).strip()))
            hotkey = data.get("hotkey") or {}
            if isinstance(hotkey, dict):
                self.default_enable_chk.setChecked(bool(hotkey.get("recognition_enabled_on_start", True)))
            gui = data.get("gui") or {}
            if isinstance(gui, dict):
                self.minimize_to_tray_chk.setChecked(
                    bool(gui.get("minimize_to_tray_on_close", True)),
                )
        except Exception as e:
            self.error_box.append(f"读取配置失败: {e}")

    def _save_config_changes(self) -> None:
        cfg_path = Path(self.config_edit.text().strip())
        try:
            data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            if not isinstance(data, dict):
                raise ValueError("配置根节点必须是对象")

            asr = data.setdefault("asr", {})
            if not isinstance(asr, dict):
                raise ValueError("asr 必须是对象")
            if self.provider_combo.currentText().strip():
                asr["active_provider"] = self.provider_combo.currentText().strip()

            delivery = data.setdefault("delivery", {})
            if not isinstance(delivery, dict):
                raise ValueError("delivery 必须是对象")
            delivery["mode"] = self.mode_combo.currentText()
            delivery["auto_send_enter"] = bool(self.auto_send_chk.isChecked())
            wl = [x.strip() for x in self.whitelist_edit.text().split(",") if x.strip()]
            delivery["window_whitelist"] = wl
            hotkey = data.setdefault("hotkey", {})
            if not isinstance(hotkey, dict):
                raise ValueError("hotkey 必须是对象")
            hotkey["recognition_enabled_on_start"] = bool(self.default_enable_chk.isChecked())
            gui = data.setdefault("gui", {})
            if not isinstance(gui, dict):
                raise ValueError("gui 必须是对象")
            gui["minimize_to_tray_on_close"] = bool(self.minimize_to_tray_chk.isChecked())

            cfg_path.write_text(
                yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            self.error_box.append("配置已保存")
        except Exception as e:
            QMessageBox.critical(self, "保存失败", str(e))

    def _on_state(self, state: str) -> None:
        self.state_label.setText(f"状态: {state}")
        running = state not in ("stopped",)
        self.btn_start.setEnabled(not running)
        self.btn_stop.setEnabled(running)
        self.btn_toggle_recognition.setEnabled(running)
        if state == "disabled":
            self._recognition_enabled = False
        elif state in ("idle", "recording", "recognizing", "delivering"):
            self._recognition_enabled = True
        self.btn_toggle_recognition.setText("禁用识别" if self._recognition_enabled else "启用识别")

    def _on_transcript(self, text: str) -> None:
        self.transcript_box.append(text)

    def _on_error(self, msg: str) -> None:
        self.error_box.append(msg)

    def _start(self) -> None:
        cfg_path = Path(self.config_edit.text().strip())
        self._save_config_changes()
        try:
            cfg = load_app_config_with_env(cfg_path)
        except Exception as e:
            QMessageBox.critical(self, "配置错误", str(e))
            return

        _setup_logging(verbose=True)
        warn_if_unsupported_platform()

        self._pipeline = VoicePipeline(
            cfg,
            on_state=lambda s: self._bridge.state.emit(s),
            on_transcript=lambda t: self._bridge.transcript.emit(t),
            on_error=lambda e: self._bridge.error.emit(e),
        )
        # 以实际加载到的配置为准，避免 UI 临时状态覆盖配置文件中的启动开关。
        enabled_on_start = bool(cfg.hotkey.recognition_enabled_on_start)
        self._recognition_enabled = enabled_on_start
        self.btn_toggle_recognition.setText("禁用识别" if enabled_on_start else "启用识别")
        self._thread = threading.Thread(target=self._pipeline.run, daemon=True, name="voice-pipeline")
        self._thread.start()
        # 启动后再显式同步一次，确保管线状态与加载配置一致。
        self._pipeline.set_recognition_enabled(enabled_on_start)
        self._bridge.state.emit("starting")

    def _stop(self) -> None:
        if self._pipeline:
            self._pipeline.request_stop()
        self._bridge.state.emit("stopping")

    def _toggle_recognition(self) -> None:
        if not self._pipeline:
            return
        target = not self._recognition_enabled
        self._pipeline.set_recognition_enabled(target)

    def _show_from_tray(self) -> None:
        self.show()
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.raise_()
        self.activateWindow()

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.DoubleClick,
            QSystemTrayIcon.ActivationReason.Trigger,
        ):
            self._show_from_tray()

    def _exit_app(self) -> None:
        self._exiting = True
        self._stop()
        self.close()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._exiting or not self.minimize_to_tray_chk.isChecked():
            try:
                self.tray.hide()
            except Exception:
                pass
            super().closeEvent(event)
            return

        event.ignore()
        self.hide()
        self.tray.showMessage(
            "AI Voice Controller",
            "程序仍在后台运行，可在托盘中恢复窗口或退出。",
            QSystemTrayIcon.MessageIcon.Information,
            2500,
        )


def launch_gui() -> int:
    app = QApplication(sys.argv)
    w = MainWindow()
    w._load_config_for_ui()
    w.show()
    return app.exec()

