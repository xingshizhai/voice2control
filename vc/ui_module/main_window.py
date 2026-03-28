from __future__ import annotations

import sys
import threading
from pathlib import Path

import yaml
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QCloseEvent, QKeyEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QStyle,
    QSystemTrayIcon,
    QTextEdit,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from vc.config import load_app_config_with_env
from vc.app_module.entry import _setup_logging
from vc.core_module.pipeline import VoicePipeline, warn_if_unsupported_platform
from vc.lexicon_module.service import LexiconStore


# ---------------------------------------------------------------------------
# 热键捕获工具
# ---------------------------------------------------------------------------

_QT_KEY_MAP: dict[int, str] = {}


def _build_qt_key_map() -> dict[int, str]:
    m: dict[int, str] = {}
    for i in range(1, 13):
        attr = f"Key_F{i}"
        k = getattr(Qt.Key, attr, None)
        if k is not None:
            m[k.value] = f"f{i}"
    simple = {
        Qt.Key.Key_Insert: "insert",
        Qt.Key.Key_Delete: "delete",
        Qt.Key.Key_Home: "home",
        Qt.Key.Key_End: "end",
        Qt.Key.Key_PageUp: "page_up",
        Qt.Key.Key_PageDown: "page_down",
        Qt.Key.Key_Pause: "pause",
        Qt.Key.Key_ScrollLock: "scroll_lock",
        Qt.Key.Key_Print: "print_screen",
        Qt.Key.Key_CapsLock: "caps_lock",
        Qt.Key.Key_Tab: "tab",
        Qt.Key.Key_Escape: "esc",
        Qt.Key.Key_Space: "space",
        Qt.Key.Key_Backspace: "backspace",
        Qt.Key.Key_Return: "enter",
        Qt.Key.Key_Enter: "enter",
        Qt.Key.Key_Up: "up",
        Qt.Key.Key_Down: "down",
        Qt.Key.Key_Left: "left",
        Qt.Key.Key_Right: "right",
    }
    for qt_k, name in simple.items():
        m[qt_k.value] = name
    # A-Z → a-z
    for c in range(ord("A"), ord("Z") + 1):
        qt_k = getattr(Qt.Key, f"Key_{chr(c)}", None)
        if qt_k is not None:
            m[qt_k.value] = chr(c).lower()
    # 0-9
    for c in range(ord("0"), ord("9") + 1):
        qt_k = getattr(Qt.Key, f"Key_{chr(c)}", None)
        if qt_k is not None:
            m[qt_k.value] = chr(c)
    return m


def _qt_key_to_str(key: int) -> str:
    """将 Qt 键值转为热键配置字符串，附加可选修饰键前缀。"""
    global _QT_KEY_MAP
    if not _QT_KEY_MAP:
        _QT_KEY_MAP = _build_qt_key_map()
    return _QT_KEY_MAP.get(key, "")


_MODIFIER_KEYS = frozenset({
    Qt.Key.Key_Control.value, Qt.Key.Key_Shift.value,
    Qt.Key.Key_Alt.value, Qt.Key.Key_Meta.value,
})


class _KeyCaptureDialog(QDialog):
    """等待用户按一个非修饰键，将其转换为热键配置字符串后关闭。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("捕获热键")
        self.setModal(True)
        self.setFixedSize(340, 120)
        self.captured: str = ""
        layout = QVBoxLayout(self)
        self._label = QLabel("请按下新的 Push-to-talk 热键\n（F1–F12、Insert、Pause 等单键最适合）")
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._label)
        btn = QPushButton("取消")
        btn.clicked.connect(self.reject)
        layout.addWidget(btn)

    def showEvent(self, event: object) -> None:
        super().showEvent(event)  # type: ignore[arg-type]
        self.grabKeyboard()

    def hideEvent(self, event: object) -> None:
        self.releaseKeyboard()
        super().hideEvent(event)  # type: ignore[arg-type]

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key in _MODIFIER_KEYS:
            return  # 忽略单独的修饰键
        key_str = _qt_key_to_str(key)
        if key_str:
            self.captured = key_str
            self.accept()
        else:
            self._label.setText("不支持此按键，请重试（建议使用 F1–F12）")


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
        self._loading_ui = False
        self._recognition_enabled = False
        self._bridge = Bridge()
        self._bridge.state.connect(self._on_state)
        self._bridge.transcript.connect(self._on_transcript)
        self._bridge.error.connect(self._on_error)

        root = QWidget(self)
        self.setCentralWidget(root)
        root_layout = QVBoxLayout(root)
        tabs = QTabWidget()
        root_layout.addWidget(tabs)

        # 运行配置页
        runtime_tab = QWidget()
        runtime_layout = QVBoxLayout(runtime_tab)
        g = QGridLayout()
        runtime_layout.addLayout(g)
        tabs.addTab(runtime_tab, "运行配置")

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

        self.default_enable_chk = QCheckBox("程序启动后自动开始监听并启用识别（保存到配置）")
        self.default_enable_chk.setToolTip('勾选后，打开 GUI 会自动开始监听；取消勾选后需手动点击"开始监听"。')
        self.default_enable_chk.setChecked(True)
        self.default_enable_chk.stateChanged.connect(self._on_default_enable_changed)
        g.addWidget(self.default_enable_chk, 6, 0, 1, 3)

        # 热键配置
        g.addWidget(QLabel("按住说话（PTT）"), 7, 0)
        self.ptt_key_edit = QLineEdit("f8")
        self.ptt_key_edit.setToolTip("按住此键录音，松开后识别。例：f8、insert、pause")
        g.addWidget(self.ptt_key_edit, 7, 1)
        self.btn_capture_ptt = QPushButton("捕获按键")
        self.btn_capture_ptt.setToolTip("点击后按下目标键自动填入")
        self.btn_capture_ptt.clicked.connect(self._capture_ptt_key)
        g.addWidget(self.btn_capture_ptt, 7, 2)

        g.addWidget(QLabel("退出热键"), 8, 0)
        self.quit_key_edit = QLineEdit("ctrl+q")
        self.quit_key_edit.setToolTip("组合键格式：ctrl+q、alt+f4 等")
        g.addWidget(self.quit_key_edit, 8, 1, 1, 2)

        g.addWidget(QLabel("重录热键"), 9, 0)
        self.rerecord_key_edit = QLineEdit("ctrl+shift+r")
        self.rerecord_key_edit.setToolTip("组合键格式：ctrl+shift+r 等")
        g.addWidget(self.rerecord_key_edit, 9, 1, 1, 2)

        self._hotkey_note = QLabel("⚠ 更改热键后需重启监听")
        self._hotkey_note.setStyleSheet("color: gray; font-size: 11px;")
        self._hotkey_note.setVisible(False)
        g.addWidget(self._hotkey_note, 10, 0, 1, 3)

        self.state_label = QLabel("状态: idle")
        g.addWidget(self.state_label, 11, 0, 1, 3)

        h = QHBoxLayout()
        runtime_layout.addLayout(h)
        self.btn_start = QPushButton("开始监听")
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

        # 词库管理页
        lexicon_tab = QWidget()
        lexicon_layout = QVBoxLayout(lexicon_tab)
        lg = QGridLayout()
        lexicon_layout.addLayout(lg)
        tabs.addTab(lexicon_tab, "词库管理")

        self.lexicon_enabled_chk = QCheckBox("启用本地词库纠正")
        lg.addWidget(self.lexicon_enabled_chk, 0, 0, 1, 3)

        lg.addWidget(QLabel("词库 SQLite"), 1, 0)
        self.lexicon_db_edit = QLineEdit("data/lexicon.db")
        lg.addWidget(self.lexicon_db_edit, 1, 1, 1, 2)

        lg.addWidget(QLabel("词库领域"), 2, 0)
        self.lexicon_domain_edit = QLineEdit("default")
        lg.addWidget(self.lexicon_domain_edit, 2, 1)
        self.btn_lexicon_refresh = QPushButton("刷新词库")
        self.btn_lexicon_refresh.clicked.connect(self._refresh_lexicon_terms)
        lg.addWidget(self.btn_lexicon_refresh, 2, 2)

        lg.addWidget(QLabel("词库搜索"), 3, 0)
        self.lexicon_search_edit = QLineEdit("")
        self.lexicon_search_edit.setPlaceholderText("按术语关键字过滤")
        self.lexicon_search_edit.textChanged.connect(self._apply_lexicon_filter)
        lg.addWidget(self.lexicon_search_edit, 3, 1, 1, 2)

        lg.addWidget(QLabel("新增术语"), 4, 0)
        self.lexicon_term_edit = QLineEdit("")
        self.lexicon_term_edit.setPlaceholderText("标准术语，例如：LangChain")
        lg.addWidget(self.lexicon_term_edit, 4, 1)
        self.btn_lexicon_add = QPushButton("添加术语")
        self.btn_lexicon_add.clicked.connect(self._add_lexicon_term)
        lg.addWidget(self.btn_lexicon_add, 4, 2)

        lg.addWidget(QLabel("术语别名"), 5, 0)
        self.lexicon_aliases_edit = QLineEdit("")
        self.lexicon_aliases_edit.setPlaceholderText("逗号分隔，例如：郎圈,朗链")
        lg.addWidget(self.lexicon_aliases_edit, 5, 1, 1, 2)

        lg.addWidget(QLabel("术语权重"), 6, 0)
        self.lexicon_weight_edit = QLineEdit("100")
        self.lexicon_weight_edit.setPlaceholderText("整数，默认 100")
        lg.addWidget(self.lexicon_weight_edit, 6, 1)
        self.lexicon_sort_combo = QComboBox()
        self.lexicon_sort_combo.addItems(["按权重", "按术语名"])
        self.lexicon_sort_combo.currentIndexChanged.connect(self._refresh_lexicon_terms)
        lg.addWidget(self.lexicon_sort_combo, 6, 2)

        self.btn_lexicon_delete = QPushButton("删除术语")
        self.btn_lexicon_delete.clicked.connect(self._delete_lexicon_term)
        lg.addWidget(self.btn_lexicon_delete, 7, 1)
        self.btn_lexicon_import = QPushButton("导入 CSV")
        self.btn_lexicon_import.clicked.connect(self._import_lexicon_csv)
        lg.addWidget(self.btn_lexicon_import, 7, 2)
        self.btn_lexicon_update_aliases = QPushButton("更新别名")
        self.btn_lexicon_update_aliases.clicked.connect(self._update_lexicon_aliases)
        lg.addWidget(self.btn_lexicon_update_aliases, 8, 1, 1, 2)
        self.btn_lexicon_export = QPushButton("导出 CSV")
        self.btn_lexicon_export.clicked.connect(self._export_lexicon_csv)
        lg.addWidget(self.btn_lexicon_export, 9, 1)
        self.btn_lexicon_template = QPushButton("保存 CSV 模板")
        self.btn_lexicon_template.clicked.connect(self._save_lexicon_csv_template)
        lg.addWidget(self.btn_lexicon_template, 9, 2)

        lexicon_layout.addWidget(QLabel("词库术语"))
        self.lexicon_box = QListWidget()
        self.lexicon_box.itemClicked.connect(self._on_lexicon_item_selected)
        lexicon_layout.addWidget(self.lexicon_box, 1)
        lexicon_layout.addWidget(QLabel("词库别名明细"))
        self.lexicon_alias_preview = QTextEdit()
        self.lexicon_alias_preview.setReadOnly(True)
        self.lexicon_alias_preview.setPlaceholderText("选中术语后显示其别名列表")
        lexicon_layout.addWidget(self.lexicon_alias_preview, 1)

        # 日志输出页
        logs_tab = QWidget()
        logs_layout = QVBoxLayout(logs_tab)
        tabs.addTab(logs_tab, "日志输出")
        logs_layout.addWidget(QLabel("最近识别文本"))
        self.transcript_box = QTextEdit()
        self.transcript_box.setReadOnly(True)
        logs_layout.addWidget(self.transcript_box, 1)
        logs_layout.addWidget(QLabel("错误"))
        self.error_box = QTextEdit()
        self.error_box.setReadOnly(True)
        logs_layout.addWidget(self.error_box, 1)

        self._init_tray()

    def _init_tray(self) -> None:
        self.tray = QSystemTrayIcon(self)
        icon = self.style().standardIcon(QStyle.StandardPixmap.SP_MediaVolume)
        self.tray.setIcon(icon)
        self.tray.setToolTip("AI Voice Controller")

        menu = QMenu()
        action_show = QAction("显示窗口", self)
        action_show.triggered.connect(self._show_from_tray)
        action_start = QAction("开始监听", self)
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
            self._loading_ui = True
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
            gui = data.get("gui") or {}
            # gui 区块优先加载，避免后续异常时 minimize_to_tray 停留在默认值 True
            if isinstance(gui, dict):
                self.minimize_to_tray_chk.setChecked(bool(gui.get("minimize_to_tray_on_close", True)))
                self.default_enable_chk.setChecked(
                    bool(gui.get("auto_start_listening", (hotkey or {}).get("recognition_enabled_on_start", True))),
                )
            if isinstance(hotkey, dict):
                self.ptt_key_edit.setText(str(hotkey.get("push_to_talk") or "f8"))
                self.quit_key_edit.setText(str(hotkey.get("quit") or "ctrl+q"))
                self.rerecord_key_edit.setText(str(hotkey.get("rerecord") or "ctrl+shift+r"))
            lexicon = data.get("lexicon") or {}
            if isinstance(lexicon, dict):
                self.lexicon_enabled_chk.setChecked(bool(lexicon.get("enabled", False)))
                self.lexicon_db_edit.setText(str(lexicon.get("db_path") or "data/lexicon.db"))
                self.lexicon_domain_edit.setText(str(lexicon.get("domain") or "default"))
            self._refresh_lexicon_terms()
        except Exception as e:
            self.error_box.append(f"读取配置失败: {e}")
        finally:
            self._loading_ui = False

    def _on_default_enable_changed(self, _state: int) -> None:
        if self._loading_ui:
            return
        self._save_config_changes()

    def _capture_ptt_key(self) -> None:
        dlg = _KeyCaptureDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.captured:
            self.ptt_key_edit.setText(dlg.captured)

    def _save_config_changes(self) -> bool:
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
            ptt = self.ptt_key_edit.text().strip().lower()
            if ptt:
                hotkey["push_to_talk"] = ptt
            quit_k = self.quit_key_edit.text().strip().lower()
            if quit_k:
                hotkey["quit"] = quit_k
            rerecord_k = self.rerecord_key_edit.text().strip().lower()
            if rerecord_k:
                hotkey["rerecord"] = rerecord_k
            gui = data.setdefault("gui", {})
            if not isinstance(gui, dict):
                raise ValueError("gui 必须是对象")
            gui["minimize_to_tray_on_close"] = bool(self.minimize_to_tray_chk.isChecked())
            gui["auto_start_listening"] = bool(self.default_enable_chk.isChecked())
            lexicon = data.setdefault("lexicon", {})
            if not isinstance(lexicon, dict):
                raise ValueError("lexicon 必须是对象")
            lexicon["enabled"] = bool(self.lexicon_enabled_chk.isChecked())
            lexicon["db_path"] = self.lexicon_db_edit.text().strip() or "data/lexicon.db"
            lexicon["domain"] = self.lexicon_domain_edit.text().strip() or "default"

            cfg_path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
            self.error_box.append("配置已保存")
            return True
        except Exception as e:
            QMessageBox.critical(self, "保存失败", str(e))
            return False

    def _refresh_lexicon_terms(self, *_args: object) -> None:
        db_path = self.lexicon_db_edit.text().strip() or "data/lexicon.db"
        domain = self.lexicon_domain_edit.text().strip() or "default"
        sort_by = "weight_desc" if self.lexicon_sort_combo.currentIndex() == 0 else "term_asc"
        self.lexicon_box.clear()
        self.lexicon_alias_preview.clear()
        try:
            store = LexiconStore(db_path)
            store.ensure_schema()
            terms = store.list_terms(domain=domain, sort_by=sort_by)
            if not terms:
                self.lexicon_box.addItem(f"当前为空：{db_path} | domain={domain}")
                return
            self.lexicon_box.addItem(f"{db_path} | domain={domain} | 术语数={len(terms)}")
            for term, weight, alias_count in terms:
                text = f"{term} (weight={weight}, aliases={alias_count})"
                item = QListWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, term)
                self.lexicon_box.addItem(item)
            self._apply_lexicon_filter()
        except Exception as e:
            self.error_box.append(f"刷新词库失败: {e}")

    def _apply_lexicon_filter(self) -> None:
        keyword = self.lexicon_search_edit.text().strip().lower()
        for i in range(self.lexicon_box.count()):
            item = self.lexicon_box.item(i)
            term = str(item.data(Qt.ItemDataRole.UserRole) or "").strip()
            if not term:
                item.setHidden(False)
                continue
            item.setHidden(not ((not keyword) or (keyword in term.lower())))

    def _add_lexicon_term(self) -> None:
        term = self.lexicon_term_edit.text().strip()
        if not term:
            QMessageBox.warning(self, "提示", "请先输入术语")
            return
        aliases = [x.strip() for x in self.lexicon_aliases_edit.text().split(",") if x.strip()]
        db_path = self.lexicon_db_edit.text().strip() or "data/lexicon.db"
        domain = self.lexicon_domain_edit.text().strip() or "default"
        weight = self._parse_lexicon_weight()
        try:
            store = LexiconStore(db_path)
            store.ensure_schema()
            store.upsert_term(term=term, aliases=aliases, domain=domain, weight=weight)
            self.error_box.append(f"术语已写入词库: {term}")
            self._refresh_lexicon_terms()
        except Exception as e:
            QMessageBox.critical(self, "词库写入失败", str(e))

    def _update_lexicon_aliases(self) -> None:
        term = self.lexicon_term_edit.text().strip()
        if not term:
            QMessageBox.warning(self, "提示", "请先输入术语")
            return
        aliases = [x.strip() for x in self.lexicon_aliases_edit.text().split(",") if x.strip()]
        db_path = self.lexicon_db_edit.text().strip() or "data/lexicon.db"
        domain = self.lexicon_domain_edit.text().strip() or "default"
        weight = self._parse_lexicon_weight()
        try:
            store = LexiconStore(db_path)
            store.ensure_schema()
            store.replace_term_aliases(term=term, aliases=aliases, domain=domain, weight=weight)
            self.error_box.append(f"术语别名已更新: {term}")
            self._refresh_lexicon_terms()
        except Exception as e:
            QMessageBox.critical(self, "词库更新失败", str(e))

    def _delete_lexicon_term(self) -> None:
        term = self.lexicon_term_edit.text().strip()
        if not term:
            QMessageBox.warning(self, "提示", '请先在"新增术语"中输入要删除的术语')
            return
        db_path = self.lexicon_db_edit.text().strip() or "data/lexicon.db"
        domain = self.lexicon_domain_edit.text().strip() or "default"
        try:
            store = LexiconStore(db_path)
            store.ensure_schema()
            ok = store.delete_term(term=term, domain=domain)
            self.error_box.append(f"术语已删除: {term}" if ok else f"未找到术语: {term}")
            self._refresh_lexicon_terms()
        except Exception as e:
            QMessageBox.critical(self, "词库删除失败", str(e))

    def _import_lexicon_csv(self) -> None:
        csv_path, _ = QFileDialog.getOpenFileName(self, "选择词库 CSV", str(Path.cwd()), "CSV (*.csv)")
        if not csv_path:
            return
        db_path = self.lexicon_db_edit.text().strip() or "data/lexicon.db"
        fallback_domain = self.lexicon_domain_edit.text().strip() or "default"
        try:
            store = LexiconStore(db_path)
            report = store.import_csv(csv_path=csv_path, fallback_domain=fallback_domain)
            self.error_box.append(
                "词库导入完成："
                f"总计={report['total']} 导入={report['imported']} "
                f"跳过={report['skipped']} 失败={report['failed']}",
            )
            self._refresh_lexicon_terms()
        except Exception as e:
            QMessageBox.critical(self, "词库导入失败", str(e))

    def _export_lexicon_csv(self) -> None:
        out_path, _ = QFileDialog.getSaveFileName(self, "导出词库 CSV", str(Path.cwd() / "lexicon_export.csv"), "CSV (*.csv)")
        if not out_path:
            return
        db_path = self.lexicon_db_edit.text().strip() or "data/lexicon.db"
        domain = self.lexicon_domain_edit.text().strip() or "default"
        try:
            import csv

            store = LexiconStore(db_path)
            store.ensure_schema()
            rows = store.export_rows(domain=domain)
            out = Path(out_path)
            out.parent.mkdir(parents=True, exist_ok=True)
            with out.open("w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=["term", "aliases", "domain", "weight"])
                writer.writeheader()
                writer.writerows(rows)
            self.error_box.append(f"词库导出完成：{len(rows)} 条 -> {out}")
        except Exception as e:
            QMessageBox.critical(self, "词库导出失败", str(e))

    def _save_lexicon_csv_template(self) -> None:
        out_path, _ = QFileDialog.getSaveFileName(self, "保存词库 CSV 模板", str(Path.cwd() / "lexicon_template.csv"), "CSV (*.csv)")
        if not out_path:
            return
        try:
            import csv

            out = Path(out_path)
            out.parent.mkdir(parents=True, exist_ok=True)
            with out.open("w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=["term", "aliases", "domain", "weight"])
                writer.writeheader()
                writer.writerow({"term": "LangChain", "aliases": "郎圈,朗链", "domain": "default", "weight": 100})
            self.error_box.append(f"CSV 模板已保存: {out}")
        except Exception as e:
            QMessageBox.critical(self, "保存模板失败", str(e))

    def _on_lexicon_item_selected(self, item: QListWidgetItem) -> None:
        term = str(item.data(Qt.ItemDataRole.UserRole) or "").strip()
        if not term:
            return
        db_path = self.lexicon_db_edit.text().strip() or "data/lexicon.db"
        domain = self.lexicon_domain_edit.text().strip() or "default"
        self.lexicon_term_edit.setText(term)
        try:
            store = LexiconStore(db_path)
            terms = store.list_terms(domain=domain, sort_by="weight_desc")
            for t, weight, _ in terms:
                if t == term:
                    self.lexicon_weight_edit.setText(str(weight))
                    break
            aliases = store.get_aliases(term=term, domain=domain)
            self.lexicon_aliases_edit.setText(", ".join(aliases))
            self.lexicon_alias_preview.setPlainText(
                "\n".join(f"- {a}" for a in aliases) if aliases else "(无别名，默认使用术语本身)",
            )
        except Exception as e:
            self.error_box.append(f"读取术语别名失败: {e}")

    def _parse_lexicon_weight(self) -> int:
        raw = self.lexicon_weight_edit.text().strip() or "100"
        try:
            value = int(raw)
        except ValueError:
            self.error_box.append(f"术语权重无效，已使用默认值 100：{raw}")
            value = 100
        return value

    def _on_state(self, state: str) -> None:
        self.state_label.setText(f"状态: {state}")
        running = state not in ("stopped",)
        self.btn_start.setEnabled(not running)
        self.btn_stop.setEnabled(running)
        self.btn_toggle_recognition.setEnabled(running)
        # 监听运行期间禁止修改热键（需重启才能生效）
        for w in (self.ptt_key_edit, self.quit_key_edit, self.rerecord_key_edit,
                  self.btn_capture_ptt):
            w.setEnabled(not running)
        self._hotkey_note.setVisible(running)
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
        if self._thread and self._thread.is_alive():
            self.error_box.append("监听已在运行，无需重复启动。")
            return
        cfg_path = Path(self.config_edit.text().strip())
        if not self._save_config_changes():
            return
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
        enabled_on_start = bool(cfg.hotkey.recognition_enabled_on_start)
        self._recognition_enabled = enabled_on_start
        self.minimize_to_tray_chk.setChecked(bool(cfg.gui.minimize_to_tray_on_close))
        self.btn_toggle_recognition.setText("禁用识别" if enabled_on_start else "启用识别")
        self._thread = threading.Thread(target=self._pipeline.run, daemon=True, name="voice-pipeline")
        self._thread.start()
        self._pipeline.set_recognition_enabled(enabled_on_start)
        self._bridge.state.emit("starting")

    def _stop(self) -> None:
        self._bridge.state.emit("stopping")
        if self._pipeline:
            self._pipeline.request_stop()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.5)
            if self._thread.is_alive():
                self.error_box.append("停止请求已发送，正在等待后台线程结束...")
            else:
                self._pipeline = None
                self._thread = None

    def _toggle_recognition(self) -> None:
        if not self._pipeline:
            return
        self._pipeline.set_recognition_enabled(not self._recognition_enabled)

    def _show_from_tray(self) -> None:
        self.show()
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.raise_()
        self.activateWindow()

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.DoubleClick, QSystemTrayIcon.ActivationReason.Trigger):
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
    cfg_path = Path(w.config_edit.text().strip())
    should_auto_start = w.default_enable_chk.isChecked()
    if cfg_path.exists():
        try:
            cfg = load_app_config_with_env(cfg_path)
            should_auto_start = bool(cfg.gui.auto_start_listening)
        except Exception:
            pass
    if should_auto_start:
        # 延迟到事件循环开始后再启动，避免界面初始化阶段触发复杂逻辑。
        QTimer.singleShot(0, w._start)
    return app.exec()
