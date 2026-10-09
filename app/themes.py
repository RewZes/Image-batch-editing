"""Themes for RenderBatch: a color set per theme turned into a Qt palette + stylesheet."""

from PySide6.QtGui import QColor, QPalette

THEMES = {
    "Midnight": dict(bg="#0e1016", panel="#141722", card="#1a1e2b", card2="#222838", border="#2a3144",
                     text="#e7eaf2", muted="#8b94a8", accent="#7c93ff", accent_text="#0b1020",
                     viewer="#090b10", danger="#ff6b7a", ok="#43d6a0"),
    "Graphite": dict(bg="#111111", panel="#171717", card="#1e1e1e", card2="#272727", border="#323232",
                     text="#ededed", muted="#9b9b9b", accent="#ffa24c", accent_text="#1c1207",
                     viewer="#0a0a0a", danger="#ff6b6b", ok="#57d18f"),
    "Nord": dict(bg="#242933", panel="#2b313c", card="#323846", card2="#3b4252", border="#434c5e",
                 text="#eceff4", muted="#a3adbf", accent="#88c0d0", accent_text="#1d2430",
                 viewer="#1f232b", danger="#bf616a", ok="#a3be8c"),
    "Emerald": dict(bg="#0c1210", panel="#111a17", card="#16211d", card2="#1d2b26", border="#26372f",
                    text="#e4f1ec", muted="#86a397", accent="#3ddc97", accent_text="#06140e",
                    viewer="#080d0b", danger="#ff7a7a", ok="#3ddc97"),
    "Rosewood": dict(bg="#140f12", panel="#1b1418", card="#23191f", card2="#2d2028", border="#3a2933",
                     text="#f3e8ee", muted="#a88f9c", accent="#f28bb5", accent_text="#1f0c15",
                     viewer="#0e0a0c", danger="#ff7a7a", ok="#6fd3a8"),
    "Studio Light": dict(bg="#eef0f4", panel="#f7f8fa", card="#ffffff", card2="#f1f3f7", border="#dde1e8",
                         text="#1b2030", muted="#6a7285", accent="#3b6cf0", accent_text="#ffffff",
                         viewer="#dfe2e8", danger="#d64550", ok="#1f9d6a"),
}
DEFAULT_THEME = "Midnight"


def palette(t):
    p = QPalette()
    c = lambda k: QColor(t[k])
    for group in (QPalette.Active, QPalette.Inactive):
        p.setColor(group, QPalette.Window, c("bg"))
        p.setColor(group, QPalette.WindowText, c("text"))
        p.setColor(group, QPalette.Base, c("card2"))
        p.setColor(group, QPalette.AlternateBase, c("card"))
        p.setColor(group, QPalette.Text, c("text"))
        p.setColor(group, QPalette.Button, c("card2"))
        p.setColor(group, QPalette.ButtonText, c("text"))
        p.setColor(group, QPalette.Highlight, c("accent"))
        p.setColor(group, QPalette.HighlightedText, c("accent_text"))
        p.setColor(group, QPalette.ToolTipBase, c("card2"))
        p.setColor(group, QPalette.ToolTipText, c("text"))
        p.setColor(group, QPalette.PlaceholderText, c("muted"))
        p.setColor(group, QPalette.Link, c("accent"))
        p.setColor(group, QPalette.Mid, c("border"))
    muted = c("muted")
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, muted)
    p.setColor(QPalette.Disabled, QPalette.Button, c("card"))
    p.setColor(QPalette.Disabled, QPalette.Base, c("card"))
    return p


def _alpha(hex_color, a):
    q = QColor(hex_color)
    return f"rgba({q.red()},{q.green()},{q.blue()},{a})"


def stylesheet(t, compact=True):
    hover = _alpha(t["accent"], 0.14)
    soft = _alpha(t["accent"], 0.22)
    z = dict(font="9pt", title="11.5pt", head="9.5pt", btn="4px 10px", prim="6px 12px", inp="3px 6px",
             seg="3px 9px", sl_h="18px", groove="4px", hdl="12px", hdl_m="-6px", hdl_b="2px", hdl_r="8px",
             menu="4px 14px", card_r="10px", chip="3px 5px", tip="5px 7px") if compact else \
        dict(font="10pt", title="13pt", head="10.5pt", btn="6px 12px", prim="9px 14px", inp="5px 8px",
             seg="5px 12px", sl_h="24px", groove="6px", hdl="14px", hdl_m="-8px", hdl_b="3px", hdl_r="10px",
             menu="6px 18px", card_r="12px", chip="4px 6px", tip="6px 8px")
    return f"""
* {{ font-family: "Segoe UI Variable Text", "Segoe UI", "Inter", "Noto Sans", sans-serif; font-size: {z['font']}; }}
QMainWindow, QDialog {{ background: {t['bg']}; }}
QWidget {{ color: {t['text']}; }}
QToolTip {{ background: {t['card2']}; color: {t['text']}; border: 1px solid {t['border']}; padding: {z['tip']}; border-radius: 6px; }}

#Header {{ background: {t['panel']}; border-bottom: 1px solid {t['border']}; }}
#AppTitle {{ font-size: {z['title']}; font-weight: 600; }}
#Subtle, QLabel[subtle="true"] {{ color: {t['muted']}; }}
#Badge {{ background: {soft}; color: {t['accent']}; border-radius: 10px; padding: 3px 10px; font-weight: 600; font-size: 9pt; }}

#Sidebar, #SidebarInner {{ background: {t['panel']}; }}
#Sidebar {{ border-right: 1px solid {t['border']}; }}
QScrollArea {{ border: none; background: transparent; }}

#Card {{ background: {t['card']}; border: 1px solid {t['border']}; border-radius: {z['card_r']}; }}
#CardHeader {{ background: transparent; border: none; text-align: left; font-weight: 600; font-size: {z['head']}; padding: 1px 0; }}
#CardHeader:hover {{ color: {t['accent']}; }}
#FieldLabel {{ color: {t['muted']}; }}

QPushButton {{ background: {t['card2']}; border: 1px solid {t['border']}; border-radius: 7px; padding: {z['btn']}; }}
QPushButton:hover {{ border-color: {t['accent']}; background: {hover}; }}
QPushButton:pressed {{ background: {soft}; }}
QPushButton:disabled {{ color: {t['muted']}; background: {t['card']}; border-color: {t['border']}; }}
QPushButton[primary="true"] {{ background: {t['accent']}; color: {t['accent_text']}; border: none; font-weight: 600; padding: {z['prim']}; }}
QPushButton[primary="true"]:hover {{ background: {t['accent']}; border: 2px solid {t['text']}; }}
QPushButton[primary="true"]:disabled {{ background: {t['card2']}; color: {t['muted']}; }}
QPushButton[chip="true"] {{ border-radius: 12px; padding: {z['chip']}; font-size: 8.5pt; }}
QPushButton[flat="true"] {{ background: transparent; border: none; padding: 4px 8px; }}
QPushButton[flat="true"]:hover {{ background: {hover}; }}
QPushButton[danger="true"]:hover {{ border-color: {t['danger']}; color: {t['danger']}; background: {_alpha(t['danger'], 0.12)}; }}

#Seg {{ background: {t['card2']}; border: 1px solid {t['border']}; border-radius: 9px; }}
#Seg QToolButton {{ background: transparent; border: none; border-radius: 7px; padding: {z['seg']}; color: {t['muted']}; }}
#Seg QToolButton:hover {{ color: {t['text']}; }}
#Seg QToolButton[tiny="true"] {{ padding: 5px 7px; min-width: 0px; }}
QPushButton[tiny="true"] {{ padding-left: 8px; padding-right: 8px; min-width: 0px; }}
#Seg QToolButton:checked {{ background: {t['accent']}; color: {t['accent_text']}; font-weight: 600; }}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background: {t['card2']}; border: 1px solid {t['border']}; border-radius: 6px; padding: {z['inp']};
    selection-background-color: {t['accent']}; selection-color: {t['accent_text']}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {t['accent']}; }}
QComboBox QAbstractItemView {{ background: {t['card2']}; border: 1px solid {t['border']}; selection-background-color: {soft}; selection-color: {t['text']}; outline: none; padding: 4px; }}
QDoubleSpinBox[compact="true"] {{ padding: 3px 4px; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
    color: {t['muted']}; background: transparent; border: 1px dashed {t['border']}; }}
QLabel:disabled, QCheckBox:disabled {{ color: {t['muted']}; }}
QSlider::handle:horizontal:disabled {{ background: {t['border']}; border-color: {t['border']}; }}
QSlider::sub-page:horizontal:disabled {{ background: {t['border']}; }}

QSlider {{ min-height: {z['sl_h']}; }}
QSlider::groove:horizontal {{ height: {z['groove']}; background: {t['border']}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {t['accent']}; border-radius: 3px; }}
QSlider::handle:horizontal {{ background: {t['text']}; border: {z['hdl_b']} solid {t['accent']}; width: {z['hdl']}; height: {z['hdl']}; margin: {z['hdl_m']} 0; border-radius: {z['hdl_r']}; }}
QSlider::handle:horizontal:hover {{ background: {t['accent']}; border: 3px solid {t['text']}; }}
QSlider::handle:horizontal:pressed {{ background: {t['accent']}; }}
#Card[dragging="true"] {{ border: 1px dashed {t['accent']}; }}
#LightPanel {{ background: {t['panel']}; border-left: 1px solid {t['border']}; }}
#PanelHead, #PanelFoot {{ background: {t['card']}; }}
#PanelHead {{ border-bottom: 1px solid {t['border']}; }}
#PanelFoot {{ border-top: 1px solid {t['border']}; }}
#GroupRow {{ background: {t['card2']}; border: 1px solid {t['border']}; border-radius: 8px; padding: 4px 8px; }}
#GroupRow[drop="true"] {{ border: 2px solid {t['accent']}; }}
#Card[drop="true"] {{ border: 2px dashed {t['accent']}; }}
#FilmBar {{ background: {t['panel']}; border-top: 1px solid {t['border']}; }}

QProgressBar {{ background: {t['card2']}; border: none; border-radius: 3px; height: 6px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {t['accent']}; border-radius: 3px; }}

QCheckBox {{ spacing: 8px; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {t['border']}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {t['muted']}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {t['border']}; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

#Toolbar {{ background: {t['bg']}; }}
#Filmstrip {{ background: {t['panel']}; border: none; border-top: 1px solid {t['border']}; padding: 6px; }}
#Filmstrip::item {{ border-radius: 8px; padding: 4px; color: {t['muted']}; }}
#Filmstrip::item:selected {{ background: {soft}; color: {t['text']}; }}
#Filmstrip::item:hover {{ background: {hover}; }}

#Activity {{ background: {t['panel']}; border-top: 1px solid {t['border']}; }}
#BatchCard {{ background: {t['card']}; border-top: 1px solid {t['border']}; }}
#Banner {{ background: {soft}; border-radius: 8px; }}
QPlainTextEdit {{ background: {t['card']}; border: none; border-top: 1px solid {t['border']}; font-family: "Cascadia Mono", "Consolas", monospace; font-size: 9pt; }}

QMenu {{ background: {t['card2']}; border: 1px solid {t['border']}; border-radius: 8px; padding: 6px; }}
QMenu::item {{ padding: {z['menu']}; border-radius: 5px; }}
QMenu::item:selected {{ background: {soft}; }}
QSplitter::handle {{ background: {t['border']}; }}
#ThemeCard {{ border-radius: 10px; padding: 10px; text-align: left; }}
#CardHeader[off="true"] {{ color: {t['muted']}; }}
#Card[error="true"] {{ border: 1px solid {t['danger']}; }}
#ErrorLabel {{ color: {t['danger']}; background: {_alpha(t['danger'], 0.12)}; border-radius: 6px; padding: 6px 8px; }}
QLineEdit[error="true"], QComboBox[error="true"], QSpinBox[error="true"], QDoubleSpinBox[error="true"] {{
    border: 2px solid {t['danger']}; background: {_alpha(t['danger'], 0.10)}; }}
"""
