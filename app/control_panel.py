#!/usr/bin/env python3

import sys
import os
import json
import subprocess
import re
from pathlib import Path
from typing import Optional, Dict, List, Set, Tuple

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QPushButton, QLabel, QComboBox, QSlider, QFrame, QFileDialog,
    QGraphicsDropShadowEffect, QScrollArea, QSizePolicy,
    QSystemTrayIcon, QMenu, QButtonGroup, QTabWidget, QCheckBox,
)
from PySide6.QtCore import (
    Qt, QTimer, Signal, Property, QPropertyAnimation, QEasingCurve, QRectF,
)
from PySide6.QtGui import (
    QColor, QPalette, QIcon, QPixmap, QPainter, QAction, QActionGroup,
    QImage, QFont, QTransform,
)
from PySide6.QtSvg import QSvgRenderer

# ── Paths ────────────────────────────────────────────────────────────────
CMD_PIPE             = "/tmp/blucast/cmd.pipe"
PREVIEW_FILE         = "/tmp/blucast/preview.jpg"
CAMERA_DISABLED_FLAG = "/tmp/blucast/camera_disabled"
CONFIG_DIR           = Path("/root/.config/blucast")
CONFIG_FILE          = CONFIG_DIR / "settings.json"
LOGO_PATH            = "/app/assets/logo.svg"
VCAM_DEVICE          = "/dev/video10"

AUTOSTART_DIR  = Path("/root/.config/autostart")
AUTOSTART_FILE = AUTOSTART_DIR / "blucast.desktop"
HOST_RUN_SCRIPT = os.environ.get("HOST_RUN_SCRIPT", "")

# ── Effect mapping ───────────────────────────────────────────────────────
EFFECT_MAP = {
    "blur":    6,
    "replace": 5,
    "remove":  3,
    "none":    4,
}

DEFAULT_FORMATS = {
    "640x480":   [15, 24, 30, 60],
    "1280x720":  [15, 24, 30, 60],
    "1920x1080": [15, 24, 30, 60],
}

STANDARD_RESOLUTIONS = [
    (320, 240), (640, 480), (800, 600), (960, 540), (1024, 576),
    (1280, 720), (1600, 900), (1920, 1080), (2560, 1440), (3840, 2160),
]

# ── Stylesheet ───────────────────────────────────────────────────────────
STYLESHEET = """
QMainWindow { background-color: #0a0f0a; }
QWidget { color: #e2e8f0; font-family: 'Ubuntu', 'Inter', sans-serif; font-size: 13px; }
QScrollArea { border: none; background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }
QLabel { color: #94a3b8; border: 0; }
QComboBox {
    background: #1a1f1a; border: 1px solid #2d3d2d; border-radius: 10px;
    padding: 12px 16px; font-size: 14px; min-height: 22px; color: #e2e8f0;
}
QComboBox:hover { border-color: #3b82f6; background: #1f2a1f; }
QComboBox::drop-down { border: none; width: 40px; }
QComboBox QAbstractItemView {
    background: #1a1f1a; border: 1px solid #2d3d2d; border-radius: 8px;
    selection-background-color: #3b82f6; padding: 4px; outline: none;
}
QComboBox QAbstractItemView::item { padding: 8px 12px; border-radius: 6px; min-height: 24px; }
QPushButton {
    background: #1a1f1a; border: 1px solid #2d3d2d; border-radius: 10px;
    padding: 12px 20px; font-size: 14px; font-weight: 500; color: #94a3b8;
}
QPushButton:hover { background: #1f2a1f; border-color: #3d4d3d; }
QPushButton:pressed { background: #2d3d2d; }
QSlider::groove:horizontal { background: #2d3d2d; height: 8px; border-radius: 4px; }
QSlider::handle:horizontal {
    background: #3b82f6; width: 20px; height: 20px; margin: -6px 0;
    border-radius: 10px; border: 3px solid #0a0f0a;
}
QSlider::sub-page:horizontal {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #3b82f6, stop:1 #60a5fa);
    border-radius: 4px;
}
QSlider::groove:horizontal:disabled { background: #1a1f1a; }
QSlider::handle:horizontal:disabled { background: #3f4a44; border-color: #0a0f0a; }
QSlider::sub-page:horizontal:disabled { background: #2d3a35; }
QTabWidget::pane {
    border: 1px solid #1f2a1f; border-radius: 16px; background: #111611; top: -1px;
}
QTabBar::tab {
    background: #1a1f1a; color: #94a3b8; border: 1px solid #2d3d2d;
    border-bottom: none; padding: 10px 18px; margin-right: 4px;
    border-top-left-radius: 10px; border-top-right-radius: 10px;
    font-size: 13px; font-weight: 500;
}
QTabBar::tab:selected { background: #111611; color: #fff; border-color: #3b82f6; }
QTabBar::tab:hover:!selected { background: #1f2a1f; color: #e2e8f0; }
"""

SECTION_LABEL_STYLE = "font-size: 12px; font-weight: 600; color: #64748b; background: transparent; letter-spacing: 0.5px;"

VCAM_BUTTON_ON_STYLE = """
    QPushButton {
        background: #3b82f6; border: none; color: white;
        border-radius: 10px; padding: 12px 16px; font-weight: 600; font-size: 13px;
    }
    QPushButton:hover { background: #2563eb; }
"""

PAN_BUTTON_STYLE = """
    QPushButton {
        background: #1a1f1a; border: 1px solid #2d3d2d; border-radius: 6px;
        font-size: 12px; color: #94a3b8; padding: 0px;
    }
    QPushButton:hover { background: #1f2a1f; border-color: #3b82f6; color: #3b82f6; }
    QPushButton:disabled { background: #14170f; border-color: #1f261f; color: #3f4a44; }
"""

PAN_STEP = 0.1
AUTOREFRAME_SMOOTH_MIN = 0.5   # fastest reaction
AUTOREFRAME_SMOOTH_MAX = 0.95  # smoothest / slowest reaction

# ── UI strings (English / Spanish) ──────────────────────────────────────
# Changing language takes effect on next launch, not live: strings are read
# once at construction time, which avoids having to retranslate every
# widget (including ones mixing static text with dynamic values, like
# "50%") in place every time the user picks a different language.
STRINGS = {
    "tab_camera":    {"en": "Camera",  "es": "Cámara"},
    "tab_effects":   {"en": "Effects", "es": "Efectos"},
    "tab_framing":   {"en": "Framing", "es": "Encuadre"},
    "tab_general":   {"en": "General", "es": "General"},

    "tray_stop_cam":  {"en": "Stop Virtual Camera",  "es": "Detener Cámara Virtual"},
    "tray_start_cam": {"en": "Start Virtual Camera", "es": "Iniciar Cámara Virtual"},
    "tray_effect":    {"en": "Effect",       "es": "Efecto"},
    "tray_startup":   {"en": "Start at Login", "es": "Iniciar con la Sesión"},
    "tray_show":      {"en": "Show Window",  "es": "Mostrar Ventana"},
    "tray_quit":      {"en": "Quit",         "es": "Salir"},

    "effect_blur":    {"en": "Blur",    "es": "Difuminar"},
    "effect_replace": {"en": "Replace", "es": "Reemplazar"},
    "effect_remove":  {"en": "Remove",  "es": "Quitar"},
    "effect_none":    {"en": "None",    "es": "Ninguno"},

    "vcam_title":      {"en": "Virtual Camera",  "es": "Cámara Virtual"},
    "camera_preview":  {"en": "Camera preview", "es": "Vista previa de cámara"},
    "camera_off":      {"en": "Camera Off",     "es": "Cámara Apagada"},

    "section_background":   {"en": "BACKGROUND",   "es": "FONDO"},
    "section_lighting":     {"en": "LIGHTING",      "es": "ILUMINACIÓN"},
    "section_manual":       {"en": "MANUAL",        "es": "MANUAL"},
    "section_auto_reframe": {"en": "AUTO REFRAME",  "es": "AUTO ENCUADRE"},

    "blur_strength":     {"en": "Blur Strength",      "es": "Intensidad de Difuminado"},
    "background_image":  {"en": "Background Image",   "es": "Imagen de Fondo"},
    "no_image_selected": {"en": "No image selected",  "es": "Sin imagen seleccionada"},
    "browse":            {"en": "Browse",              "es": "Examinar"},
    "virtual_light":      {"en": "Virtual Light", "es": "Luz Virtual"},
    "virtual_light_tip": {
        "en": "Brightens a soft area around your face, like an extra light\n"
              "aimed at you. Approximates NVIDIA Broadcast's Virtual Key\n"
              "Light using face detection instead of AI relighting.",
        "es": "Aclara suavemente el área alrededor de tu rostro, como si\n"
              "hubiese una luz extra apuntándote. Aproxima la \"Virtual Key\n"
              "Light\" de NVIDIA Broadcast usando detección facial en vez\n"
              "de reiluminación por IA.",
    },
    "intensity": {"en": "Intensity", "es": "Intensidad"},

    "zoom": {"en": "Zoom", "es": "Zoom"},
    "pan":  {"en": "Pan",  "es": "Paneo"},
    "zoom_slider_tip": {
        "en": "Digital zoom on the camera itself, applied before any background\n"
              "effect — not a crop of the final composited image.",
        "es": "Zoom digital sobre la propia cámara, aplicado antes de cualquier\n"
              "efecto de fondo — no es un recorte de la imagen final compuesta.",
    },
    "autoreframe_enable": {"en": "Enable", "es": "Activar"},
    "autoreframe_enable_tip": {
        "en": "AI face tracking: automatically zooms and pans to keep your\n"
              "face centered as you move. Overrides manual Zoom/Pan while on.",
        "es": "Seguimiento facial por IA: hace zoom y paneo automáticamente\n"
              "para mantener tu rostro centrado al moverte. Anula el Zoom/Paneo\n"
              "manual mientras está activo.",
    },
    "detector_model": {"en": "Detector Model", "es": "Modelo Detector"},
    "detector_model_tip": {
        "en": "DNN SSD: more accurate across head angle and occlusion (glasses,\n"
              "headphones), slightly heavier on CPU.\n"
              "Haar Cascade: lighter, but misses non-frontal poses more often.",
        "es": "DNN SSD: más preciso ante ángulos de cabeza y oclusiones (lentes,\n"
              "audífonos), un poco más pesado para la CPU.\n"
              "Haar Cascade: más liviano, pero falla más seguido en poses no frontales.",
    },
    "model_dnn":  {"en": "DNN SSD",       "es": "DNN SSD"},
    "model_haar": {"en": "Haar Cascade",  "es": "Cascada Haar"},
    "autoreframe_zoom": {"en": "Auto Reframe Zoom", "es": "Zoom de Auto Encuadre"},
    "autoreframe_zoom_tip": {
        "en": "How much Auto Reframe zooms in to keep you centered. Higher\n"
              "values give it more room to pan, so tracking is more noticeable.",
        "es": "Cuánto acerca el Auto Encuadre para mantenerte centrado. Valores\n"
              "más altos le dan más margen para panear, así el seguimiento se\n"
              "nota más.",
    },
    "tracking_speed": {"en": "Tracking Speed", "es": "Velocidad de Seguimiento"},
    "tracking_speed_tip": {
        "en": "How quickly Auto Reframe reacts to your movement. Higher is\n"
              "more responsive but can feel less stable; lower is smoother\n"
              "but slower to catch up.",
        "es": "Qué tan rápido reacciona el Auto Encuadre a tu movimiento. Más\n"
              "alto es más responsivo pero puede sentirse menos estable; más\n"
              "bajo es más suave pero más lento en alcanzarte.",
    },

    "input_device": {"en": "Input Device", "es": "Dispositivo de Entrada"},
    "resolution":   {"en": "Resolution",   "es": "Resolución"},
    "frame_rate":   {"en": "Frame Rate",   "es": "Cuadros por Segundo"},
    "mirror_preview": {"en": "Mirror Preview", "es": "Espejar Vista Previa"},
    "mirror_preview_tip": {
        "en": "Flips only your local preview (like a mirror). The video sent\n"
              "to calls/apps is not flipped, matching how Zoom/Teams/Broadcast\n"
              "handle this — flipping the transmitted feed would show any text\n"
              "or logos behind you backwards to other participants.",
        "es": "Solo espeja tu vista previa local (como un espejo). El video\n"
              "enviado a llamadas/apps no se espeja, igual que Zoom/Teams/\n"
              "Broadcast — espejar la señal transmitida mostraría cualquier\n"
              "texto o logo detrás tuyo al revés para los demás participantes.",
    },

    "about_text": {
        "en": "Real-time AI-powered video effects using NVIDIA Maxine VideoFX SDK.\n"
              "Basically NVIDIA Broadcast, but for Linux.",
        "es": "Efectos de video con IA en tiempo real usando NVIDIA Maxine VideoFX SDK.\n"
              "Básicamente NVIDIA Broadcast, pero para Linux.",
    },
    "startup_title": {"en": "Start at Login", "es": "Iniciar con la Sesión"},
    "startup_sub": {
        "en": "Launch BluCast automatically when you log in",
        "es": "Inicia BluCast automáticamente al iniciar sesión",
    },
    "startup_unavailable": {
        "en": "Unavailable (needs a newer run.sh)",
        "es": "No disponible (requiere un run.sh más reciente)",
    },
    "language": {"en": "Language", "es": "Idioma"},
    "language_note": {
        "en": "Restart BluCast for the language change to take effect.",
        "es": "Reinicia BluCast para que el cambio de idioma tenga efecto.",
    },

    "reset_defaults": {"en": "Reset to Defaults", "es": "Restablecer Valores"},
    "quit":           {"en": "Quit", "es": "Salir"},

    "unknown_camera": {"en": "Unknown Camera", "es": "Cámara Desconocida"},
    "default_camera": {"en": "Default Camera", "es": "Cámara Predeterminada"},
}


# ═════════════════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════════════════

def send_command(cmd: str) -> bool:
    """Send a command to the server via named pipe."""
    try:
        with open(CMD_PIPE, 'w') as f:
            f.write(cmd + '\n')
        return True
    except OSError:
        return False


def get_video_devices(lang: str = "en") -> List[Tuple[str, str]]:
    """Return list of (path, name) for real camera devices (excluding our vcam)."""
    devices = []
    try:
        for entry in sorted(Path("/dev").iterdir()):
            if not entry.name.startswith("video"):
                continue
            path = str(entry)
            if path == VCAM_DEVICE:
                continue
            try:
                res = subprocess.run(
                    ["v4l2-ctl", "-d", path, "--info"],
                    capture_output=True, text=True, timeout=1,
                )
                name = STRINGS["unknown_camera"].get(lang, STRINGS["unknown_camera"]["en"])
                for line in res.stdout.splitlines():
                    if "Card type" in line:
                        name = line.split(":", 1)[1].strip()
                        break
                devices.append((path, name))
            except Exception:
                devices.append((path, f"Camera ({entry.name})"))
    except Exception:
        pass
    default_name = STRINGS["default_camera"].get(lang, STRINGS["default_camera"]["en"])
    return devices or [("/dev/video0", default_name)]


def get_supported_formats(device: str) -> Dict[str, List[int]]:
    """Query device for supported resolutions and frame rates."""
    try:
        res = subprocess.run(
            ["v4l2-ctl", "-d", device, "--list-formats-ext"],
            capture_output=True, text=True, timeout=2,
        )
    except Exception:
        return {}

    output = res.stdout or ""
    if not output:
        return {}

    size_re = re.compile(r"Size:\s+Discrete\s+(\d+)x(\d+)")
    step_re = re.compile(r"Size:\s+Stepwise\s+(\d+)x(\d+)\s*-\s*(\d+)x(\d+)")
    fps_re  = re.compile(r"\(([\d.]+)\s*fps\)")
    frac_re = re.compile(r"Interval:\s+Discrete\s+(\d+)\s*/\s*(\d+)")
    step_fps_re = re.compile(r"Interval:\s+Stepwise\s+([\d.]+)s\s*-\s*([\d.]+)s")

    formats: Dict[str, Set[int]] = {}
    current_res = None
    stepwise_range = None
    stepwise_fps: Set[int] = set()
    stepwise_fps_range = None

    for line in output.splitlines():
        m = size_re.search(line)
        if m:
            current_res = f"{m.group(1)}x{m.group(2)}"
            formats.setdefault(current_res, set())
            continue

        m = step_re.search(line)
        if m:
            stepwise_range = tuple(map(int, m.groups()))
            current_res = None
            continue

        m = fps_re.search(line)
        if m:
            fps = int(round(float(m.group(1))))
            if 0 < fps <= 240:
                if current_res:
                    formats.setdefault(current_res, set()).add(fps)
                else:
                    stepwise_fps.add(fps)
            continue

        m = frac_re.search(line)
        if m:
            n, d = float(m.group(1)), float(m.group(2))
            if n > 0:
                fps = int(round(d / n))
                if 0 < fps <= 240:
                    if current_res:
                        formats.setdefault(current_res, set()).add(fps)
                    else:
                        stepwise_fps.add(fps)
            continue

        m = step_fps_re.search(line)
        if m:
            min_s, max_s = float(m.group(1)), float(m.group(2))
            if min_s > 0 and max_s > 0:
                stepwise_fps_range = (int(round(1 / max_s)), int(round(1 / min_s)))

    if formats:
        return {r: sorted(f) for r, f in formats.items() if f}

    if stepwise_range:
        min_w, min_h, max_w, max_h = stepwise_range
        resolutions = [f"{w}x{h}" for w, h in STANDARD_RESOLUTIONS
                       if min_w <= w <= max_w and min_h <= h <= max_h]
        if stepwise_fps_range:
            lo, hi = stepwise_fps_range
            fps_list = [f for f in [15, 24, 30, 60, 120] if lo <= f <= hi]
        else:
            fps_list = sorted(stepwise_fps) or [30]
        return {r: fps_list for r in resolutions} if resolutions else {}

    return {}


def autostart_enabled() -> bool:
    return AUTOSTART_FILE.exists()


def set_autostart(enabled: bool) -> bool:
    """Write/remove the host autostart entry. Requires HOST_RUN_SCRIPT (set by run.sh)
    and ~/.config/autostart mounted read-write into the container."""
    if not HOST_RUN_SCRIPT:
        return False
    try:
        if enabled:
            AUTOSTART_DIR.mkdir(parents=True, exist_ok=True)
            AUTOSTART_FILE.write_text(
                "[Desktop Entry]\n"
                "Type=Application\n"
                "Name=BluCast\n"
                "Comment=AI-Powered Virtual Camera\n"
                f"Exec={HOST_RUN_SCRIPT} --autostart\n"
                "Icon=blucast\n"
                "Terminal=false\n"
                "X-GNOME-Autostart-enabled=true\n"
            )
        else:
            AUTOSTART_FILE.unlink(missing_ok=True)
        return True
    except OSError:
        return False


# ═════════════════════════════════════════════════════════════════════════
# Settings
# ═════════════════════════════════════════════════════════════════════════

class Settings:
    DEFAULTS = {
        "effect_mode": "blur",
        "background_image": "",
        "blur_strength": 50,
        "resolution": "1280x720",
        "fps": 30,
        "input_device": "",
        "mirror_preview": False,
        "zoom_factor": 100,
        "pan_x": 0.0,
        "pan_y": 0.0,
        "auto_reframe": False,
        "autoreframe_model": "dnn",
        "autoreframe_zoom": 115,
        "autoreframe_speed": 35,
        "virtual_light": False,
        "virtual_light_intensity": 50,
        "language": "en",
    }

    def __init__(self):
        self._data = self.DEFAULTS.copy()
        try:
            if CONFIG_FILE.exists():
                self._data.update(json.loads(CONFIG_FILE.read_text()))
        except Exception:
            pass

    def get(self, key):
        return self._data.get(key, self.DEFAULTS.get(key))

    def set(self, key, value):
        self._data[key] = value
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            CONFIG_FILE.write_text(json.dumps(self._data, indent=2))
        except Exception:
            pass


# ═════════════════════════════════════════════════════════════════════════
# Reusable widgets
# ═════════════════════════════════════════════════════════════════════════

class Card(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setStyleSheet("""
            #card {
                background: #111611;
                border: 1px solid #1f2a1f;
                border-radius: 16px;
            }
        """)


class NoWheelComboBox(QComboBox):
    """A QComboBox that never changes value from mouse-wheel scrolling —
    only an explicit click should change a setting like input device,
    resolution, or frame rate."""
    def wheelEvent(self, event):
        event.ignore()


class NoWheelSlider(QSlider):
    """Same rationale as NoWheelComboBox, for sliders."""
    def wheelEvent(self, event):
        event.ignore()


class ToggleSwitch(QCheckBox):
    """iOS/Android-style sliding toggle: blue pill with the knob on the
    right and 'ON' text when checked, gray pill with the knob on the left
    and 'OFF' text when unchecked. Drop-in replacement for a checkable
    QPushButton — same .toggled/.setChecked/.isChecked API."""

    _ON_COLOR = QColor("#3b82f6")
    _OFF_COLOR = QColor("#2d3d2d")
    _OFF_COLOR_DISABLED = QColor("#1a1f1a")
    _KNOB_COLOR = QColor("#ffffff")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(64, 32)
        self._knob_pos = 0.0  # 0.0 = off (left), 1.0 = on (right)
        self._anim = QPropertyAnimation(self, b"knobPos", self)
        self._anim.setDuration(150)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.toggled.connect(self._animate_to)

    def _animate_to(self, checked: bool):
        self._anim.stop()
        self._anim.setStartValue(self._knob_pos)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def _get_knob_pos(self) -> float:
        return self._knob_pos

    def _set_knob_pos(self, value: float):
        self._knob_pos = value
        self.update()

    knobPos = Property(float, _get_knob_pos, _set_knob_pos)

    def setCheckedSilently(self, checked: bool):
        """Set state without emitting toggled or animating — for syncing the
        switch to an external source of truth (e.g. after a failed action)."""
        self.blockSignals(True)
        self.setChecked(checked)
        self.blockSignals(False)
        self._knob_pos = 1.0 if checked else 0.0
        self.update()

    def hitButton(self, pos):
        return self.contentsRect().contains(pos)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        rect = self.rect().adjusted(1, 1, -1, -1)
        radius = rect.height() / 2.0

        off_color = self._OFF_COLOR if self.isEnabled() else self._OFF_COLOR_DISABLED
        t = self._knob_pos
        bg_color = QColor(
            int(off_color.red()   + (self._ON_COLOR.red()   - off_color.red())   * t),
            int(off_color.green() + (self._ON_COLOR.green() - off_color.green()) * t),
            int(off_color.blue()  + (self._ON_COLOR.blue()  - off_color.blue())  * t),
        )
        if not self.isEnabled():
            bg_color = bg_color.darker(115)

        painter.setPen(Qt.NoPen)
        painter.setBrush(bg_color)
        painter.drawRoundedRect(rect, radius, radius)

        knob_d = rect.height() - 6
        margin = 8
        text_color = QColor("#ffffff") if t > 0.5 else QColor("#94a3b8")
        if not self.isEnabled():
            text_color = text_color.darker(140)
        painter.setPen(text_color)
        font = painter.font()
        font.setPointSize(9)
        font.setBold(True)
        painter.setFont(font)
        if t > 0.5:
            text_rect = QRectF(rect.left() + margin, rect.top(),
                                rect.width() - knob_d - margin - 4, rect.height())
            painter.drawText(text_rect, int(Qt.AlignVCenter | Qt.AlignLeft), "ON")
        else:
            text_rect = QRectF(rect.left() + knob_d + 4, rect.top(),
                                rect.width() - knob_d - margin - 4, rect.height())
            painter.drawText(text_rect, int(Qt.AlignVCenter | Qt.AlignRight), "OFF")

        knob_x = rect.left() + 3 + (rect.width() - knob_d - 6) * t
        painter.setBrush(self._KNOB_COLOR if self.isEnabled() else self._KNOB_COLOR.darker(120))
        painter.drawEllipse(QRectF(knob_x, rect.top() + 3, knob_d, knob_d))


class EffectButton(QPushButton):
    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setCheckable(True)
        self.setMinimumHeight(70)
        self.setMinimumWidth(75)
        self._apply(False)
        self.toggled.connect(self._apply)

    def _apply(self, checked):
        if checked:
            self.setStyleSheet("""
                QPushButton {
                    background: #3b82f6; border: 2px solid #3b82f6; color: white;
                    border-radius: 12px; padding: 8px; font-weight: 600; font-size: 11px;
                }
                QPushButton:hover { background: #2563eb; }
            """)
        else:
            self.setStyleSheet("""
                QPushButton {
                    background: #1a1f1a; border: 1px solid #2d3d2d; color: #64748b;
                    border-radius: 12px; padding: 8px; font-weight: 500; font-size: 11px;
                }
                QPushButton:hover { background: #1f2a1f; border-color: #3d4d3d; }
            """)


# ═════════════════════════════════════════════════════════════════════════
# Main Window
# ═════════════════════════════════════════════════════════════════════════

class ControlPanel(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = Settings()
        self.lang = self.settings.get("language")
        self.setWindowTitle("BluCast")
        self.setMinimumSize(540, 860)
        self.resize(540, 1040)
        self.supported_formats: Dict[str, List[int]] = {}

        # Camera on/off is transient (always starts enabled), never persisted.
        self.camera_enabled = True
        try:
            Path(CAMERA_DISABLED_FLAG).unlink(missing_ok=True)
        except OSError:
            pass
        self.mirror_preview = False

        self._build_ui()
        self._setup_tray()
        self._apply_saved_settings()
        self._start_preview_timer()

    # ── Preview timer ────────────────────────────────────────────────────
    def _start_preview_timer(self):
        self.preview_timer = QTimer(self)
        self.preview_timer.timeout.connect(self._update_preview)
        self.preview_timer.start(33)  # ~30 fps

    def _update_preview(self):
        """Read JPEG preview written by the server."""
        if not self.camera_enabled:
            return

        path = Path(PREVIEW_FILE)
        if not path.exists():
            if self.preview_label.pixmap() and not self.preview_label.pixmap().isNull():
                pass  # Keep last good frame
            else:
                self.preview_placeholder.show()
                self.preview_label.hide()
            return

        try:
            pixmap = QPixmap(str(path))
            if pixmap.isNull():
                return
            scaled = pixmap.scaled(
                self.preview_label.width() - 4,
                self.preview_label.height() - 4,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
            if self.mirror_preview:
                scaled = scaled.transformed(QTransform().scale(-1, 1))
            self.preview_label.setPixmap(scaled)
            self.preview_placeholder.hide()
            self.preview_label.show()
        except Exception:
            pass

    # ── Virtual camera on/off ────────────────────────────────────────────
    def _on_toggle_camera(self):
        self.camera_enabled = not self.camera_enabled
        self._apply_camera_enabled_state()

    def _on_vcam_toggle(self, checked: bool):
        self.camera_enabled = checked
        self._apply_camera_enabled_state()

    def _apply_camera_enabled_state(self):
        flag = Path(CAMERA_DISABLED_FLAG)
        self.vcam_button.setCheckedSilently(self.camera_enabled)
        if self.camera_enabled:
            try:
                flag.unlink(missing_ok=True)
            except OSError:
                pass
            self.preview_placeholder_label.setText(self._t("camera_preview"))
        else:
            try:
                flag.touch()
            except OSError:
                pass
            self.preview_label.hide()
            self.preview_placeholder_label.setText(self._t("camera_off"))
            self.preview_placeholder.show()

        if hasattr(self, "tray_toggle_cam_act"):
            self.tray_toggle_cam_act.setText(
                self._t("tray_stop_cam") if self.camera_enabled else self._t("tray_start_cam")
            )
        self._sync_window_state()

    # ── System tray ──────────────────────────────────────────────────────
    def _make_tray_icon(self) -> QIcon:
        px = QPixmap(64, 64)
        px.fill(Qt.transparent)
        if Path(LOGO_PATH).exists():
            renderer = QSvgRenderer(LOGO_PATH)
            painter = QPainter(px)
            renderer.render(painter)
            painter.end()
        else:
            painter = QPainter(px)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setBrush(QColor(59, 130, 246))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(4, 4, 56, 56)
            painter.setBrush(QColor(255, 255, 255))
            painter.drawEllipse(20, 20, 24, 24)
            painter.end()
        return QIcon(px)

    def _setup_tray(self):
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_available = QSystemTrayIcon.isSystemTrayAvailable()
        if not self.tray_available:
            return

        self.tray_icon.setIcon(self._make_tray_icon())
        self.tray_icon.setToolTip("BluCast")

        menu = QMenu()

        # Quick: virtual camera on/off
        self.tray_toggle_cam_act = QAction(
            self._t("tray_stop_cam") if self.camera_enabled else self._t("tray_start_cam"), self
        )
        self.tray_toggle_cam_act.triggered.connect(self._on_toggle_camera)
        menu.addAction(self.tray_toggle_cam_act)

        # Quick: effect switch
        effect_menu = QMenu(self._t("tray_effect"), menu)
        self.tray_effect_actions: Dict[str, QAction] = {}
        effect_group = QActionGroup(self)
        effect_group.setExclusive(True)
        for key in ("blur", "replace", "remove", "none"):
            act = QAction(self._t(f"effect_{key}"), self, checkable=True)
            act.triggered.connect(lambda checked, k=key: self.effect_buttons[k].setChecked(True))
            effect_group.addAction(act)
            effect_menu.addAction(act)
            self.tray_effect_actions[key] = act
        menu.addMenu(effect_menu)

        menu.addSeparator()

        # Start at login
        self.tray_autostart_act = QAction(self._t("tray_startup"), self, checkable=True)
        self.tray_autostart_act.setChecked(autostart_enabled())
        self.tray_autostart_act.setEnabled(bool(HOST_RUN_SCRIPT))
        if not HOST_RUN_SCRIPT:
            self.tray_autostart_act.setToolTip("Unavailable: HOST_RUN_SCRIPT not set by run.sh")
        self.tray_autostart_act.toggled.connect(self._on_toggle_autostart)
        menu.addAction(self.tray_autostart_act)

        menu.addSeparator()

        show_act = QAction(self._t("tray_show"), self)
        show_act.triggered.connect(self._show_window)
        menu.addAction(show_act)
        menu.addSeparator()
        quit_act = QAction(self._t("tray_quit"), self)
        quit_act.triggered.connect(self._quit)
        menu.addAction(quit_act)

        self.tray_icon.setContextMenu(menu)
        self.tray_icon.activated.connect(self._on_tray_click)
        self.tray_icon.show()

    def _on_toggle_autostart(self, checked: bool):
        if not set_autostart(checked):
            self.tray_autostart_act.blockSignals(True)
            self.tray_autostart_act.setChecked(not checked)
            self.tray_autostart_act.blockSignals(False)

    def _sync_window_state(self):
        """Tell the server the effective visibility: real window visibility
        AND whether the user hasn't explicitly turned the camera off."""
        effective_visible = self.isVisible() and self.camera_enabled
        send_command(f"WINDOW:{'visible' if effective_visible else 'hidden'}")

    def _show_window(self):
        self.show()
        self.raise_()
        self.activateWindow()
        self._sync_window_state()

    def _on_tray_click(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            if self.isVisible():
                self.hide()
                self._sync_window_state()
            else:
                self._show_window()

    def closeEvent(self, event):
        if self.tray_available and self.tray_icon.isVisible():
            self.hide()
            self._sync_window_state()
            event.ignore()
        else:
            self._quit()
            event.accept()

    # ── UI construction ──────────────────────────────────────────────────
    def _build_ui(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setCentralWidget(scroll)

        container = QWidget()
        scroll.setWidget(container)
        layout = QVBoxLayout(container)
        layout.setSpacing(16)
        layout.setContentsMargins(20, 20, 20, 20)

        # ── Preview ──
        preview_card = Card()
        pv_layout = QVBoxLayout(preview_card)
        pv_layout.setContentsMargins(4, 4, 4, 4)

        self.preview_container = QWidget()
        self.preview_container.setMinimumHeight(200)
        self.preview_container.setStyleSheet("background: #0d120d; border-radius: 12px;")
        inner = QVBoxLayout(self.preview_container)
        inner.setContentsMargins(0, 0, 0, 0)

        self.preview_placeholder = QWidget()
        ph_layout = QVBoxLayout(self.preview_placeholder)
        ph_layout.setAlignment(Qt.AlignCenter)
        self.preview_placeholder_label = QLabel(self._t("camera_preview"))
        self.preview_placeholder_label.setStyleSheet("color: #64748b; font-size: 13px; background: transparent;")
        self.preview_placeholder_label.setAlignment(Qt.AlignCenter)
        ph_layout.addWidget(self.preview_placeholder_label)
        inner.addWidget(self.preview_placeholder)

        self.preview_label = QLabel()
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumHeight(180)
        self.preview_label.hide()
        inner.addWidget(self.preview_label)

        pv_layout.addWidget(self.preview_container)

        self.preview_info = QLabel("1280x720 @ 30fps")
        self.preview_info.setStyleSheet("color: #64748b; font-size: 11px; background: transparent; padding: 4px;")
        self.preview_info.setAlignment(Qt.AlignRight)
        pv_layout.addWidget(self.preview_info)
        layout.addWidget(preview_card)

        # ── Status indicator / virtual camera on-off ──
        status_card = Card()
        status_layout = QHBoxLayout(status_card)
        status_layout.setContentsMargins(16, 12, 16, 12)

        status_info = QVBoxLayout()
        status_info.setSpacing(2)
        status_title = QLabel(self._t("vcam_title"))
        status_title.setStyleSheet("font-size: 14px; font-weight: 600; color: #fff; background: transparent;")
        status_info.addWidget(status_title)
        self.status_label = QLabel(VCAM_DEVICE)
        self.status_label.setStyleSheet("font-size: 12px; color: #64748b; background: transparent;")
        status_info.addWidget(self.status_label)
        status_layout.addLayout(status_info)
        status_layout.addStretch()

        self.vcam_button = ToggleSwitch()
        self.vcam_button.setChecked(True)
        self.vcam_button.toggled.connect(self._on_vcam_toggle)
        status_layout.addWidget(self.vcam_button)
        layout.addWidget(status_card)

        # ── Tabs: settings grouped by function so nothing requires scrolling
        # through unrelated sections to find what you need. ──
        self.tabs = QTabWidget()

        # ── Effects tab: Background and Lighting grouped together since
        # both are compositing adjustments applied to the same frame. ──
        effects_tab = QWidget()
        fx_layout = QVBoxLayout(effects_tab)
        fx_layout.setContentsMargins(16, 16, 16, 16)
        fx_layout.setSpacing(16)

        background_section_label = QLabel(self._t("section_background"))
        background_section_label.setStyleSheet(SECTION_LABEL_STYLE)
        fx_layout.addWidget(background_section_label)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.effect_buttons: Dict[str, EffectButton] = {}
        self.effect_group = QButtonGroup(self)
        self.effect_group.setExclusive(True)

        for key in ("blur", "replace", "remove", "none"):
            btn = EffectButton(self._t(f"effect_{key}").upper())
            self.effect_buttons[key] = btn
            self.effect_group.addButton(btn)
            btn_row.addWidget(btn)
            btn.toggled.connect(lambda checked, k=key: self._on_effect(k, checked))
        fx_layout.addLayout(btn_row)

        # Blur controls
        self.blur_controls = QWidget()
        bl_layout = QVBoxLayout(self.blur_controls)
        bl_layout.setContentsMargins(0, 8, 0, 0)
        bl_layout.setSpacing(8)

        bh = QHBoxLayout()
        bh.addWidget(self._styled_label(self._t("blur_strength")))
        self.blur_value_label = QLabel("50%")
        self.blur_value_label.setStyleSheet("color: #3b82f6; font-size: 13px; font-weight: 600; background: transparent;")
        bh.addWidget(self.blur_value_label)
        bl_layout.addLayout(bh)

        self.blur_slider = NoWheelSlider(Qt.Horizontal)
        self.blur_slider.setRange(0, 100)
        self.blur_slider.setValue(50)
        self.blur_slider.valueChanged.connect(self._on_blur)
        bl_layout.addWidget(self.blur_slider)
        fx_layout.addWidget(self.blur_controls)
        self.blur_controls.hide()

        # Background image controls
        self.bg_controls = QWidget()
        bg_layout = QVBoxLayout(self.bg_controls)
        bg_layout.setContentsMargins(0, 8, 0, 0)
        bg_layout.setSpacing(8)
        bg_layout.addWidget(self._styled_label(self._t("background_image")))

        bg_row = QHBoxLayout()
        self.bg_path_label = QLabel(self._t("no_image_selected"))
        self.bg_path_label.setStyleSheet("color: #64748b; font-size: 12px; background: transparent;")
        bg_row.addWidget(self.bg_path_label, 1)
        self.bg_button = QPushButton(self._t("browse"))
        self.bg_button.setMinimumWidth(90)  # "Examinar" (es) is longer than "Browse" (en)
        self.bg_button.setCursor(Qt.PointingHandCursor)
        self.bg_button.setStyleSheet(VCAM_BUTTON_ON_STYLE)
        self.bg_button.clicked.connect(self._on_browse_bg)
        bg_row.addWidget(self.bg_button)
        bg_layout.addLayout(bg_row)
        fx_layout.addWidget(self.bg_controls)
        self.bg_controls.hide()

        fx_layout.addWidget(self._separator())

        # ── Lighting: approximates NVIDIA Broadcast's "Virtual Key Light"
        # AI relighting (which needs a GPU/SDK combo this machine doesn't
        # have) with a simple face-anchored brightening — one toggle, one
        # intensity slider, nothing more to configure. ──
        lighting_section_label = QLabel(self._t("section_lighting"))
        lighting_section_label.setStyleSheet(SECTION_LABEL_STYLE)
        fx_layout.addWidget(lighting_section_label)

        virtuallight_row = QHBoxLayout()
        virtuallight_label = self._styled_label(self._t("virtual_light"))
        virtuallight_label.setToolTip(self._t("virtual_light_tip"))
        virtuallight_row.addWidget(virtuallight_label)
        virtuallight_row.addStretch()
        self.virtuallight_button = ToggleSwitch()
        self.virtuallight_button.toggled.connect(self._on_virtuallight_toggle)
        virtuallight_row.addWidget(self.virtuallight_button)
        fx_layout.addLayout(virtuallight_row)

        virtuallight_intensity_header = QHBoxLayout()
        virtuallight_intensity_header.addWidget(self._styled_label(self._t("intensity")))
        self.virtuallight_intensity_value_label = QLabel("50%")
        self.virtuallight_intensity_value_label.setStyleSheet("color: #3b82f6; font-size: 13px; font-weight: 600; background: transparent;")
        virtuallight_intensity_header.addWidget(self.virtuallight_intensity_value_label)
        fx_layout.addLayout(virtuallight_intensity_header)

        self.virtuallight_intensity_slider = NoWheelSlider(Qt.Horizontal)
        self.virtuallight_intensity_slider.setRange(0, 100)
        self.virtuallight_intensity_slider.setValue(50)
        self.virtuallight_intensity_slider.valueChanged.connect(self._on_virtuallight_intensity)
        fx_layout.addWidget(self.virtuallight_intensity_slider)
        fx_layout.addStretch()

        self.tabs.addTab(effects_tab, self._t("tab_effects"))

        # ── Framing tab: manual zoom/pan and Auto Reframe together, since
        # they're two ways of controlling the same thing (how you're framed)
        # and Auto Reframe disables the manual controls while it's active. ──
        framing_tab = QWidget()
        cam_layout = QVBoxLayout(framing_tab)
        cam_layout.setContentsMargins(16, 16, 16, 16)
        cam_layout.setSpacing(14)

        manual_section_label = QLabel(self._t("section_manual"))
        manual_section_label.setStyleSheet(SECTION_LABEL_STYLE)
        cam_layout.addWidget(manual_section_label)

        zoom_header = QHBoxLayout()
        zoom_header.addWidget(self._styled_label(self._t("zoom")))
        self.zoom_value_label = QLabel("1.0x")
        self.zoom_value_label.setStyleSheet("color: #3b82f6; font-size: 13px; font-weight: 600; background: transparent;")
        zoom_header.addWidget(self.zoom_value_label)
        zoom_header.addStretch()
        pan_label = self._styled_label(self._t("pan"))
        pan_label.setFixedWidth(100)
        pan_label.setAlignment(Qt.AlignCenter)
        zoom_header.addWidget(pan_label)
        cam_layout.addLayout(zoom_header)

        zoom_row = QHBoxLayout()
        zoom_row.setSpacing(12)

        self.zoom_slider = NoWheelSlider(Qt.Horizontal)
        self.zoom_slider.setRange(100, 200)
        self.zoom_slider.setValue(100)
        self.zoom_slider.setToolTip(self._t("zoom_slider_tip"))
        self.zoom_slider.valueChanged.connect(self._on_zoom)
        zoom_row.addWidget(self.zoom_slider, 1)

        zoom_row.addWidget(self._build_pan_control())
        cam_layout.addLayout(zoom_row)

        cam_layout.addWidget(self._separator())

        auto_section_label = QLabel(self._t("section_auto_reframe"))
        auto_section_label.setStyleSheet(SECTION_LABEL_STYLE)
        cam_layout.addWidget(auto_section_label)

        autoreframe_row = QHBoxLayout()
        autoreframe_label = self._styled_label(self._t("autoreframe_enable"))
        autoreframe_label.setToolTip(self._t("autoreframe_enable_tip"))
        autoreframe_row.addWidget(autoreframe_label)
        autoreframe_row.addStretch()
        self.autoreframe_button = ToggleSwitch()
        self.autoreframe_button.toggled.connect(self._on_autoreframe_toggle)
        autoreframe_row.addWidget(self.autoreframe_button)
        cam_layout.addLayout(autoreframe_row)

        autoreframe_model_row = QHBoxLayout()
        autoreframe_model_label = self._styled_label(self._t("detector_model"))
        autoreframe_model_label.setToolTip(self._t("detector_model_tip"))
        autoreframe_model_row.addWidget(autoreframe_model_label)
        autoreframe_model_row.addStretch()
        self.autoreframe_model_combo = NoWheelComboBox()
        self.autoreframe_model_combo.addItem(self._t("model_dnn"), "dnn")
        self.autoreframe_model_combo.addItem(self._t("model_haar"), "haar")
        self.autoreframe_model_combo.setFixedWidth(160)
        self.autoreframe_model_combo.currentIndexChanged.connect(self._on_autoreframe_model)
        autoreframe_model_row.addWidget(self.autoreframe_model_combo)
        cam_layout.addLayout(autoreframe_model_row)

        autoreframe_zoom_header = QHBoxLayout()
        autoreframe_zoom_header.addWidget(self._styled_label(self._t("autoreframe_zoom")))
        self.autoreframe_zoom_value_label = QLabel("1.15x")
        self.autoreframe_zoom_value_label.setStyleSheet("color: #3b82f6; font-size: 13px; font-weight: 600; background: transparent;")
        autoreframe_zoom_header.addWidget(self.autoreframe_zoom_value_label)
        cam_layout.addLayout(autoreframe_zoom_header)

        self.autoreframe_zoom_slider = NoWheelSlider(Qt.Horizontal)
        self.autoreframe_zoom_slider.setRange(100, 200)
        self.autoreframe_zoom_slider.setValue(115)
        self.autoreframe_zoom_slider.setToolTip(self._t("autoreframe_zoom_tip"))
        self.autoreframe_zoom_slider.valueChanged.connect(self._on_autoreframe_zoom)
        cam_layout.addWidget(self.autoreframe_zoom_slider)

        autoreframe_speed_header = QHBoxLayout()
        autoreframe_speed_header.addWidget(self._styled_label(self._t("tracking_speed")))
        self.autoreframe_speed_value_label = QLabel("35%")
        self.autoreframe_speed_value_label.setStyleSheet("color: #3b82f6; font-size: 13px; font-weight: 600; background: transparent;")
        autoreframe_speed_header.addWidget(self.autoreframe_speed_value_label)
        cam_layout.addLayout(autoreframe_speed_header)

        self.autoreframe_speed_slider = NoWheelSlider(Qt.Horizontal)
        self.autoreframe_speed_slider.setRange(0, 100)
        self.autoreframe_speed_slider.setValue(35)
        self.autoreframe_speed_slider.setToolTip(self._t("tracking_speed_tip"))
        self.autoreframe_speed_slider.valueChanged.connect(self._on_autoreframe_speed)
        cam_layout.addWidget(self.autoreframe_speed_slider)
        cam_layout.addStretch()

        self.tabs.addTab(framing_tab, self._t("tab_framing"))

        # ── Camera tab: capture hardware, local display, and app startup —
        # the "set once and forget" settings, separate from the ones you
        # tweak while actively using the app (Background, Framing). ──
        camera_tab = QWidget()
        dev_layout = QVBoxLayout(camera_tab)
        dev_layout.setContentsMargins(16, 16, 16, 16)
        dev_layout.setSpacing(14)

        dev_layout.addWidget(self._styled_label(self._t("input_device")))
        dev_row = QHBoxLayout()
        self.device_combo = NoWheelComboBox()
        self.device_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._populate_devices()
        self.device_combo.currentIndexChanged.connect(self._on_device)
        dev_row.addWidget(self.device_combo)

        refresh_btn = QPushButton("⟳")
        refresh_btn.setFixedSize(46, 46)
        refresh_btn.setStyleSheet("""
            QPushButton {
                background: #1a1f1a; border: 1px solid #2d3d2d; border-radius: 10px;
                font-size: 18px; color: #94a3b8; padding: 8px;
            }
            QPushButton:hover { background: #1f2a1f; border-color: #3b82f6; color: #3b82f6; }
        """)
        refresh_btn.clicked.connect(self._refresh_devices)
        dev_row.addWidget(refresh_btn)
        dev_layout.addLayout(dev_row)

        dev_layout.addWidget(self._styled_label(self._t("resolution")))
        self.res_combo = NoWheelComboBox()
        self.res_combo.currentIndexChanged.connect(self._on_resolution)
        dev_layout.addWidget(self.res_combo)

        dev_layout.addWidget(self._styled_label(self._t("frame_rate")))
        self.fps_combo = NoWheelComboBox()
        self.fps_combo.currentIndexChanged.connect(self._on_fps)
        dev_layout.addWidget(self.fps_combo)

        dev_layout.addWidget(self._separator())

        mirror_row = QHBoxLayout()
        mirror_label = self._styled_label(self._t("mirror_preview"))
        mirror_label.setToolTip(self._t("mirror_preview_tip"))
        mirror_row.addWidget(mirror_label)
        mirror_row.addStretch()
        self.mirror_button = ToggleSwitch()
        self.mirror_button.toggled.connect(self._on_mirror_toggle)
        mirror_row.addWidget(self.mirror_button)
        dev_layout.addLayout(mirror_row)
        dev_layout.addStretch()

        # ── General tab: app-level settings that aren't about the video
        # pipeline itself (startup behavior, UI language) plus the About
        # info, shown first since it identifies what the app is. ──
        general_tab = QWidget()
        general_layout = QVBoxLayout(general_tab)
        general_layout.setContentsMargins(16, 16, 16, 16)
        general_layout.setSpacing(14)

        general_layout.addSpacing(4)

        about_icon_label = QLabel()
        about_icon_label.setAlignment(Qt.AlignCenter)
        if Path(LOGO_PATH).exists():
            px = QPixmap(64, 64)
            px.fill(Qt.transparent)
            painter = QPainter(px)
            painter.setRenderHint(QPainter.Antialiasing)
            QSvgRenderer(LOGO_PATH).render(painter)
            painter.end()
            about_icon_label.setPixmap(px)
        general_layout.addWidget(about_icon_label)

        about_title_label = QLabel("BluCast")
        about_title_label.setAlignment(Qt.AlignCenter)
        about_title_label.setStyleSheet("font-size: 22px; font-weight: 700; color: #fff; background: transparent;")
        general_layout.addWidget(about_title_label)

        general_layout.addSpacing(4)
        general_layout.addWidget(self._separator())
        general_layout.addSpacing(4)

        about_label = QLabel(self._t("about_text"))
        about_label.setWordWrap(True)
        about_label.setAlignment(Qt.AlignCenter)
        about_label.setStyleSheet("font-size: 13px; color: #94a3b8; background: transparent;")
        general_layout.addWidget(about_label)

        general_layout.addWidget(self._separator())

        startup_row = QHBoxLayout()
        startup_info = QVBoxLayout()
        startup_info.setSpacing(2)
        startup_title = QLabel(self._t("startup_title"))
        startup_title.setStyleSheet("font-size: 14px; font-weight: 600; color: #fff; background: transparent;")
        startup_info.addWidget(startup_title)
        self.startup_sub_label = QLabel(self._t("startup_sub"))
        self.startup_sub_label.setStyleSheet("font-size: 12px; color: #64748b; background: transparent;")
        startup_info.addWidget(self.startup_sub_label)
        startup_row.addLayout(startup_info)
        startup_row.addStretch()

        self.startup_button = ToggleSwitch()
        self.startup_button.toggled.connect(self._on_toggle_autostart_button)
        if not HOST_RUN_SCRIPT:
            self.startup_button.setEnabled(False)
            self.startup_sub_label.setText(self._t("startup_unavailable"))
        startup_row.addWidget(self.startup_button)
        general_layout.addLayout(startup_row)

        general_layout.addWidget(self._separator())

        general_layout.addWidget(self._styled_label(self._t("language")))
        self.language_combo = NoWheelComboBox()
        self.language_combo.addItem("English", "en")
        self.language_combo.addItem("Español", "es")
        self.language_combo.currentIndexChanged.connect(self._on_language)
        general_layout.addWidget(self.language_combo)
        self.language_note_label = QLabel(self._t("language_note"))
        self.language_note_label.setWordWrap(True)
        self.language_note_label.setStyleSheet("font-size: 11px; color: #64748b; background: transparent;")
        general_layout.addWidget(self.language_note_label)

        general_layout.addStretch()

        self.tabs.addTab(general_tab, self._t("tab_general"))

        self.tabs.insertTab(0, camera_tab, self._t("tab_camera"))
        self.tabs.setCurrentIndex(0)

        layout.addWidget(self.tabs)
        layout.addStretch()

        # ── Reset to defaults ──
        reset_btn = QPushButton(self._t("reset_defaults"))
        reset_btn.clicked.connect(self._on_reset_defaults)
        layout.addWidget(reset_btn)

        # ── Quit ──
        quit_btn = QPushButton(self._t("quit"))
        quit_btn.setStyleSheet("""
            QPushButton {
                background: #1a1515; border: 1px solid #3d2d2d; color: #ef4444;
                border-radius: 10px; padding: 14px; font-size: 14px; font-weight: 600;
            }
            QPushButton:hover { background: #2d1f1f; border-color: #4d3d3d; }
        """)
        quit_btn.clicked.connect(self._quit)
        layout.addWidget(quit_btn)

        self._refresh_startup_button()

    # ── Helpers ──────────────────────────────────────────────────────────
    def _t(self, key: str) -> str:
        entry = STRINGS.get(key)
        if entry is None:
            return key
        return entry.get(self.lang, entry["en"])

    def _styled_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("color: #94a3b8; font-size: 12px; background: transparent;")
        return lbl

    def _separator(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("background: #1f2a1f; max-height: 1px; border: none;")
        return line

    def _build_pan_control(self) -> QWidget:
        """Compact D-pad to recenter the zoomed crop — zooming in doesn't
        keep the frame centered on your face, so this nudges it back."""
        container = QWidget()
        container.setFixedSize(100, 67)
        grid = QGridLayout(container)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(2)

        self.pan_buttons = []

        def make_btn(glyph: str, handler) -> QPushButton:
            btn = QPushButton(glyph)
            btn.setFixedSize(32, 21)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(PAN_BUTTON_STYLE)
            btn.clicked.connect(handler)
            self.pan_buttons.append(btn)
            return btn

        grid.addWidget(make_btn("▲", self._on_pan_up),    0, 1)
        grid.addWidget(make_btn("◀", self._on_pan_left),  1, 0)
        grid.addWidget(make_btn("⊙", self._on_pan_reset), 1, 1)
        grid.addWidget(make_btn("▶", self._on_pan_right), 1, 2)
        grid.addWidget(make_btn("▼", self._on_pan_down),  2, 1)
        return container

    def _refresh_startup_button(self):
        self.startup_button.setCheckedSilently(autostart_enabled())

    def _on_toggle_autostart_button(self, checked: bool):
        if set_autostart(checked):
            if hasattr(self, "tray_autostart_act"):
                self.tray_autostart_act.blockSignals(True)
                self.tray_autostart_act.setChecked(checked)
                self.tray_autostart_act.blockSignals(False)
        else:
            self.startup_button.setCheckedSilently(not checked)

    def _populate_devices(self):
        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        for path, name in get_video_devices(self.lang):
            self.device_combo.addItem(f"{name}  ({path})", path)
        self.device_combo.blockSignals(False)

    def _refresh_devices(self):
        cur = self.device_combo.currentData()
        self._populate_devices()
        for i in range(self.device_combo.count()):
            if self.device_combo.itemData(i) == cur:
                self.device_combo.setCurrentIndex(i)
                break

    def _refresh_formats(self):
        device = self.settings.get("input_device")
        if not device or not Path(device).exists():
            devs = get_video_devices(self.lang)
            device = devs[0][0] if devs else "/dev/video0"
            self.settings.set("input_device", device)
        fmts = get_supported_formats(device)
        self.supported_formats = fmts if fmts else DEFAULT_FORMATS.copy()

    def _populate_res_combo(self, preferred: Optional[str] = None) -> Optional[str]:
        if not self.supported_formats:
            return None
        resolutions = sorted(self.supported_formats.keys(),
                             key=lambda r: tuple(map(int, r.split("x"))))
        self.res_combo.blockSignals(True)
        self.res_combo.clear()
        for r in resolutions:
            self.res_combo.addItem(r, r)
        self.res_combo.blockSignals(False)
        target = preferred if preferred in self.supported_formats else resolutions[0]
        idx = self.res_combo.findData(target)
        if idx >= 0:
            self.res_combo.setCurrentIndex(idx)
        return target

    def _populate_fps_combo(self, res: str, preferred: Optional[int] = None) -> Optional[int]:
        fps_list = self.supported_formats.get(res, [])
        if not fps_list:
            self.fps_combo.blockSignals(True)
            self.fps_combo.clear()
            self.fps_combo.blockSignals(False)
            return None
        self.fps_combo.blockSignals(True)
        self.fps_combo.clear()
        for f in fps_list:
            self.fps_combo.addItem(f"{f} fps", f)
        self.fps_combo.blockSignals(False)
        target = preferred if preferred in fps_list else fps_list[0]
        idx = self.fps_combo.findData(target)
        if idx >= 0:
            self.fps_combo.setCurrentIndex(idx)
        return target

    def _update_info_label(self):
        res = self.settings.get("resolution")
        fps = self.settings.get("fps")
        self.preview_info.setText(f"{res} @ {fps}fps")

    # ── Apply saved settings ─────────────────────────────────────────────
    def _apply_saved_settings(self):
        # Language (strings were already resolved at construction time from
        # self.lang; this just syncs the combo's displayed selection)
        idx = self.language_combo.findData(self.lang)
        if idx >= 0:
            self.language_combo.blockSignals(True)
            self.language_combo.setCurrentIndex(idx)
            self.language_combo.blockSignals(False)

        # Effect
        eff = self.settings.get("effect_mode")
        btn = self.effect_buttons.get(eff, self.effect_buttons["blur"])
        btn.setChecked(True)

        # Blur
        self.blur_slider.setValue(self.settings.get("blur_strength"))

        # Zoom
        zoom = self.settings.get("zoom_factor")
        self.zoom_slider.setValue(zoom)
        self.zoom_value_label.setText(f"{zoom / 100.0:.1f}x")

        # Mirror preview (local display only, never sent to the virtual camera)
        self.mirror_preview = bool(self.settings.get("mirror_preview"))
        self.mirror_button.setChecked(self.mirror_preview)

        # Auto Reframe (disables manual zoom/pan controls while active)
        saved_model = self.settings.get("autoreframe_model")
        idx = self.autoreframe_model_combo.findData(saved_model)
        if idx >= 0:
            self.autoreframe_model_combo.setCurrentIndex(idx)
        az = self.settings.get("autoreframe_zoom")
        self.autoreframe_zoom_slider.setValue(az)
        self.autoreframe_zoom_value_label.setText(f"{az / 100.0:.2f}x")
        asp = self.settings.get("autoreframe_speed")
        self.autoreframe_speed_slider.setValue(asp)
        self.autoreframe_speed_value_label.setText(f"{asp}%")
        self.autoreframe_button.setChecked(bool(self.settings.get("auto_reframe")))

        # Virtual Light
        vli = self.settings.get("virtual_light_intensity")
        self.virtuallight_intensity_slider.setValue(vli)
        self.virtuallight_intensity_value_label.setText(f"{vli}%")
        self.virtuallight_button.setChecked(bool(self.settings.get("virtual_light")))

        # Background
        bg = self.settings.get("background_image")
        if bg and Path(bg).exists():
            self.bg_path_label.setText(Path(bg).name)
            self.bg_path_label.setStyleSheet("color: #e2e8f0; font-size: 12px; background: transparent;")

        # Device
        saved_dev = self.settings.get("input_device")
        if saved_dev:
            for i in range(self.device_combo.count()):
                if self.device_combo.itemData(i) == saved_dev:
                    self.device_combo.setCurrentIndex(i)
                    break

        # Resolution / FPS
        self._refresh_formats()
        sel_res = self._populate_res_combo(self.settings.get("resolution"))
        sel_fps = None
        if sel_res:
            sel_fps = self._populate_fps_combo(sel_res, self.settings.get("fps"))
            self.settings.set("resolution", sel_res)
        if sel_fps is not None:
            self.settings.set("fps", sel_fps)
        self._update_info_label()

        # Send everything to server
        self._send_all()

    def _send_all(self):
        eff = self.settings.get("effect_mode")
        send_command(f"MODE:{EFFECT_MAP.get(eff, 6)}")

        dev = self.settings.get("input_device")
        if dev:
            send_command(f"DEVICE:{dev}")

        bg = self.settings.get("background_image")
        if bg and Path(bg).exists():
            send_command(f"BG:{bg}")

        send_command(f"BLUR:{self.settings.get('blur_strength') / 100.0}")
        send_command(f"ZOOM:{self.settings.get('zoom_factor') / 100.0}")
        send_command(f"PANX:{self.settings.get('pan_x')}")
        send_command(f"PANY:{self.settings.get('pan_y')}")
        send_command(f"AUTOREFRAME_MODEL:{self.settings.get('autoreframe_model')}")
        send_command(f"AUTOREFRAME_ZOOM:{self.settings.get('autoreframe_zoom') / 100.0}")
        send_command(f"AUTOREFRAME_SPEED:{self._speed_to_smoothing(self.settings.get('autoreframe_speed'))}")
        send_command(f"AUTOREFRAME:{1 if self.settings.get('auto_reframe') else 0}")
        send_command(f"VIRTUALLIGHT_INTENSITY:{self.settings.get('virtual_light_intensity') / 100.0}")
        send_command(f"VIRTUALLIGHT:{1 if self.settings.get('virtual_light') else 0}")
        send_command(f"RESOLUTION:{self.settings.get('resolution')}")
        send_command(f"FPS:{self.settings.get('fps')}")
        # Window isn't shown yet at this point (main() calls .show() right
        # after construction) — announce the state it's about to be in.
        send_command("WINDOW:visible" if self.camera_enabled else "WINDOW:hidden")

    # ── Callbacks ────────────────────────────────────────────────────────
    def _on_effect(self, key: str, checked: bool):
        if not checked:
            return
        self.blur_controls.setVisible(key == "blur")
        self.bg_controls.setVisible(key == "replace")
        send_command(f"MODE:{EFFECT_MAP.get(key, 6)}")
        self.settings.set("effect_mode", key)
        act = getattr(self, "tray_effect_actions", {}).get(key)
        if act is not None:
            act.setChecked(True)

    def _on_blur(self, value: int):
        self.blur_value_label.setText(f"{value}%")
        send_command(f"BLUR:{value / 100.0}")
        self.settings.set("blur_strength", value)

    def _on_zoom(self, value: int):
        self.zoom_value_label.setText(f"{value / 100.0:.1f}x")
        send_command(f"ZOOM:{value / 100.0}")
        self.settings.set("zoom_factor", value)

    def _nudge_pan(self, key: str, command: str, delta: float):
        value = max(-1.0, min(1.0, self.settings.get(key) + delta))
        self.settings.set(key, value)
        send_command(f"{command}:{value}")

    def _on_pan_up(self):
        self._nudge_pan("pan_y", "PANY", -PAN_STEP)

    def _on_pan_down(self):
        self._nudge_pan("pan_y", "PANY", PAN_STEP)

    def _on_pan_left(self):
        self._nudge_pan("pan_x", "PANX", -PAN_STEP)

    def _on_pan_right(self):
        self._nudge_pan("pan_x", "PANX", PAN_STEP)

    def _on_pan_reset(self):
        self.settings.set("pan_x", 0.0)
        self.settings.set("pan_y", 0.0)
        send_command("PANX:0.0")
        send_command("PANY:0.0")

    def _on_autoreframe_toggle(self, checked: bool):
        self.settings.set("auto_reframe", checked)
        send_command(f"AUTOREFRAME:{1 if checked else 0}")
        # Auto Reframe drives zoom/pan itself — manual controls would fight it.
        self.zoom_slider.setEnabled(not checked)
        for btn in self.pan_buttons:
            btn.setEnabled(not checked)

    def _on_autoreframe_model(self, index: int):
        value = self.autoreframe_model_combo.itemData(index)
        self.settings.set("autoreframe_model", value)
        send_command(f"AUTOREFRAME_MODEL:{value}")

    @staticmethod
    def _speed_to_smoothing(speed: int) -> float:
        return AUTOREFRAME_SMOOTH_MAX - (speed / 100.0) * (AUTOREFRAME_SMOOTH_MAX - AUTOREFRAME_SMOOTH_MIN)

    def _on_autoreframe_zoom(self, value: int):
        zoom = value / 100.0
        self.autoreframe_zoom_value_label.setText(f"{zoom:.2f}x")
        send_command(f"AUTOREFRAME_ZOOM:{zoom}")
        self.settings.set("autoreframe_zoom", value)

    def _on_autoreframe_speed(self, value: int):
        self.autoreframe_speed_value_label.setText(f"{value}%")
        send_command(f"AUTOREFRAME_SPEED:{self._speed_to_smoothing(value)}")
        self.settings.set("autoreframe_speed", value)

    def _on_virtuallight_toggle(self, checked: bool):
        self.settings.set("virtual_light", checked)
        send_command(f"VIRTUALLIGHT:{1 if checked else 0}")

    def _on_virtuallight_intensity(self, value: int):
        self.virtuallight_intensity_value_label.setText(f"{value}%")
        send_command(f"VIRTUALLIGHT_INTENSITY:{value / 100.0}")
        self.settings.set("virtual_light_intensity", value)

    def _on_language(self, index: int):
        value = self.language_combo.itemData(index)
        if value:
            self.settings.set("language", value)

    def _on_mirror_toggle(self, checked: bool):
        self.mirror_preview = checked
        self.settings.set("mirror_preview", checked)

    def _on_browse_bg(self):
        start = "/host_home" if Path("/host_home").exists() else ""
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Background Image", start,
            "Images (*.png *.jpg *.jpeg *.bmp *.webp)",
        )
        if path:
            self.bg_path_label.setText(Path(path).name)
            self.bg_path_label.setStyleSheet("color: #e2e8f0; font-size: 12px; background: transparent;")
            send_command(f"BG:{path}")
            self.settings.set("background_image", path)

    def _on_device(self, index: int):
        if index < 0:
            return
        device = self.device_combo.itemData(index)
        send_command(f"DEVICE:{device}")
        self.settings.set("input_device", device)
        self._refresh_formats()
        sel_res = self._populate_res_combo(self.settings.get("resolution"))
        if sel_res:
            sel_fps = self._populate_fps_combo(sel_res, self.settings.get("fps"))
            self.settings.set("resolution", sel_res)
            send_command(f"RESOLUTION:{sel_res}")
            if sel_fps is not None:
                self.settings.set("fps", sel_fps)
                send_command(f"FPS:{sel_fps}")
        self._update_info_label()
        self._resend_background_after_resize()

    def _resend_background_after_resize(self):
        """Work around a server bug: it never re-scales the background image
        to the new frame size when resolution/fps changes reopen the camera,
        which corrupts (and can stall) the composite in "replace" mode. It
        does re-scale on every BG: command, so just resend the same path
        once the camera has had time to reopen at the new size."""
        if self.settings.get("effect_mode") != "replace":
            return
        bg = self.settings.get("background_image")
        if bg and Path(bg).exists():
            QTimer.singleShot(800, lambda: send_command(f"BG:{bg}"))

    def _on_resolution(self, index: int):
        res = self.res_combo.itemData(index)
        if not res:
            return
        prev_fps = self.settings.get("fps")
        self.settings.set("resolution", res)
        sel_fps = self._populate_fps_combo(res, prev_fps)
        send_command(f"RESOLUTION:{res}")
        if sel_fps is not None and sel_fps != prev_fps:
            self.settings.set("fps", sel_fps)
            send_command(f"FPS:{sel_fps}")
        self._update_info_label()
        self._resend_background_after_resize()

    def _on_fps(self, index: int):
        fps = self.fps_combo.itemData(index)
        if fps is None:
            return
        send_command(f"FPS:{fps}")
        self.settings.set("fps", fps)
        self._update_info_label()
        self._resend_background_after_resize()

    def _on_reset_defaults(self):
        for key, value in Settings.DEFAULTS.items():
            self.settings.set(key, value)
        self.bg_path_label.setText(self._t("no_image_selected"))
        self.bg_path_label.setStyleSheet("color: #64748b; font-size: 12px; background: transparent;")
        self._apply_saved_settings()

    def _quit(self):
        try:
            Path(CAMERA_DISABLED_FLAG).unlink(missing_ok=True)
        except OSError:
            pass
        send_command("QUIT")
        QApplication.quit()


# ═════════════════════════════════════════════════════════════════════════
# Entry point
# ═════════════════════════════════════════════════════════════════════════

def main():
    # We run as `python3 /app/control_panel.py`, so Qt/X11 would otherwise
    # derive WM_CLASS from that path ("control_panel.py"). Force it to match
    # the installed .desktop's StartupWMClass=blucast so GNOME's dock/taskbar
    # resolves our real icon instead of falling back to a generic one.
    sys.argv[0] = "blucast"
    app = QApplication(sys.argv)
    app.setDesktopFileName("blucast")
    app.setQuitOnLastWindowClosed(False)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)

    palette = QPalette()
    palette.setColor(QPalette.Window,          QColor("#0a0f0a"))
    palette.setColor(QPalette.WindowText,      QColor("#e2e8f0"))
    palette.setColor(QPalette.Base,            QColor("#111611"))
    palette.setColor(QPalette.AlternateBase,   QColor("#1a1f1a"))
    palette.setColor(QPalette.Text,            QColor("#e2e8f0"))
    palette.setColor(QPalette.Button,          QColor("#1a1f1a"))
    palette.setColor(QPalette.ButtonText,      QColor("#94a3b8"))
    palette.setColor(QPalette.Highlight,       QColor("#3b82f6"))
    palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    app.setPalette(palette)

    if Path(LOGO_PATH).exists():
        app.setWindowIcon(QIcon(LOGO_PATH))

    window = ControlPanel()
    # Autostart launches with this env var (set by run.sh --autostart) so the
    # app opens straight to the tray instead of popping its window on login —
    # only when the tray actually works, otherwise there'd be no way to
    # reach the app at all.
    start_minimized = os.environ.get("BLUCAST_START_MINIMIZED") == "1" and window.tray_available
    if not start_minimized:
        window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
