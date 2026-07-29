"""Tema escuro centralizado da aplicação.

Concentra todas as cores, espaçamentos e regras de estilo usados
pela camada `ui`. Aplicando `apply_app_theme()` uma vez no boot do
aplicativo, todos os widgets herdam o visual sem precisar declarar
`setStyleSheet` em cada arquivo.

Design system:
    * Inspiração: estilo "developer tool" (GitHub Dark, Linear, VS Code Dark+)
    * Paleta: tons de cinza-azulado com acentos suaves
    * Tipografia: hierarquia por peso, não por cor saturada
    * Espaçamentos: múltiplos de 8px (grid 8/16/24)
    * Estados: hover/pressed/disabled todos visíveis
"""

from __future__ import annotations

import logging

from PySide6.QtGui import QFont, QPalette

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Paleta                                                                      #
# --------------------------------------------------------------------------- #


class Colors:
    """Tokens semânticos de cor.

    Mantemos nomes por FUNÇÃO (fundo, borda) e não por matiz. Trocar
    a paleta é uma operação isolada neste arquivo.
    """

    # Fundos em camadas: cada nível "afunda" mais.
    BG_BASE = "#0f131b"        # janela principal
    BG_SURFACE = "#161b22"     # cards, groupbox, sidebar
    BG_SURFACE_ALT = "#1c2128" # linhas alternadas, hover
    BG_ELEVATED = "#21262d"    # inputs, popups
    BG_INPUT = "#0d1117"       # campos de texto (mais profundo)
    BG_OVERLAY = "#30363d"     # divisor, scrollbar track

    # Bordas / divisores.
    BORDER = "#30363d"
    BORDER_STRONG = "#484f58"
    BORDER_FOCUS = "#58a6ff"

    # Texto.
    TEXT_PRIMARY = "#e6edf3"
    TEXT_SECONDARY = "#9da7b3"
    TEXT_MUTED = "#6e7681"
    TEXT_DISABLED = "#484f58"
    TEXT_ON_ACCENT = "#ffffff"

    # Acentos (azul como cor de marca).
    ACCENT = "#4493f8"
    ACCENT_HOVER = "#58a6ff"
    ACCENT_PRESSED = "#2f7be0"
    ACCENT_SUBTLE = "#1f3a5f"  # fundo de badge/link

    # Estados semânticos.
    SUCCESS = "#3fb950"
    SUCCESS_SUBTLE = "#1b3a23"
    WARNING = "#d29922"
    WARNING_SUBTLE = "#3d2f0a"
    DANGER = "#f85149"
    DANGER_SUBTLE = "#3d1416"
    INFO = "#58a6ff"

    # Sombras (usadas via rgba em border/background).


# --------------------------------------------------------------------------- #
# Espaçamentos                                                                 #
# --------------------------------------------------------------------------- #


class Spacing:
    """Grid de espaçamento — múltiplos de 4px."""

    XS = 4
    SM = 8
    MD = 16
    LG = 24
    XL = 32


RADIUS = {
    "sm": 4,
    "md": 6,
    "lg": 10,
    "pill": 9999,
}


# --------------------------------------------------------------------------- #
# QSS (Qt Style Sheet)                                                          #
# --------------------------------------------------------------------------- #


def _font_block() -> str:
    """Bloco de tipografia aplicado globalmente."""
    return f"""
    QWidget {{
        font-family: "Segoe UI", "Helvetica Neue", "Inter", "Ubuntu", sans-serif;
        font-size: 13px;
        color: {Colors.TEXT_PRIMARY};
    }}
    """


STYLESHEET = f"""
/* =========================================================== */
/*  Base                                                       */
/* =========================================================== */
{_font_block()}

QMainWindow, QDialog {{
    background-color: {Colors.BG_BASE};
}}

QWidget {{
    background-color: {Colors.BG_BASE};
    color: {Colors.TEXT_PRIMARY};
}}

/* =========================================================== */
/*  Abas                                                        */
/* =========================================================== */
QTabWidget::pane {{
    border: none;
    background-color: {Colors.BG_BASE};
    border-top: 1px solid {Colors.BORDER};
    border-radius: 0;
    top: -1px;
}}

QTabBar {{
    background-color: transparent;
    border: none;
    qproperty-drawBase: 0;
}}

QTabBar::tab {{
    background-color: transparent;
    color: {Colors.TEXT_SECONDARY};
    padding: 12px 22px;
    border: none;
    border-bottom: 2px solid transparent;
    font-weight: 500;
    min-width: 80px;
}}

QTabBar::tab:hover {{
    color: {Colors.TEXT_PRIMARY};
    background-color: {Colors.BG_SURFACE};
}}

QTabBar::tab:selected {{
    color: {Colors.ACCENT_HOVER};
    border-bottom: 2px solid {Colors.ACCENT};
    background-color: transparent;
}}

QTabBar::tab:disabled {{
    color: {Colors.TEXT_DISABLED};
}}

/* =========================================================== */
/*  Botões                                                      */
/* =========================================================== */
QPushButton {{
    background-color: {Colors.BG_ELEVATED};
    color: {Colors.TEXT_PRIMARY};
    border: 1px solid {Colors.BORDER};
    border-radius: {RADIUS["md"]}px;
    padding: 8px 16px;
    font-weight: 500;
    min-height: 18px;
    text-align: center;
}}

QPushButton:hover {{
    background-color: {Colors.BG_SURFACE_ALT};
    border-color: {Colors.BORDER_STRONG};
}}

QPushButton:pressed {{
    background-color: {Colors.BG_OVERLAY};
    border-color: {Colors.BORDER_FOCUS};
}}

QPushButton:disabled {{
    background-color: {Colors.BG_SURFACE};
    color: {Colors.TEXT_DISABLED};
    border-color: {Colors.BORDER};
}}

QPushButton:default {{
    background-color: {Colors.ACCENT};
    color: {Colors.TEXT_ON_ACCENT};
    border: 1px solid {Colors.ACCENT};
    font-weight: 600;
}}

QPushButton:default:hover {{
    background-color: {Colors.ACCENT_HOVER};
    border-color: {Colors.ACCENT_HOVER};
}}

QPushButton:default:pressed {{
    background-color: {Colors.ACCENT_PRESSED};
    border-color: {Colors.ACCENT_PRESSED};
}}

QPushButton:default:disabled {{
    background-color: {Colors.BG_ELEVATED};
    color: {Colors.TEXT_DISABLED};
    border-color: {Colors.BORDER};
}}

/* =========================================================== */
/*  Inputs (QLineEdit)                                          */
/* =========================================================== */
QLineEdit, QPlainTextEdit, QTextEdit {{
    background-color: {Colors.BG_INPUT};
    color: {Colors.TEXT_PRIMARY};
    border: 1px solid {Colors.BORDER};
    border-radius: {RADIUS["sm"]}px;
    padding: 7px 10px;
    selection-background-color: {Colors.ACCENT_PRESSED};
    selection-color: {Colors.TEXT_ON_ACCENT};
}}

QLineEdit:hover, QPlainTextEdit:hover, QTextEdit:hover {{
    border-color: {Colors.BORDER_STRONG};
}}

QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus {{
    border: 1px solid {Colors.BORDER_FOCUS};
    background-color: {Colors.BG_ELEVATED};
}}

QLineEdit:disabled {{
    color: {Colors.TEXT_DISABLED};
    background-color: {Colors.BG_SURFACE};
}}

QPlainTextEdit, QTextEdit {{
    padding: 8px 10px;
}}

/* =========================================================== */
/*  Combobox                                                    */
/* =========================================================== */
QComboBox {{
    background-color: {Colors.BG_ELEVATED};
    color: {Colors.TEXT_PRIMARY};
    border: 1px solid {Colors.BORDER};
    border-radius: {RADIUS["sm"]}px;
    padding: 6px 30px 6px 10px;
    min-height: 22px;
}}

QComboBox:hover {{
    border-color: {Colors.BORDER_STRONG};
}}

QComboBox:focus {{
    border: 1px solid {Colors.BORDER_FOCUS};
}}

QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 24px;
    border: none;
    border-left: 1px solid {Colors.BORDER};
}}

QComboBox::down-arrow {{
    width: 10px;
    height: 10px;
    image: none;
    /* Triângulo desenhado com bordas (sem precisar de asset). */
    border-top: 4px solid {Colors.TEXT_SECONDARY};
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    margin-right: 8px;
}}

QComboBox QAbstractItemView {{
    background-color: {Colors.BG_ELEVATED};
    color: {Colors.TEXT_PRIMARY};
    border: 1px solid {Colors.BORDER_STRONG};
    border-radius: {RADIUS["sm"]}px;
    selection-background-color: {Colors.ACCENT_PRESSED};
    selection-color: {Colors.TEXT_ON_ACCENT};
    outline: none;
    padding: 4px;
}}

QComboBox QAbstractItemView::item {{
    padding: 6px 10px;
    border-radius: {RADIUS["sm"]}px;
}}

QComboBox QAbstractItemView::item:selected {{
    background-color: {Colors.ACCENT_PRESSED};
}}

/* =========================================================== */
/*  GroupBox                                                    */
/* =========================================================== */
QGroupBox {{
    background-color: {Colors.BG_SURFACE};
    border: 1px solid {Colors.BORDER};
    border-radius: {RADIUS["md"]}px;
    margin-top: 14px;
    padding: 14px 12px 10px 12px;
    font-weight: 600;
    color: {Colors.TEXT_SECONDARY};
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 8px;
    color: {Colors.TEXT_SECONDARY};
    background-color: {Colors.BG_SURFACE};
    left: 12px;
}}

/* =========================================================== */
/*  Tabelas                                                     */
/* =========================================================== */
QHeaderView::section {{
    background-color: {Colors.BG_SURFACE};
    color: {Colors.TEXT_SECONDARY};
    padding: 8px 10px;
    border: none;
    border-bottom: 1px solid {Colors.BORDER};
    border-right: 1px solid {Colors.BORDER};
    font-weight: 600;
    text-transform: none;
}}

QHeaderView::section:last {{
    border-right: none;
}}

QHeaderView::section:hover {{
    background-color: {Colors.BG_SURFACE_ALT};
    color: {Colors.TEXT_PRIMARY};
}}

QTableWidget, QTableView {{
    background-color: {Colors.BG_SURFACE};
    alternate-background-color: {Colors.BG_BASE};
    gridline-color: {Colors.BORDER};
    color: {Colors.TEXT_PRIMARY};
    border: 1px solid {Colors.BORDER};
    border-radius: {RADIUS["md"]}px;
    selection-background-color: {Colors.ACCENT_PRESSED};
    selection-color: {Colors.TEXT_ON_ACCENT};
    outline: none;
}}

QTableWidget::item, QTableView::item {{
    padding: 6px 10px;
    border: none;
    border-bottom: 1px solid {Colors.BORDER};
}}

QTableWidget::item:selected, QTableView::item:selected {{
    background-color: {Colors.ACCENT_SUBTLE};
    color: {Colors.TEXT_PRIMARY};
}}

QTableWidget::item:hover, QTableView::item:hover {{
    background-color: {Colors.BG_SURFACE_ALT};
}}

/* =========================================================== */
/*  Lista lateral (preset dialog)                               */
/* =========================================================== */
QListWidget {{
    background-color: {Colors.BG_SURFACE};
    color: {Colors.TEXT_PRIMARY};
    border: 1px solid {Colors.BORDER};
    border-radius: {RADIUS["md"]}px;
    padding: 4px;
    outline: none;
}}

QListWidget::item {{
    padding: 8px 10px;
    border-radius: {RADIUS["sm"]}px;
    margin: 1px 2px;
}}

QListWidget::item:hover {{
    background-color: {Colors.BG_SURFACE_ALT};
}}

QListWidget::item:selected {{
    background-color: {Colors.ACCENT_SUBTLE};
    color: {Colors.ACCENT_HOVER};
}}

/* =========================================================== */
/*  Scrollbars                                                  */
/* =========================================================== */
QScrollBar:vertical {{
    background-color: transparent;
    width: 10px;
    margin: 4px 2px 4px 2px;
    border: none;
}}

QScrollBar::handle:vertical {{
    background-color: {Colors.BG_OVERLAY};
    border-radius: 4px;
    min-height: 24px;
}}

QScrollBar::handle:vertical:hover {{
    background-color: {Colors.BORDER_STRONG};
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
    background: none;
    border: none;
}}

QScrollBar:horizontal {{
    background-color: transparent;
    height: 10px;
    margin: 2px 4px 2px 4px;
    border: none;
}}

QScrollBar::handle:horizontal {{
    background-color: {Colors.BG_OVERLAY};
    border-radius: 4px;
    min-width: 24px;
}}

QScrollBar::handle:horizontal:hover {{
    background-color: {Colors.BORDER_STRONG};
}}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
    background: none;
    border: none;
}}

/* =========================================================== */
/*  Barra de progresso                                          */
/* =========================================================== */
QProgressBar {{
    background-color: {Colors.BG_ELEVATED};
    color: {Colors.TEXT_PRIMARY};
    border: 1px solid {Colors.BORDER};
    border-radius: {RADIUS["pill"]}px;
    text-align: center;
    min-height: 22px;
    font-weight: 600;
}}

QProgressBar::chunk {{
    background-color: {Colors.ACCENT};
    border-radius: {RADIUS["pill"]}px;
    margin: 1px;
}}

/* =========================================================== */
/*  Status bar                                                  */
/* =========================================================== */
QStatusBar {{
    background-color: {Colors.BG_BASE};
    color: {Colors.TEXT_SECONDARY};
    border-top: 1px solid {Colors.BORDER};
    padding: 4px;
    font-size: 12px;
}}

QStatusBar::item {{
    border: none;
}}

/* =========================================================== */
/*  Splitter                                                    */
/* =========================================================== */
QSplitter::handle {{
    background-color: {Colors.BORDER};
}}

QSplitter::handle:horizontal {{
    width: 1px;
}}

QSplitter::handle:vertical {{
    height: 1px;
}}

QSplitter::handle:hover {{
    background-color: {Colors.BORDER_STRONG};
}}

/* =========================================================== */
/*  Form layout rows (corrige cor de labels)                     */
/* =========================================================== */
QLabel {{
    background-color: transparent;
    color: {Colors.TEXT_PRIMARY};
}}

QLabel[hint="true"] {{
    color: {Colors.TEXT_SECONDARY};
}}

QLabel[heading="true"] {{
    font-size: 20px;
    font-weight: 700;
    color: {Colors.TEXT_PRIMARY};
    padding-bottom: 4px;
}}

/* =========================================================== */
/*  Dialog button box (footer "Close")                          */
/* =========================================================== */
QDialogButtonBox {{
    background-color: transparent;
}}

/* =========================================================== */
/*  Message boxes nativos                                       */
/* =========================================================== */
QMessageBox {{
    background-color: {Colors.BG_SURFACE};
}}

QMessageBox QLabel {{
    color: {Colors.TEXT_PRIMARY};
}}

QToolTip {{
    background-color: {Colors.BG_ELEVATED};
    color: {Colors.TEXT_PRIMARY};
    border: 1px solid {Colors.BORDER_STRONG};
    border-radius: {RADIUS["sm"]}px;
    padding: 4px 6px;
}}
"""


# --------------------------------------------------------------------------- #
# Helpers de aplicação                                                          #
# --------------------------------------------------------------------------- #


def apply_app_theme(app) -> None:
    """Aplica a paleta + QSS globalmente à QApplication.

    Idempotente — chamar mais de uma vez não acumula estado.
    """
    palette = _build_palette()
    app.setPalette(palette)
    app.setStyleSheet(STYLESHEET)
    # Fonte base consistente.
    font = QFont()
    if not font.exactMatch():
        # fallback silencioso — Qt já resolve a família default.
        pass
    font.setPointSize(10)
    app.setFont(font)
    logger.debug("Tema escuro aplicado.")


def _build_palette() -> QPalette:
    """Constrói um QPalette coerente com a paleta do QSS.

    O QSS vence para quase tudo, mas algumas primitivas (QMessageBox
    em algumas versões do Qt) ainda leem o QPalette — mantemos
    coerência para evitar surpresas.
    """
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, Colors.BG_BASE)
    palette.setColor(QPalette.ColorRole.WindowText, Colors.TEXT_PRIMARY)
    palette.setColor(QPalette.ColorRole.Base, Colors.BG_INPUT)
    palette.setColor(QPalette.ColorRole.AlternateBase, Colors.BG_SURFACE_ALT)
    palette.setColor(QPalette.ColorRole.Text, Colors.TEXT_PRIMARY)
    palette.setColor(QPalette.ColorRole.Button, Colors.BG_ELEVATED)
    palette.setColor(QPalette.ColorRole.ButtonText, Colors.TEXT_PRIMARY)
    palette.setColor(QPalette.ColorRole.Highlight, Colors.ACCENT_PRESSED)
    palette.setColor(QPalette.ColorRole.HighlightedText, Colors.TEXT_ON_ACCENT)
    palette.setColor(QPalette.ColorRole.PlaceholderText, Colors.TEXT_MUTED)
    palette.setColor(QPalette.ColorRole.ToolTipBase, Colors.BG_ELEVATED)
    palette.setColor(QPalette.ColorRole.ToolTipText, Colors.TEXT_PRIMARY)
    palette.setColor(QPalette.ColorRole.Link, Colors.ACCENT_HOVER)
    return palette


# --------------------------------------------------------------------------- #
# Helpers semânticos                                                            #
# --------------------------------------------------------------------------- #


def badge(text: str, palette: str = "accent") -> str:
    """Retorna um fragmento HTML para badge inline (não usado em QSS).

    Útil em cabeçalhos ou como rótulo decorativo.
    """
    palettes = {
        "accent": (Colors.ACCENT_SUBTLE, Colors.ACCENT_HOVER),
        "success": (Colors.SUCCESS_SUBTLE, Colors.SUCCESS),
        "warning": (Colors.WARNING_SUBTLE, Colors.WARNING),
        "danger": (Colors.DANGER_SUBTLE, Colors.DANGER),
    }
    bg, fg = palettes.get(palette, palettes["accent"])
    return (
        f"<span style='background-color: {bg}; color: {fg}; "
        f"padding: 2px 8px; border-radius: {RADIUS['pill']}px; "
        f"font-size: 11px; font-weight: 600;'>{text}</span>"
    )


__all__ = ["Colors", "Spacing", "RADIUS", "STYLESHEET", "apply_app_theme", "badge"]