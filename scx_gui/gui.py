from __future__ import annotations

from dataclasses import dataclass
import shlex
from typing import Callable

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QColor, QFontDatabase
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .discovery import BundleInfo, ProgramInfo, discover_bundle
from .help_parser import OptionSpec
from .runtime import (
    can_install_scx_package,
    CommandResult,
    ScxConfig,
    ServiceState,
    install_scx_package,
    read_scx_config,
    read_service_journal,
    read_service_state,
    render_scx_config,
    run_service_action,
    write_scx_config,
)


IGNORED_FORM_FLAGS = {
    "--help",
    "--version",
    "--help-stats",
    "-h",
    "-V",
}


def _safe_split(text: str) -> list[str]:
    if not text.strip():
        return []
    try:
        return shlex.split(text)
    except ValueError:
        return [text]


@dataclass(slots=True)
class RefreshSnapshot:
    bundle: BundleInfo
    config: ScxConfig
    service_state: ServiceState
    journal_text: str


class TaskWorker(QObject):
    finished = Signal(str, object)
    failed = Signal(str, str)

    def __init__(self, label: str, task: Callable[[], object]) -> None:
        super().__init__()
        self.label = label
        self.task = task

    def run(self) -> None:
        try:
            result = self.task()
        except Exception as exc:
            self.failed.emit(self.label, f"{type(exc).__name__}: {exc}")
            return
        self.finished.emit(self.label, result)


class ScxGuiWindow(QMainWindow):
    def __init__(self, *, auto_refresh: bool = True) -> None:
        super().__init__()
        self.bundle = BundleInfo(schedulers=[], utilities=[], docs=[])
        self.current_config = ScxConfig(scheduler="", flags_raw="", original_lines=[])
        self.service_state = ServiceState()
        self.journal_text = ""
        self.scheduler_drafts: dict[str, str] = {}
        self.current_scheduler_name: str | None = None
        self.current_program: ProgramInfo | None = None
        self.refresh_action: QAction | None = None
        self._task_thread: QThread | None = None
        self._task_worker: TaskWorker | None = None
        self._task_success_handler: Callable[[object], None] | None = None
        self._has_loaded_snapshot = False
        self.quick_add_dialog: QDialog | None = None
        self.option_search_edit: QLineEdit | None = None
        self.option_list: QListWidget | None = None
        self.option_detail_label: QLabel | None = None
        self.add_option_button: QPushButton | None = None
        self.copy_option_button: QPushButton | None = None
        self.open_quick_add_button: QPushButton | None = None
        self.install_button: QPushButton | None = None
        self.apply_scheduler_button: QPushButton | None = None

        self.setWindowTitle("SCX GUI")
        self.resize(1220, 860)

        self._build_ui()
        self._populate_scheduler_list()
        self._refresh_summary()
        self._refresh_service_box()
        self._update_preview()
        if auto_refresh:
            QTimer.singleShot(0, self._refresh_all)

    def _build_ui(self) -> None:
        self._apply_palette()
        central = QWidget()
        central.setObjectName("appSurface")
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)
        root.addWidget(self._build_status_frame())
        root.addWidget(self._build_main_splitter(), stretch=1)
        self.setCentralWidget(central)
        self._build_menu()

    def _build_menu(self) -> None:
        self.refresh_action = QAction("Refresh", self)
        self.refresh_action.triggered.connect(self._refresh_all)
        copy_command_action = QAction("Copy Command", self)
        copy_command_action.triggered.connect(self._copy_command)
        save_action = QAction("Save Config", self)
        save_action.triggered.connect(self._save_config)

        menu = self.menuBar().addMenu("Actions")
        menu.addAction(self.refresh_action)
        menu.addAction(save_action)
        menu.addAction(copy_command_action)

    def _build_status_frame(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("statusFrame")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(12)

        info = QVBoxLayout()
        info.setContentsMargins(0, 0, 0, 0)
        info.setSpacing(4)

        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        self.summary_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.summary_label.setStyleSheet("font-size: 15px; font-weight: 600;")
        info.addWidget(self.summary_label)

        self.summary_detail_label = QLabel()
        self.summary_detail_label.setWordWrap(True)
        self.summary_detail_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.summary_detail_label.setStyleSheet("color: #c7ccd2;")
        info.addWidget(self.summary_detail_label)

        self.busy_label = QLabel()
        self.busy_label.setWordWrap(True)
        self.busy_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.busy_label.setStyleSheet("color: #ffd58a;")
        self.busy_label.hide()
        info.addWidget(self.busy_label)

        layout.addLayout(info, stretch=1)

        self.install_button = QPushButton("Install SCX")
        self.install_button.clicked.connect(self._install_scx)
        self.install_button.hide()
        layout.addWidget(self.install_button)

        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self._refresh_all)
        layout.addWidget(self.refresh_button)
        return frame

    def _build_main_splitter(self) -> QWidget:
        splitter = QSplitter()
        splitter.setChildrenCollapsible(False)

        self.scheduler_list = QListWidget()
        self.scheduler_list.setMinimumWidth(250)
        self.scheduler_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.scheduler_list.currentItemChanged.connect(self._on_scheduler_changed)
        splitter.addWidget(self.scheduler_list)

        splitter.addWidget(self._build_editor_scroll())
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([270, 930])
        return splitter

    def _build_editor_scroll(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        container.setObjectName("appSurface")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self.scheduler_title_label = QLabel("No scheduler selected.")
        self.scheduler_title_label.setWordWrap(True)
        self.scheduler_title_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.scheduler_title_label.setStyleSheet("font-size: 23px; font-weight: 700;")
        layout.addWidget(self.scheduler_title_label)

        self.scheduler_summary_label = QLabel("Refresh the app to discover installed schedulers.")
        self.scheduler_summary_label.setWordWrap(True)
        self.scheduler_summary_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.scheduler_summary_label.setStyleSheet("color: #d6d9de;")
        layout.addWidget(self.scheduler_summary_label)

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(8)
        self.help_button = QPushButton("Show Help")
        self.help_button.setObjectName("secondaryButton")
        self.help_button.clicked.connect(self._show_scheduler_help_dialog)
        action_row.addWidget(self.help_button)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        self.dirty_label = QLabel()
        self.dirty_label.setWordWrap(True)
        self.dirty_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.dirty_label)

        layout.addWidget(self._build_flags_group())
        layout.addWidget(self._build_preview_group())
        layout.addWidget(self._build_service_group())
        layout.addStretch(1)

        scroll.setWidget(container)
        return scroll

    def _build_flags_group(self) -> QWidget:
        group = QGroupBox("Flags Saved To /etc/default/scx")
        layout = QVBoxLayout(group)

        info = QLabel(
            "This is the raw SCX_FLAGS value. Keep it simple: add only the flags you actually want saved."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        self.flags_edit = QPlainTextEdit()
        self.flags_edit.setPlaceholderText("--performance --slice-max-us 4000")
        self.flags_edit.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.flags_edit.setMaximumHeight(100)
        self.flags_edit.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.flags_edit.textChanged.connect(self._on_flags_changed)
        layout.addWidget(self.flags_edit)

        row = QHBoxLayout()
        self.save_button = QPushButton("Save Config")
        self.save_button.clicked.connect(self._save_config)
        self.reset_flags_button = QPushButton("Reset To Saved")
        self.reset_flags_button.clicked.connect(self._reset_flags_to_saved)
        self.open_quick_add_button = QPushButton("Open Quick Add")
        self.open_quick_add_button.clicked.connect(self._open_quick_add_dialog)
        self.apply_scheduler_button = QPushButton("Apply Scheduler")
        self.apply_scheduler_button.clicked.connect(self._apply_scheduler)
        row.addWidget(self.save_button)
        row.addWidget(self.reset_flags_button)
        row.addWidget(self.open_quick_add_button)
        row.addWidget(self.apply_scheduler_button)
        row.addStretch(1)
        layout.addLayout(row)
        return group

    def _build_preview_group(self) -> QWidget:
        group = QGroupBox("Command")
        layout = QVBoxLayout(group)

        self.command_preview_label = QLabel("Command preview will appear here.")
        self.command_preview_label.setWordWrap(True)
        self.command_preview_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.command_preview_label)

        note = QLabel(
            "Use Show Config File if you want to inspect the exact contents that will be written to /etc/default/scx."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #d6d9de;")
        layout.addWidget(note)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.copy_command_button = QPushButton("Copy Command")
        self.copy_command_button.clicked.connect(self._copy_command)
        self.show_preview_button = QPushButton("Show Config File")
        self.show_preview_button.setObjectName("secondaryButton")
        self.show_preview_button.clicked.connect(self._show_config_preview_dialog)
        row.addWidget(self.copy_command_button)
        row.addWidget(self.show_preview_button)
        row.addStretch(1)
        layout.addLayout(row)
        return group

    def _build_service_group(self) -> QWidget:
        group = QGroupBox("Service")
        layout = QVBoxLayout(group)
        layout.setSpacing(10)

        cards = QHBoxLayout()
        cards.setContentsMargins(0, 0, 0, 0)
        cards.setSpacing(10)
        (
            self.service_state_card,
            self.service_state_value_label,
            self.service_state_meta_label,
        ) = self._build_status_card("Service")
        (
            self.boot_state_card,
            self.boot_state_value_label,
            self.boot_state_meta_label,
        ) = self._build_status_card("Boot")
        (
            self.sched_ext_card,
            self.sched_ext_value_label,
            self.sched_ext_meta_label,
        ) = self._build_status_card("sched_ext")
        cards.addWidget(self.service_state_card, stretch=1)
        cards.addWidget(self.boot_state_card, stretch=1)
        cards.addWidget(self.sched_ext_card, stretch=1)
        layout.addLayout(cards)

        self.service_status_label = QLabel("Refresh to load service state.")
        self.service_status_label.setWordWrap(True)
        self.service_status_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.service_status_label.setStyleSheet("font-size: 15px; font-weight: 600;")
        layout.addWidget(self.service_status_label)

        self.service_hint_label = QLabel(
            "The saved scheduler in /etc/default/scx will be used unless service override values are active."
        )
        self.service_hint_label.setWordWrap(True)
        self.service_hint_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.service_hint_label.setStyleSheet("color: #c7ccd2;")
        layout.addWidget(self.service_hint_label)

        buttons = QGridLayout()
        buttons.setHorizontalSpacing(8)
        buttons.setVerticalSpacing(8)

        self.service_toggle_button = QPushButton("Start Service")
        self.service_toggle_button.clicked.connect(self._toggle_service)
        self.restart_button = QPushButton("Restart Service")
        self.restart_button.clicked.connect(lambda: self._run_service_action("restart"))
        self.boot_toggle_button = QPushButton("Enable At Boot")
        self.boot_toggle_button.clicked.connect(self._toggle_boot_state)
        self.reset_failed_button = QPushButton("Reset Failed State")
        self.reset_failed_button.clicked.connect(lambda: self._run_service_action("reset-failed"))

        buttons.addWidget(self.service_toggle_button, 0, 0)
        buttons.addWidget(self.restart_button, 0, 1)
        buttons.addWidget(self.reset_failed_button, 0, 2)
        buttons.addWidget(self.boot_toggle_button, 1, 0)
        layout.addLayout(buttons)

        details_row = QHBoxLayout()
        details_row.setContentsMargins(0, 0, 0, 0)
        details_row.setSpacing(8)
        self.service_details_button = QPushButton("Show Service Details")
        self.service_details_button.setObjectName("secondaryButton")
        self.service_details_button.clicked.connect(self._show_service_details_dialog)
        details_row.addWidget(self.service_details_button)
        details_row.addStretch(1)
        layout.addLayout(details_row)
        return group

    def _build_status_card(self, title: str) -> tuple[QFrame, QLabel, QLabel]:
        card = QFrame()
        card.setObjectName("statusCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(4)

        eyebrow = QLabel(title)
        eyebrow.setObjectName("statusEyebrow")
        layout.addWidget(eyebrow)

        value = QLabel("Unknown")
        value.setObjectName("statusValue")
        value.setWordWrap(True)
        layout.addWidget(value)

        meta = QLabel("Refresh to load state.")
        meta.setObjectName("statusMeta")
        meta.setWordWrap(True)
        meta.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(meta)
        layout.addStretch(1)
        return card, value, meta

    def _apply_palette(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow {
                background: #16171a;
                color: #eef2f6;
            }
            QWidget {
                color: #eef2f6;
                font-size: 14px;
            }
            QWidget#appSurface {
                background: #1f2124;
            }
            QLabel, QGroupBox {
                color: #eef2f6;
            }
            QMenuBar, QMenu {
                background: #1f2124;
                color: #eef2f6;
            }
            QMenuBar::item {
                color: #eef2f6;
                background: transparent;
                padding: 4px 8px;
            }
            QMenuBar::item:selected,
            QMenu::item:selected {
                background: #2a2d31;
            }
            QFrame#statusFrame {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #26292d, stop:1 #1d2024);
                border: 1px solid #363a40;
                border-radius: 10px;
            }
            QFrame#statusCard {
                background: #1a1c20;
                border: 1px solid #34383d;
                border-radius: 10px;
            }
            QLabel#statusEyebrow {
                color: #b0b5bc;
                font-size: 12px;
                font-weight: 700;
                letter-spacing: 0.08em;
            }
            QLabel#statusValue {
                color: #eef2f6;
                font-size: 27px;
                font-weight: 700;
            }
            QLabel#statusMeta {
                color: #c7ccd2;
            }
            QListWidget, QPlainTextEdit, QLineEdit, QScrollArea {
                background: #1f2124;
            }
            QListWidget, QPlainTextEdit, QLineEdit {
                border: 1px solid #3b4046;
                border-radius: 6px;
                color: #eef2f6;
                selection-background-color: #4b5057;
            }
            QListWidget {
                font-size: 14px;
            }
            QPushButton {
                background: #3b4046;
                border: 1px solid #575d65;
                border-radius: 6px;
                color: white;
                font-size: 14px;
                padding: 8px 12px;
            }
            QPushButton:hover {
                background: #474d55;
            }
            QPushButton#secondaryButton {
                background: #292d32;
                border: 1px solid #474c53;
            }
            QPushButton#secondaryButton:hover {
                background: #33383e;
            }
            QGroupBox {
                border: 1px solid #3a3f45;
                border-radius: 8px;
                margin-top: 12px;
                padding-top: 12px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
            }
            QSplitter::handle {
                background: #34383e;
            }
            """
        )

    def _refresh_all(self) -> None:
        if self._task_thread is not None and self._task_thread.isRunning():
            self.statusBar().showMessage("A background task is already running.", 3000)
            return
        if self.current_scheduler_name:
            self.scheduler_drafts[self.current_scheduler_name] = self._current_flags_text()
        self._start_task("Refreshing...", self._load_snapshot, self._apply_refresh_snapshot)

    def _load_snapshot(self) -> RefreshSnapshot:
        return RefreshSnapshot(
            bundle=discover_bundle(),
            config=read_scx_config(),
            service_state=read_service_state(),
            journal_text=read_service_journal(),
        )

    def _apply_refresh_snapshot(self, result: object) -> None:
        snapshot = result
        assert isinstance(snapshot, RefreshSnapshot)
        self._has_loaded_snapshot = True
        self.bundle = snapshot.bundle
        self.current_config = snapshot.config
        self.service_state = snapshot.service_state
        self.journal_text = snapshot.journal_text
        if self.current_config.scheduler:
            self.scheduler_drafts.setdefault(self.current_config.scheduler, self.current_config.flags_raw)
        self._populate_scheduler_list()
        self._refresh_summary()
        self._refresh_service_box()
        self.statusBar().showMessage("Refresh complete.", 2500)

    def _start_task(
        self,
        label: str,
        task: Callable[[], object],
        on_success: Callable[[object], None],
    ) -> None:
        if self._task_thread is not None and self._task_thread.isRunning():
            self.statusBar().showMessage("A background task is already running.", 3000)
            return
        self._task_success_handler = on_success
        self._set_busy_state(True, label)

        thread = QThread(self)
        worker = TaskWorker(label, task)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._handle_task_finished)
        worker.failed.connect(self._handle_task_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(self._clear_task_state)

        self._task_thread = thread
        self._task_worker = worker
        thread.start()

    def _handle_task_finished(self, label: str, result: object) -> None:
        handler = self._task_success_handler
        self._task_success_handler = None
        self._set_busy_state(False, "")
        if handler is not None:
            handler(result)

    def _handle_task_failed(self, label: str, message: str) -> None:
        self._task_success_handler = None
        self._set_busy_state(False, "")
        self.statusBar().showMessage(f"{label} failed.", 5000)
        self._show_error(f"{label}\n\n{message}")

    def _clear_task_state(self) -> None:
        self._task_thread = None
        self._task_worker = None

    def _set_busy_state(self, busy: bool, note: str) -> None:
        widgets = [
            self.refresh_button,
            self.install_button,
            self.save_button,
            self.reset_flags_button,
            self.copy_command_button,
            self.show_preview_button,
            self.help_button,
            self.open_quick_add_button,
            self.apply_scheduler_button,
            self.add_option_button,
            self.copy_option_button,
            self.service_toggle_button,
            self.restart_button,
            self.boot_toggle_button,
            self.reset_failed_button,
            self.service_details_button,
        ]
        for widget in widgets:
            if widget is not None:
                widget.setEnabled(not busy)
        self.scheduler_list.setEnabled(not busy)
        if self.refresh_action is not None:
            self.refresh_action.setEnabled(not busy)
        if busy:
            self.refresh_button.setText("Working...")
            self.busy_label.setText(f"{note} The app is working in the background.")
            self.busy_label.show()
            self.statusBar().showMessage(note, 0)
        else:
            self.refresh_button.setText("Refresh")
            self.busy_label.hide()
            self.busy_label.clear()
            self.statusBar().clearMessage()
            self._refresh_install_button()
            self._refresh_apply_scheduler_button()

    def _populate_scheduler_list(self) -> None:
        previous_name = self.current_scheduler_name or self.current_config.scheduler
        active_name = self._active_scheduler_name()
        self.scheduler_list.blockSignals(True)
        self.scheduler_list.clear()
        for program in self.bundle.schedulers:
            tags: list[str] = []
            if program.name == active_name:
                tags.append("active")
            elif program.name == self.current_config.scheduler:
                tags.append("saved")
            label = program.name if not tags else f"{program.name}  [{', '.join(tags)}]"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, program.name)
            item.setToolTip(program.summary)
            if program.name == active_name:
                item.setForeground(QColor("#e4ffe9"))
                item.setBackground(QColor("#274135"))
                font = item.font()
                font.setBold(True)
                item.setFont(font)
            self.scheduler_list.addItem(item)
        self.scheduler_list.blockSignals(False)

        if self.scheduler_list.count() == 0:
            self.current_scheduler_name = None
            self.current_program = None
            self.scheduler_title_label.setText("No schedulers found.")
            if self._has_loaded_snapshot and can_install_scx_package():
                self.scheduler_summary_label.setText("SCX is not installed yet. Use Install SCX above, then refresh.")
            else:
                self.scheduler_summary_label.setText("Install the openSUSE scx package and refresh.")
            self.flags_edit.blockSignals(True)
            self.flags_edit.setPlainText("")
            self.flags_edit.blockSignals(False)
            self._populate_option_list()
            self._update_preview()
            return

        self._select_scheduler(previous_name)

    def _select_scheduler(self, name: str | None) -> None:
        if name:
            for index in range(self.scheduler_list.count()):
                item = self.scheduler_list.item(index)
                if item.data(Qt.UserRole) == name:
                    self.scheduler_list.setCurrentItem(item)
                    return
        self.scheduler_list.setCurrentRow(0)

    def _on_scheduler_changed(self, current: QListWidgetItem | None, previous: QListWidgetItem | None) -> None:
        if previous is not None and self.current_scheduler_name:
            self.scheduler_drafts[self.current_scheduler_name] = self._current_flags_text()
        if current is None:
            return
        name = current.data(Qt.UserRole)
        program = self._program_by_name(name)
        if program is None:
            return
        self.current_scheduler_name = program.name
        self.current_program = program
        self.scheduler_title_label.setText(program.name)
        self.scheduler_summary_label.setText(program.summary or "No summary available.")
        raw_flags = self.scheduler_drafts.get(
            program.name,
            self.current_config.flags_raw if program.name == self.current_config.scheduler else "",
        )
        self.flags_edit.blockSignals(True)
        self.flags_edit.setPlainText(raw_flags)
        self.flags_edit.blockSignals(False)
        if self.quick_add_dialog is not None:
            self.quick_add_dialog.setWindowTitle(f"{program.name} Quick Add")
        self._populate_option_list()
        self._refresh_service_box()
        self._update_preview()

    def _populate_option_list(self) -> None:
        if self.option_list is None or self.option_detail_label is None:
            return
        self.option_list.clear()
        program = self.current_program
        if program is None:
            self.option_detail_label.setText("No scheduler selected.")
            return
        filter_text = self.option_search_edit.text().strip().lower() if self.option_search_edit is not None else ""
        options = [option for option in program.options if option.flag_name not in IGNORED_FORM_FLAGS]
        for option in options:
            haystack = " ".join(
                [
                    option.display_name,
                    option.description,
                    option.default or "",
                    " ".join(option.possible_values),
                ]
            ).lower()
            if filter_text and filter_text not in haystack:
                continue
            item = QListWidgetItem(option.display_name)
            item.setData(Qt.UserRole, option)
            item.setToolTip(self._option_tooltip(option))
            self.option_list.addItem(item)
        if self.option_list.count():
            self.option_list.setCurrentRow(0)
        else:
            self.option_detail_label.setText("No matching options for this scheduler.")

    def _update_option_detail(self, current: QListWidgetItem | None, _: QListWidgetItem | None = None) -> None:
        if self.option_detail_label is None:
            return
        if current is None:
            self.option_detail_label.setText("Select an option to see its details.")
            return
        spec = current.data(Qt.UserRole)
        if not isinstance(spec, OptionSpec):
            self.option_detail_label.setText("Select an option to see its details.")
            return
        parts = [spec.display_name]
        if spec.description:
            parts.append(spec.description)
        if spec.default:
            parts.append(f"Default: {spec.default}")
        if spec.possible_values:
            parts.append(f"Choices: {', '.join(spec.possible_values)}")
        self.option_detail_label.setText(" | ".join(parts))

    def _add_selected_option(self, *_args) -> None:
        if self.option_list is None:
            self._show_error("Open Quick Add first.")
            return
        item = self.option_list.currentItem()
        if item is None:
            self._show_error("Select an option first.")
            return
        spec = item.data(Qt.UserRole)
        if not isinstance(spec, OptionSpec):
            self._show_error("Select an option first.")
            return
        tokens = self._tokens_for_option(spec)
        if not tokens:
            return
        existing = _safe_split(self._current_flags_text())
        self.flags_edit.blockSignals(True)
        self.flags_edit.setPlainText(shlex.join(existing + tokens))
        self.flags_edit.blockSignals(False)
        self._update_preview()

    def _copy_selected_option(self) -> None:
        if self.option_list is None:
            self._show_error("Open Quick Add first.")
            return
        item = self.option_list.currentItem()
        if item is None:
            self._show_error("Select an option first.")
            return
        spec = item.data(Qt.UserRole)
        if not isinstance(spec, OptionSpec):
            self._show_error("Select an option first.")
            return
        QApplication.clipboard().setText(self._option_snippet(spec))
        self.statusBar().showMessage("Selected flag copied to clipboard.", 2500)

    def _tokens_for_option(self, spec: OptionSpec) -> list[str]:
        if not spec.takes_value:
            return [spec.flag_name]
        if spec.possible_values:
            value, ok = QInputDialog.getItem(
                self,
                "Choose Value",
                f"Value for {spec.display_name}:",
                spec.possible_values,
                editable=False,
            )
            if not ok or not value:
                return []
            return [spec.flag_name, value]
        value, ok = QInputDialog.getText(
            self,
            "Enter Value",
            f"Value for {spec.display_name}:",
            text=spec.default or "",
        )
        if not ok or not value.strip():
            return []
        return [spec.flag_name, value.strip()]

    def _option_snippet(self, spec: OptionSpec) -> str:
        if not spec.takes_value:
            return spec.flag_name
        placeholder = spec.default or (spec.possible_values[0] if spec.possible_values else (spec.metavar or "VALUE"))
        return shlex.join([spec.flag_name, placeholder])

    def _on_flags_changed(self) -> None:
        self._update_preview()

    def _current_flags_text(self) -> str:
        raw = self.flags_edit.toPlainText().strip()
        if not raw:
            return ""
        tokens = _safe_split(raw)
        return shlex.join(tokens)

    def _update_preview(self) -> None:
        scheduler = self.current_scheduler_name or self.current_config.scheduler
        flags_raw = self._current_flags_text()
        self.command_preview_label.setText(f"<b>Saved command:</b> {self._build_command_preview(scheduler, flags_raw)}")
        self._refresh_dirty_label()
        self._refresh_apply_scheduler_button()

    def _build_command_preview(self, scheduler: str | None, flags_raw: str) -> str:
        if not scheduler:
            return "(no scheduler selected)"
        tokens = [scheduler]
        if flags_raw:
            tokens.extend(_safe_split(flags_raw))
        return shlex.join(tokens)

    def _refresh_dirty_label(self) -> None:
        scheduler = self.current_scheduler_name or ""
        flags_raw = self._current_flags_text()
        dirty = scheduler != self.current_config.scheduler or flags_raw != self.current_config.flags_raw
        if dirty:
            self.dirty_label.setText(
                "Unsaved changes: the selected scheduler or flags do not match /etc/default/scx yet."
            )
            self.dirty_label.setStyleSheet("color: #ffd58a;")
        else:
            self.dirty_label.setText("Saved state: this matches /etc/default/scx.")
            self.dirty_label.setStyleSheet("color: #9fd8b5;")

    def _reset_flags_to_saved(self) -> None:
        if self.current_program is None:
            return
        raw_flags = self.scheduler_drafts.get(
            self.current_program.name,
            self.current_config.flags_raw if self.current_program.name == self.current_config.scheduler else "",
        )
        self.flags_edit.blockSignals(True)
        self.flags_edit.setPlainText(raw_flags)
        self.flags_edit.blockSignals(False)
        self._update_preview()

    def _save_config(
        self,
        after_success: Callable[[], None] | None = None,
        *,
        show_success_dialog: bool = True,
    ) -> None:
        scheduler = self.current_scheduler_name
        if not scheduler:
            self._show_error("Select a scheduler first.")
            return
        flags_raw = self._current_flags_text()
        config = ScxConfig(
            scheduler=scheduler,
            flags_raw=flags_raw,
            original_lines=self.current_config.original_lines,
            path=self.current_config.path,
        )

        def on_saved(result: object) -> None:
            assert isinstance(result, CommandResult)
            if not result.ok:
                self._show_result("Save Config Failed", result, error=True)
                return
            self.current_config = read_scx_config()
            self.scheduler_drafts[scheduler] = flags_raw
            self._populate_scheduler_list()
            self._update_preview()
            self._refresh_summary()
            self._refresh_service_box()
            if show_success_dialog:
                self._show_result("Config Saved", result)
            if after_success is not None:
                QTimer.singleShot(0, after_success)

        self._start_task("Saving config...", lambda: write_scx_config(config), on_saved)

    def _run_service_action(self, action: str) -> None:
        if action in {"start", "restart", "enable"} and self._has_unsaved_changes():
            choice = self._confirm_save_before_action(action)
            if choice == "cancel":
                return
            if choice == "save":
                self._save_config(after_success=lambda: self._run_service_action(action))
                return

        def on_complete(result: object) -> None:
            assert isinstance(result, CommandResult)
            title = f"Service {action.replace('-', ' ').title()}"
            if result.ok:
                self._show_result(title, result)
            else:
                self._show_result(title, result, error=True)
            self._refresh_all()

        self._start_task(
            f"{action.replace('-', ' ').title()}...",
            lambda: run_service_action(action),
            on_complete,
        )

    def _toggle_service(self) -> None:
        self._run_service_action(self._service_toggle_action_name())

    def _toggle_boot_state(self) -> None:
        self._run_service_action(self._boot_toggle_action_name())

    def _confirm_save_before_action(self, action: str) -> str:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("Unsaved Changes")
        box.setText(f"The current scheduler settings are not saved. Save before {action}?")
        save_button = box.addButton("Save First", QMessageBox.AcceptRole)
        box.addButton("Use Saved Config", QMessageBox.DestructiveRole)
        cancel_button = box.addButton(QMessageBox.Cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked is save_button:
            return "save"
        if clicked is cancel_button:
            return "cancel"
        return "use-saved"

    def _has_unsaved_changes(self) -> bool:
        scheduler = self.current_scheduler_name or ""
        flags_raw = self._current_flags_text()
        return scheduler != self.current_config.scheduler or flags_raw != self.current_config.flags_raw

    def _apply_scheduler(self) -> None:
        if not self.current_scheduler_name:
            self._show_error("Select a scheduler first.")
            return
        if self.service_state.override_active:
            choice = QMessageBox.question(
                self,
                "Service Overrides Active",
                "scx.service override values are active, so applying the saved scheduler may not change the live one until those overrides are removed. Continue anyway?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if choice != QMessageBox.Yes:
                return
        if self._has_unsaved_changes():
            self._save_config(after_success=self._continue_apply_scheduler, show_success_dialog=False)
            return
        self._continue_apply_scheduler()

    def _continue_apply_scheduler(self) -> None:
        if self.service_state.active_state == "failed":
            self._start_task(
                "Resetting failed state...",
                lambda: run_service_action("reset-failed"),
                self._handle_apply_reset_complete,
            )
            return
        self._run_apply_service_step(self._apply_service_action_name())

    def _handle_apply_reset_complete(self, result: object) -> None:
        assert isinstance(result, CommandResult)
        if not result.ok:
            self._show_result("Apply Scheduler", result, error=True)
            QTimer.singleShot(0, self._refresh_all)
            return
        QTimer.singleShot(0, lambda: self._run_apply_service_step("start"))

    def _apply_service_action_name(self) -> str:
        return "restart" if self.service_state.active_state == "active" else "start"

    def _run_apply_service_step(self, action: str) -> None:
        label = "Applying scheduler..."

        def on_complete(result: object) -> None:
            assert isinstance(result, CommandResult)
            if result.ok:
                self._show_result("Apply Scheduler", result)
            else:
                self._show_result("Apply Scheduler", result, error=True)
            QTimer.singleShot(0, self._refresh_all)

        self._start_task(label, lambda: run_service_action(action), on_complete)

    def _refresh_summary(self) -> None:
        if not self._has_loaded_snapshot:
            self.summary_label.setText("Refresh to load installed SCX state.")
            self.summary_detail_label.setText("The app will check your installed schedulers, saved config, and scx.service state.")
            self._refresh_install_button()
            return

        if not self._scx_available():
            self.summary_label.setText("SCX is not installed.")
            if can_install_scx_package():
                self.summary_detail_label.setText(
                    "Click Install SCX to download it from your configured openSUSE repositories, then refresh."
                )
            else:
                self.summary_detail_label.setText(
                    "Install the openSUSE scx package manually, then refresh."
                )
            self._refresh_install_button()
            return

        scheduler = self.current_config.scheduler or "No scheduler saved yet"
        self.summary_label.setText(f"Saved scheduler: {scheduler}")
        if self.service_state.override_active:
            detail_text = (
                "Service override values are active, so scx.service may ignore the saved scheduler or flags below."
            )
        elif self.bundle.schedulers:
            detail_text = (
                f"{len(self.bundle.schedulers)} schedulers detected. Pick one, save the flags you want, then use the service buttons."
            )
        else:
            detail_text = "Refresh after installing scx to discover available schedulers."
        self.summary_detail_label.setText(detail_text)
        self._refresh_install_button()
        self._refresh_apply_scheduler_button()

    def _refresh_service_box(self) -> None:
        service_value, service_meta, service_color = self._service_card_state()
        boot_value, boot_meta, boot_color = self._boot_card_state()
        sched_ext_value, sched_ext_meta, sched_ext_color = self._sched_ext_card_state()

        self._set_status_card(self.service_state_value_label, self.service_state_meta_label, service_value, service_meta, service_color)
        self._set_status_card(self.boot_state_value_label, self.boot_state_meta_label, boot_value, boot_meta, boot_color)
        self._set_status_card(self.sched_ext_value_label, self.sched_ext_meta_label, sched_ext_value, sched_ext_meta, sched_ext_color)

        if self.service_state.active_state == "active":
            self.service_status_label.setText("scx.service is running.")
        elif self.service_state.active_state == "failed":
            self.service_status_label.setText("scx.service is failed.")
        elif self.service_state.active_state == "activating":
            self.service_status_label.setText("scx.service is starting.")
        elif self.service_state.active_state == "deactivating":
            self.service_status_label.setText("scx.service is stopping.")
        elif self.service_state.active_state == "inactive":
            self.service_status_label.setText("scx.service is stopped.")
        else:
            state = self.service_state.active_state or "unknown"
            self.service_status_label.setText(f"scx.service state is {state}.")

        hint_parts = []
        if self.service_state.active_state == "failed":
            hint_parts.append("Use Reset Failed State before trying Start Service again.")
        if self.service_state.override_active:
            override_parts = []
            if self.service_state.scheduler_override:
                override_parts.append(f"scheduler override: {self.service_state.scheduler_override}")
            if self.service_state.flags_override:
                override_parts.append(f"flags override: {self.service_state.flags_override}")
            hint_parts.append("Overrides are active: " + "; ".join(override_parts) + ".")
        elif self.service_state.uses_override_placeholders:
            hint_parts.append("The service will use the saved values from /etc/default/scx.")
        ops_text = ", ".join(self.service_state.sched_ext_ops) if self.service_state.sched_ext_ops else "none"
        hint_parts.append(f"Active sched_ext ops: {ops_text}.")
        self.service_hint_label.setText(" ".join(hint_parts))
        self._refresh_service_action_buttons()
        self._refresh_apply_scheduler_button()

    def _copy_command(self) -> None:
        command = self._build_command_preview(self.current_scheduler_name, self._current_flags_text())
        QApplication.clipboard().setText(command)
        self.statusBar().showMessage("Command copied to clipboard.", 2500)

    def _active_scheduler_name(self) -> str | None:
        if self.service_state.active_state != "active":
            return None
        name = self.service_state.active_scheduler.strip()
        return name or None

    def _service_toggle_action_name(self) -> str:
        return "stop" if self.service_state.active_state == "active" else "start"

    def _boot_toggle_action_name(self) -> str:
        return "disable" if self.service_state.unit_file_state == "enabled" else "enable"

    def _scx_available(self) -> bool:
        return bool(self.bundle.schedulers)

    def _refresh_install_button(self) -> None:
        if self.install_button is None:
            return
        should_show = self._has_loaded_snapshot and not self._scx_available() and can_install_scx_package()
        self.install_button.setVisible(should_show)
        self.install_button.setEnabled(should_show and self._task_thread is None)

    def _refresh_service_action_buttons(self) -> None:
        service_action = self._service_toggle_action_name()
        self.service_toggle_button.setText("Stop Service" if service_action == "stop" else "Start Service")
        if service_action == "stop":
            self.service_toggle_button.setToolTip("Stop scx.service.")
        else:
            self.service_toggle_button.setToolTip("Start scx.service using the saved scheduler settings.")

        boot_action = self._boot_toggle_action_name()
        self.boot_toggle_button.setText("Disable At Boot" if boot_action == "disable" else "Enable At Boot")
        if boot_action == "disable":
            self.boot_toggle_button.setToolTip("Stop scx.service from starting automatically at boot.")
        else:
            self.boot_toggle_button.setToolTip("Start scx.service automatically at boot.")

    def _refresh_apply_scheduler_button(self) -> None:
        if self.apply_scheduler_button is None:
            return
        if self.current_scheduler_name is None or not self._scx_available():
            self.apply_scheduler_button.setEnabled(False)
            self.apply_scheduler_button.setToolTip("Select a scheduler first.")
            return
        active_name = self._active_scheduler_name()
        needs_apply = (
            self._has_unsaved_changes()
            or self.service_state.active_state != "active"
            or self.current_scheduler_name != active_name
        )
        action = self._apply_service_action_name()
        if self.service_state.active_state == "failed":
            tooltip = "Save the selected scheduler, reset the failed service state, and start scx.service."
        elif action == "restart":
            tooltip = "Save the selected scheduler and restart scx.service."
        else:
            tooltip = "Save the selected scheduler and start scx.service."
        if not needs_apply:
            tooltip = "The selected scheduler is already running with the saved settings."
        self.apply_scheduler_button.setToolTip(tooltip)
        self.apply_scheduler_button.setEnabled(needs_apply and self._task_thread is None)

    def _install_scx(self) -> None:
        if not can_install_scx_package():
            self._show_error("Automatic installation is only supported on systems with zypper available.")
            return

        choice = QMessageBox.question(
            self,
            "Install SCX",
            "Install the openSUSE scx package from your configured repositories now?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if choice != QMessageBox.Yes:
            return

        def on_complete(result: object) -> None:
            assert isinstance(result, CommandResult)
            if result.ok:
                self._show_result("Install SCX", result)
                QTimer.singleShot(0, self._refresh_all)
            else:
                self._show_result("Install SCX", result, error=True)

        self._start_task("Installing SCX...", install_scx_package, on_complete)

    def _open_quick_add_dialog(self) -> None:
        program = self.current_program
        if program is None:
            self._show_error("Select a scheduler first.")
            return
        if self.quick_add_dialog is not None:
            self.quick_add_dialog.show()
            self.quick_add_dialog.raise_()
            self.quick_add_dialog.activateWindow()
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(f"{program.name} Quick Add")
        dialog.resize(920, 760)
        dialog.setModal(False)
        dialog.setAttribute(Qt.WA_DeleteOnClose, True)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        intro = QLabel(
            "This list is built from the installed scheduler's --help output. Double-click an option to add it to SCX_FLAGS."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.option_search_edit = QLineEdit()
        self.option_search_edit.setClearButtonEnabled(True)
        self.option_search_edit.setPlaceholderText("Filter options by name or description")
        self.option_search_edit.textChanged.connect(self._populate_option_list)
        layout.addWidget(self.option_search_edit)

        self.option_list = QListWidget()
        self.option_list.currentItemChanged.connect(self._update_option_detail)
        self.option_list.itemDoubleClicked.connect(self._add_selected_option)
        layout.addWidget(self.option_list, stretch=1)

        self.option_detail_label = QLabel("Select an option to see its details.")
        self.option_detail_label.setWordWrap(True)
        self.option_detail_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.option_detail_label.setStyleSheet("color: #d6d9de;")
        layout.addWidget(self.option_detail_label)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.add_option_button = QPushButton("Add Selected")
        self.add_option_button.clicked.connect(self._add_selected_option)
        self.copy_option_button = QPushButton("Copy Selected Flag")
        self.copy_option_button.clicked.connect(self._copy_selected_option)
        close_button = QPushButton("Close")
        close_button.setObjectName("secondaryButton")
        close_button.clicked.connect(dialog.close)
        row.addWidget(self.add_option_button)
        row.addWidget(self.copy_option_button)
        row.addStretch(1)
        row.addWidget(close_button)
        layout.addLayout(row)

        dialog.finished.connect(self._close_quick_add_dialog)
        self.quick_add_dialog = dialog
        self._populate_option_list()
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _close_quick_add_dialog(self, *_args) -> None:
        self.quick_add_dialog = None
        self.option_search_edit = None
        self.option_list = None
        self.option_detail_label = None
        self.add_option_button = None
        self.copy_option_button = None

    def _service_card_state(self) -> tuple[str, str, str]:
        state = self.service_state.active_state
        if state == "active":
            value = "Running"
            meta = f"PID {self.service_state.exec_main_pid or '0'} is active."
            color = "#9fd8b5"
        elif state == "failed":
            value = "Failed"
            meta = "Reset the failed state, then try starting again."
            color = "#ff8d8d"
        elif state == "activating":
            value = "Starting"
            meta = "systemd is starting scx.service."
            color = "#ffd58a"
        elif state == "deactivating":
            value = "Stopping"
            meta = "systemd is stopping scx.service."
            color = "#ffd58a"
        elif state == "inactive":
            value = "Stopped"
            meta = "scx.service is not running right now."
            color = "#d4d7dc"
        else:
            value = state.replace("-", " ").title() or "Unknown"
            meta = "The current service state could not be classified."
            color = "#ffd58a"
        return value, meta, color

    def _boot_card_state(self) -> tuple[str, str, str]:
        state = self.service_state.unit_file_state
        if state == "enabled":
            return "Enabled", "Starts automatically at boot.", "#9fd8b5"
        if state == "disabled":
            return "Disabled", "Starts only when you launch it yourself.", "#d4d7dc"
        if state == "static":
            return "Static", "Controlled by another unit or dependency.", "#d0b3ff"
        if state == "masked":
            return "Masked", "systemd is blocking this service from starting.", "#ff8d8d"
        return state.replace("-", " ").title() or "Unknown", "Read from systemctl unit file state.", "#d4d7dc"

    def _sched_ext_card_state(self) -> tuple[str, str, str]:
        state = self.service_state.sched_ext_state
        if state == "enabled":
            meta = "The kernel sched_ext hook is active."
            if self.service_state.sched_ext_ops:
                meta = f"Active ops: {', '.join(self.service_state.sched_ext_ops)}."
            return "Enabled", meta, "#9fd8b5"
        if state == "disabled":
            return "Disabled", "No sched_ext scheduler is active.", "#d4d7dc"
        return state.replace("-", " ").title() or "Unknown", "Kernel sched_ext state could not be read.", "#ffd58a"

    def _set_status_card(self, value_label: QLabel, meta_label: QLabel, value: str, meta: str, color: str) -> None:
        value_label.setText(value)
        value_label.setStyleSheet(f"color: {color}; font-size: 27px; font-weight: 700;")
        meta_label.setText(meta)

    def _program_by_name(self, name: str | None) -> ProgramInfo | None:
        if not name:
            return None
        return next((program for program in self.bundle.schedulers if program.name == name), None)

    def _option_tooltip(self, spec: OptionSpec) -> str:
        parts = [spec.raw_spec]
        if spec.description:
            parts.append(spec.description)
        if spec.default:
            parts.append(f"Default: {spec.default}")
        if spec.possible_values:
            parts.append(f"Choices: {', '.join(spec.possible_values)}")
        return "\n".join(parts)

    def _show_result(self, title: str, result: CommandResult, *, error: bool = False) -> None:
        if error:
            QMessageBox.critical(self, title, self._format_result_text(result))
        else:
            QMessageBox.information(self, title, self._format_result_text(result))

    def _show_error(self, message: str) -> None:
        QMessageBox.critical(self, "SCX GUI", message)

    def _show_scheduler_help_dialog(self) -> None:
        program = self.current_program
        if program is None:
            self._show_error("Select a scheduler first.")
            return
        text = program.help_text or "No help output available."
        self._show_text_dialog(f"{program.name} Help", text)

    def _show_config_preview_dialog(self) -> None:
        scheduler = self.current_scheduler_name or self.current_config.scheduler
        rendered = render_scx_config(
            ScxConfig(
                scheduler=scheduler or "",
                flags_raw=self._current_flags_text(),
                original_lines=self.current_config.original_lines,
                path=self.current_config.path,
            )
        )
        self._show_text_dialog("/etc/default/scx Preview", rendered)

    def _show_service_details_dialog(self) -> None:
        state = self.service_state
        detail_lines = [
            f"LoadState={state.load_state}",
            f"ActiveState={state.active_state}",
            f"SubState={state.sub_state}",
            f"UnitFileState={state.unit_file_state}",
            f"ExecMainPID={state.exec_main_pid}",
            f"sched_ext_state={state.sched_ext_state}",
            f"sched_ext_ops={', '.join(state.sched_ext_ops) if state.sched_ext_ops else 'none'}",
            f"FragmentPath={state.fragment_path or '(unknown)'}",
            f"EnvironmentFiles={state.environment_files or '(none)'}",
            f"DropInPaths={state.drop_in_paths or '(none)'}",
            f"ExecStart={state.exec_start or '(unknown)'}",
        ]
        if state.override_active:
            detail_lines.append(f"SCX_SCHEDULER_OVERRIDE={state.scheduler_override or '(empty)'}")
            detail_lines.append(f"SCX_FLAGS_OVERRIDE={state.flags_override or '(empty)'}")
        elif state.uses_override_placeholders:
            detail_lines.append("SCX override placeholders exist in the unit, but no override values are currently set.")
        body = "\n".join(detail_lines).rstrip()
        if self.journal_text.strip():
            body += f"\n\nRecent journal:\n{self.journal_text.strip()}"
        self._show_text_dialog("scx.service Details", body)

    def _show_text_dialog(self, title: str, text: str) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(900, 640)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        edit = QPlainTextEdit()
        edit.setReadOnly(True)
        edit.setLineWrapMode(QPlainTextEdit.NoWrap)
        edit.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        edit.setPlainText(text)
        layout.addWidget(edit, stretch=1)

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_button = QPushButton("Close")
        close_button.clicked.connect(dialog.accept)
        close_row.addWidget(close_button)
        layout.addLayout(close_row)

        dialog.exec()

    def _format_result_text(self, result: CommandResult) -> str:
        command = shlex.join(result.args)
        if result.combined_output:
            output = result.combined_output
        elif result.ok:
            output = "Command completed without extra output."
        else:
            output = "(no output)"
        return f"Command:\n{command}\n\nExit code: {result.returncode}\n\nOutput:\n{output}"

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self._task_thread is not None and self._task_thread.isRunning():
            self._task_thread.quit()
            self._task_thread.wait(2000)
        super().closeEvent(event)
