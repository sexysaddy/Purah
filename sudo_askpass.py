#!/usr/bin/env python3
"""Local GUI askpass for sudo -A. Password is written only to sudo's stdout pipe."""
from __future__ import annotations

import sys

try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (
        QApplication, QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
    )
except Exception:
    raise SystemExit("Purah sudo askpass needs PySide6")


def main() -> int:
    app = QApplication([])
    app.setApplicationName("Purah Administrator Authorization")
    dialog = QDialog()
    dialog.setWindowTitle("PURAH // ADMIN ACCESS")
    dialog.setWindowFlags(dialog.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
    dialog.setMinimumWidth(390)
    dialog.setStyleSheet("""
        QDialog { background: #07111c; color: #e9f7f7; border: 1px solid #b8954b; }
        QLabel { color: #d5e6e8; font: 10pt 'DejaVu Sans'; }
        QLabel#heading { color: #e6c576; font: bold 10pt 'DejaVu Sans Mono'; }
        QLineEdit { background: #030912; color: #f4ffff; border: 1px solid #397888; padding: 8px; }
        QPushButton { background: #101f2c; color: #e6c576; border: 1px solid #a98943; padding: 7px 13px; }
        QPushButton:hover { background: #1a3141; border-color: #53dce8; }
    """)
    layout = QVBoxLayout(dialog)
    heading = QLabel("SHEIKAH SYSTEM AUTHORIZATION")
    heading.setObjectName("heading")
    layout.addWidget(heading)
    prompt = QLabel("Для этой операции нужны права администратора.\nПароль вводится локально и не отправляется модели.")
    prompt.setWordWrap(True)
    layout.addWidget(prompt)
    edit = QLineEdit()
    edit.setEchoMode(QLineEdit.EchoMode.Password)
    edit.setPlaceholderText("Пароль sudo")
    layout.addWidget(edit)
    buttons = QHBoxLayout()
    buttons.addStretch(1)
    cancel = QPushButton("Отмена")
    okay = QPushButton("Разрешить")
    okay.setDefault(True)
    buttons.addWidget(cancel)
    buttons.addWidget(okay)
    layout.addLayout(buttons)
    cancel.clicked.connect(dialog.reject)
    okay.clicked.connect(dialog.accept)
    edit.returnPressed.connect(dialog.accept)
    edit.setFocus()
    accepted = dialog.exec() == QDialog.DialogCode.Accepted
    password = edit.text() if accepted else ""
    edit.clear()
    if not accepted:
        return 1
    # sudo reads this helper's stdout through a pipe. Don't log or persist the value.
    sys.stdout.write(password + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
