from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import yaml
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QCloseEvent, QMouseEvent
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


class FloatingStatusWindow(QWidget):
    clicked = Signal()
    position_changed = Signal(int, int)

    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._label = QLabel("语音助手待机")
        self._position = "bottom_right"
        self._font_size = 12
        self._opacity = 220
        self._manual_pos: tuple[int, int] | None = None
        self._drag_offset: tuple[int, int] | None = None
        self._apply_style()
        layout.addWidget(self._label)
        self._auto_hide_timer = QTimer(self)
        self._auto_hide_timer.setSingleShot(True)
        self._auto_hide_timer.timeout.connect(self.hide)
        self.adjustSize()

    def show_message(self, text: str, auto_hide_ms: int = 0) -> None:
        self._label.setText(text)
        self._label.adjustSize()
        self.adjustSize()
        self._move_to_anchor()
        self.show()
        if auto_hide_ms > 0:
            self._auto_hide_timer.start(auto_hide_ms)
        else:
            self._auto_hide_timer.stop()

    def _move_to_anchor(self) -> None:
        if self._manual_pos is not None:
            self.move(self._manual_pos[0], self._manual_pos[1])
            return
        screen = QApplication.primaryScreen()
        if not screen:
            return
        area = screen.availableGeometry()
        x = area.right() - self.width() - 24
        if self._position == "bottom_left":
            x = area.left() + 24
        self.move(x, area.bottom() - self.height() - 24)

    def set_display_options(
        self,
        *,
        position: str,
        font_size: int,
        opacity: int,
        manual_pos: tuple[int, int] | None,
    ) -> None:
        self._position = position if position in ("bottom_right", "bottom_left") else "bottom_right"
        self._font_size = max(10, min(18, int(font_size)))
        self._opacity = max(120, min(255, int(opacity)))
        self._manual_pos = manual_pos
        self._apply_style()
        self._move_to_anchor()

    def _apply_style(self) -> None:
        self._label.setStyleSheet(
            f"background-color: rgba(30, 41, 59, {self._opacity});"
            "color: #f8fafc;"
            "border-radius: 8px;"
            "padding: 8px 12px;"
            f"font-size: {self._font_size}px;",
        )

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.globalPosition().toPoint()
            self._drag_offset = (pos.x() - self.x(), pos.y() - self.y())
        self.clicked.emit()
        return super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_offset is not None and (event.buttons() & Qt.MouseButton.LeftButton):
            pos = event.globalPosition().toPoint()
            new_x = pos.x() - self._drag_offset[0]
            new_y = pos.y() - self._drag_offset[1]
            self.move(new_x, new_y)
            self._manual_pos = (new_x, new_y)
            self.position_changed.emit(new_x, new_y)
        return super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_offset = None
        return super().mouseReleaseEvent(event)


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
        self._push_to_talk_key = "F8"
        self._show_startup_guide = True
        self._show_floating_status = True
        self._floating_position = "bottom_right"
        self._floating_font_size = 12
        self._floating_opacity = 220
        self._floating_manual_pos: tuple[int, int] | None = None
        self._floating_mode = "always"
        self._strategy_hint_shown_in_session = False
        self._recognize_started_at: float | None = None
        self._floating_status = FloatingStatusWindow()
        self._floating_status.set_display_options(
            position=self._floating_position,
            font_size=self._floating_font_size,
            opacity=self._floating_opacity,
            manual_pos=self._floating_manual_pos,
        )
        self._floating_status.clicked.connect(self._show_from_tray)
        self._floating_status.position_changed.connect(self._on_floating_position_changed)
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

        g.addWidget(QLabel("ASR Provider"), 0, 0)
        self.provider_combo = QComboBox()
        g.addWidget(self.provider_combo, 0, 1, 1, 2)

        g.addWidget(QLabel("投递模式"), 1, 0)
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["paste_and_send", "paste_only", "review"])
        g.addWidget(self.mode_combo, 1, 1, 1, 2)
        self.state_label = QLabel("状态: idle")
        g.addWidget(self.state_label, 2, 0, 1, 3)
        self.strategy_label = QLabel("录音策略: Push-to-Talk")
        self.strategy_label.setStyleSheet("color: #495057;")
        g.addWidget(self.strategy_label, 3, 0, 1, 3)
        self.hint_label = QLabel("")
        self.hint_label.setWordWrap(True)
        self.hint_label.setStyleSheet("color: #0b7285;")
        g.addWidget(self.hint_label, 4, 0, 1, 3)
        self.cross_app_hint_label = QLabel(
            "使用提示：启动监听后可切换到其他应用窗口，按 F8 录音识别，松开后自动把结果投递到当前前台窗口。",
        )
        self.cross_app_hint_label.setWordWrap(True)
        self.cross_app_hint_label.setStyleSheet("color: #495057;")
        g.addWidget(self.cross_app_hint_label, 5, 0, 1, 3)
        g.addWidget(QLabel("最近识别结果（运行预览）"), 6, 0, 1, 3)
        self.runtime_preview = QTextEdit()
        self.runtime_preview.setReadOnly(True)
        self.runtime_preview.setPlaceholderText("识别后会在此显示最新结果，便于快速验证效果。")
        self.runtime_preview.setMaximumHeight(120)
        g.addWidget(self.runtime_preview, 7, 0, 1, 3)
        self._update_runtime_hint("idle")
        trigger_layout = QHBoxLayout()
        trigger_layout.addWidget(QLabel("录音触发模式"))
        self.trigger_mode_combo = QComboBox()
        self.trigger_mode_combo.addItem("按住说话（Push-to-Talk）", "push_to_talk")
        self.trigger_mode_combo.addItem("点击切换（Toggle）", "toggle")
        self.trigger_mode_combo.currentIndexChanged.connect(self._on_trigger_mode_changed)
        trigger_layout.addWidget(self.trigger_mode_combo)
        self.vad_enabled_chk = QCheckBox("启用 VAD 自动结束（仅 Toggle 生效）")
        self.vad_enabled_chk.stateChanged.connect(self._on_vad_changed)
        trigger_layout.addWidget(self.vad_enabled_chk)
        self.vad_mode_hint_label = QLabel("")
        self.vad_mode_hint_label.setStyleSheet("color: #868e96;")
        trigger_layout.addWidget(self.vad_mode_hint_label)
        trigger_layout.addStretch(1)
        runtime_layout.addLayout(trigger_layout)

        # 高级设置页
        advanced_tab = QWidget()
        advanced_layout = QVBoxLayout(advanced_tab)
        ag = QGridLayout()
        advanced_layout.addLayout(ag)
        tabs.addTab(advanced_tab, "高级设置")

        lbl_system = QLabel("系统行为")
        lbl_system.setStyleSheet("font-weight: 600; color: #343a40;")
        ag.addWidget(lbl_system, 0, 0, 1, 3)
        ag.addWidget(QLabel("配置文件"), 1, 0)
        self.config_edit = QLineEdit(str(Path("config.yaml").resolve()))
        ag.addWidget(self.config_edit, 1, 1)
        btn_browse = QPushButton("浏览")
        btn_browse.clicked.connect(self._browse_config)
        ag.addWidget(btn_browse, 1, 2)

        self.auto_send_chk = QCheckBox("自动发送回车（Enter）")
        self.auto_send_chk.setToolTip("识别文本粘贴后自动发送回车，适合聊天窗口。")
        ag.addWidget(self.auto_send_chk, 2, 0, 1, 3)
        ag.addWidget(QLabel("窗口白名单"), 3, 0)
        self.whitelist_edit = QLineEdit("")
        self.whitelist_edit.setPlaceholderText("逗号分隔，例如：Cursor, Notepad++")
        self.whitelist_edit.setToolTip("仅当前台窗口标题命中白名单时才执行文本投递。")
        ag.addWidget(self.whitelist_edit, 3, 1, 1, 2)

        self.minimize_to_tray_chk = QCheckBox("关闭窗口时最小化到托盘")
        self.minimize_to_tray_chk.setChecked(True)
        self.minimize_to_tray_chk.setToolTip("关闭主窗口后继续后台运行，可在系统托盘恢复。")
        ag.addWidget(self.minimize_to_tray_chk, 4, 0, 1, 3)
        self.default_enable_chk = QCheckBox("程序启动后自动开始监听并启用识别（保存到配置）")
        self.default_enable_chk.setToolTip('勾选后，打开 GUI 会自动开始监听；取消勾选后需手动点击"开始监听"。')
        self.default_enable_chk.setChecked(True)
        self.default_enable_chk.stateChanged.connect(self._on_default_enable_changed)
        ag.addWidget(self.default_enable_chk, 5, 0, 1, 3)

        lbl_float = QLabel("悬浮与VAD")
        lbl_float.setStyleSheet("font-weight: 600; color: #343a40;")
        ag.addWidget(lbl_float, 6, 0, 1, 3)
        self.show_floating_status_chk = QCheckBox("显示跨窗口悬浮状态提示")
        self.show_floating_status_chk.setChecked(True)
        self.show_floating_status_chk.stateChanged.connect(self._on_show_floating_status_changed)
        self.show_floating_status_chk.setToolTip("切换到其他应用时，悬浮条显示录音/识别/投递状态。")
        ag.addWidget(self.show_floating_status_chk, 7, 0, 1, 3)
        ag.addWidget(QLabel("悬浮条显示模式"), 8, 0)
        self.floating_mode_combo = QComboBox()
        self.floating_mode_combo.addItem("始终显示关键状态", "always")
        self.floating_mode_combo.addItem("仅录音/识别时显示", "recording_only")
        self.floating_mode_combo.currentIndexChanged.connect(self._on_floating_style_changed)
        self.floating_mode_combo.setToolTip("可降低打扰：仅在录音/识别阶段显示悬浮提示。")
        ag.addWidget(self.floating_mode_combo, 8, 1, 1, 2)
        ag.addWidget(QLabel("悬浮条位置"), 9, 0)
        self.floating_position_combo = QComboBox()
        self.floating_position_combo.addItem("右下角", "bottom_right")
        self.floating_position_combo.addItem("左下角", "bottom_left")
        self.floating_position_combo.currentIndexChanged.connect(self._on_floating_style_changed)
        self.floating_position_combo.setToolTip("选择悬浮条默认锚点，拖拽后会记忆实际位置。")
        ag.addWidget(self.floating_position_combo, 9, 1, 1, 2)
        ag.addWidget(QLabel("悬浮条字号"), 10, 0)
        self.floating_font_combo = QComboBox()
        self.floating_font_combo.addItem("小 (11)", 11)
        self.floating_font_combo.addItem("标准 (12)", 12)
        self.floating_font_combo.addItem("大 (14)", 14)
        self.floating_font_combo.currentIndexChanged.connect(self._on_floating_style_changed)
        self.floating_font_combo.setToolTip("调节悬浮条文本大小，适配不同分辨率。")
        ag.addWidget(self.floating_font_combo, 10, 1, 1, 2)
        ag.addWidget(QLabel("悬浮条透明度"), 11, 0)
        self.floating_opacity_combo = QComboBox()
        self.floating_opacity_combo.addItem("低 (160)", 160)
        self.floating_opacity_combo.addItem("标准 (220)", 220)
        self.floating_opacity_combo.addItem("高 (245)", 245)
        self.floating_opacity_combo.currentIndexChanged.connect(self._on_floating_style_changed)
        self.floating_opacity_combo.setToolTip("数值越高越不透明，建议 220。")
        ag.addWidget(self.floating_opacity_combo, 11, 1)
        self.btn_reset_floating_pos = QPushButton("重置悬浮条位置")
        self.btn_reset_floating_pos.clicked.connect(self._reset_floating_position)
        self.btn_reset_floating_pos.setToolTip("清除拖拽坐标并回到默认锚点位置。")
        ag.addWidget(self.btn_reset_floating_pos, 11, 2)

        ag.addWidget(QLabel("VAD 静音阈值(ms)"), 12, 0)
        self.vad_silence_ms_edit = QLineEdit("1500")
        self.vad_silence_ms_edit.setPlaceholderText("例如 1500")
        self.vad_silence_ms_edit.editingFinished.connect(self._on_vad_changed)
        self.vad_silence_ms_edit.setToolTip("静音持续达到该时长时自动结束录音（仅 Toggle 生效）。")
        ag.addWidget(self.vad_silence_ms_edit, 12, 1)
        ag.addWidget(QLabel("VAD 能量阈值"), 13, 0)
        self.vad_energy_edit = QLineEdit("500")
        self.vad_energy_edit.setPlaceholderText("例如 500")
        self.vad_energy_edit.editingFinished.connect(self._on_vad_changed)
        self.vad_energy_edit.setToolTip("越小越敏感，环境噪声大时可适当调高。")
        ag.addWidget(self.vad_energy_edit, 13, 1)
        ag.addWidget(QLabel("（仅在 Toggle 模式下生效）"), 13, 2)
        self.btn_restore_recommended = QPushButton("恢复推荐设置")
        self.btn_restore_recommended.setToolTip("恢复为推荐的稳定参数，并立即保存到配置文件。")
        self.btn_restore_recommended.clicked.connect(self._restore_recommended_settings)
        ag.addWidget(self.btn_restore_recommended, 14, 0, 1, 3)
        self._sync_vad_controls()

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
        self.btn_copy_diag = QPushButton("复制诊断信息")
        self.btn_copy_diag.setToolTip("复制当前配置、状态与最近错误，便于排查问题。")
        self.btn_copy_diag.clicked.connect(self._copy_diagnostics)
        h.addWidget(self.btn_copy_diag)
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
            if isinstance(hotkey, dict):
                push_to_talk = str(hotkey.get("push_to_talk") or "f8").strip()
                self._push_to_talk_key = push_to_talk.upper() if push_to_talk else "F8"
                trigger_mode = str(hotkey.get("trigger_mode") or "push_to_talk").strip().lower()
                idx = self.trigger_mode_combo.findData(trigger_mode)
                if idx >= 0:
                    self.trigger_mode_combo.setCurrentIndex(idx)
            vad = data.get("vad") or {}
            if isinstance(vad, dict):
                self.vad_enabled_chk.setChecked(bool(vad.get("enabled", False)))
                try:
                    silence_ms = int(vad.get("silence_threshold_ms", 1500))
                except (TypeError, ValueError):
                    silence_ms = 1500
                try:
                    energy_threshold = int(vad.get("energy_threshold", 500))
                except (TypeError, ValueError):
                    energy_threshold = 500
                self.vad_silence_ms_edit.setText(str(max(300, silence_ms)))
                self.vad_energy_edit.setText(str(max(50, energy_threshold)))
            self._sync_vad_controls()
            gui = data.get("gui") or {}
            # gui 区块优先加载，避免后续异常时 minimize_to_tray 停留在默认值 True
            if isinstance(gui, dict):
                self.minimize_to_tray_chk.setChecked(bool(gui.get("minimize_to_tray_on_close", True)))
                self.default_enable_chk.setChecked(
                    bool(gui.get("auto_start_listening", (hotkey or {}).get("recognition_enabled_on_start", True))),
                )
                self._show_startup_guide = bool(gui.get("show_startup_guide", True))
                self._show_floating_status = bool(gui.get("show_floating_status", True))
                self._floating_position = str(gui.get("floating_status_position") or "bottom_right").strip().lower()
                if self._floating_position not in ("bottom_right", "bottom_left"):
                    self._floating_position = "bottom_right"
                try:
                    self._floating_font_size = int(gui.get("floating_status_font_size", 12))
                except (TypeError, ValueError):
                    self._floating_font_size = 12
                self._floating_font_size = max(10, min(18, self._floating_font_size))
                try:
                    self._floating_opacity = int(gui.get("floating_status_opacity", 220))
                except (TypeError, ValueError):
                    self._floating_opacity = 220
                self._floating_opacity = max(120, min(255, self._floating_opacity))
                x_raw = gui.get("floating_status_x")
                y_raw = gui.get("floating_status_y")
                self._floating_manual_pos = (int(x_raw), int(y_raw)) if isinstance(x_raw, int) and isinstance(y_raw, int) else None
                self._floating_mode = str(gui.get("floating_status_mode") or "always").strip().lower()
                if self._floating_mode not in ("always", "recording_only"):
                    self._floating_mode = "always"
                self.show_floating_status_chk.setChecked(self._show_floating_status)
                self._floating_status.setVisible(self._show_floating_status)
                self._floating_status.set_display_options(
                    position=self._floating_position,
                    font_size=self._floating_font_size,
                    opacity=self._floating_opacity,
                    manual_pos=self._floating_manual_pos,
                )
                pos_idx = self.floating_position_combo.findData(self._floating_position)
                if pos_idx >= 0:
                    self.floating_position_combo.setCurrentIndex(pos_idx)
                font_idx = self.floating_font_combo.findData(self._floating_font_size)
                if font_idx >= 0:
                    self.floating_font_combo.setCurrentIndex(font_idx)
                opacity_idx = self.floating_opacity_combo.findData(self._floating_opacity)
                if opacity_idx >= 0:
                    self.floating_opacity_combo.setCurrentIndex(opacity_idx)
                mode_idx = self.floating_mode_combo.findData(self._floating_mode)
                if mode_idx >= 0:
                    self.floating_mode_combo.setCurrentIndex(mode_idx)
            self._update_runtime_hint("idle")
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

    def _on_trigger_mode_changed(self, _index: int) -> None:
        if self._loading_ui:
            return
        self._sync_vad_controls()
        self._update_runtime_hint(self._state_label_state())
        self._save_config_changes()

    def _on_vad_changed(self, *_args: object) -> None:
        if self._loading_ui:
            return
        self._update_strategy_label()
        self._update_runtime_hint(self._state_label_state())
        self._save_config_changes()

    def _restore_recommended_settings(self) -> None:
        self._loading_ui = True
        try:
            self.auto_send_chk.setChecked(True)
            self.whitelist_edit.setText("")
            self.minimize_to_tray_chk.setChecked(True)
            self.default_enable_chk.setChecked(True)
            self.show_floating_status_chk.setChecked(True)
            mode_idx = self.floating_mode_combo.findData("recording_only")
            if mode_idx >= 0:
                self.floating_mode_combo.setCurrentIndex(mode_idx)
            pos_idx = self.floating_position_combo.findData("bottom_right")
            if pos_idx >= 0:
                self.floating_position_combo.setCurrentIndex(pos_idx)
            font_idx = self.floating_font_combo.findData(12)
            if font_idx >= 0:
                self.floating_font_combo.setCurrentIndex(font_idx)
            opacity_idx = self.floating_opacity_combo.findData(220)
            if opacity_idx >= 0:
                self.floating_opacity_combo.setCurrentIndex(opacity_idx)
            self._floating_manual_pos = None
            self.vad_enabled_chk.setChecked(True)
            self.vad_silence_ms_edit.setText("1500")
            self.vad_energy_edit.setText("500")
            self._sync_vad_controls()
            self._update_strategy_label()
            self._update_runtime_hint(self._state_label_state())
        finally:
            self._loading_ui = False
        self._save_config_changes()
        self.error_box.append("已恢复推荐设置并保存到配置文件。")

    def _sync_vad_controls(self) -> None:
        trigger_mode = str(self.trigger_mode_combo.currentData() or "push_to_talk")
        enabled = trigger_mode == "toggle"
        self.vad_enabled_chk.setEnabled(enabled)
        self.vad_silence_ms_edit.setEnabled(enabled)
        self.vad_energy_edit.setEnabled(enabled)
        self.vad_mode_hint_label.setText("" if enabled else "VAD 仅在 Toggle 模式下生效")
        self._update_strategy_label()

    def _update_strategy_label(self) -> None:
        trigger_mode = str(self.trigger_mode_combo.currentData() or "push_to_talk")
        if trigger_mode == "toggle":
            if self.vad_enabled_chk.isChecked():
                self.strategy_label.setText("录音策略: Toggle + VAD 自动结束")
            else:
                self.strategy_label.setText("录音策略: Toggle（手动结束）")
            return
        self.strategy_label.setText("录音策略: Push-to-Talk（按下说话，松开结束）")

    def _strategy_short_text(self) -> str:
        trigger_mode = str(self.trigger_mode_combo.currentData() or "push_to_talk")
        if trigger_mode == "toggle" and self.vad_enabled_chk.isChecked():
            return "Toggle + VAD"
        if trigger_mode == "toggle":
            return "Toggle"
        return "Push-to-Talk"

    def _state_label_state(self) -> str:
        text = self.state_label.text().strip()
        return text.split(":", 1)[1].strip() if ":" in text else "idle"

    def _on_show_floating_status_changed(self, _state: int) -> None:
        if self._loading_ui:
            return
        self._show_floating_status = bool(self.show_floating_status_chk.isChecked())
        if not self._show_floating_status:
            self._floating_status.hide()
        else:
            self._floating_status.show_message("语音助手已启用悬浮提示")
        self._save_config_changes()

    def _on_floating_style_changed(self, _index: int) -> None:
        if self._loading_ui:
            return
        self._floating_position = str(self.floating_position_combo.currentData() or "bottom_right")
        try:
            self._floating_font_size = int(self.floating_font_combo.currentData() or 12)
        except (TypeError, ValueError):
            self._floating_font_size = 12
        try:
            self._floating_opacity = int(self.floating_opacity_combo.currentData() or 220)
        except (TypeError, ValueError):
            self._floating_opacity = 220
        self._floating_mode = str(self.floating_mode_combo.currentData() or "always")
        self._floating_status.set_display_options(
            position=self._floating_position,
            font_size=self._floating_font_size,
            opacity=self._floating_opacity,
            manual_pos=self._floating_manual_pos,
        )
        self._save_config_changes()

    def _on_floating_position_changed(self, x: int, y: int) -> None:
        self._floating_manual_pos = (x, y)
        self._save_config_changes()

    def _reset_floating_position(self) -> None:
        self._floating_manual_pos = None
        self._floating_status.set_display_options(
            position=self._floating_position,
            font_size=self._floating_font_size,
            opacity=self._floating_opacity,
            manual_pos=self._floating_manual_pos,
        )
        self._save_config_changes()

    def _copy_diagnostics(self) -> None:
        payload = self._build_diagnostic_text()
        QApplication.clipboard().setText(payload)
        self.error_box.append("诊断信息已复制到剪贴板。")

    def _build_diagnostic_text(self) -> str:
        cfg_path = Path(self.config_edit.text().strip())
        active_provider = self.provider_combo.currentText().strip() or "(unknown)"
        mode = self.mode_combo.currentText().strip() or "(unknown)"
        errors = [line.strip() for line in self.error_box.toPlainText().splitlines() if line.strip()]
        recent_errors = "\n".join(errors[-5:]) if errors else "(none)"
        return (
            "voice2control diagnostics\n"
            f"config_path={cfg_path}\n"
            f"state_label={self.state_label.text().strip()}\n"
            f"auto_start_listening={self.default_enable_chk.isChecked()}\n"
            f"recognition_toggle_text={self.btn_toggle_recognition.text().strip()}\n"
            f"push_to_talk={self._push_to_talk_key}\n"
            f"trigger_mode={self.trigger_mode_combo.currentData()}\n"
            f"vad_enabled={self.vad_enabled_chk.isChecked()}\n"
            f"vad_silence_threshold_ms={self.vad_silence_ms_edit.text().strip() or '1500'}\n"
            f"vad_energy_threshold={self.vad_energy_edit.text().strip() or '500'}\n"
            f"asr_provider={active_provider}\n"
            f"delivery_mode={mode}\n"
            f"minimize_to_tray={self.minimize_to_tray_chk.isChecked()}\n"
            f"lexicon_enabled={self.lexicon_enabled_chk.isChecked()}\n"
            "recent_errors:\n"
            f"{recent_errors}\n"
        )

    def _set_show_startup_guide(self, enabled: bool) -> None:
        self._show_startup_guide = enabled
        cfg_path = Path(self.config_edit.text().strip())
        try:
            data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            if not isinstance(data, dict):
                return
            gui = data.setdefault("gui", {})
            if not isinstance(gui, dict):
                return
            gui["show_startup_guide"] = bool(enabled)
            cfg_path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        except Exception:
            return

    def _show_startup_guide_dialog(self) -> None:
        if not self._show_startup_guide:
            return
        key = self._push_to_talk_key or "F8"
        trigger_mode = str(self.trigger_mode_combo.currentData() or "push_to_talk")
        step2 = (
            f"2) 切换到目标应用窗口，按 {key} 开始录音，再按一次结束\n"
            if trigger_mode == "toggle"
            else f"2) 切换到目标应用窗口，按 {key} 开始录音，松开结束\n"
        )
        msg = QMessageBox(self)
        msg.setWindowTitle("首次使用提示")
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setText(
            "快速上手：\n"
            "1) 点击“开始监听”\n"
            f"{step2}"
            "3) 识别结果会自动投递到前台窗口，可在本窗口查看预览",
        )
        dont_show_chk = QCheckBox("不再显示此提示")
        msg.setCheckBox(dont_show_chk)
        msg.addButton("我知道了", QMessageBox.ButtonRole.AcceptRole)
        msg.exec()
        if dont_show_chk.isChecked():
            self._set_show_startup_guide(False)

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
            hotkey["trigger_mode"] = str(self.trigger_mode_combo.currentData() or "push_to_talk")
            vad = data.setdefault("vad", {})
            if not isinstance(vad, dict):
                raise ValueError("vad 必须是对象")
            try:
                silence_ms = int(self.vad_silence_ms_edit.text().strip() or "1500")
            except ValueError:
                silence_ms = 1500
            try:
                energy_threshold = int(self.vad_energy_edit.text().strip() or "500")
            except ValueError:
                energy_threshold = 500
            vad["enabled"] = bool(self.vad_enabled_chk.isChecked())
            vad["silence_threshold_ms"] = max(300, silence_ms)
            vad["energy_threshold"] = max(50, energy_threshold)
            vad["check_window_ms"] = int(vad.get("check_window_ms") or 320)
            gui = data.setdefault("gui", {})
            if not isinstance(gui, dict):
                raise ValueError("gui 必须是对象")
            gui["minimize_to_tray_on_close"] = bool(self.minimize_to_tray_chk.isChecked())
            gui["auto_start_listening"] = bool(self.default_enable_chk.isChecked())
            gui["show_startup_guide"] = bool(self._show_startup_guide)
            gui["show_floating_status"] = bool(self.show_floating_status_chk.isChecked())
            gui["floating_status_position"] = self._floating_position
            gui["floating_status_font_size"] = self._floating_font_size
            gui["floating_status_opacity"] = self._floating_opacity
            gui["floating_status_x"] = self._floating_manual_pos[0] if self._floating_manual_pos else None
            gui["floating_status_y"] = self._floating_manual_pos[1] if self._floating_manual_pos else None
            gui["floating_status_mode"] = self._floating_mode
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
        if state == "recognizing":
            self._recognize_started_at = time.perf_counter()
        elif state in ("stopped", "idle", "disabled"):
            self._recognize_started_at = None
        if state == "disabled":
            self._recognition_enabled = False
        elif state in ("idle", "recording", "recognizing", "delivering"):
            self._recognition_enabled = True
        self.btn_toggle_recognition.setText("禁用识别" if self._recognition_enabled else "启用识别")
        self._update_runtime_hint(state)
        if self._show_floating_status and self._should_show_floating_for_state(state):
            state_text = {
                "starting": "正在启动监听",
                "idle": "待命中（按热键说话）",
                "recording": "录音中",
                "recognizing": "识别中",
                "delivering": "正在投递",
                "disabled": "识别已禁用",
                "stopping": "正在停止",
                "stopped": "监听已停止",
            }.get(state, f"状态: {state}")
            transient_states = {"starting", "stopping", "stopped", "idle", "disabled"}
            auto_hide_ms = 2200 if state in transient_states else 0
            prefix = self._strategy_short_text()
            self._floating_status.show_message(f"语音助手[{prefix}]：{state_text}", auto_hide_ms=auto_hide_ms)
            if state in ("stopped",):
                self._floating_status.hide()
        elif state in ("stopped", "idle", "disabled"):
            self._floating_status.hide()

    def _should_show_floating_for_state(self, state: str) -> bool:
        if self._floating_mode == "recording_only":
            return state in ("recording", "recognizing", "delivering")
        return True

    def _update_runtime_hint(self, state: str) -> None:
        key = self._push_to_talk_key or "F8"
        trigger_mode = str(self.trigger_mode_combo.currentData() or "push_to_talk") if hasattr(self, "trigger_mode_combo") else "push_to_talk"
        vad_suffix = ""
        if trigger_mode == "toggle" and hasattr(self, "vad_enabled_chk") and self.vad_enabled_chk.isChecked():
            vad_suffix = "（VAD 静音自动结束已启用）"
        action_hint = (
            f"按 {key} 开始录音，再按一次结束。"
            if trigger_mode == "toggle"
            else f"按住 {key} 进行语音识别（按下开始录音，松开结束）。"
        )
        if state == "disabled":
            text = f"监听已运行，但识别已禁用。请点击“启用识别”，然后{action_hint}{vad_suffix}"
        elif state in ("starting", "idle", "recording", "recognizing", "delivering"):
            text = f"监听中：{action_hint}{vad_suffix}"
        else:
            text = f"提示：先点击“开始监听”，然后{action_hint}{vad_suffix}"
        self.hint_label.setText(text)
        if hasattr(self, "cross_app_hint_label"):
            self.cross_app_hint_label.setText(
                "跨窗口使用：启动监听后切换到目标应用窗口，"
                + (
                    f"按 {key} 开始录音，再按一次结束；"
                    if trigger_mode == "toggle"
                    else f"按住 {key} 开始录音，松开结束；"
                )
                + "识别结果会自动投递到当前前台窗口。",
            )

    def _on_transcript(self, text: str) -> None:
        latency_info = ""
        if self._recognize_started_at is not None:
            elapsed = max(0.0, time.perf_counter() - self._recognize_started_at)
            latency_info = f" (耗时 {elapsed:.2f}s)"
            self._recognize_started_at = None
        self.transcript_box.append(text)
        self.runtime_preview.setPlainText(f"{text}{latency_info}")
        if self._show_floating_status and self._floating_mode == "always":
            preview = text.strip().replace("\n", " ")
            if len(preview) > 36:
                preview = preview[:36] + "..."
            self._floating_status.show_message(f"识别结果：{preview}{latency_info}", auto_hide_ms=2200)

    def _on_error(self, msg: str) -> None:
        self._recognize_started_at = None
        self.error_box.append(msg)
        if self._show_floating_status and self._floating_mode == "always":
            preview = msg.strip().replace("\n", " ")
            if len(preview) > 36:
                preview = preview[:36] + "..."
            self._floating_status.show_message(f"异常：{preview}", auto_hide_ms=2200)

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
        self.show_floating_status_chk.blockSignals(True)
        self.show_floating_status_chk.setChecked(bool(cfg.gui.show_floating_status))
        self.show_floating_status_chk.blockSignals(False)
        self.trigger_mode_combo.blockSignals(True)
        mode_idx = self.trigger_mode_combo.findData(cfg.hotkey.trigger_mode)
        if mode_idx >= 0:
            self.trigger_mode_combo.setCurrentIndex(mode_idx)
        self.trigger_mode_combo.blockSignals(False)
        self._sync_vad_controls()
        self._show_floating_status = bool(cfg.gui.show_floating_status)
        self._floating_position = cfg.gui.floating_status_position
        self._floating_font_size = cfg.gui.floating_status_font_size
        self._floating_opacity = cfg.gui.floating_status_opacity
        self._floating_manual_pos = (
            (cfg.gui.floating_status_x, cfg.gui.floating_status_y)
            if cfg.gui.floating_status_x is not None and cfg.gui.floating_status_y is not None
            else None
        )
        self._floating_mode = cfg.gui.floating_status_mode
        self.floating_mode_combo.blockSignals(True)
        mode_idx = self.floating_mode_combo.findData(self._floating_mode)
        if mode_idx >= 0:
            self.floating_mode_combo.setCurrentIndex(mode_idx)
        self.floating_mode_combo.blockSignals(False)
        self.floating_position_combo.blockSignals(True)
        pos_idx = self.floating_position_combo.findData(self._floating_position)
        if pos_idx >= 0:
            self.floating_position_combo.setCurrentIndex(pos_idx)
        self.floating_position_combo.blockSignals(False)
        self.floating_font_combo.blockSignals(True)
        font_idx = self.floating_font_combo.findData(self._floating_font_size)
        if font_idx >= 0:
            self.floating_font_combo.setCurrentIndex(font_idx)
        self.floating_font_combo.blockSignals(False)
        self.floating_opacity_combo.blockSignals(True)
        opacity_idx = self.floating_opacity_combo.findData(self._floating_opacity)
        if opacity_idx >= 0:
            self.floating_opacity_combo.setCurrentIndex(opacity_idx)
        self.floating_opacity_combo.blockSignals(False)
        self._floating_status.set_display_options(
            position=self._floating_position,
            font_size=self._floating_font_size,
            opacity=self._floating_opacity,
            manual_pos=self._floating_manual_pos,
        )
        if self._show_floating_status and not self._strategy_hint_shown_in_session:
            self._floating_status.show_message(
                f"当前录音策略：{self._strategy_short_text()}",
                auto_hide_ms=1500,
            )
            self._strategy_hint_shown_in_session = True
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
        self._floating_status.hide()
        self.close()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._exiting or not self.minimize_to_tray_chk.isChecked():
            try:
                self.tray.hide()
                self._floating_status.hide()
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
    QTimer.singleShot(200, w._show_startup_guide_dialog)
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
