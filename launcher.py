# ============================================================
#  launcher.py -- GUI лаунчер голосового ассистента
# ============================================================
 
import sys
if sys.stdout is not None and sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')


# -- DPI awareness (Windows) --------------------------------
# Без этого Windows рендерит окно в "виртуальном" низком
# разрешении и потом растягивает картинку программно при
# масштабе экрана 125%/150%/200% -- из-за этого текст, рамки
# карточек и кнопки выглядят размыто/низкокачественно,
# особенно заметно в плотных окнах вроде настроек.
# Объявляем awareness ДО создания tk.Tk(), иначе не сработает.
if sys.platform == "win32":
    try:
        import ctypes
        try:
            # Per-Monitor DPI awareness (Windows 8.1+) -- самый чёткий вариант
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                # Фоллбек для старых Windows -- хотя бы System DPI aware
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
    except Exception:
        pass

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import subprocess
import threading
import os
import signal
import re
import colorsys
from datetime import datetime

import theme
import cost_tracker as ct

BG         = "#0d0d0d"
BG_PANEL   = "#141414"
BG_MSG     = "#1a1a1a"
GREEN      = "#39d98a"
RED        = "#ff5c5c"
YELLOW     = "#f5c542"
CYAN       = "#5cf0ff"
TEXT       = "#e8e8e8"
TEXT_DIM   = "#555555"
BORDER     = "#222222"

# ACCENT / ACCENT_DIM -- не константы: зависят от выбора пользователя
# в разделе настроек "Кастомизация" и хранятся в launcher_settings.json.
# _refresh_accent_globals() перечитывает их и обновляет глобальные имена
# в этом модуле -- так весь существующий код ниже (который читает ACCENT
# как обычную глобальную переменную) продолжает работать без изменений.
ACCENT     = theme.get_accent_color()
ACCENT_DIM = theme.dim(ACCENT)


def _refresh_accent_globals():
    global ACCENT, ACCENT_DIM
    ACCENT = theme.get_accent_color()
    ACCENT_DIM = theme.dim(ACCENT)


# ------------------------------------------------------------------
# Копировать / Вставить / Вырезать -- для всех полей ввода сразу.
# Биндим на уровне классов Entry и Text, поэтому работает везде в
# приложении (API-ключ, URL, название модели, контекст, память и т.п.),
# включая случаи нестандартной раскладки клавиатуры, где встроенные
# хоткеи Tkinter иногда не срабатывают.
# ------------------------------------------------------------------
def _copy_paste_event(event):
    widget = event.widget
    try:
        if event.keysym.lower() == "c":
            widget.event_generate("<<Copy>>")
        elif event.keysym.lower() == "v":
            widget.event_generate("<<Paste>>")
        elif event.keysym.lower() == "x":
            widget.event_generate("<<Cut>>")
        elif event.keysym.lower() == "a":
            if isinstance(widget, tk.Entry):
                widget.selection_range(0, "end")
            else:
                widget.tag_add("sel", "1.0", "end")
    except tk.TclError:
        pass
    return "break"


def _show_context_menu(event):
    widget = event.widget
    menu = tk.Menu(
        widget, tearoff=0, bg=BG_PANEL, fg=TEXT,
        activebackground=ACCENT_DIM, activeforeground="#fff",
        relief="flat", bd=0, borderwidth=0, activeborderwidth=0,
    )
    menu.add_command(label="Вырезать", command=lambda: widget.event_generate("<<Cut>>"))
    menu.add_command(label="Копировать", command=lambda: widget.event_generate("<<Copy>>"))
    menu.add_command(label="Вставить", command=lambda: widget.event_generate("<<Paste>>"))
    menu.add_separator()
    if isinstance(widget, tk.Entry):
        menu.add_command(label="Выделить всё", command=lambda: widget.selection_range(0, "end"))
    else:
        menu.add_command(label="Выделить всё", command=lambda: widget.tag_add("sel", "1.0", "end"))
    try:
        menu.tk_popup(event.x_root, event.y_root)
    finally:
        menu.grab_release()


def _install_global_copy_paste(root):
    """Включает копирование/вставку/вырезание (Ctrl+C/V/X/A и ПКМ) для
    всех Entry и Text виджетов приложения, включая создаваемые позже."""
    for cls in ("Entry", "Text", "TEntry", "TCombobox"):
        root.bind_class(cls, "<Control-c>", _copy_paste_event)
        root.bind_class(cls, "<Control-v>", _copy_paste_event)
        root.bind_class(cls, "<Control-x>", _copy_paste_event)
        root.bind_class(cls, "<Control-a>", _copy_paste_event)
        root.bind_class(cls, "<Control-C>", _copy_paste_event)
        root.bind_class(cls, "<Control-V>", _copy_paste_event)
        root.bind_class(cls, "<Control-X>", _copy_paste_event)
        root.bind_class(cls, "<Control-A>", _copy_paste_event)
        root.bind_class(cls, "<Button-3>", _show_context_menu)

 
FONT_MONO  = ("Consolas", 11)
FONT_UI    = ("Segoe UI", 10)
FONT_BIG   = ("Segoe UI", 13, "bold")
FONT_SMALL = ("Segoe UI", 9)
FONT_STAT  = ("Consolas", 10)
 
 

def _clamp_scroll(first, last, canvas):
    """Запрещает прокрутку выше начала содержимого.
    Вызывается из yscrollcommand-обёртки каждого Canvas с прокруткой."""
    try:
        if float(first) < 0.0:
            canvas.yview_moveto(0.0)
    except Exception:
        pass

def _make_round_avatar(path, size, return_error=False):
    """Открывает картинку по пути, вписывает в квадрат size×size (обрезка по
    центру) и накладывает круглую маску. Возвращает PIL.Image (RGBA) или
    None, если файла нет / Pillow недоступен / картинка битая.
    Если return_error=True -- возвращает кортеж (image_or_None, error_str_or_None).

    Делает несколько попыток с небольшой паузой -- на папках, синхронизируемых
    OneDrive (например, Рабочий стол), только что записанный файл иногда на
    долю секунды недоступен для чтения, пока идёт синхронизация."""
    def _result(img, err):
        return (img, err) if return_error else img

    if not path or not os.path.exists(path):
        return _result(None, None)

    import time
    last_error = None
    for attempt in range(3):
        try:
            from PIL import Image, ImageDraw
            img = Image.open(path)
            img.load()  # форсируем полную загрузку файла сразу, чтобы поймать ошибки здесь же
            img = img.convert("RGBA")
            w, h = img.size
            side = min(w, h)
            left = (w - side) // 2
            top = (h - side) // 2
            img = img.crop((left, top, left + side, top + side)).resize((size, size), Image.LANCZOS)

            mask = Image.new("L", (size, size), 0)
            ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)

            out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            out.paste(img, (0, 0), mask)
            return _result(out, None)
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
            time.sleep(0.1)

    print(f"[AVATAR] Не удалось открыть аватарку {path}: {last_error}")
    return _result(None, last_error)


def _apply_dark_titlebar(win):
    """
    Красит системную title bar окна в тёмный цвет (Windows 10 1809+/11).
    На других ОС или старых сборках Windows -- молча ничего не делает.
    """
    if sys.platform != "win32":
        return
 
    def _do_apply():
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
            if not hwnd:
                hwnd = win.winfo_id()
            DWMWA_USE_IMMERSIVE_DARK_MODE = 20
            DWMWA_USE_IMMERSIVE_DARK_MODE_OLD = 19  # для сборок Windows 10 до 20H1
            value = ctypes.c_int(1)
            for attr in (DWMWA_USE_IMMERSIVE_DARK_MODE, DWMWA_USE_IMMERSIVE_DARK_MODE_OLD):
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)
                )
            try:
                DWMWA_BORDER_COLOR = 34
                r, g, b = theme.hex_to_rgb(ACCENT)
                colorref = ctypes.c_int((b << 16) | (g << 8) | r)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, DWMWA_BORDER_COLOR, ctypes.byref(colorref), ctypes.sizeof(colorref)
                )
            except Exception:
                pass
            # Форсируем перерисовку рамки чтобы изменение применилось сразу,
            # без сворачивания/разворачивания окна вручную
            try:
                ctypes.windll.user32.SetWindowPos(
                    hwnd, 0, 0, 0, 0, 0,
                    0x0001 | 0x0002 | 0x0004 | 0x0020  # NOSIZE|NOMOVE|NOZORDER|FRAMECHANGED
                )
            except Exception:
                pass
        except Exception:
            pass
 
    # winfo_id() даёт надёжный HWND только после того как окно реально
    # материализовано -- update() (не idletasks) гонит полный цикл отрисовки.
    win.update()
    _do_apply()
    # Подстраховка: некоторые сборки Windows применяют атрибут только если
    # вызвать его ещё раз чуть позже, после того как DWM окончательно
    # инициализировал композицию окна.
    win.after(50, _do_apply)

    # На некоторых сборках Windows DWM сбрасывает тёмную рамку titlebar
    # после сворачивания/разворачивания окна (особенно для Toplevel,
    # т.е. окна настроек) -- переприменяем атрибут при каждом восстановлении.
    def _on_map(event):
        if event.widget is win:
            win.after(10, _do_apply)

    win.bind("<Map>", _on_map, add="+")
 
 
class RoundedSlider(tk.Canvas):
    """Тонкий горизонтальный слайдер с закруглённым жёлобом и маленьким
    круглым бегунком -- визуально в стиле скроллбаров приложения (узкие,
    flat, со скруглёнными краями), вместо угловатого штатного tk.Scale.
    API-совместим с минимально нужным подмножеством tk.Scale: get()/set(),
    command(value: float), state ("normal"/"disabled")."""

    TRACK_H = 4       # толщина жёлоба
    HANDLE_R = 7      # радиус бегунка
    EDGE_PAD = 20     # отступ трека от краёв canvas (чтобы не "прилипал" к краю)

    def __init__(self, parent, from_=0.0, to=1.0, resolution=1.0,
                 variable=None, command=None, bg=None, troughcolor=None,
                 activebackground=None, height=20, **kw):
        super().__init__(parent, bg=bg, highlightthickness=0, height=height, **kw)
        self._from = float(from_)
        self._to = float(to)
        self._resolution = float(resolution) if resolution else 0.0
        self._var = variable
        self._command = command
        self._track_color = troughcolor or "#3a3a3a"
        self._fill_color = activebackground or bg or "#cc4ecb"
        self._handle_color = self._fill_color
        self._state = "normal"
        self._dragging = False

        init_val = variable.get() if variable is not None else self._from
        self._value = min(max(float(init_val), self._from), self._to)

        self.bind("<Configure>", lambda e: self._redraw())
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)

        if variable is not None:
            self._var_trace = variable.trace_add("write", self._on_var_write)

        self.after(0, self._redraw)

    # -- значение ------------------------------------------------
    def _snap(self, val):
        val = min(max(val, self._from), self._to)
        if self._resolution and self._resolution > 0:
            steps = round((val - self._from) / self._resolution)
            val = self._from + steps * self._resolution
            val = min(max(val, self._from), self._to)
        return val

    def get(self):
        return self._value

    def set(self, val):
        self._value = self._snap(float(val))
        if self._var is not None:
            try:
                self._var.set(self._value)
            except Exception:
                pass
        self._redraw()

    def _on_var_write(self, *_):
        if self._var is None:
            return
        try:
            v = float(self._var.get())
        except Exception:
            return
        if abs(v - self._value) > 1e-9:
            self._value = self._snap(v)
            self._redraw()

    def config(self, **kw):
        if "state" in kw:
            self._state = kw.pop("state")
        if kw:
            super().config(**kw)
        self._redraw()

    configure = config

    # -- взаимодействие -------------------------------------------
    def _edge_pad(self, w):
        # Отступ = максимум из фиксированного минимума и доли ширины --
        # на узких слайдерах фикс. пиксели достаточны, на широких (во всё
        # окно настроек) масштабируется вместе с шириной, чтобы зазор был
        # всегда явно заметен, а не "терялся" на большом экране.
        return max(self.HANDLE_R + self.EDGE_PAD, int(w * 0.025))

    def _x_to_value(self, x):
        w = max(self.winfo_width(), 1)
        pad = self._edge_pad(w)
        usable = max(w - pad * 2, 1)
        frac = (x - pad) / usable
        frac = min(max(frac, 0.0), 1.0)
        return self._from + frac * (self._to - self._from)

    def _on_press(self, event):
        if self._state == "disabled":
            return
        self._dragging = True
        self.set(self._x_to_value(event.x))
        if self._command:
            self._command(self._value)

    def _on_drag(self, event):
        if self._state == "disabled" or not self._dragging:
            return
        self.set(self._x_to_value(event.x))
        if self._command:
            self._command(self._value)

    def _on_release(self, event):
        self._dragging = False

    # -- отрисовка ---------------------------------------------------
    def _redraw(self):
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w <= 1 or h <= 1:
            return
        pad = self._edge_pad(w)
        cy = h / 2
        x0, x1 = pad, w - pad

        frac = 0.0
        if self._to > self._from:
            frac = (self._value - self._from) / (self._to - self._from)
        frac = min(max(frac, 0.0), 1.0)
        handle_x = x0 + frac * (x1 - x0)

        th = self.TRACK_H / 2
        track_color = self._track_color
        fill_color = self._fill_color if self._state != "disabled" else self._track_color

        # Жёлоб (фон) -- закруглённая капсула
        self._round_capsule(x0, cy - th, x1, cy + th, th, track_color)
        # Заполненная часть слева от бегунка
        if handle_x > x0:
            self._round_capsule(x0, cy - th, handle_x, cy + th, th, fill_color)
        # Бегунок -- маленький кружок
        hr = self.HANDLE_R if self._state != "disabled" else self.HANDLE_R - 1
        handle_color = self._handle_color if self._state != "disabled" else self._track_color
        self.create_oval(
            handle_x - hr, cy - hr, handle_x + hr, cy + hr,
            fill=handle_color, outline="", width=0,
        )

    def _round_capsule(self, x0, y0, x1, y1, r, color):
        if x1 - x0 < 2 * r:
            x1 = x0 + 2 * r
        self.create_oval(x0, y0, x0 + 2 * r, y1, fill=color, outline="")
        self.create_oval(x1 - 2 * r, y0, x1, y1, fill=color, outline="")
        self.create_rectangle(x0 + r, y0, x1 - r, y1, fill=color, outline="")



class Launcher:
    def __init__(self, root):
        self.root = root
        self.process = None
        self.running = False
        self._stopping = False
        self._settings_win = None       # единое окно настроек (Контекст/Память/Иконка/Модель)
        self._settings_panels = {}      # name -> Frame, для переключения разделов без пересоздания
        self._settings_section_btns = {}
        self._settings_win_geometry = None  # запомненная позиция/размер окна настроек -- чтобы не прыгало при пересборке темы
        self._model_card_imgtks = []    # держим ссылки на PhotoImage аватарок карточек моделей
        self._header_avatar_imgtk = None  # аватарка активной модели в шапке окна
        self._build_ui()
        self._sync_active_model_label()
        self._apply_window_icon()
        _apply_dark_titlebar(self.root)
 
 
    def _get_dpi_scale(self) -> float:
        """Возвращает коэффициент масштаба экрана (1.0 = 100%, 1.5 = 150% и т.д.).
        Используется чтобы размеры окон в пикселях не "сжимались" визуально
        после включения per-monitor DPI awareness."""
        if sys.platform == "win32":
            try:
                import ctypes
                hwnd = self.root.winfo_id()
                dpi = ctypes.windll.user32.GetDpiForWindow(hwnd)
                return dpi / 96.0  # 96 DPI = масштаб 100% в Windows
            except Exception:
                pass
        return 1.0

    def _build_ui(self):
        self.root.title("Голосовой ассистент")
        self.root.configure(bg=BG)

        # Размеры окна (860x640 и т.д.) подобраны для DPI 96 (масштаб 100%).
        # Теперь, когда включён per-monitor DPI awareness, окно рендерится
        # в реальных физических пикселях -- на экране с масштабом 125%/150%
        # оно стало бы заметно меньше на вид, чем было раньше. Поэтому
        # подгоняем размер под реальный DPI, чтобы окно занимало
        # физически столько же места на экране, сколько и раньше.
        scale = self._get_dpi_scale()
        win_w, win_h = int(870 * scale), int(790 * scale)
        min_w, min_h = int(640 * scale), int(480 * scale)
        self.root.geometry(f"{win_w}x{win_h}")
        self.root.minsize(min_w, min_h)
 
        # -- Заголовок --------------------------------------
        header = tk.Frame(self.root, bg=BG, pady=14)
        header.pack(fill="x", padx=20)
 
        HEADER_AVATAR_SIZE = theme.get_header_avatar_size()
        self.header_avatar_canvas = tk.Canvas(
            header, width=HEADER_AVATAR_SIZE, height=HEADER_AVATAR_SIZE,
            bg=BG, highlightthickness=0,
        )
        self.header_avatar_canvas.pack(side="left")
        self._header_avatar_size = HEADER_AVATAR_SIZE
        self._ready = False  # готовность ассистента (STT/TTS/LLM загружены и слушает) -- не то же самое, что "процесс запущен"
        self._refresh_header_avatar()
        tk.Label(header, text="  Голосовой ассистент", font=FONT_BIG, fg=TEXT, bg=BG).pack(side="left")
        self.header_model_label = tk.Label(header, text="", font=FONT_SMALL, fg=ACCENT, bg=BG)
        self.header_model_label.pack(side="left", padx=(8, 0))
 
        tk.Frame(self.root, bg=BORDER, height=1).pack(fill="x", padx=20)
 
        # -- Тело -------------------------------------------
        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=20, pady=12)
 
        # Левая -- лог
        left = tk.Frame(body, bg=BG)
        left.pack(side="left", fill="both", expand=True)
        # Запрещаем left "просить" у body ширину по размеру своего контента --
        # иначе при переключении вкладок Журнал/Чат (tk.Text с дефолтным
        # width=80 символов против пустого Canvas без width) left каждый раз
        # пересчитывает требуемую ширину и наезжает на фиксированную правую
        # панель. Ширина left должна определяться ТОЛЬКО pack()/expand
        # относительно body, а не contents.
        left.pack_propagate(False)
 
        # -- Вкладки: Журнал / Чат ---------------------------
        # Журнал -- технический лог как был (STT, статусы, ошибки, токены).
        # Чат -- только реплики диалога в виде пузырей сообщений + лёгкие
        # статусные вставки ("Слушаю...", "Думаю...", "Говорю..."). Контент
        # реплик не меняется -- это просто другое отображение тех же событий,
        # которые уже разбираются построчно в _parse_line().
        tabs_row = tk.Frame(left, bg=BG)
        tabs_row.pack(fill="x", pady=(0, 4))

        self._left_tab_btns = {}

        def select_left_tab(name):
            self._left_tab = name
            for key, btn in self._left_tab_btns.items():
                btn.config(fg=TEXT if key == name else TEXT_DIM)
            if name == "log":
                self.chat_frame.pack_forget()
                self.log_frame.pack(fill="both", expand=True)
            else:
                self.log_frame.pack_forget()
                self.chat_frame.pack(fill="both", expand=True)
                # Синхронизируем ширину чата сразу, не дожидаясь следующего
                # <Configure> -- иначе на один кадр виден "скачок" ширины
                # левой колонки (canvas ещё держит старый/дефолтный размер).
                self.chat_frame.update_idletasks()
                w = self.chat_canvas.winfo_width()
                if w > 1:
                    self.chat_canvas.itemconfig(self._chat_inner_window, width=w)

        log_tab_btn = tk.Button(
            tabs_row, text="ЖУРНАЛ", font=("Segoe UI", 8, "bold"),
            fg=TEXT, bg=BG, activebackground=BG, activeforeground=TEXT,
            relief="flat", bd=0, padx=0, pady=0, cursor="hand2",
            command=lambda: select_left_tab("log"),
        )
        log_tab_btn.pack(side="left")
        self._left_tab_btns["log"] = log_tab_btn

        tk.Label(tabs_row, text="   |   ", font=("Segoe UI", 8, "bold"),
                 fg=TEXT_DIM, bg=BG).pack(side="left")

        chat_tab_btn = tk.Button(
            tabs_row, text="ЧАТ", font=("Segoe UI", 8, "bold"),
            fg=TEXT_DIM, bg=BG, activebackground=BG, activeforeground=TEXT,
            relief="flat", bd=0, padx=0, pady=0, cursor="hand2",
            command=lambda: select_left_tab("chat"),
        )
        chat_tab_btn.pack(side="left")
        self._left_tab_btns["chat"] = chat_tab_btn

        self._select_left_tab = select_left_tab
        self._left_tab = "log"

        log_frame = tk.Frame(left, bg=BG_MSG)
        self.log_frame = log_frame
        log_frame.pack(fill="both", expand=True)
 
        style = ttk.Style()
        style.theme_use("default")
        style.layout("Dark.Vertical.TScrollbar", [
            ("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
                ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"}),
            ]}),
        ])
        style.configure(
            "Dark.Vertical.TScrollbar",
            background=ACCENT_DIM,
            troughcolor=BG_MSG,
            bordercolor=BG_MSG,
            relief="flat",
            borderwidth=0,
            width=8,
        )
        style.map(
            "Dark.Vertical.TScrollbar",
            background=[("active", ACCENT), ("pressed", ACCENT)],
        )

 
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", style="Dark.Vertical.TScrollbar")
        log_scroll.pack(side="right", fill="y")
 
        self.log = tk.Text(
            log_frame, bg=BG_MSG, fg=TEXT, insertbackground=TEXT,
            font=FONT_MONO, relief="flat", bd=0, wrap="word",
            state="disabled", selectbackground=ACCENT_DIM,
            yscrollcommand=log_scroll.set,
        )
        self.log.pack(side="left", fill="both", expand=True)
        log_scroll.config(command=self.log.yview)
 
        self.log.tag_config("you",    foreground="#7eb8f7")
        self.log.tag_config("ai",     foreground=GREEN)
        self.log.tag_config("vision", foreground="#f7a35c")
        self.log.tag_config("system", foreground=TEXT_DIM)
        self.log.tag_config("error",  foreground=RED)
        self.log.tag_config("time",   foreground="#333333")
        self.log.tag_config("tokens", foreground=YELLOW)

        # -- Вкладка ЧАТ: пузыри сообщений (мессенджер-стиль) ---
        # Это ТОЛЬКО другое отображение -- тот же контент реплик, что и
        # в журнале ([Ты]/[Ассистент]), просто визуально как переписка,
        # плюс компактные статусные вставки между репликами ("Слушаю...",
        # "Думаю...", "Говорю..."). Никакой новой логики диалога здесь нет.
        chat_frame = tk.Frame(left, bg=BG_MSG)
        self.chat_frame = chat_frame
        # chat_frame ещё не упакован -- по умолчанию активна вкладка "Журнал".
        # select_left_tab() выше переключает pack()/pack_forget() между
        # log_frame и chat_frame, как и панели в окне настроек.

        chat_scroll = ttk.Scrollbar(chat_frame, orient="vertical", style="Dark.Vertical.TScrollbar")
        chat_scroll.pack(side="right", fill="y")

        self.chat_canvas = tk.Canvas(
            chat_frame, bg=BG_MSG, highlightthickness=0,
            height=300,
            yscrollcommand=lambda f, l: (_clamp_scroll(f, l, self.chat_canvas), chat_scroll.set(f, l)),
        )
        self.chat_canvas.pack(side="left", fill="both", expand=True)
        chat_scroll.config(command=self.chat_canvas.yview)

        # Внутренний фрейм с пузырями -- живёт внутри Canvas через create_window,
        # это стандартный tkinter-паттерн для скроллируемого списка виджетов
        # (tk.Text для подобного не подходит -- нужны отдельные "облачка"
        # разной ширины, выровненные то влево, то вправо).
        self.chat_inner = tk.Frame(self.chat_canvas, bg=BG_MSG)
        self._chat_inner_window = self.chat_canvas.create_window(
            (0, 0), window=self.chat_inner, anchor="nw",
        )

        def _on_chat_inner_configure(event):
            self.chat_canvas.configure(scrollregion=self.chat_canvas.bbox("all"))

        def _on_chat_canvas_configure(event):
            # Растягиваем внутренний фрейм по ширине канваса, чтобы пузыри
            # переносились по словам в нужных границах, а не обрезались.
            self.chat_canvas.itemconfig(self._chat_inner_window, width=event.width)

        self.chat_inner.bind("<Configure>", _on_chat_inner_configure)
        self.chat_canvas.bind("<Configure>", _on_chat_canvas_configure)

        def _chat_mousewheel(event):
            self.chat_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            if self.chat_canvas.yview()[0] < 0.0: self.chat_canvas.yview_moveto(0.0)

        self.chat_canvas.bind("<Enter>", lambda e: self.chat_canvas.bind_all("<MouseWheel>", _chat_mousewheel))
        self.chat_canvas.bind("<Leave>", lambda e: self.chat_canvas.unbind_all("<MouseWheel>"))

        self._chat_bubble_widgets = []  # держим ссылки, чтобы можно было очистить чат
        self._chat_messages = []        # (kind, text, who) -- для перерисовки при смене радиуса/масштаба
 
        # Правая -- инфо панель
        right = tk.Frame(body, bg=BG, width=230)
        right.pack(side="right", fill="y", padx=(14, 0))
        right.pack_propagate(False)
 
        # Последний ответ
        tk.Label(right, text="ПОСЛЕДНИЙ ОТВЕТ", font=("Segoe UI", 8, "bold"),
                 fg=TEXT_DIM, bg=BG).pack(anchor="w", pady=(0, 4))
 
        self.last_answer = tk.Text(
            right, bg=BG_PANEL, fg=GREEN,
            font=("Segoe UI", 10), relief="flat", bd=0,
            wrap="word", state="disabled", cursor="arrow",
            selectbackground=ACCENT_DIM, height=10,
        )
        self.last_answer.pack(fill="x")
 
        # Токены
        tk.Frame(right, bg=BORDER, height=1).pack(fill="x", pady=10)
        tk.Label(right, text="ТОКЕНЫ", font=("Segoe UI", 8, "bold"),
                 fg=TEXT_DIM, bg=BG).pack(anchor="w", pady=(0, 6))
 
        stats_frame = tk.Frame(right, bg=BG_PANEL, padx=12, pady=10)
        stats_frame.pack(fill="x")
 
        def stat_row(label, color):
            row = tk.Frame(stats_frame, bg=BG_PANEL)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=label, font=FONT_SMALL, fg=TEXT_DIM,
                     bg=BG_PANEL, width=10, anchor="w").pack(side="left")
            val = tk.Label(row, text="--", font=FONT_STAT, fg=color,
                           bg=BG_PANEL, anchor="e")
            val.pack(side="right")
            return val
 
        self.tok_input   = stat_row("Вход",   CYAN)
        self.tok_output  = stat_row("Выход",  GREEN)
        self.tok_total   = stat_row("Итого",  YELLOW)
        self.tok_max     = stat_row("Макс",   TEXT_DIM)
 
        tk.Frame(right, bg=BORDER, height=1).pack(fill="x", pady=10)
 
        # Сессия
        tk.Label(right, text="СЕССИЯ", font=("Segoe UI", 8, "bold"),
                 fg=TEXT_DIM, bg=BG).pack(anchor="w", pady=(0, 6))
 
        sess_frame = tk.Frame(right, bg=BG_PANEL, padx=12, pady=10)
        sess_frame.pack(fill="x")
 
        def sess_row(label, color):
            row = tk.Frame(sess_frame, bg=BG_PANEL)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=label, font=FONT_SMALL, fg=TEXT_DIM,
                     bg=BG_PANEL, width=10, anchor="w").pack(side="left")
            val = tk.Label(row, text="0", font=FONT_STAT, fg=color, bg=BG_PANEL, anchor="e")
            val.pack(side="right")
            return val
 
        self.sess_total   = sess_row("Всего токенов", YELLOW)
        self.sess_msgs    = sess_row("Сообщений",     TEXT)
 
        self._session_tokens = 0
        self._session_msgs   = 0

        # -- Расходы (только для облачных моделей: DeepSeek и т.п.) -----
        # Локальные движки (Ollama, LM Studio) бесплатны -- блок скрыт для них.
        self.cost_separator = tk.Frame(right, bg=BORDER, height=1)
        self.cost_header = tk.Label(right, text="РАСХОДЫ", font=("Segoe UI", 8, "bold"),
                                     fg=TEXT_DIM, bg=BG)

        self.cost_frame = tk.Frame(right, bg=BG_PANEL, padx=12, pady=10)

        def cost_row(label, color):
            row = tk.Frame(self.cost_frame, bg=BG_PANEL)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=label, font=FONT_SMALL, fg=TEXT_DIM,
                     bg=BG_PANEL, width=10, anchor="w").pack(side="left")
            val = tk.Label(row, text="--", font=FONT_STAT, fg=color, bg=BG_PANEL, anchor="e")
            val.pack(side="right")
            return val

        self.cost_spent     = cost_row("Потрачено", YELLOW)
        self.cost_remaining = cost_row("Остаток",    GREEN)

        self._cost_visible = False
        self._update_cost_visibility()
        self._refresh_cost_display()
 
        # -- Кнопки -----------------------------------------
        tk.Frame(self.root, bg=BORDER, height=1).pack(fill="x", padx=20)
 
        footer = tk.Frame(self.root, bg=BG, pady=14)
        footer.pack(fill="x", padx=20)
 
        self.btn_start = tk.Button(
            footer, text="Запустить", font=FONT_UI,
            fg="#fff", bg=ACCENT, activebackground=ACCENT_DIM, activeforeground="#fff",
            relief="flat", bd=0, padx=22, pady=8, cursor="hand2", command=self.start,
        )
        self.btn_start.pack(side="left", padx=(0, 8))
 
        self.btn_stop = tk.Button(
            footer, text="Остановить", font=FONT_UI,
            fg=TEXT, bg=BG_PANEL, activebackground=BORDER, activeforeground=TEXT,
            relief="flat", bd=0, padx=22, pady=8, cursor="hand2",
            command=self.stop, state="disabled",
        )
        self.btn_stop.pack(side="left", padx=(0, 8))
 
        self.btn_clear = tk.Button(
            footer, text="Очистить", font=FONT_UI,
            fg=TEXT_DIM, bg=BG, activebackground=BG_PANEL, activeforeground=TEXT,
            relief="flat", bd=0, padx=16, pady=8, cursor="hand2", command=self.clear_log,
        )
        self.btn_clear.pack(side="left")
 
        self.btn_settings = tk.Button(
            footer, text="Настройки", font=FONT_UI,
            fg=ACCENT, bg=BG, activebackground=BG_PANEL, activeforeground=ACCENT,
            relief="flat", bd=0, padx=16, pady=8, cursor="hand2", command=self.open_settings,
        )
        self.btn_settings.pack(side="left")
 
        tk.Label(footer, text=f"v{theme.APP_VERSION}", font=FONT_SMALL, fg=TEXT_DIM, bg=BG).pack(side="right")
 
        # Инициализируем макс токены и активную модель из профилей
        try:
            import model_profiles as mp
            active = mp.get_active_profile()
            self.tok_max.config(text=str(active.get("max_tokens", 500)))
        except Exception:
            self.tok_max.config(text="500")

        self._update_cost_visibility()
        self._refresh_cost_display()
 
    # -- Единое окно настроек -------------------------------------
 
    def _save_settings_win_geometry(self):
        """Запоминает текущую позицию и размер окна настроек, чтобы при
        следующем открытии (включая пересборку из-за смены темы) оно
        появилось там же, а не прыгало на дефолтное место."""
        if self._settings_win is not None:
            try:
                if self._settings_win.winfo_exists():
                    self._settings_win_geometry = self._settings_win.geometry()
            except Exception:
                pass

    def open_settings(self, section: str = "about"):
        """Открывает (или поднимает) окно настроек и переключает на нужный раздел.
        Разделы: persona (с под-вкладками промпт/контекст/память) / model /
        vision / theme (Кастомизация: иконка + цвет + чат) / cost / about --
        переключаются мгновенно, без пересоздания
        окна (все панели строятся один раз). Раздел cost существует только если
        есть хотя бы один облачный профиль. По умолчанию (без явного раздела)
        открывается "О программе"."""
        if section not in self._settings_panels and section == "cost" and not self._has_cloud_profile():
            section = "about"
        if self._settings_win is not None and self._settings_win.winfo_exists():
            self._settings_win.lift()
            self._settings_win.focus_force()
            if section not in self._settings_panels:
                section = "about"
            self._show_settings_section(section)
            return
 
        win = tk.Toplevel(self.root)
        self._settings_win = win
        self._settings_panels = {}
        self._settings_section_btns = {}

        def on_close():
            self._save_settings_win_geometry()
            self._settings_win = None
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", on_close)
        win.title("Настройки")
        win.configure(bg=BG)
        scale = self._get_dpi_scale()
        min_w, min_h = int(900 * scale), int(660 * scale)
        if self._settings_win_geometry:
            win.geometry(self._settings_win_geometry)
        else:
            win.geometry(f"{int(1000 * scale)}x{int(720 * scale)}")
        win.minsize(min_w, min_h)
        # Если запомненная с прошлого раза геометрия меньше нового минимума
        # (например, окно сохранили совсем маленьким) -- подрезаем до
        # минимума сразу же, иначе нижние кнопки (Сохранить и т.п.) могут
        # оказаться за пределами видимой области.
        try:
            win.update_idletasks()
            cur_w = win.winfo_width()
            cur_h = win.winfo_height()
            if self._settings_win_geometry and (cur_w < min_w or cur_h < min_h):
                win.geometry(f"{max(cur_w, min_w)}x{max(cur_h, min_h)}")
        except Exception:
            pass
        win.transient(self.root)
        _apply_dark_titlebar(win)
        self._apply_window_icon(win)
 
        # -- Каркас: меню слева, контент справа --
        outer = tk.Frame(win, bg=BG)
        outer.pack(fill="both", expand=True)
 
        sidebar = tk.Frame(outer, bg=BG_PANEL, width=170)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
 
        tk.Label(
            sidebar, text="НАСТРОЙКИ", font=("Segoe UI", 8, "bold"),
            fg=TEXT_DIM, bg=BG_PANEL, anchor="w",
        ).pack(fill="x", padx=16, pady=(18, 10))
 
        content = tk.Frame(outer, bg=BG)
        content.pack(side="left", fill="both", expand=True)
 
        sections = [
            ("persona", "Личность", ACCENT),
            ("model",   "Модель", ACCENT),
            ("vision",  "Vision", ACCENT),
            ("theme",   "Кастомизация", ACCENT),
        ]
        if self._has_cloud_profile():
            sections.append(("cost", "Расходы", ACCENT))
        sections.append(("about", "О программе", ACCENT))

        def select_section(name):
            for key, btn in self._settings_section_btns.items():
                color = next(c for k, _, c in sections if k == key)
                btn.config(bg=ACCENT_DIM if key == name else BG_PANEL,
                           fg="#fff" if key == name else color)
            for key, panel in self._settings_panels.items():
                if key == name:
                    panel.pack(fill="both", expand=True)
                else:
                    panel.pack_forget()

        for key, label, color in sections:
            b = tk.Button(
                sidebar, text=label, font=FONT_UI, anchor="w",
                fg=color, bg=BG_PANEL, activebackground=ACCENT_DIM, activeforeground="#fff",
                relief="flat", bd=0, padx=16, pady=10, cursor="hand2",
                command=lambda k=key: select_section(k),
            )
            b.pack(fill="x")
            self._settings_section_btns[key] = b

        # -- Строим панели каждого раздела --
        self._settings_panels["persona"] = self._build_persona_panel(content)
        self._settings_panels["model"]   = self._build_model_panel(content)
        self._settings_panels["vision"]  = self._build_vision_panel(content)
        self._settings_panels["theme"]   = self._build_theme_panel(content)
        if self._has_cloud_profile():
            self._settings_panels["cost"] = self._build_cost_panel(content)
        self._settings_panels["about"]   = self._build_about_panel(content)

        self._show_settings_section = select_section
        if section not in self._settings_panels:
            section = "about"
        select_section(section)
    # -- Раздел: Личность (system_prompt активного профиля) ----

    # -- Раздел: Личность (Промпт / Контекст / Память) --------
    # Все три темы объединены в один пункт меню "Личность", потому что
    # вместе они и образуют то, что реально получает нейросеть на входе
    # (system_prompt активного профиля + context.txt + history_context.txt).
    # Под-вкладки переключаются так же, как и сами разделы верхнего меню --
    # pack()/pack_forget() уже построенных панелей, без пересоздания.

    def _build_persona_panel(self, parent):
        panel = tk.Frame(parent, bg=BG)

        header = tk.Frame(panel, bg=BG, pady=16)
        header.pack(fill="x", padx=20)
        tk.Label(header, text="Личность", font=FONT_BIG, fg=TEXT, bg=BG).pack(side="left")

        sub_tabs_row = tk.Frame(panel, bg=BG)
        sub_tabs_row.pack(fill="x", padx=20, pady=(0, 12))

        sub_content = tk.Frame(panel, bg=BG)
        sub_content.pack(fill="both", expand=True)

        sub_panels = {}
        sub_btns = {}
        sub_defs = [
            ("prompt",  "Промпт"),
            ("context", "Контекст"),
            ("history", "Память"),
        ]

        def select_sub(name):
            for key, btn in sub_btns.items():
                btn.config(fg=TEXT if key == name else TEXT_DIM,
                           bg=ACCENT_DIM if key == name else BG)
            for key, p in sub_panels.items():
                if key == name:
                    p.pack(fill="both", expand=True)
                else:
                    p.pack_forget()

        for key, label in sub_defs:
            b = tk.Button(
                sub_tabs_row, text=label, font=FONT_UI,
                fg=TEXT_DIM, bg=BG, activebackground=ACCENT_DIM, activeforeground="#fff",
                relief="flat", bd=0, padx=14, pady=6, cursor="hand2",
                command=lambda k=key: select_sub(k),
            )
            b.pack(side="left", padx=(0, 6))
            sub_btns[key] = b

        sub_panels["prompt"]  = self._build_persona_prompt_subpanel(sub_content)
        sub_panels["context"] = self._build_context_subpanel(sub_content)
        sub_panels["history"] = self._build_history_subpanel(sub_content)

        select_sub("prompt")

        return panel

    def _build_persona_prompt_subpanel(self, parent):
        """Редактирование system_prompt активного в данный момент профиля модели.
        Личность ассистента (см. историю изменений #10/#16) -- это system_prompt
        ВНУТРИ профиля, а не глобальная константа, поэтому этот раздел читает
        и сохраняет напрямую в models.json через model_profiles.update_profile()."""
        import model_profiles as mp_module

        panel = tk.Frame(parent, bg=BG)

        active_profile = mp_module.get_active_profile()
        active_name_label = tk.Label(
            panel,
            text=f"Редактируется характер активного профиля: {active_profile['name']}",
            font=FONT_SMALL, fg=ACCENT, bg=BG, wraplength=520, justify="left",
        )
        active_name_label.pack(fill="x", padx=20, pady=(0, 2))

        tk.Label(
            panel,
            text="Это системный промпт -- кем ассистент себя считает, как разговаривает, "
                 "какой у него характер. У каждого профиля модели (раздел \"Модель\") -- свой.",
            font=FONT_SMALL, fg=TEXT_DIM, bg=BG, wraplength=520, justify="left",
        ).pack(fill="x", padx=20, pady=(0, 10))

        body = tk.Frame(panel, bg=BG_MSG)
        body.pack(fill="both", expand=True, padx=20)

        style = ttk.Style()
        style.layout("Persona.Vertical.TScrollbar", [
            ("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
                ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"}),
            ]}),
        ])
        style.configure(
            "Persona.Vertical.TScrollbar",
            background=ACCENT_DIM, troughcolor=BG_MSG,
            bordercolor=BG_MSG, relief="flat", borderwidth=0, width=8,
        )
        style.map("Persona.Vertical.TScrollbar", background=[("active", ACCENT), ("pressed", ACCENT)])

        scroll = ttk.Scrollbar(body, orient="vertical", style="Persona.Vertical.TScrollbar")
        scroll.pack(side="right", fill="y")

        text_box = tk.Text(
            body, bg=BG_MSG, fg=TEXT, insertbackground=TEXT,
            font=FONT_MONO, relief="flat", bd=0, wrap="word",
            selectbackground=ACCENT_DIM, yscrollcommand=scroll.set,
        )
        text_box.pack(side="left", fill="both", expand=True)
        scroll.config(command=text_box.yview)
        text_box.insert("1.0", active_profile.get("system_prompt", ""))

        status_label = tk.Label(panel, text="", font=FONT_SMALL, fg=GREEN, bg=BG)
        status_label.pack(anchor="w", padx=20, pady=(6, 0))

        def save():
            try:
                current_active = mp_module.get_active_profile()
                new_prompt = text_box.get("1.0", "end-1c")
                mp_module.update_profile(current_active["id"], {"system_prompt": new_prompt})
                status_label.config(text="Сохранено", fg=GREEN)
                self._log(f"[p] Личность профиля «{current_active['name']}» обновлена", "system")
            except Exception as e:
                status_label.config(text=f"Ошибка сохранения: {e}", fg=RED)

        def reset_to_default():
            text_box.delete("1.0", "end")
            text_box.insert("1.0", mp_module.DEFAULT_SYSTEM_PROMPT)

        footer = tk.Frame(panel, bg=BG, pady=14)
        footer.pack(fill="x", padx=20)

        tk.Button(
            footer, text="Сохранить", font=FONT_UI,
            fg="#fff", bg=ACCENT, activebackground=ACCENT_DIM, activeforeground="#fff",
            relief="flat", bd=0, padx=18, pady=6, cursor="hand2", command=save,
        ).pack(side="left")

        tk.Button(
            footer, text="Сбросить на стандартный", font=FONT_UI,
            fg=TEXT_DIM, bg=BG, activebackground=BG, activeforeground=TEXT,
            relief="flat", bd=0, padx=12, pady=6, cursor="hand2", command=reset_to_default,
        ).pack(side="left", padx=(10, 0))

        if self.running:
            tk.Label(
                footer, text="Изменения применятся после перезапуска ассистента",
                font=FONT_SMALL, fg=YELLOW, bg=BG,
            ).pack(side="right")

        return panel

    def _build_context_subpanel(self, parent):
        path = self._get_context_path()

        if not os.path.exists(path):
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(
                        "# Этот файл читается ассистентом при каждом запуске\n"
                        "# Строки с # -- комментарии, игнорируются\n\n"
                        "## Обо мне\n"
                        "Меня зовут [впиши имя]\n\n"
                        "## Как должен вести себя ассистент\n"
                        "[например: отвечай кратко, на \"ты\"]\n"
                    )
            except Exception as e:
                self._log(f"[ERR] Не удалось создать context.txt: {e}", "error")

        try:
            with open(path, "r", encoding="utf-8") as f:
                current_text = f.read()
        except Exception:
            current_text = ""

        panel = tk.Frame(parent, bg=BG)

        tk.Label(
            panel,
            text="Эта информация добавляется к каждому диалогу. Опиши себя и как должен вести себя ассистент.",
            font=FONT_SMALL, fg=TEXT_DIM, bg=BG, wraplength=520, justify="left",
        ).pack(fill="x", padx=20, pady=(0, 10))

        body = tk.Frame(panel, bg=BG_MSG)
        body.pack(fill="both", expand=True, padx=20)

        style = ttk.Style()
        style.layout("Ctx.Vertical.TScrollbar", [
            ("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
                ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"}),
            ]}),
        ])
        style.configure(
            "Ctx.Vertical.TScrollbar",
            background=ACCENT_DIM, troughcolor=BG_MSG,
            bordercolor=BG_MSG, relief="flat", borderwidth=0, width=8,
        )
        style.map("Ctx.Vertical.TScrollbar", background=[("active", ACCENT), ("pressed", ACCENT)])

        scroll = ttk.Scrollbar(body, orient="vertical", style="Ctx.Vertical.TScrollbar")
        scroll.pack(side="right", fill="y")

        text_box = tk.Text(
            body, bg=BG_MSG, fg=TEXT, insertbackground=TEXT,
            font=FONT_MONO, relief="flat", bd=0, wrap="word",
            selectbackground=ACCENT_DIM, yscrollcommand=scroll.set,
        )
        text_box.pack(side="left", fill="both", expand=True)
        scroll.config(command=text_box.yview)
        text_box.insert("1.0", current_text)

        status_label = tk.Label(panel, text="", font=FONT_SMALL, fg=GREEN, bg=BG)
        status_label.pack(anchor="w", padx=20, pady=(6, 0))

        def save():
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text_box.get("1.0", "end-1c"))
                status_label.config(text="Сохранено", fg=GREEN)
                self._log("[i] Контекст обновлён", "system")
            except Exception as e:
                status_label.config(text=f"Ошибка сохранения: {e}", fg=RED)

        footer = tk.Frame(panel, bg=BG, pady=14)
        footer.pack(fill="x", padx=20)

        tk.Button(
            footer, text="Сохранить", font=FONT_UI,
            fg="#fff", bg=ACCENT, activebackground=ACCENT_DIM, activeforeground="#fff",
            relief="flat", bd=0, padx=18, pady=6, cursor="hand2", command=save,
        ).pack(side="left")

        if self.running:
            tk.Label(
                footer, text="Изменения применятся после перезапуска ассистента",
                font=FONT_SMALL, fg=YELLOW, bg=BG,
            ).pack(side="right")

        return panel

    def _build_history_subpanel(self, parent):
        path = self._get_history_context_path()

        if not os.path.exists(path):
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write("")
            except Exception as e:
                self._log(f"[ERR] Не удалось создать history_context.txt: {e}", "error")

        try:
            with open(path, "r", encoding="utf-8") as f:
                current_text = f.read()
        except Exception:
            current_text = ""

        panel = tk.Frame(parent, bg=BG)

        tk.Label(
            panel,
            text="Сюда ассистент сам дописывает краткое саммари после каждого разговора "
                 "(команда \"выход\"/\"пока\"). Можешь отредактировать или почистить вручную.",
            font=FONT_SMALL, fg=TEXT_DIM, bg=BG, wraplength=520, justify="left",
        ).pack(fill="x", padx=20, pady=(0, 10))

        body = tk.Frame(panel, bg=BG_MSG)
        body.pack(fill="both", expand=True, padx=20)

        style = ttk.Style()
        style.layout("Hist.Vertical.TScrollbar", [
            ("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
                ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"}),
            ]}),
        ])
        style.configure(
            "Hist.Vertical.TScrollbar",
            background=ACCENT_DIM, troughcolor=BG_MSG,
            bordercolor=BG_MSG, relief="flat", borderwidth=0, width=8,
        )
        style.map("Hist.Vertical.TScrollbar", background=[("active", ACCENT), ("pressed", ACCENT)])

        scroll = ttk.Scrollbar(body, orient="vertical", style="Hist.Vertical.TScrollbar")
        scroll.pack(side="right", fill="y")

        text_box = tk.Text(
            body, bg=BG_MSG, fg=TEXT, insertbackground=TEXT,
            font=FONT_MONO, relief="flat", bd=0, wrap="word",
            selectbackground=ACCENT_DIM, yscrollcommand=scroll.set,
        )
        text_box.pack(side="left", fill="both", expand=True)
        scroll.config(command=text_box.yview)
        text_box.insert("1.0", current_text)
        text_box.see("end")

        status_label = tk.Label(panel, text="", font=FONT_SMALL, fg=GREEN, bg=BG)
        status_label.pack(anchor="w", padx=20, pady=(6, 0))

        def save():
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text_box.get("1.0", "end-1c"))
                status_label.config(text="Сохранено", fg=GREEN)
                self._log("[~] Память о разговорах обновлена", "system")
            except Exception as e:
                status_label.config(text=f"Ошибка сохранения: {e}", fg=RED)

        def clear_all():
            text_box.delete("1.0", "end")

        footer = tk.Frame(panel, bg=BG, pady=14)
        footer.pack(fill="x", padx=20)

        tk.Button(
            footer, text="Сохранить", font=FONT_UI,
            fg="#fff", bg=ACCENT, activebackground=ACCENT_DIM, activeforeground="#fff",
            relief="flat", bd=0, padx=18, pady=6, cursor="hand2", command=save,
        ).pack(side="left", padx=(0, 8))

        tk.Button(
            footer, text="Очистить всё", font=FONT_UI,
            fg=RED, bg=BG, activebackground=BG_PANEL, activeforeground=RED,
            relief="flat", bd=0, padx=14, pady=6, cursor="hand2", command=clear_all,
        ).pack(side="left")

        return panel

    # -- Раздел: Модель -------------------------------------------
 
    def _build_model_panel(self, parent):
        import model_profiles as mp

        panel = tk.Frame(parent, bg=BG)

        # Горизонтальный split: левая колонка -- список, правая -- форма редактирования
        body_split = tk.Frame(panel, bg=BG)
        body_split.pack(fill="both", expand=True)

        # ---- ЛЕВАЯ КОЛОНКА: список профилей ----
        left_col = tk.Frame(body_split, bg=BG, width=360)
        left_col.pack(side="left", fill="y")
        left_col.pack_propagate(False)

        header = tk.Frame(left_col, bg=BG, pady=16)
        header.pack(fill="x", padx=16)
        tk.Label(header, text="Модели", font=FONT_BIG, fg=TEXT, bg=BG).pack(side="left")

        tk.Label(
            left_col,
            text="Облачные API и локальные движки.\nАктивный профиль помечен •",
            font=FONT_SMALL, fg=TEXT_DIM, bg=BG, wraplength=220, justify="left",
        ).pack(fill="x", padx=16, pady=(0, 8))

        list_outer = tk.Frame(left_col, bg=BG)
        list_outer.pack(fill="both", expand=True, padx=16)

        list_canvas = tk.Canvas(list_outer, bg=BG, highlightthickness=0)
        _mdl_style = ttk.Style()
        _mdl_style.layout("Mdl.Vertical.TScrollbar", [
            ("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
                ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"}),
            ]}),
        ])
        _mdl_style.configure(
            "Mdl.Vertical.TScrollbar",
            background=ACCENT_DIM, troughcolor=BG, bordercolor=BG,
            relief="flat", borderwidth=0, width=8,
        )
        _mdl_style.map("Mdl.Vertical.TScrollbar", background=[("active", ACCENT), ("pressed", ACCENT)])
        list_scroll = ttk.Scrollbar(list_outer, orient="vertical", command=list_canvas.yview, style="Mdl.Vertical.TScrollbar")
        list_frame = tk.Frame(list_canvas, bg=BG)
        list_frame.bind("<Configure>", lambda e: list_canvas.configure(scrollregion=list_canvas.bbox("all")))
        list_canvas_win = list_canvas.create_window((0, 0), window=list_frame, anchor="nw")
        list_canvas.configure(yscrollcommand=lambda f, l, c="list_canvas", s="list_scroll": (_clamp_scroll(f, l, list_canvas), list_scroll.set(f, l)))
        list_canvas.bind("<Configure>", lambda e: list_canvas.itemconfig(list_canvas_win, width=e.width))
        list_canvas.pack(side="left", fill="both", expand=True)
        list_scroll.pack(side="right", fill="y")

        def _lmw(event):
            list_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            if list_canvas.yview()[0] < 0.0: list_canvas.yview_moveto(0.0)
        list_canvas.bind("<Enter>", lambda e: list_canvas.bind_all("<MouseWheel>", _lmw))
        list_canvas.bind("<Leave>", lambda e: list_canvas.unbind_all("<MouseWheel>"))

        left_footer = tk.Frame(left_col, bg=BG, pady=10)
        left_footer.pack(fill="x", padx=16)

        # ---- РАЗДЕЛИТЕЛЬ ----
        tk.Frame(body_split, bg=BORDER, width=1).pack(side="left", fill="y")

        # ---- ПРАВАЯ КОЛОНКА: форма редактирования ----
        right_col = tk.Frame(body_split, bg=BG)
        right_col.pack(side="left", fill="both", expand=True)

        # Placeholder когда ничего не выбрано
        right_placeholder = tk.Frame(right_col, bg=BG)
        right_placeholder.place(relx=0.5, rely=0.5, anchor="center")
        tk.Label(
            right_placeholder, text="Выберите профиль\nдля редактирования",
            font=FONT_SMALL, fg=TEXT_DIM, bg=BG, justify="center",
        ).pack()

        right_form_container = [None]  # держим ссылку на текущую форму

        def open_inline_form(existing=None):
            """Рендерит форму профиля прямо в правой колонке."""
            # Убираем предыдущую форму
            if right_form_container[0]:
                right_form_container[0].destroy()
            right_placeholder.place_forget()

            form_outer = tk.Frame(right_col, bg=BG)
            form_outer.pack(fill="both", expand=True)
            right_form_container[0] = form_outer

            form_canvas = tk.Canvas(form_outer, bg=BG, highlightthickness=0)
            _fs = ttk.Style()
            _fs.layout("InlineForm.Vertical.TScrollbar", [
                ("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
                    ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"}),
                ]}),
            ])
            _fs.configure(
                "InlineForm.Vertical.TScrollbar",
                background=ACCENT_DIM, troughcolor=BG, bordercolor=BG,
                relief="flat", borderwidth=0, width=8,
            )
            _fs.map("InlineForm.Vertical.TScrollbar", background=[("active", ACCENT), ("pressed", ACCENT)])
            form_scroll = ttk.Scrollbar(form_outer, orient="vertical", command=form_canvas.yview, style="InlineForm.Vertical.TScrollbar")
            form_canvas.configure(yscrollcommand=lambda f, l, c="form_canvas", s="form_scroll": (_clamp_scroll(f, l, form_canvas), form_scroll.set(f, l)))
            form_canvas.pack(side="left", fill="both", expand=True)
            form_scroll.pack(side="right", fill="y")

            def _fmw(event):
                form_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
                if form_canvas.yview()[0] < 0.0: form_canvas.yview_moveto(0.0)
            form_canvas.bind("<Enter>", lambda e: form_canvas.bind_all("<MouseWheel>", _fmw))
            form_canvas.bind("<Leave>", lambda e: form_canvas.unbind_all("<MouseWheel>"))

            body = tk.Frame(form_canvas, bg=BG, padx=20, pady=16)
            body_win = form_canvas.create_window((0, 0), window=body, anchor="nw")
            body.bind("<Configure>", lambda e: form_canvas.configure(scrollregion=form_canvas.bbox("all")))
            form_canvas.bind("<Configure>", lambda e: form_canvas.itemconfig(body_win, width=e.width))

            # Заголовок формы
            form_title = "Новый профиль" if existing is None else f"Редактирование: {existing['name']}"
            tk.Label(body, text=form_title, font=FONT_BIG, fg=TEXT, bg=BG, anchor="w").pack(fill="x", pady=(0, 12))

            def field(label_text, default=""):
                tk.Label(body, text=label_text, font=FONT_SMALL, fg=TEXT_DIM, bg=BG, anchor="w").pack(fill="x", pady=(8, 2))
                entry = tk.Entry(
                    body, font=FONT_UI, bg=BG_MSG, fg=TEXT, insertbackground=TEXT,
                    relief="flat", bd=0, highlightthickness=1,
                    highlightbackground=BORDER, highlightcolor=ACCENT,
                )
                entry.pack(fill="x", ipady=5)
                entry.insert(0, default)
                return entry

            name_entry = field("Название", existing["name"] if existing else "")

            # Аватарка
            tk.Label(body, text="Аватарка", font=FONT_SMALL, fg=TEXT_DIM, bg=BG, anchor="w").pack(fill="x", pady=(8, 2))
            avatar_row = tk.Frame(body, bg=BG)
            avatar_row.pack(fill="x")

            AVATAR_PREVIEW_SIZE = 72
            existing_avatar_path = os.path.join(self._get_avatars_dir(), f"{existing['id']}.png") if existing else None
            avatar_state = {"src_path": None, "remove": False}

            avatar_canvas = tk.Canvas(avatar_row, width=AVATAR_PREVIEW_SIZE, height=AVATAR_PREVIEW_SIZE, bg=BG, highlightthickness=0)
            avatar_canvas.pack(side="left")
            self._form_avatar_imgtk = None

            avatar_error_label = tk.Label(avatar_row, text="", font=("Segoe UI", 8), fg=RED, bg=BG, anchor="w", justify="left", wraplength=260)

            def refresh_avatar_preview():
                avatar_canvas.delete("all")
                src = avatar_state["src_path"] if avatar_state["src_path"] else (None if avatar_state["remove"] else existing_avatar_path)
                round_img, err = _make_round_avatar(src, AVATAR_PREVIEW_SIZE, return_error=True)
                if round_img is not None:
                    from PIL import ImageTk
                    imgtk = ImageTk.PhotoImage(round_img)
                    self._form_avatar_imgtk = imgtk
                    avatar_canvas.create_image(0, 0, anchor="nw", image=imgtk)
                    avatar_error_label.config(text="")
                else:
                    avatar_canvas.create_oval(1, 1, AVATAR_PREVIEW_SIZE - 1, AVATAR_PREVIEW_SIZE - 1, outline=BORDER, fill=BG_PANEL)
                    avatar_canvas.create_text(AVATAR_PREVIEW_SIZE // 2, AVATAR_PREVIEW_SIZE // 2, text="?", fill=TEXT_DIM, font=("Segoe UI", 14))
                    avatar_error_label.config(text=f"Не удалось открыть: {err}" if src and err else "")

            def choose_avatar():
                src_path = filedialog.askopenfilename(title="Выбери аватарку для модели", filetypes=[("Изображения", "*.png *.jpg *.jpeg *.bmp *.gif *.webp"), ("Все файлы", "*.*")])
                if not src_path:
                    return
                avatar_state["src_path"] = src_path
                avatar_state["remove"] = False
                refresh_avatar_preview()

            def remove_avatar():
                avatar_state["src_path"] = None
                avatar_state["remove"] = True
                refresh_avatar_preview()

            avatar_btns = tk.Frame(avatar_row, bg=BG)
            avatar_btns.pack(side="left", padx=(10, 0))
            tk.Button(avatar_btns, text="Выбрать", font=FONT_SMALL, fg="#fff", bg=ACCENT, activebackground=ACCENT_DIM, activeforeground="#fff", relief="flat", bd=0, padx=10, pady=4, cursor="hand2", command=choose_avatar).pack(anchor="w")
            tk.Button(avatar_btns, text="Убрать", font=FONT_SMALL, fg=TEXT_DIM, bg=BG, activebackground=BG_PANEL, activeforeground=RED, relief="flat", bd=0, padx=10, pady=2, cursor="hand2", command=remove_avatar).pack(anchor="w", pady=(4, 0))
            avatar_error_label.pack(fill="x", pady=(4, 0))
            refresh_avatar_preview()

            # Тип профиля
            tk.Label(body, text="Тип", font=FONT_SMALL, fg=TEXT_DIM, bg=BG, anchor="w").pack(fill="x", pady=(8, 2))
            type_var = tk.StringVar(value=existing["type"] if existing else "cloud")
            type_row = tk.Frame(body, bg=BG)
            type_row.pack(fill="x")
            type_options = [("cloud", "Облако (API)"), ("ollama", "Ollama"), ("lmstudio", "LM Studio")]
            type_buttons = {}
            default_urls = {"cloud": "https://api.deepseek.com", "ollama": "http://localhost:11434", "lmstudio": "http://127.0.0.1:1234"}

            def select_type(t):
                type_var.set(t)
                for key, btn in type_buttons.items():
                    btn.config(bg=ACCENT if key == t else BG_PANEL, fg="#fff" if key == t else TEXT_DIM)
                if not existing and not url_entry.get().strip():
                    url_entry.delete(0, "end")
                    url_entry.insert(0, default_urls[t])
                api_key_row.pack_forget()
                if t == "cloud":
                    api_key_row.pack(fill="x", after=url_wrap)
                show_model_field_for(t)

            for key, label in type_options:
                b = tk.Button(type_row, text=label, font=FONT_SMALL, relief="flat", bd=0, padx=10, pady=6, cursor="hand2", command=lambda k=key: select_type(k))
                b.pack(side="left", padx=(0, 6))
                type_buttons[key] = b

            url_wrap = tk.Frame(body, bg=BG)
            url_wrap.pack(fill="x")
            tk.Label(url_wrap, text="Базовый URL", font=FONT_SMALL, fg=TEXT_DIM, bg=BG, anchor="w").pack(fill="x", pady=(8, 2))
            url_entry = tk.Entry(url_wrap, font=FONT_UI, bg=BG_MSG, fg=TEXT, insertbackground=TEXT, relief="flat", bd=0, highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT)
            url_entry.pack(fill="x", ipady=5)
            url_entry.insert(0, existing["base_url"] if existing else default_urls["cloud"])

            api_key_row = tk.Frame(body, bg=BG)
            api_key_label_row = tk.Frame(api_key_row, bg=BG)
            api_key_label_row.pack(fill="x", pady=(8, 2))
            tk.Label(api_key_label_row, text="API ключ", font=FONT_SMALL, fg=TEXT_DIM, bg=BG, anchor="w").pack(side="left")
            api_key_visible = tk.BooleanVar(value=True)

            def toggle_api_key_visibility():
                api_key_visible.set(not api_key_visible.get())
                api_key_entry.config(show="" if api_key_visible.get() else "•")
                toggle_btn.config(text="Скрыть" if api_key_visible.get() else "Показать")

            toggle_btn = tk.Button(api_key_label_row, text="Скрыть", font=("Segoe UI", 8), fg=ACCENT, bg=BG, relief="flat", bd=0, cursor="hand2", activebackground=BG, activeforeground=ACCENT, command=toggle_api_key_visibility)
            toggle_btn.pack(side="right")
            api_key_entry = tk.Entry(api_key_row, font=FONT_UI, bg=BG_MSG, fg=TEXT, insertbackground=TEXT, relief="flat", bd=0, highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT, show="")
            api_key_entry.pack(fill="x", ipady=5)
            api_key_entry.insert(0, existing.get("api_key", "") if existing else "")
            api_key_row.pack(fill="x")

            # Модель
            model_wrap = tk.Frame(body, bg=BG)
            model_wrap.pack(fill="x")
            tk.Label(model_wrap, text="Модель", font=FONT_SMALL, fg=TEXT_DIM, bg=BG, anchor="w").pack(fill="x", pady=(8, 2))
            model_entry = tk.Entry(model_wrap, font=FONT_UI, bg=BG_MSG, fg=TEXT, insertbackground=TEXT, relief="flat", bd=0, highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT)
            model_entry.insert(0, existing["model"] if existing else "")
            model_combo_row = tk.Frame(model_wrap, bg=BG)
            model_var = tk.StringVar(value=existing["model"] if existing else "")
            model_combo = ttk.Combobox(model_combo_row, textvariable=model_var, state="readonly", font=FONT_UI, values=[existing["model"]] if existing else [])
            model_combo.pack(side="left", fill="x", expand=True, ipady=3)
            model_status = tk.Label(model_wrap, text="", font=FONT_SMALL, fg=TEXT_DIM, bg=BG, anchor="w")

            def do_refresh_models():
                url = url_entry.get().strip()
                key = api_key_entry.get().strip()
                if not url:
                    model_status.config(text="Сначала укажи базовый URL.", fg=RED)
                    return
                model_status.config(text="Загружаю список...", fg=TEXT_DIM)
                body.update_idletasks()
                try:
                    ids = mp.fetch_available_models(url, key)
                    if not ids:
                        model_status.config(text="Сервер не вернул ни одной модели.", fg=YELLOW)
                        return
                    model_combo["values"] = ids
                    if model_var.get() not in ids:
                        model_var.set(ids[0])
                    model_status.config(text=f"Найдено моделей: {len(ids)}", fg=GREEN)
                except Exception as e:
                    model_status.config(text=f"Не удалось получить список: {e}", fg=RED)

            refresh_btn = tk.Button(model_combo_row, text="⟳", font=FONT_UI, fg=TEXT, bg=BG_PANEL, activebackground=BORDER, activeforeground=TEXT, relief="flat", bd=0, padx=10, cursor="hand2", command=do_refresh_models)
            refresh_btn.pack(side="left", padx=(6, 0))

            def show_model_field_for(t):
                model_combo_row.pack_forget()
                model_entry.pack_forget()
                model_status.pack_forget()
                if t in ("ollama", "lmstudio"):
                    model_combo_row.pack(fill="x")
                    model_status.pack(fill="x", pady=(4, 0))
                else:
                    model_entry.pack(fill="x", ipady=5)

            def get_model_value():
                return model_var.get().strip() if type_var.get() in ("ollama", "lmstudio") else model_entry.get().strip()

            # Max tokens / Temperature
            row2 = tk.Frame(body, bg=BG)
            row2.pack(fill="x", pady=(8, 0))
            tk.Label(row2, text="Max tokens", font=FONT_SMALL, fg=TEXT_DIM, bg=BG).pack(side="left")
            max_tok_entry = tk.Entry(row2, font=FONT_UI, bg=BG_MSG, fg=TEXT, insertbackground=TEXT, width=8, relief="flat", bd=0, highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT)
            max_tok_entry.pack(side="left", padx=(8, 18), ipady=4)
            max_tok_entry.insert(0, str(existing["max_tokens"]) if existing else "500")
            tk.Label(row2, text="Temperature", font=FONT_SMALL, fg=TEXT_DIM, bg=BG).pack(side="left")
            temp_entry = tk.Entry(row2, font=FONT_UI, bg=BG_MSG, fg=TEXT, insertbackground=TEXT, width=6, relief="flat", bd=0, highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT)
            temp_entry.pack(side="left", padx=(8, 0), ipady=4)
            temp_entry.insert(0, str(existing["temperature"]) if existing else "0.7")

            # Сэмплирование
            tk.Frame(body, bg=BORDER, height=1).pack(fill="x", pady=(14, 10))
            samp_header = tk.Frame(body, bg=BG)
            samp_header.pack(fill="x")
            tk.Label(samp_header, text="Сэмплирование", font=("Segoe UI", 10, "bold"), fg=TEXT, bg=BG, anchor="w").pack(side="left")
            tk.Label(samp_header, text="выключенные -- не отправляются, используется дефолт модели", font=("Segoe UI", 8), fg=TEXT_DIM, bg=BG, anchor="w").pack(side="left", padx=(8, 0))

            existing_sampling = (existing.get("sampling") if existing else None) or {}
            sampling_vars = {}
            sampling_value_labels = {}

            def make_sampling_row(key):
                spec = mp.SAMPLING_PARAMS_SPEC[key]
                saved = existing_sampling.get(key, {})
                enabled_default = bool(saved.get("enabled", False))
                value_default = saved.get("value", spec["default"])

                row = tk.Frame(body, bg=BG)
                row.pack(fill="x", pady=(8, 0), padx=(0, 20))

                enabled_var = tk.BooleanVar(value=enabled_default)
                value_var = tk.DoubleVar(value=value_default)

                top = tk.Frame(row, bg=BG)
                top.pack(fill="x")
                chk = tk.Checkbutton(top, text=spec["label"], variable=enabled_var, font=FONT_SMALL, fg=TEXT, bg=BG, activebackground=BG, selectcolor=BG_MSG, anchor="w", cursor="hand2")
                chk.pack(side="left")
                value_label = tk.Label(top, text=f"{value_var.get():g}", font=FONT_SMALL, fg=ACCENT, bg=BG, width=6, anchor="e")
                value_label.pack(side="right")
                sampling_value_labels[key] = value_label

                slider = RoundedSlider(
                    row, from_=spec["min"], to=spec["max"], resolution=spec["step"],
                    variable=value_var, bg=BG, troughcolor=BG_MSG, activebackground=ACCENT,
                    command=lambda v, k=key: sampling_value_labels[k].config(text=f"{float(v):g}"),
                )
                slider.pack(fill="x", pady=(2, 0))

                tk.Label(row, text=spec["hint"], font=("Segoe UI", 8), fg=TEXT_DIM, bg=BG, anchor="w", wraplength=380, justify="left").pack(fill="x", pady=(1, 0))

                def sync_slider_state(*_):
                    slider.config(state="normal" if enabled_var.get() else "disabled")
                enabled_var.trace_add("write", sync_slider_state)
                sync_slider_state()
                sampling_vars[key] = (enabled_var, value_var)

            for _samp_key in mp.SAMPLING_PARAMS_SPEC:
                make_sampling_row(_samp_key)

            def get_sampling_payload():
                return {key: {"enabled": bool(en.get()), "value": float(val.get())} for key, (en, val) in sampling_vars.items()}

            select_type(type_var.get())

            # TTS настройки
            from tts import TTS_MODEL_VOICES, TTS_DEFAULT_SPEAKERS

            tk.Frame(body, bg=BORDER, height=1).pack(fill="x", pady=(14, 10))
            tk.Label(body, text="Синтез речи (TTS)", font=("Segoe UI", 10, "bold"), fg=TEXT, bg=BG, anchor="w").pack(fill="x")

            tts_lang_row = tk.Frame(body, bg=BG)
            tts_lang_row.pack(fill="x", pady=(8, 0))
            tk.Label(tts_lang_row, text="Язык модели TTS", font=FONT_SMALL, fg=TEXT_DIM, bg=BG, anchor="w").pack(side="left")

            tts_lang_var = tk.StringVar(value=existing.get("tts_lang", "ru") if existing else "ru")
            tts_lang_btns = {}

            def _select_tts_lang(lang):
                tts_lang_var.set(lang)
                for k, b in tts_lang_btns.items():
                    b.config(bg=ACCENT if k == lang else BG_PANEL, fg="#fff" if k == lang else TEXT_DIM)
                voices = TTS_MODEL_VOICES.get(lang, ["random"])
                tts_speaker_combo["values"] = voices
                if tts_speaker_var.get() not in voices:
                    tts_speaker_var.set(TTS_DEFAULT_SPEAKERS.get(lang, voices[0]))

            for _lang, _label in [("ru", "Русский"), ("en", "Английский")]:
                _b = tk.Button(tts_lang_row, text=_label, font=FONT_SMALL, relief="flat", bd=0, padx=10, pady=6, cursor="hand2", command=lambda l=_lang: _select_tts_lang(l))
                _b.pack(side="left", padx=(8, 0))
                tts_lang_btns[_lang] = _b

            tk.Label(body, text="Голос TTS", font=FONT_SMALL, fg=TEXT_DIM, bg=BG, anchor="w").pack(fill="x", pady=(8, 2))
            _init_lang = tts_lang_var.get()
            _init_voices = TTS_MODEL_VOICES.get(_init_lang, ["random"])
            _init_speaker = existing.get("tts_speaker", TTS_DEFAULT_SPEAKERS.get(_init_lang, _init_voices[0])) if existing else TTS_DEFAULT_SPEAKERS.get(_init_lang, _init_voices[0])
            tts_speaker_var = tk.StringVar(value=_init_speaker)
            tts_speaker_combo = ttk.Combobox(body, textvariable=tts_speaker_var, values=_init_voices, state="readonly", font=FONT_UI)
            tts_speaker_combo.pack(fill="x", ipady=3)
            _select_tts_lang(_init_lang)

            tk.Label(body, text="Смена языка TTS потребует перезапуска ассистента (модель перезагрузится).", font=("Segoe UI", 8), fg=TEXT_DIM, bg=BG, anchor="w", wraplength=380).pack(fill="x", pady=(4, 0))

            status_label = tk.Label(body, text="", font=FONT_SMALL, fg=RED, bg=BG)
            status_label.pack(fill="x", pady=(10, 0))

            def do_save():
                name = name_entry.get().strip()
                url  = url_entry.get().strip()
                model = get_model_value()
                if not name or not url or not model:
                    status_label.config(text="Заполни название, URL и модель.")
                    return
                try:
                    max_tokens = int(max_tok_entry.get().strip())
                    temperature = float(temp_entry.get().strip())
                except ValueError:
                    status_label.config(text="Max tokens должен быть числом, temperature -- дробным.")
                    return
                data = {
                    "name": name, "type": type_var.get(), "base_url": url,
                    "api_key": api_key_entry.get().strip(), "model": model,
                    "max_tokens": max_tokens, "temperature": temperature,
                    "system_prompt": existing["system_prompt"] if existing else mp.DEFAULT_SYSTEM_PROMPT,
                    "sampling": get_sampling_payload(),
                    "tts_lang": tts_lang_var.get(), "tts_speaker": tts_speaker_var.get(),
                }
                if existing:
                    mp.update_profile(existing["id"], data)
                    if avatar_state["src_path"]:
                        try:
                            self._save_profile_avatar(existing["id"], avatar_state["src_path"])
                        except Exception as e:
                            self._log(f"[ERR] Не удалось сохранить аватарку: {e}", "error")
                    elif avatar_state["remove"]:
                        self._delete_profile_avatar(existing["id"])
                    self._log(f"[m] Профиль обновлён: {name}", "system")
                else:
                    new_id = mp.add_profile(data)
                    if avatar_state["src_path"]:
                        try:
                            self._save_profile_avatar(new_id, avatar_state["src_path"])
                        except Exception as e:
                            self._log(f"[ERR] Не удалось сохранить аватарку: {e}", "error")
                    if len(mp.list_profiles()) == 1 or messagebox.askyesno("Сделать активной?", f"Сделать «{name}» активной моделью прямо сейчас?"):
                        mp.set_active_profile(new_id)
                    self._log(f"[m] Профиль добавлен: {name}", "system")
                self._sync_active_model_label()
                refresh_list()
                # Обновляем заголовок формы и перерисовываем список
                status_label.config(text="Сохранено ✓", fg=GREEN)

            footer_f = tk.Frame(body, bg=BG)
            footer_f.pack(fill="x", pady=(14, 0))
            tk.Button(footer_f, text="Сохранить", font=FONT_UI, fg="#fff", bg=ACCENT, activebackground=ACCENT_DIM, activeforeground="#fff", relief="flat", bd=0, padx=18, pady=7, cursor="hand2", command=do_save).pack(side="left", padx=(0, 8))
            tk.Button(footer_f, text="Отмена", font=FONT_UI, fg=TEXT_DIM, bg=BG, activebackground=BG_PANEL, activeforeground=TEXT, relief="flat", bd=0, padx=14, pady=7, cursor="hand2", command=close_form).pack(side="left")

        def close_form():
            if right_form_container[0]:
                right_form_container[0].destroy()
                right_form_container[0] = None
            right_placeholder.place(relx=0.5, rely=0.5, anchor="center")

        def refresh_list():
            for widget in list_frame.winfo_children():
                widget.destroy()

            self._model_card_imgtks = []
            data = mp.load_profiles()
            active_id = data["active_id"]

            type_labels = {"cloud": "Облако", "ollama": "Ollama", "lmstudio": "LM Studio"}
            type_colors = {"cloud": CYAN, "ollama": GREEN, "lmstudio": "#f7a35c"}

            cloud_profiles = [p for p in data["profiles"] if p["type"] == "cloud"]
            local_profiles = [p for p in data["profiles"] if p["type"] != "cloud"]

            sections = []
            if cloud_profiles:
                sections.append(("ОБЛАЧНЫЕ", cloud_profiles))
            if local_profiles:
                sections.append(("ЛОКАЛЬНЫЕ", local_profiles))

            for section_title, profiles_in_section in sections:
                tk.Label(list_frame, text=section_title, font=("Segoe UI", 9, "bold"), fg=TEXT_DIM, bg=BG, anchor="w").pack(fill="x", pady=(10, 2), padx=2)
                for profile in profiles_in_section:
                    is_active = profile["id"] == active_id
                    card = tk.Frame(list_frame, bg=BG_PANEL, highlightbackground=ACCENT if is_active else BORDER, highlightthickness=2 if is_active else 1)
                    card.pack(fill="x", pady=4, padx=2)

                    inner = tk.Frame(card, bg=BG_PANEL, padx=10, pady=8)
                    inner.pack(fill="x")

                    top_row = tk.Frame(inner, bg=BG_PANEL)
                    top_row.pack(fill="x")

                    AVATAR_SIZE = 24
                    avatar_path = os.path.join(self._get_avatars_dir(), f"{profile['id']}.png")
                    round_img = _make_round_avatar(avatar_path, AVATAR_SIZE)
                    av_c = tk.Canvas(top_row, width=AVATAR_SIZE, height=AVATAR_SIZE, bg=BG_PANEL, highlightthickness=0)
                    av_c.pack(side="left", padx=(0, 6))
                    if round_img is not None:
                        from PIL import ImageTk
                        imgtk = ImageTk.PhotoImage(round_img)
                        self._model_card_imgtks.append(imgtk)
                        av_c.create_image(0, 0, anchor="nw", image=imgtk)
                    else:
                        av_c.create_oval(1, 1, AVATAR_SIZE - 1, AVATAR_SIZE - 1, outline=ACCENT if is_active else BORDER, fill=BG)
                    if is_active:
                        av_c.create_oval(AVATAR_SIZE - 8, AVATAR_SIZE - 8, AVATAR_SIZE - 1, AVATAR_SIZE - 1, outline=BG_PANEL, width=2, fill=GREEN)

                    tk.Label(top_row, text=profile["name"], font=("Segoe UI", 10, "bold"), fg=TEXT, bg=BG_PANEL, anchor="w", wraplength=200).pack(side="left")
                    tk.Label(top_row, text=type_labels.get(profile["type"], profile["type"]), font=FONT_SMALL, fg=type_colors.get(profile["type"], TEXT_DIM), bg=BG_PANEL).pack(side="right")

                    tk.Label(inner, text=profile["model"], font=FONT_SMALL, fg=TEXT_DIM, bg=BG_PANEL, anchor="w", wraplength=280).pack(fill="x", pady=(2, 6))

                    btn_row = tk.Frame(inner, bg=BG_PANEL)
                    btn_row.pack(fill="x")

                    def make_activate(pid=profile["id"]):
                        def _activate():
                            mp.set_active_profile(pid)
                            self._sync_active_model_label()
                            refresh_list()
                            self._log(f"[m] Активная модель: {mp.get_active_profile()['name']}", "system")
                        return _activate

                    def make_edit(p=profile):
                        return lambda: open_inline_form(existing=p)

                    def make_delete(pid=profile["id"], pname=profile["name"]):
                        def _delete():
                            if not messagebox.askyesno("Удалить профиль", f"Удалить «{pname}»?"):
                                return
                            if not mp.delete_profile(pid):
                                messagebox.showwarning("Нельзя удалить", "Должен остаться хотя бы один профиль.")
                                return
                            self._delete_profile_avatar(pid)
                            self._sync_active_model_label()
                            close_form()
                            refresh_list()
                        return _delete

                    tk.Button(btn_row, text="Сделать активной" if not is_active else "Активна", font=FONT_SMALL, fg="#fff" if not is_active else TEXT_DIM, bg=ACCENT if not is_active else BORDER, activebackground=ACCENT_DIM, activeforeground="#fff", relief="flat", bd=0, padx=8, pady=3, cursor="hand2", state="normal" if not is_active else "disabled", command=make_activate()).pack(side="left", padx=(0, 4))
                    tk.Button(btn_row, text="Изменить", font=FONT_SMALL, fg=TEXT, bg=BG, activebackground=BORDER, activeforeground=TEXT, relief="flat", bd=0, padx=8, pady=3, cursor="hand2", command=make_edit()).pack(side="left", padx=(0, 4))
                    tk.Button(btn_row, text="Удалить", font=FONT_SMALL, fg=RED, bg=BG, activebackground=BORDER, activeforeground=RED, relief="flat", bd=0, padx=8, pady=3, cursor="hand2", command=make_delete()).pack(side="left")

        refresh_list()

        tk.Button(
            left_footer, text="+ Добавить профиль", font=FONT_UI,
            fg="#fff", bg=ACCENT, activebackground=ACCENT_DIM, activeforeground="#fff",
            relief="flat", bd=0, padx=14, pady=6, cursor="hand2",
            command=lambda: open_inline_form(existing=None),
        ).pack(side="left")

        if self.running:
            tk.Label(
                left_footer, text="Изменения применятся\nпосле перезапуска",
                font=FONT_SMALL, fg=YELLOW, bg=BG, justify="left",
            ).pack(side="left", padx=(8, 0))

        return panel
 

    # -- Раздел: Vision (анализ экрана) ----------------------------

    def _build_vision_panel(self, parent):
        import vision_profiles as vp

        panel = tk.Frame(parent, bg=BG)

        header = tk.Frame(panel, bg=BG, pady=16)
        header.pack(fill="x", padx=20)
        tk.Label(header, text="Vision", font=FONT_BIG, fg=TEXT, bg=BG).pack(side="left")

        tk.Label(
            panel,
            text="Модель, которая анализирует скриншот экрана по команде \"посмотри на экран\". "
                 "Локальные .gguf, vision-модель в LM Studio, или облачный Moondream API -- "
                 "выбирай любую и переключайся свободно.",
            font=FONT_SMALL, fg=TEXT_DIM, bg=BG, wraplength=520, justify="left",
        ).pack(fill="x", padx=20, pady=(0, 10))

        list_outer = tk.Frame(panel, bg=BG)
        list_outer.pack(fill="both", expand=True, padx=20)

        canvas = tk.Canvas(list_outer, bg=BG, highlightthickness=0)
        style = ttk.Style()
        style.layout("Vis.Vertical.TScrollbar", [
            ("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
                ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"}),
            ]}),
        ])
        style.configure(
            "Vis.Vertical.TScrollbar",
            background=ACCENT_DIM, troughcolor=BG, bordercolor=BG,
            relief="flat", borderwidth=0, width=8,
        )
        style.map("Vis.Vertical.TScrollbar", background=[("active", ACCENT), ("pressed", ACCENT)])
        scroll = ttk.Scrollbar(list_outer, orient="vertical", command=canvas.yview, style="Vis.Vertical.TScrollbar")
        list_frame = tk.Frame(canvas, bg=BG)

        list_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas_window = canvas.create_window((0, 0), window=list_frame, anchor="nw")
        canvas.configure(yscrollcommand=lambda f, l, c="canvas", s="scroll": (_clamp_scroll(f, l, canvas), scroll.set(f, l)))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(canvas_window, width=e.width))

        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        def _mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            if canvas.yview()[0] < 0.0: canvas.yview_moveto(0.0)
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _mousewheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

        type_labels = {"gguf": "Локально (.gguf)", "lmstudio": "LM Studio", "cloud": "Облако"}
        type_colors = {"gguf": GREEN, "lmstudio": "#f7a35c", "cloud": CYAN}

        def refresh_list():
            for widget in list_frame.winfo_children():
                widget.destroy()

            data = vp.load_profiles()
            active_id = data["active_id"]

            for profile in data["profiles"]:
                is_active = profile["id"] == active_id
                card = tk.Frame(
                    list_frame, bg=BG_PANEL,
                    highlightbackground=ACCENT if is_active else BORDER,
                    highlightthickness=2 if is_active else 1,
                )
                card.pack(fill="x", pady=5, padx=2)

                inner = tk.Frame(card, bg=BG_PANEL, padx=12, pady=10)
                inner.pack(fill="x")

                top_row = tk.Frame(inner, bg=BG_PANEL)
                top_row.pack(fill="x")

                mark = "[*] " if is_active else "    "
                tk.Label(
                    top_row, text=f"{mark}{profile['name']}", font=("Segoe UI", 11, "bold"),
                    fg=TEXT, bg=BG_PANEL, anchor="w",
                ).pack(side="left")

                tk.Label(
                    top_row, text=type_labels.get(profile["type"], profile["type"]),
                    font=FONT_SMALL, fg=type_colors.get(profile["type"], TEXT_DIM), bg=BG_PANEL,
                ).pack(side="right")

                detail = (
                    profile.get("model_path") or "путь не указан"
                    if profile["type"] == "gguf"
                    else f"{profile.get('model', '')}  ·  {profile.get('base_url', '')}"
                )
                tk.Label(
                    inner, text=detail, font=FONT_SMALL, fg=TEXT_DIM, bg=BG_PANEL, anchor="w",
                ).pack(fill="x", pady=(2, 8))

                btn_row = tk.Frame(inner, bg=BG_PANEL)
                btn_row.pack(fill="x")

                def make_activate(pid=profile["id"]):
                    def _activate():
                        vp.set_active_profile(pid)
                        refresh_list()
                        self._log(f"[v] Активная Vision-модель: {vp.get_active_profile()['name']}", "system")
                    return _activate

                def make_edit(p=profile):
                    return lambda: self._open_vision_profile_form(self._settings_win, refresh_list, existing=p)

                def make_delete(pid=profile["id"], pname=profile["name"]):
                    def _delete():
                        if not messagebox.askyesno("Удалить профиль", f"Удалить «{pname}»?"):
                            return
                        if not vp.delete_profile(pid):
                            messagebox.showwarning("Нельзя удалить", "Должен остаться хотя бы один профиль.")
                            return
                        refresh_list()
                    return _delete

                tk.Button(
                    btn_row, text="Сделать активной" if not is_active else "Активна",
                    font=FONT_SMALL, fg="#fff" if not is_active else TEXT_DIM,
                    bg=ACCENT if not is_active else BORDER,
                    activebackground=ACCENT_DIM, activeforeground="#fff",
                    relief="flat", bd=0, padx=10, pady=4, cursor="hand2",
                    state="normal" if not is_active else "disabled",
                    command=make_activate(),
                ).pack(side="left", padx=(0, 6))

                tk.Button(
                    btn_row, text="Изменить", font=FONT_SMALL, fg=TEXT, bg=BG,
                    activebackground=BORDER, activeforeground=TEXT,
                    relief="flat", bd=0, padx=10, pady=4, cursor="hand2",
                    command=make_edit(),
                ).pack(side="left", padx=(0, 6))

                tk.Button(
                    btn_row, text="Удалить", font=FONT_SMALL, fg=RED, bg=BG,
                    activebackground=BORDER, activeforeground=RED,
                    relief="flat", bd=0, padx=10, pady=4, cursor="hand2",
                    command=make_delete(),
                ).pack(side="left")

        refresh_list()

        footer = tk.Frame(panel, bg=BG, pady=14)
        footer.pack(fill="x", padx=20)

        tk.Button(
            footer, text="+ Добавить профиль", font=FONT_UI,
            fg="#fff", bg=ACCENT, activebackground=ACCENT_DIM, activeforeground="#fff",
            relief="flat", bd=0, padx=18, pady=7, cursor="hand2",
            command=lambda: self._open_vision_profile_form(self._settings_win, refresh_list),
        ).pack(side="left")

        if self.running:
            tk.Label(
                footer, text="Смена Vision-модели применится после перезапуска ассистента",
                font=FONT_SMALL, fg=YELLOW, bg=BG,
            ).pack(side="right")

        return panel

    def _open_vision_profile_form(self, parent_win, on_saved, existing=None):
        """Форма добавления/редактирования Vision-профиля."""
        import vision_profiles as vp

        form = tk.Toplevel(parent_win)
        form.title("Vision-профиль" if existing is None else f"Изменить -- {existing['name']}")
        form.configure(bg=BG)
        _s = self._get_dpi_scale()
        form.geometry(f"{int(500 * _s)}x{int(640 * _s)}")
        form.minsize(int(420 * _s), int(480 * _s))
        form.transient(parent_win)
        form.grab_set()
        _apply_dark_titlebar(form)
        self._apply_window_icon(form)

        outer = tk.Frame(form, bg=BG)
        outer.pack(fill="both", expand=True)

        form_canvas = tk.Canvas(outer, bg=BG, highlightthickness=0)
        _vform_style = ttk.Style()
        _vform_style.layout("VisForm.Vertical.TScrollbar", [
            ("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
                ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"}),
            ]}),
        ])
        _vform_style.configure(
            "VisForm.Vertical.TScrollbar",
            background=ACCENT_DIM, troughcolor=BG, bordercolor=BG,
            relief="flat", borderwidth=0, width=8,
        )
        _vform_style.map("VisForm.Vertical.TScrollbar", background=[("active", ACCENT), ("pressed", ACCENT)])
        form_scroll = ttk.Scrollbar(outer, orient="vertical", command=form_canvas.yview, style="VisForm.Vertical.TScrollbar")
        form_canvas.configure(yscrollcommand=lambda f, l, c="form_canvas", s="form_scroll": (_clamp_scroll(f, l, form_canvas), form_scroll.set(f, l)))
        form_canvas.pack(side="left", fill="both", expand=True)
        form_scroll.pack(side="right", fill="y")

        def _form_mousewheel(event):
            form_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            if form_canvas.yview()[0] < 0.0: form_canvas.yview_moveto(0.0)
        form_canvas.bind("<Enter>", lambda e: form_canvas.bind_all("<MouseWheel>", _form_mousewheel))
        form_canvas.bind("<Leave>", lambda e: form_canvas.unbind_all("<MouseWheel>"))

        body = tk.Frame(form_canvas, bg=BG, padx=18, pady=16)
        body_window = form_canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda e: form_canvas.configure(scrollregion=form_canvas.bbox("all")))
        form_canvas.bind("<Configure>", lambda e: form_canvas.itemconfig(body_window, width=e.width))

        def field(label_text, default=""):
            tk.Label(body, text=label_text, font=FONT_SMALL, fg=TEXT_DIM, bg=BG, anchor="w").pack(fill="x", pady=(8, 2))
            entry = tk.Entry(
                body, font=FONT_UI, bg=BG_MSG, fg=TEXT, insertbackground=TEXT,
                relief="flat", bd=0, highlightthickness=1,
                highlightbackground=BORDER, highlightcolor=ACCENT,
            )
            entry.pack(fill="x", ipady=5)
            entry.insert(0, default)
            return entry

        name_entry = field("Название", existing["name"] if existing else "")

        # -- Тип профиля -- выбор кнопками --
        tk.Label(body, text="Тип", font=FONT_SMALL, fg=TEXT_DIM, bg=BG, anchor="w").pack(fill="x", pady=(8, 2))
        type_var = tk.StringVar(value=existing["type"] if existing else "gguf")
        type_row = tk.Frame(body, bg=BG)
        type_row.pack(fill="x")

        type_options = [("gguf", "Локально (.gguf)"), ("lmstudio", "LM Studio"), ("cloud", "Облако")]
        type_buttons = {}

        # -- Поля для gguf --
        gguf_wrap = tk.Frame(body, bg=BG)

        model_path_entry = field("Путь к .gguf модели (text-model)", existing.get("model_path", "") if existing else "models/moondream2-text-model-f16.gguf")
        model_path_entry.pack_forget()  # переподчиняем ниже вручную в нужном порядке
        mmproj_path_entry_holder = {}

        def build_gguf_fields():
            tk.Label(gguf_wrap, text="Путь к .gguf модели (text-model)", font=FONT_SMALL, fg=TEXT_DIM, bg=BG, anchor="w").pack(fill="x", pady=(8, 2))
            mp_entry = tk.Entry(
                gguf_wrap, font=FONT_UI, bg=BG_MSG, fg=TEXT, insertbackground=TEXT,
                relief="flat", bd=0, highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT,
            )
            mp_entry.pack(fill="x", ipady=5)
            mp_entry.insert(0, existing.get("model_path", "") if existing else "models/moondream2-text-model-f16.gguf")

            browse_row = tk.Frame(gguf_wrap, bg=BG)
            browse_row.pack(fill="x", pady=(4, 0))

            def browse_model():
                path = filedialog.askopenfilename(
                    title="Выбери .gguf файл text-model",
                    filetypes=[("GGUF модели", "*.gguf"), ("Все файлы", "*.*")],
                )
                if path:
                    mp_entry.delete(0, "end")
                    mp_entry.insert(0, path)

            tk.Button(
                browse_row, text="Обзор...", font=FONT_SMALL, fg=TEXT, bg=BG_PANEL,
                activebackground=BORDER, activeforeground=TEXT,
                relief="flat", bd=0, padx=10, pady=3, cursor="hand2", command=browse_model,
            ).pack(side="left")

            tk.Label(
                gguf_wrap, text="mmproj (vision projector) ищется автоматически рядом, "
                                "если оставить поле ниже пустым:",
                font=("Segoe UI", 8), fg=TEXT_DIM, bg=BG, anchor="w", wraplength=440, justify="left",
            ).pack(fill="x", pady=(10, 2))

            mmproj_entry = tk.Entry(
                gguf_wrap, font=FONT_UI, bg=BG_MSG, fg=TEXT, insertbackground=TEXT,
                relief="flat", bd=0, highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT,
            )
            mmproj_entry.pack(fill="x", ipady=5)
            mmproj_entry.insert(0, existing.get("mmproj_path", "") if existing else "")

            def browse_mmproj():
                path = filedialog.askopenfilename(
                    title="Выбери .gguf файл mmproj (необязательно)",
                    filetypes=[("GGUF модели", "*.gguf"), ("Все файлы", "*.*")],
                )
                if path:
                    mmproj_entry.delete(0, "end")
                    mmproj_entry.insert(0, path)

            browse_row2 = tk.Frame(gguf_wrap, bg=BG)
            browse_row2.pack(fill="x", pady=(4, 0))
            tk.Button(
                browse_row2, text="Обзор...", font=FONT_SMALL, fg=TEXT, bg=BG_PANEL,
                activebackground=BORDER, activeforeground=TEXT,
                relief="flat", bd=0, padx=10, pady=3, cursor="hand2", command=browse_mmproj,
            ).pack(side="left")

            mmproj_path_entry_holder["entry"] = mmproj_entry
            mmproj_path_entry_holder["model_entry"] = mp_entry

        build_gguf_fields()

        # -- Поля для lmstudio/cloud (URL, ключ, модель) --
        remote_wrap = tk.Frame(body, bg=BG)

        url_entry = tk.Entry(
            remote_wrap, font=FONT_UI, bg=BG_MSG, fg=TEXT, insertbackground=TEXT,
            relief="flat", bd=0, highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT,
        )
        api_key_entry = tk.Entry(
            remote_wrap, font=FONT_UI, bg=BG_MSG, fg=TEXT, insertbackground=TEXT,
            relief="flat", bd=0, highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT, show="*",
        )
        model_entry = tk.Entry(
            remote_wrap, font=FONT_UI, bg=BG_MSG, fg=TEXT, insertbackground=TEXT,
            relief="flat", bd=0, highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT,
        )

        default_urls = {
            "gguf": "", "lmstudio": "http://127.0.0.1:1234", "cloud": "https://api.moondream.ai/v1",
        }

        def build_remote_fields():
            tk.Label(remote_wrap, text="Базовый URL", font=FONT_SMALL, fg=TEXT_DIM, bg=BG, anchor="w").pack(fill="x", pady=(8, 2))
            url_entry.pack(fill="x", ipady=5)
            url_entry.insert(0, existing.get("base_url", "") if existing else "")

            tk.Label(remote_wrap, text="API ключ (если нужен)", font=FONT_SMALL, fg=TEXT_DIM, bg=BG, anchor="w").pack(fill="x", pady=(8, 2))
            api_key_entry.pack(fill="x", ipady=5)
            api_key_entry.insert(0, existing.get("api_key", "") if existing else "")

            tk.Label(remote_wrap, text="Название модели", font=FONT_SMALL, fg=TEXT_DIM, bg=BG, anchor="w").pack(fill="x", pady=(8, 2))
            model_entry.pack(fill="x", ipady=5)
            model_entry.insert(0, existing.get("model", "") if existing else "")

        build_remote_fields()

        # -- Промпт (общий для всех типов) --
        prompt_entry_wrap = tk.Frame(body, bg=BG)
        tk.Label(prompt_entry_wrap, text="Промпт по умолчанию", font=FONT_SMALL, fg=TEXT_DIM, bg=BG, anchor="w").pack(fill="x", pady=(8, 2))
        prompt_text = tk.Text(
            prompt_entry_wrap, height=3, font=FONT_UI, bg=BG_MSG, fg=TEXT, insertbackground=TEXT,
            relief="flat", bd=0, highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT, wrap="word",
        )
        prompt_text.pack(fill="x")
        prompt_text.insert("1.0", existing.get("prompt", vp.DEFAULT_VISION_PROMPT) if existing else vp.DEFAULT_VISION_PROMPT)
        prompt_entry_wrap.pack(fill="x")

        def select_type(t):
            type_var.set(t)
            for key, btn in type_buttons.items():
                btn.config(bg=ACCENT if key == t else BG_PANEL, fg="#fff" if key == t else TEXT_DIM)

            gguf_wrap.pack_forget()
            remote_wrap.pack_forget()

            if t == "gguf":
                gguf_wrap.pack(fill="x", before=prompt_entry_wrap)
            else:
                if not existing and not url_entry.get().strip():
                    url_entry.delete(0, "end")
                    url_entry.insert(0, default_urls.get(t, ""))
                remote_wrap.pack(fill="x", before=prompt_entry_wrap)

        for key, label in type_options:
            b = tk.Button(
                type_row, text=label, font=FONT_SMALL,
                relief="flat", bd=0, padx=10, pady=6, cursor="hand2",
                command=lambda k=key: select_type(k),
            )
            b.pack(side="left", padx=(0, 6))
            type_buttons[key] = b

        select_type(type_var.get())

        status_label = tk.Label(body, text="", font=FONT_SMALL, fg=RED, bg=BG, wraplength=440, justify="left")
        status_label.pack(fill="x", pady=(10, 0))

        def do_save():
            name = name_entry.get().strip()
            if not name:
                status_label.config(text="Укажи название профиля.")
                return

            t = type_var.get()
            data = {
                "name": name,
                "type": t,
                "prompt": prompt_text.get("1.0", "end-1c").strip() or vp.DEFAULT_VISION_PROMPT,
            }

            if t == "gguf":
                model_path = mmproj_path_entry_holder["model_entry"].get().strip()
                if not model_path:
                    status_label.config(text="Укажи путь к .gguf модели.")
                    return
                data["model_path"]  = model_path
                data["mmproj_path"] = mmproj_path_entry_holder["entry"].get().strip()
                data["base_url"] = ""
                data["api_key"]  = ""
                data["model"]    = ""
            else:
                url = url_entry.get().strip()
                if not url:
                    status_label.config(text="Укажи базовый URL.")
                    return
                data["base_url"]    = url
                data["api_key"]     = api_key_entry.get().strip()
                data["model"]       = model_entry.get().strip()
                data["model_path"]  = ""
                data["mmproj_path"] = ""

            if existing:
                vp.update_profile(existing["id"], data)
                self._log(f"[v] Vision-профиль обновлён: {name}", "system")
            else:
                new_id = vp.add_profile(data)
                if len(vp.list_profiles()) == 1 or messagebox.askyesno(
                    "Сделать активной?", f"Сделать «{name}» активной Vision-моделью прямо сейчас?"
                ):
                    vp.set_active_profile(new_id)
                self._log(f"[v] Vision-профиль добавлен: {name}", "system")

            on_saved()
            form.destroy()

        footer = tk.Frame(body, bg=BG)
        footer.pack(fill="x", pady=(14, 0))

        tk.Button(
            footer, text="Сохранить", font=FONT_UI,
            fg="#fff", bg=ACCENT, activebackground=ACCENT_DIM, activeforeground="#fff",
            relief="flat", bd=0, padx=18, pady=7, cursor="hand2", command=do_save,
        ).pack(side="left", padx=(0, 8))

        tk.Button(
            footer, text="Отмена", font=FONT_UI,
            fg=TEXT_DIM, bg=BG, activebackground=BG_PANEL, activeforeground=TEXT,
            relief="flat", bd=0, padx=14, pady=7, cursor="hand2", command=form.destroy,
        ).pack(side="left")

    # -- Раздел: Кастомизация (иконка, акцентный цвет, чат) -------

    def _build_theme_panel(self, parent):
        """Раздел "Кастомизация": иконка приложения, акцентный цвет интерфейса
        (круговая палитра) и внешний вид пузырей сообщений в чате. Всё
        сохраняется в launcher_settings.json / icon.ico и применяется сразу,
        в том числе при следующем запуске."""
        outer_panel = tk.Frame(parent, bg=BG)

        canvas_wrap = tk.Frame(outer_panel, bg=BG)
        canvas_wrap.pack(fill="both", expand=True, padx=20)

        canvas = tk.Canvas(canvas_wrap, bg=BG, highlightthickness=0)
        style = ttk.Style()
        style.layout("Theme.Vertical.TScrollbar", [
            ("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
                ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"}),
            ]}),
        ])
        style.configure(
            "Theme.Vertical.TScrollbar",
            background=ACCENT_DIM, troughcolor=BG, bordercolor=BG,
            relief="flat", borderwidth=0, width=8,
        )
        style.map("Theme.Vertical.TScrollbar", background=[("active", ACCENT), ("pressed", ACCENT)])
        scroll = ttk.Scrollbar(canvas_wrap, orient="vertical", command=canvas.yview, style="Theme.Vertical.TScrollbar")
        panel = tk.Frame(canvas, bg=BG)

        panel.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas_window = canvas.create_window((0, 0), window=panel, anchor="nw")
        canvas.configure(yscrollcommand=lambda f, l, c="canvas", s="scroll": (_clamp_scroll(f, l, canvas), scroll.set(f, l)))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(canvas_window, width=e.width))

        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        def _mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            if canvas.yview()[0] < 0.0: canvas.yview_moveto(0.0)
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _mousewheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

        header = tk.Frame(panel, bg=BG, pady=16)
        header.pack(fill="x", padx=20)
        tk.Label(header, text="Кастомизация", font=FONT_BIG, fg=TEXT, bg=BG).pack(side="left")

        # -- Иконка приложения -------------------------------------
        tk.Label(
            panel, text="Иконка приложения", font=FONT_UI, fg=TEXT, bg=BG, anchor="w",
        ).pack(fill="x", padx=20, pady=(0, 4))
        tk.Label(
            panel,
            text="Выбери картинку -- она станет иконкой окна сразу, а иконкой .exe/Пуска "
                 "после следующей пересборки (build.bat).",
            font=FONT_SMALL, fg=TEXT_DIM, bg=BG, wraplength=560, justify="left",
        ).pack(fill="x", padx=20, pady=(0, 12))

        icon_row = tk.Frame(panel, bg=BG)
        icon_row.pack(fill="x", padx=20, pady=(0, 6))

        icon_preview_wrap = tk.Frame(icon_row, bg=BG_PANEL, width=80, height=80)
        icon_preview_wrap.pack(side="left")
        icon_preview_wrap.pack_propagate(False)
        icon_preview_label = tk.Label(icon_preview_wrap, bg=BG_PANEL, text="Нет\nиконки", fg=TEXT_DIM, font=FONT_SMALL)
        icon_preview_label.pack(expand=True)

        self._icon_preview_imgtk = None  # держим ссылку, иначе tkinter соберёт картинку в мусор

        def load_icon_preview():
            icon_path = self._get_icon_path()
            if not os.path.exists(icon_path):
                icon_preview_label.config(image="", text="Нет\nиконки")
                return
            try:
                from PIL import Image, ImageTk
                img = Image.open(icon_path).convert("RGBA")
                img.thumbnail((68, 68), Image.LANCZOS)
                imgtk = ImageTk.PhotoImage(img)
                self._icon_preview_imgtk = imgtk
                icon_preview_label.config(image=imgtk, text="")
            except ImportError:
                icon_preview_label.config(image="", text="Нужен\nPillow")
            except Exception:
                icon_preview_label.config(image="", text="Ошибка\nпревью")

        icon_col = tk.Frame(icon_row, bg=BG)
        icon_col.pack(side="left", fill="x", expand=True, padx=(14, 0))

        icon_status_label = tk.Label(icon_col, text="", font=FONT_SMALL, fg=GREEN, bg=BG, anchor="w", justify="left")

        def choose_app_icon():
            filetypes = [
                ("Изображения", "*.png *.jpg *.jpeg *.bmp *.ico *.gif"),
                ("Все файлы", "*.*"),
            ]
            src_path = filedialog.askopenfilename(title="Выбери картинку для иконки", filetypes=filetypes)
            if not src_path:
                return

            try:
                from PIL import Image
            except ImportError:
                messagebox.showerror(
                    "Иконка",
                    "Для смены иконки нужна библиотека Pillow.\n\nУстанови её командой:\npip install Pillow"
                )
                return

            icon_path = self._get_icon_path()
            try:
                img = Image.open(src_path).convert("RGBA")
                w, h = img.size
                side = min(w, h)
                left = (w - side) // 2
                top = (h - side) // 2
                img = img.crop((left, top, left + side, top + side))
                sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
                img.save(icon_path, format="ICO", sizes=sizes)
            except Exception as e:
                icon_status_label.config(text=f"Не удалось сохранить: {e}", fg=RED)
                return

            self._apply_window_icon()
            if self._settings_win is not None and self._settings_win.winfo_exists():
                self._apply_window_icon(self._settings_win)
            load_icon_preview()
            icon_status_label.config(
                text="Готово! Окно обновлено сразу.\nДля .exe/Пуска -- пересобери через build.bat.",
                fg=GREEN,
            )
            self._log("[t] Иконка приложения обновлена", "system")

        tk.Button(
            icon_col, text="Выбрать картинку", font=FONT_UI,
            fg="#fff", bg=ACCENT, activebackground=ACCENT_DIM, activeforeground="#fff",
            relief="flat", bd=0, padx=18, pady=7, cursor="hand2", command=choose_app_icon,
        ).pack(anchor="w")

        icon_status_label.pack(anchor="w", pady=(8, 0))

        load_icon_preview()

        # -- Размер аватарки в шапке окна --------------------------
        tk.Frame(panel, bg=BORDER, height=1).pack(fill="x", padx=20, pady=(16, 14))

        avatar_size_row = tk.Frame(panel, bg=BG)
        avatar_size_row.pack(fill="x", padx=20)

        avatar_size_top = tk.Frame(avatar_size_row, bg=BG)
        avatar_size_top.pack(fill="x")
        tk.Label(avatar_size_top, text="Размер аватарки в шапке", font=FONT_UI, fg=TEXT, bg=BG).pack(side="left")

        avatar_size_val = theme.get_header_avatar_size()
        avatar_size_value_label = tk.Label(
            avatar_size_top, text=f"{avatar_size_val} px", font=FONT_SMALL, fg=ACCENT, bg=BG, width=8, anchor="e",
        )
        avatar_size_value_label.pack(side="right")

        tk.Label(
            panel,
            text="Круглая аватарка активной модели слева от названия программы. "
                 "Вся строка заголовка подстраивается под новый размер сама.",
            font=FONT_SMALL, fg=TEXT_DIM, bg=BG, wraplength=560, justify="left",
        ).pack(fill="x", padx=20, pady=(2, 8))

        avatar_size_var = tk.IntVar(value=avatar_size_val)

        def on_avatar_size_change(v):
            iv = int(round(float(v)))
            avatar_size_value_label.config(text=f"{iv} px")
            self._apply_header_avatar_size(iv)
            try:
                theme.set_header_avatar_size(iv)
            except Exception:
                pass

        avatar_size_slider = RoundedSlider(
            avatar_size_row, from_=theme.HEADER_AVATAR_SIZE_MIN, to=theme.HEADER_AVATAR_SIZE_MAX,
            resolution=1,
            variable=avatar_size_var, bg=BG, troughcolor=BG_MSG,
            activebackground=ACCENT, command=on_avatar_size_change,
        )
        avatar_size_slider.pack(fill="x", padx=(0, 24), pady=(4, 0))

        def reset_avatar_size():
            avatar_size_var.set(theme.DEFAULT_HEADER_AVATAR_SIZE)
            avatar_size_slider.set(theme.DEFAULT_HEADER_AVATAR_SIZE)
            avatar_size_value_label.config(text=f"{theme.DEFAULT_HEADER_AVATAR_SIZE} px")
            self._apply_header_avatar_size(theme.DEFAULT_HEADER_AVATAR_SIZE)
            theme.set_header_avatar_size(theme.DEFAULT_HEADER_AVATAR_SIZE)

        tk.Button(
            panel, text="Сбросить размер", font=FONT_UI,
            fg=TEXT_DIM, bg=BG, activebackground=BG_PANEL, activeforeground=TEXT,
            relief="flat", bd=0, padx=14, pady=6, cursor="hand2", command=reset_avatar_size,
        ).pack(anchor="w", padx=20, pady=(8, 0))

        tk.Frame(panel, bg=BORDER, height=1).pack(fill="x", padx=20, pady=(16, 14))

        tk.Label(
            panel,
            text="Выбери акцентный цвет интерфейса на круговой палитре. "
                 "Изменение сохраняется и применяется сразу, в том числе при следующем запуске.",
            font=FONT_SMALL, fg=TEXT_DIM, bg=BG, wraplength=560, justify="left",
        ).pack(fill="x", padx=20, pady=(0, 14))

        body = tk.Frame(panel, bg=BG)
        body.pack(fill="both", expand=True, padx=20)

        # -- Левая часть: колесо + ползунок яркости --------------
        wheel_col = tk.Frame(body, bg=BG)
        wheel_col.pack(side="left")

        WHEEL_SIZE = 200
        cur_h, cur_s, cur_v = theme.hex_to_hsv(ACCENT)
        state = {"h": cur_h, "s": cur_s, "v": cur_v}

        wheel_canvas = tk.Canvas(
            wheel_col, width=WHEEL_SIZE, height=WHEEL_SIZE,
            bg=BG, highlightthickness=0, cursor="hand2",
        )
        wheel_canvas.pack()

        _wheel_cache = {}

        def render_wheel(v):
            """Рисует HSV-колесо (H = угол, S = расстояние от центра) при заданной
            яркости v. Векторизовано через numpy -- иначе попиксельный цикл на
            каждое движение слайдера яркости заметно лагает. Кэшируется по
            округлённой яркости, чтобы повторные значения не пересчитывались."""
            from PIL import Image, ImageTk
            import numpy as np

            v_key = round(v, 3)
            cached = _wheel_cache.get(v_key)
            if cached is not None:
                return ImageTk.PhotoImage(cached)

            size = WHEEL_SIZE
            cx = cy = size / 2
            radius = size / 2 - 2

            yy, xx = np.mgrid[0:size, 0:size].astype(np.float64)
            dx = xx - cx
            dy = yy - cy
            dist = np.sqrt(dx * dx + dy * dy)
            mask = dist <= radius

            angle = np.arctan2(dy, dx)
            h = ((np.degrees(angle) + 360) % 360) / 360.0
            s = np.clip(dist / radius, 0, 1.0)

            # HSV -> RGB, векторизованная версия (colorsys работает только
            # на скалярах, поэтому формулу расписываем явно по секторам).
            i = np.floor(h * 6.0).astype(int) % 6
            f = h * 6.0 - np.floor(h * 6.0)
            p = v * (1 - s)
            q = v * (1 - s * f)
            t = v * (1 - s * (1 - f))

            r = np.select([i == 0, i == 1, i == 2, i == 3, i == 4, i == 5],
                          [v, q, p, p, t, v])
            g = np.select([i == 0, i == 1, i == 2, i == 3, i == 4, i == 5],
                          [t, v, v, q, p, p])
            b = np.select([i == 0, i == 1, i == 2, i == 3, i == 4, i == 5],
                          [p, p, t, v, v, q])

            alpha = np.where(mask, 255, 0).astype(np.uint8)
            rgba = np.stack([
                (r * 255).astype(np.uint8),
                (g * 255).astype(np.uint8),
                (b * 255).astype(np.uint8),
                alpha,
            ], axis=-1)

            img = Image.fromarray(rgba, "RGBA")
            _wheel_cache[v_key] = img
            return ImageTk.PhotoImage(img)

        wheel_imgtk = {"img": None}

        def redraw_wheel():
            wheel_imgtk["img"] = render_wheel(state["v"])
            wheel_canvas.delete("wheelimg")
            wheel_canvas.create_image(0, 0, anchor="nw", image=wheel_imgtk["img"], tags="wheelimg")
            draw_cursor()

        def draw_cursor():
            import math
            wheel_canvas.delete("cursor")
            size = WHEEL_SIZE
            cx = cy = size / 2
            radius = size / 2 - 2
            angle_rad = state["h"] * 2 * math.pi
            dist = state["s"] * radius
            x = cx + dist * math.cos(angle_rad)
            y = cy + dist * math.sin(angle_rad)
            r = 7
            wheel_canvas.create_oval(
                x - r, y - r, x + r, y + r,
                outline="#ffffff", width=2, tags="cursor",
            )
            wheel_canvas.create_oval(
                x - r + 2, y - r + 2, x + r - 2, y + r - 2,
                outline="#000000", width=1, tags="cursor",
            )

        def current_hex():
            return theme.hsv_to_hex(state["h"], state["s"], state["v"])

        def update_preview():
            hexval = current_hex()
            preview_swatch.config(bg=hexval)
            hex_var.set(hexval.upper())
            preview_btn.config(bg=hexval, activebackground=theme.dim(hexval, 0.7))

        def pick_from_wheel(event):
            import math
            size = WHEEL_SIZE
            cx = cy = size / 2
            radius = size / 2 - 2
            dx = event.x - cx
            dy = event.y - cy
            dist = (dx * dx + dy * dy) ** 0.5
            if dist > radius:
                # клик за пределами круга -- подрезаем к границе, чтобы
                # не "терять" выбор, если кликнули чуть-чуть мимо
                dist = radius
            angle = math.atan2(dy, dx)
            state["h"] = (angle / (2 * math.pi)) % 1.0
            state["s"] = min(1.0, dist / radius)
            draw_cursor()
            update_preview()

        wheel_canvas.bind("<Button-1>", pick_from_wheel)
        wheel_canvas.bind("<B1-Motion>", pick_from_wheel)

        # -- Ползунок яркости (V) --------------------------------
        bright_row = tk.Frame(wheel_col, bg=BG)
        bright_row.pack(fill="x", pady=(10, 0))
        tk.Label(bright_row, text="Яркость", font=FONT_SMALL, fg=TEXT_DIM, bg=BG).pack(anchor="w")

        bright_var = tk.DoubleVar(value=cur_v)

        def on_brightness(val):
            state["v"] = float(val)
            redraw_wheel()
            update_preview()

        brightness_scale = RoundedSlider(
            bright_row, from_=0.15, to=1.0, resolution=0.01,
            variable=bright_var, bg=BG, troughcolor=BG_PANEL,
            activebackground=ACCENT, command=on_brightness,
        )
        brightness_scale.pack(fill="x", padx=(0, 24), pady=(4, 0))

        # -- Правая часть: превью + hex + пресеты + сохранить ----
        right_col = tk.Frame(body, bg=BG)
        right_col.pack(side="left", fill="both", expand=True, padx=(28, 0))

        tk.Label(right_col, text="Превью", font=FONT_SMALL, fg=TEXT_DIM, bg=BG, anchor="w").pack(fill="x")

        preview_row = tk.Frame(right_col, bg=BG)
        preview_row.pack(fill="x", pady=(6, 14))

        preview_swatch = tk.Frame(preview_row, bg=ACCENT, width=40, height=40,
                                   highlightthickness=1, highlightbackground=BORDER)
        preview_swatch.pack(side="left")
        preview_swatch.pack_propagate(False)

        preview_btn = tk.Button(
            preview_row, text="Пример кнопки", font=FONT_UI,
            fg="#fff", bg=ACCENT, activebackground=ACCENT_DIM, activeforeground="#fff",
            relief="flat", bd=0, padx=16, pady=7, cursor="hand2",
            state="disabled",
        )
        preview_btn.pack(side="left", padx=(12, 0))

        # -- Hex-поле для точного ввода ---------------------------
        hex_row = tk.Frame(right_col, bg=BG)
        hex_row.pack(fill="x", pady=(0, 14))
        tk.Label(hex_row, text="HEX", font=FONT_SMALL, fg=TEXT_DIM, bg=BG, width=6, anchor="w").pack(side="left")

        hex_var = tk.StringVar(value=ACCENT.upper())
        hex_entry = tk.Entry(
            hex_row, textvariable=hex_var, font=FONT_MONO, bg=BG_MSG, fg=TEXT,
            insertbackground=TEXT, relief="flat", bd=0, highlightthickness=1,
            highlightbackground=BORDER, highlightcolor=ACCENT, width=10,
        )
        hex_entry.pack(side="left", ipady=4)

        hex_status = tk.Label(hex_row, text="", font=FONT_SMALL, fg=RED, bg=BG)
        hex_status.pack(side="left", padx=(10, 0))

        def apply_hex_from_entry(event=None):
            value = hex_var.get().strip()
            if not value.startswith("#"):
                value = "#" + value
            if not theme.is_valid_hex_color(value):
                hex_status.config(text="Неверный формат (например #7c5cfc)")
                return
            h, s, v = theme.hex_to_hsv(value)
            state["h"], state["s"], state["v"] = h, s, v
            brightness_scale.set(v)
            redraw_wheel()
            update_preview()
            hex_status.config(text="")

        hex_entry.bind("<Return>", apply_hex_from_entry)
        hex_entry.bind("<FocusOut>", apply_hex_from_entry)

        # -- Быстрые пресеты ---------------------------------------
        tk.Label(right_col, text="Быстрый выбор", font=FONT_SMALL, fg=TEXT_DIM, bg=BG, anchor="w").pack(
            fill="x", pady=(0, 6)
        )

        presets_row = tk.Frame(right_col, bg=BG)
        presets_row.pack(fill="x", pady=(0, 16))

        presets = [
            "#7c5cfc",  # фиолетовый (оригинальный)
            "#5cf0ff",  # циан
            "#39d98a",  # зелёный
            "#f5c542",  # жёлтый
            "#ff5c5c",  # красный
            "#f76cb0",  # розовый
            "#5c8bfc",  # синий
            "#f7a35c",  # оранжевый
        ]

        def make_pick_preset(hex_color):
            def _pick():
                h, s, v = theme.hex_to_hsv(hex_color)
                state["h"], state["s"], state["v"] = h, s, v
                brightness_scale.set(v)
                redraw_wheel()
                update_preview()
            return _pick

        for hex_color in presets:
            sw = tk.Frame(
                presets_row, bg=hex_color, width=28, height=28, cursor="hand2",
                highlightthickness=1, highlightbackground=BORDER,
            )
            sw.pack(side="left", padx=(0, 8))
            sw.pack_propagate(False)
            sw.bind("<Button-1>", lambda e, c=hex_color: make_pick_preset(c)())

        status_label = tk.Label(right_col, text="", font=FONT_SMALL, fg=GREEN, bg=BG)
        status_label.pack(anchor="w", pady=(0, 6))

        def save_color():
            hexval = current_hex()
            try:
                theme.set_accent_color(hexval)
                # Сохраняем заодно и текущие значения слайдеров "Закруглённость
                # пузырей" / "Масштаб сообщений" -- иначе при полной пересборке
                # окна (см. ниже) панель чата заново подхватит значения из
                # файла, а несохранённые движения этих слайдеров потеряются
                # и выглядят как "сброс настроек".
                theme.set_chat_bubble_radius(radius_var.get())
                theme.set_chat_font_scale(scale_var.get())
            except Exception as e:
                status_label.config(text=f"Ошибка сохранения: {e}", fg=RED)
                return
            self._log(f"[t] Акцентный цвет изменён: {hexval}", "system")
            self._log("[t] Внешний вид чата обновлён", "system")
            # Цвет хранится глобально как переменные модуля -- перечитываем
            # их и полностью перестраиваем интерфейс, иначе уже созданные
            # виджеты (которые получили старый ACCENT при создании) не
            # обновятся сами по себе.
            _refresh_accent_globals()
            self._chat_redraw_all()
            self._rebuild_ui_with_new_theme(reopen_section="theme")

        footer = tk.Frame(right_col, bg=BG, pady=4)
        footer.pack(fill="x")

        tk.Button(
            footer, text="Сохранить и применить", font=FONT_UI,
            fg="#fff", bg=ACCENT, activebackground=ACCENT_DIM, activeforeground="#fff",
            relief="flat", bd=0, padx=18, pady=7, cursor="hand2", command=save_color,
        ).pack(side="left")

        def reset_default():
            h, s, v = theme.hex_to_hsv(theme.DEFAULT_ACCENT)
            state["h"], state["s"], state["v"] = h, s, v
            brightness_scale.set(v)
            redraw_wheel()
            update_preview()

        tk.Button(
            footer, text="Сбросить", font=FONT_UI,
            fg=TEXT_DIM, bg=BG, activebackground=BG_PANEL, activeforeground=TEXT,
            relief="flat", bd=0, padx=14, pady=7, cursor="hand2", command=reset_default,
        ).pack(side="left", padx=(8, 0))

        redraw_wheel()
        update_preview()

        # -- Кастомизация вкладки "Чат" (пузыри сообщений) --------
        # Касается только отображения в чате -- журнал ([ЖУРНАЛ]) этими
        # настройками не управляется, рендерится как обычный текст.
        tk.Frame(panel, bg=BORDER, height=1).pack(fill="x", padx=20, pady=(18, 14))

        chat_custom_header = tk.Frame(panel, bg=BG)
        chat_custom_header.pack(fill="x", padx=20)
        tk.Label(
            chat_custom_header, text="Чат", font=FONT_BIG, fg=TEXT, bg=BG,
        ).pack(side="left")

        tk.Label(
            panel,
            text="Внешний вид пузырей сообщений во вкладке \"Чат\" (журнал не меняется).",
            font=FONT_SMALL, fg=TEXT_DIM, bg=BG, wraplength=560, justify="left",
        ).pack(fill="x", padx=20, pady=(2, 12))

        chat_custom_body = tk.Frame(panel, bg=BG)
        chat_custom_body.pack(fill="x", padx=20)

        radius_val = theme.get_chat_bubble_radius()
        scale_val = theme.get_chat_font_scale()

        # -- Закруглённость пузырей --
        radius_row = tk.Frame(chat_custom_body, bg=BG)
        radius_row.pack(fill="x", pady=(0, 14))

        radius_top = tk.Frame(radius_row, bg=BG)
        radius_top.pack(fill="x")
        tk.Label(radius_top, text="Закруглённость пузырей", font=FONT_SMALL, fg=TEXT, bg=BG).pack(side="left")
        radius_value_label = tk.Label(
            radius_top, text=f"{radius_val} px", font=FONT_SMALL, fg=ACCENT, bg=BG, width=8, anchor="e",
        )
        radius_value_label.pack(side="right")

        radius_var = tk.IntVar(value=radius_val)

        def on_radius_change(v):
            radius_value_label.config(text=f"{int(float(v))} px")

        radius_slider = RoundedSlider(
            radius_row, from_=theme.CHAT_BUBBLE_RADIUS_MIN, to=theme.CHAT_BUBBLE_RADIUS_MAX, resolution=1,
            variable=radius_var, bg=BG, troughcolor=BG_MSG,
            activebackground=ACCENT, command=on_radius_change,
        )
        radius_slider.pack(fill="x", padx=(0, 24), pady=(4, 0))

        # -- Масштаб сообщений --
        scale_row = tk.Frame(chat_custom_body, bg=BG)
        scale_row.pack(fill="x", pady=(0, 14))

        scale_top = tk.Frame(scale_row, bg=BG)
        scale_top.pack(fill="x")
        tk.Label(scale_top, text="Масштаб сообщений", font=FONT_SMALL, fg=TEXT, bg=BG).pack(side="left")
        scale_value_label = tk.Label(
            scale_top, text=f"{scale_val:.1f}x", font=FONT_SMALL, fg=ACCENT, bg=BG, width=8, anchor="e",
        )
        scale_value_label.pack(side="right")

        scale_var = tk.DoubleVar(value=scale_val)

        def on_scale_change(v):
            scale_value_label.config(text=f"{float(v):.1f}x")

        scale_slider = RoundedSlider(
            scale_row, from_=theme.CHAT_FONT_SCALE_MIN, to=theme.CHAT_FONT_SCALE_MAX, resolution=0.1,
            variable=scale_var, bg=BG, troughcolor=BG_MSG,
            activebackground=ACCENT, command=on_scale_change,
        )
        scale_slider.pack(fill="x", padx=(0, 24), pady=(4, 0))

        chat_status_label = tk.Label(chat_custom_body, text="", font=FONT_SMALL, fg=GREEN, bg=BG)
        chat_status_label.pack(anchor="w", pady=(2, 10))

        def apply_chat_customization():
            try:
                theme.set_chat_bubble_radius(radius_var.get())
                theme.set_chat_font_scale(scale_var.get())
                self._chat_redraw_all()
                chat_status_label.config(text="Применено", fg=GREEN)
                self._log("[t] Внешний вид чата обновлён", "system")
            except Exception as e:
                chat_status_label.config(text=f"Ошибка: {e}", fg=RED)

        def reset_chat_customization():
            radius_var.set(theme.DEFAULT_CHAT_BUBBLE_RADIUS)
            scale_var.set(theme.DEFAULT_CHAT_FONT_SCALE)
            radius_slider.set(theme.DEFAULT_CHAT_BUBBLE_RADIUS)
            scale_slider.set(theme.DEFAULT_CHAT_FONT_SCALE)
            radius_value_label.config(text=f"{theme.DEFAULT_CHAT_BUBBLE_RADIUS} px")
            scale_value_label.config(text=f"{theme.DEFAULT_CHAT_FONT_SCALE:.1f}x")
            apply_chat_customization()

        chat_footer = tk.Frame(chat_custom_body, bg=BG)
        chat_footer.pack(fill="x", pady=(0, 4))

        tk.Button(
            chat_footer, text="Применить к чату", font=FONT_UI,
            fg="#fff", bg=ACCENT, activebackground=ACCENT_DIM, activeforeground="#fff",
            relief="flat", bd=0, padx=18, pady=7, cursor="hand2", command=apply_chat_customization,
        ).pack(side="left")

        tk.Button(
            chat_footer, text="Сбросить", font=FONT_UI,
            fg=TEXT_DIM, bg=BG, activebackground=BG_PANEL, activeforeground=TEXT,
            relief="flat", bd=0, padx=14, pady=7, cursor="hand2", command=reset_chat_customization,
        ).pack(side="left", padx=(8, 0))

        return outer_panel

    # -- Раздел: О программе ---------------------------------------

    def _build_about_panel(self, parent):
        """Информационная панель: версия, имя ассистента, используемые
        компоненты. Только для просмотра -- здесь ничего не сохраняется."""
        panel = tk.Frame(parent, bg=BG)

        header = tk.Frame(panel, bg=BG, pady=16)
        header.pack(fill="x", padx=20)
        tk.Label(header, text="О программе", font=FONT_BIG, fg=TEXT, bg=BG).pack(side="left")

        card = tk.Frame(panel, bg=BG_PANEL, highlightthickness=1, highlightbackground=BORDER)
        card.pack(fill="x", padx=20, pady=(0, 16))

        inner = tk.Frame(card, bg=BG_PANEL, padx=20, pady=18)
        inner.pack(fill="x")

        tk.Label(
            inner, text="Ария", font=("Segoe UI", 20, "bold"), fg=ACCENT, bg=BG_PANEL,
        ).pack(anchor="w")
        tk.Label(
            inner, text=f"Версия {theme.APP_VERSION}", font=FONT_UI, fg=TEXT, bg=BG_PANEL,
        ).pack(anchor="w", pady=(2, 0))
        tk.Label(
            inner, text="Голосовой ассистент с локальным распознаванием речи,\n"
                        "синтезом голоса и поддержкой облачных и локальных LLM.",
            font=FONT_SMALL, fg=TEXT_DIM, bg=BG_PANEL, justify="left",
        ).pack(anchor="w", pady=(10, 0))

        tk.Frame(panel, bg=BORDER, height=1).pack(fill="x", padx=20, pady=(4, 14))

        info_body = tk.Frame(panel, bg=BG)
        info_body.pack(fill="x", padx=20)

        rows = [
            ("Распознавание речи", "faster-whisper (large-v3-turbo)"),
            ("Синтез речи", "Silero TTS"),
            ("Зрение (Vision)", "Moondream (локально) / облачные модели"),
            ("Язык интерфейса", "Русский"),
            ("Python", f"{sys.version.split()[0]}"),
        ]

        for label, value in rows:
            row = tk.Frame(info_body, bg=BG)
            row.pack(fill="x", pady=4)
            tk.Label(row, text=label, font=FONT_SMALL, fg=TEXT_DIM, bg=BG, width=22, anchor="w").pack(side="left")
            tk.Label(row, text=value, font=FONT_SMALL, fg=TEXT, bg=BG, anchor="w").pack(side="left")

        tk.Frame(panel, bg=BORDER, height=1).pack(fill="x", padx=20, pady=(14, 14))

        tk.Label(
            panel,
            text="Все настраиваемые данные (личность, модели, тема, расходы) хранятся\n"
                 "локально рядом с программой и никуда не передаются.",
            font=FONT_SMALL, fg=TEXT_DIM, bg=BG, justify="left",
        ).pack(anchor="w", padx=20)

        return panel

    # -- Раздел: Расходы (только облачные модели) -----------------

    def _build_cost_panel(self, parent):
        """Учёт расходов на облачные модели (DeepSeek и другие cloud-профили).
        Показывает потраченную сумму и остаток от стартового баланса,
        позволяет менять валюту отображения, курсы обмена и стартовый
        баланс. Цены за токены берутся из cost_tracker (с дефолтами для
        DeepSeek), их также можно поправить здесь под актуальный тариф."""
        import model_profiles as mp

        panel = tk.Frame(parent, bg=BG)

        header = tk.Frame(panel, bg=BG, pady=16)
        header.pack(fill="x", padx=20)
        tk.Label(header, text="Расходы", font=FONT_BIG, fg=TEXT, bg=BG).pack(side="left")

        tk.Label(
            panel,
            text="Учёт расходов по токенам для облачных моделей (DeepSeek и другие "
                 "облачные провайдеры). Локальные модели (Ollama, LM Studio) бесплатны "
                 "и здесь не считаются.",
            font=FONT_SMALL, fg=TEXT_DIM, bg=BG, wraplength=560, justify="left",
        ).pack(fill="x", padx=20, pady=(0, 14))

        scroll_outer = tk.Frame(panel, bg=BG)
        scroll_outer.pack(fill="both", expand=True, padx=20)

        canvas = tk.Canvas(scroll_outer, bg=BG, highlightthickness=0)
        style = ttk.Style()
        style.layout("Cost.Vertical.TScrollbar", [
            ("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
                ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"}),
            ]}),
        ])
        style.configure(
            "Cost.Vertical.TScrollbar",
            background=ACCENT_DIM, troughcolor=BG, bordercolor=BG,
            relief="flat", borderwidth=0, width=8,
        )
        style.map("Cost.Vertical.TScrollbar", background=[("active", ACCENT), ("pressed", ACCENT)])
        scroll = ttk.Scrollbar(scroll_outer, orient="vertical", command=canvas.yview,
                                style="Cost.Vertical.TScrollbar")

        body = tk.Frame(canvas, bg=BG)

        body.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas_window = canvas.create_window((0, 0), window=body, anchor="nw")
        canvas.configure(yscrollcommand=lambda f, l, c="canvas", s="scroll": (_clamp_scroll(f, l, canvas), scroll.set(f, l)))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(canvas_window, width=e.width))
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        def _mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            if canvas.yview()[0] < 0.0: canvas.yview_moveto(0.0)
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _mousewheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

        status_label = tk.Label(panel, text="", font=FONT_SMALL, fg=GREEN, bg=BG)
        status_label.pack(anchor="w", padx=20, pady=(6, 10))

        def show_status(text, color=GREEN):
            status_label.config(text=text, fg=color)

        # -- Текущее состояние: баланс ----------------------------
        tk.Label(body, text="Баланс", font=FONT_BIG, fg=TEXT, bg=BG, anchor="w").pack(
            fill="x", pady=(0, 8)
        )

        balance_card = tk.Frame(body, bg=BG_PANEL, padx=14, pady=12)
        balance_card.pack(fill="x", pady=(0, 16))

        def balance_row(label):
            row = tk.Frame(balance_card, bg=BG_PANEL)
            row.pack(fill="x", pady=3)
            tk.Label(row, text=label, font=FONT_SMALL, fg=TEXT_DIM,
                     bg=BG_PANEL, width=16, anchor="w").pack(side="left")
            val = tk.Label(row, text="--", font=FONT_STAT, fg=TEXT, bg=BG_PANEL, anchor="e")
            val.pack(side="right")
            return val

        spent_val = balance_row("Потрачено всего")
        remaining_val = balance_row("Остаток")

        def refresh_balance_display():
            currency = ct.get_display_currency()
            spent_cny = ct.get_lifetime_spent_cny()
            remaining_cny = ct.get_remaining_balance_cny()
            spent_val.config(text=ct.format_amount(ct.cny_to_currency(spent_cny, currency), currency))
            remaining_disp = ct.cny_to_currency(remaining_cny, currency)
            remaining_val.config(
                text=ct.format_amount(remaining_disp, currency),
                fg=RED if remaining_disp < 0 else GREEN,
            )

        refresh_balance_display()

        def do_reset_usage():
            if not messagebox.askyesno(
                "Сбросить расходы",
                "Это сбросит счётчик потраченных токенов в ноль (например, после "
                "реального пополнения баланса на платформе DeepSeek). Продолжить?",
            ):
                return
            ct.reset_usage()
            refresh_balance_display()
            self._refresh_cost_display()
            self._log("[$] Счётчик расходов сброшен", "system")
            show_status("Счётчик расходов сброшен")

        tk.Button(
            balance_card, text="Сбросить счётчик расходов", font=FONT_SMALL,
            fg=TEXT_DIM, bg=BG, activebackground=BORDER, activeforeground=TEXT,
            relief="flat", bd=0, padx=10, pady=5, cursor="hand2", command=do_reset_usage,
        ).pack(anchor="w", pady=(8, 0))

        # -- Стартовый баланс ---------------------------------------
        tk.Label(body, text="Стартовый баланс (в юанях)", font=FONT_SMALL, fg=TEXT_DIM,
                 bg=BG, anchor="w").pack(fill="x", pady=(0, 4))

        balance_row_frame = tk.Frame(body, bg=BG)
        balance_row_frame.pack(fill="x", pady=(0, 16))

        balance_entry = tk.Entry(
            balance_row_frame, font=FONT_UI, bg=BG_MSG, fg=TEXT, insertbackground=TEXT,
            relief="flat", bd=0, highlightthickness=1, highlightbackground=BORDER,
            highlightcolor=ACCENT, width=12,
        )
        balance_entry.insert(0, f"{ct.get_starting_balance_cny():.2f}")
        balance_entry.pack(side="left", ipady=5)

        def save_balance():
            try:
                value = float(balance_entry.get().strip().replace(",", "."))
            except ValueError:
                show_status("Баланс должен быть числом", RED)
                return
            ct.set_starting_balance_cny(value)
            refresh_balance_display()
            self._refresh_cost_display()
            show_status("Стартовый баланс сохранён")
            self._log(f"[$] Стартовый баланс изменён: {value:.2f} ¥", "system")

        tk.Button(
            balance_row_frame, text="Сохранить", font=FONT_UI,
            fg="#fff", bg=ACCENT, activebackground=ACCENT_DIM, activeforeground="#fff",
            relief="flat", bd=0, padx=14, pady=6, cursor="hand2", command=save_balance,
        ).pack(side="left", padx=(8, 0))

        # -- Валюта отображения ------------------------------------
        tk.Label(body, text="Валюта отображения", font=FONT_SMALL, fg=TEXT_DIM,
                 bg=BG, anchor="w").pack(fill="x", pady=(0, 6))

        currency_row = tk.Frame(body, bg=BG)
        currency_row.pack(fill="x", pady=(0, 16))

        currency_buttons = {}

        def select_currency(code):
            ct.set_display_currency(code)
            for c, btn in currency_buttons.items():
                is_sel = c == code
                btn.config(bg=ACCENT if is_sel else BG_PANEL, fg="#fff" if is_sel else TEXT)
            refresh_balance_display()
            self._refresh_cost_display()
            self._log(f"[$] Валюта отображения расходов: {ct.CURRENCY_NAMES[code]}", "system")

        for code in ct.CURRENCIES:
            is_sel = code == ct.get_display_currency()
            b = tk.Button(
                currency_row, text=f"{ct.CURRENCY_NAMES[code]} ({ct.CURRENCY_SYMBOLS[code]})",
                font=FONT_SMALL, fg="#fff" if is_sel else TEXT,
                bg=ACCENT if is_sel else BG_PANEL, activebackground=ACCENT_DIM, activeforeground="#fff",
                relief="flat", bd=0, padx=12, pady=6, cursor="hand2",
                command=lambda c=code: select_currency(c),
            )
            b.pack(side="left", padx=(0, 8))
            currency_buttons[code] = b

        # -- Курсы обмена (сколько единиц валюты за 1 юань) ---------
        tk.Label(body, text="Курс обмена (за 1 юань)", font=FONT_SMALL, fg=TEXT_DIM,
                 bg=BG, anchor="w").pack(fill="x", pady=(0, 6))
        tk.Label(
            body,
            text="Поставь курс, по которому реально пополнял баланс DeepSeek -- "
                 "так пересчёт остатка будет точным.",
            font=FONT_SMALL, fg=TEXT_DIM, bg=BG, wraplength=560, justify="left",
        ).pack(fill="x", pady=(0, 8))

        rates_card = tk.Frame(body, bg=BG_PANEL, padx=14, pady=12)
        rates_card.pack(fill="x", pady=(0, 16))

        rate_entries = {}
        for code in ct.CURRENCIES:
            if code == "CNY":
                continue
            row = tk.Frame(rates_card, bg=BG_PANEL)
            row.pack(fill="x", pady=4)
            tk.Label(
                row, text=f"1 ¥ =", font=FONT_SMALL, fg=TEXT_DIM, bg=BG_PANEL, width=6, anchor="w"
            ).pack(side="left")
            entry = tk.Entry(
                row, font=FONT_UI, bg=BG_MSG, fg=TEXT, insertbackground=TEXT,
                relief="flat", bd=0, highlightthickness=1, highlightbackground=BORDER,
                highlightcolor=ACCENT, width=10,
            )
            entry.insert(0, f"{ct.get_rate_per_cny(code):.4f}")
            entry.pack(side="left", ipady=4)
            tk.Label(
                row, text=ct.CURRENCY_NAMES[code], font=FONT_SMALL, fg=TEXT_DIM, bg=BG_PANEL,
            ).pack(side="left", padx=(8, 0))
            rate_entries[code] = entry

        def save_rates():
            try:
                parsed = {
                    code: float(entry.get().strip().replace(",", "."))
                    for code, entry in rate_entries.items()
                }
            except ValueError:
                show_status("Курс должен быть числом", RED)
                return
            for code, value in parsed.items():
                ct.set_rate_per_cny(code, value)
            refresh_balance_display()
            self._refresh_cost_display()
            show_status("Курсы обмена сохранены")
            self._log("[$] Курсы обмена валют обновлены", "system")

        tk.Button(
            rates_card, text="Сохранить курсы", font=FONT_UI,
            fg="#fff", bg=ACCENT, activebackground=ACCENT_DIM, activeforeground="#fff",
            relief="flat", bd=0, padx=14, pady=6, cursor="hand2", command=save_rates,
        ).pack(anchor="w", pady=(8, 0))

        # -- Цены за токены по моделям -------------------------------
        tk.Label(body, text="Цены за 1M токенов (в юанях)", font=FONT_SMALL, fg=TEXT_DIM,
                 bg=BG, anchor="w").pack(fill="x", pady=(0, 6))
        tk.Label(
            body,
            text="Дефолты соответствуют официальному прайсу DeepSeek V4 Flash/Pro. "
                 "Поправь, если тариф изменился, или для другого облачного провайдера.",
            font=FONT_SMALL, fg=TEXT_DIM, bg=BG, wraplength=560, justify="left",
        ).pack(fill="x", pady=(0, 8))

        prices_card = tk.Frame(body, bg=BG_PANEL, padx=14, pady=12)
        prices_card.pack(fill="x", pady=(0, 16))

        cloud_profiles = [p for p in mp.list_profiles() if p.get("type") == "cloud"]
        seen_models = set()
        price_entries = {}

        for profile in cloud_profiles:
            model_id = profile["model"]
            if model_id in seen_models:
                continue
            seen_models.add(model_id)

            price = ct.get_price_for_model(model_id)
            row = tk.Frame(prices_card, bg=BG_PANEL)
            row.pack(fill="x", pady=4)

            tk.Label(
                row, text=f"{profile['name']} ({model_id})", font=FONT_SMALL, fg=TEXT,
                bg=BG_PANEL, anchor="w",
            ).pack(fill="x")

            sub_row = tk.Frame(row, bg=BG_PANEL)
            sub_row.pack(fill="x", pady=(2, 0))

            tk.Label(sub_row, text="вход:", font=FONT_SMALL, fg=TEXT_DIM, bg=BG_PANEL).pack(side="left")
            in_entry = tk.Entry(
                sub_row, font=FONT_UI, bg=BG_MSG, fg=TEXT, insertbackground=TEXT, width=8,
                relief="flat", bd=0, highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT,
            )
            in_entry.insert(0, f"{price['input']:.4f}")
            in_entry.pack(side="left", padx=(4, 12), ipady=3)

            tk.Label(sub_row, text="выход:", font=FONT_SMALL, fg=TEXT_DIM, bg=BG_PANEL).pack(side="left")
            out_entry = tk.Entry(
                sub_row, font=FONT_UI, bg=BG_MSG, fg=TEXT, insertbackground=TEXT, width=8,
                relief="flat", bd=0, highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT,
            )
            out_entry.insert(0, f"{price['output']:.4f}")
            out_entry.pack(side="left", padx=(4, 0), ipady=3)

            price_entries[model_id] = (in_entry, out_entry)

        def save_prices():
            try:
                parsed = {
                    model_id: (float(i.get().strip().replace(",", ".")),
                               float(o.get().strip().replace(",", ".")))
                    for model_id, (i, o) in price_entries.items()
                }
            except ValueError:
                show_status("Цены должны быть числами", RED)
                return
            for model_id, (input_price, output_price) in parsed.items():
                ct.set_price_for_model(model_id, input_price, output_price)
            refresh_balance_display()
            self._refresh_cost_display()
            show_status("Цены сохранены")
            self._log("[$] Цены за токены обновлены", "system")

        if price_entries:
            tk.Button(
                prices_card, text="Сохранить цены", font=FONT_UI,
                fg="#fff", bg=ACCENT, activebackground=ACCENT_DIM, activeforeground="#fff",
                relief="flat", bd=0, padx=14, pady=6, cursor="hand2", command=save_prices,
            ).pack(anchor="w", pady=(8, 0))
        else:
            tk.Label(
                prices_card, text="Нет облачных профилей с указанной моделью.",
                font=FONT_SMALL, fg=TEXT_DIM, bg=BG_PANEL,
            ).pack(anchor="w")

        return panel

    def _rebuild_ui_with_new_theme(self, reopen_section: str = None):
        """Полностью перестраивает главное окно и (если было открыто) окно
        настроек с уже обновлёнными ACCENT/ACCENT_DIM. Tkinter-виджеты не
        умеют менять цвет «реактивно» -- они получают цвет один раз при
        создании, поэтому самый надёжный способ применить новый акцент
        ко всему интерфейсу -- пересобрать виджеты заново."""
        was_running = self.running
        settings_was_open = self._settings_win is not None

        if self._settings_win is not None:
            self._save_settings_win_geometry()
            try:
                self._settings_win.destroy()
            except Exception:
                pass
            self._settings_win = None
            self._settings_panels = {}
            self._settings_section_btns = {}

        for widget in self.root.winfo_children():
            widget.destroy()

        self._build_ui()
        _apply_dark_titlebar(self.root)
        self._set_status(was_running)

        if settings_was_open:
            self.open_settings(section=reopen_section or "theme")

    def _sync_active_model_label(self):
        try:
            import model_profiles as mp
            name = mp.get_active_profile()["name"]
            self.header_model_label.config(text=f"·  {name}")
        except Exception:
            pass
        self._refresh_header_avatar()
        self._update_cost_visibility()
        self._refresh_cost_display()

    def _apply_header_avatar_size(self, size: int):
        """Применяет новый размер круглой аватарки в шапке окна сразу,
        без пересборки всего интерфейса. Строка заголовка (header) -- это
        обычный pack-контейнер без фиксированной высоты, поэтому при смене
        размера canvas вся строка (включая текст и статус справа) сама
        подстраивается под новую высоту -- выравнивание не ломается."""
        size = max(theme.HEADER_AVATAR_SIZE_MIN, min(theme.HEADER_AVATAR_SIZE_MAX, int(size)))
        self._header_avatar_size = size
        self.header_avatar_canvas.config(width=size, height=size)
        self._refresh_header_avatar()

    def _refresh_header_avatar(self):
        """Перерисовывает круглую аватарку активной модели в шапке окна,
        плюс маленький индикатор готовности поверх неё (зелёный = ассистент
        загружен и слушает, серый = ещё не готов/остановлен)."""
        canvas = self.header_avatar_canvas
        size = self._header_avatar_size
        canvas.delete("all")

        avatar_path = None
        try:
            import model_profiles as mp
            active = mp.get_active_profile()
            avatar_path = os.path.join(self._get_avatars_dir(), f"{active['id']}.png")
        except Exception:
            pass

        round_img = _make_round_avatar(avatar_path, size)
        if round_img is not None:
            from PIL import ImageTk
            imgtk = ImageTk.PhotoImage(round_img)
            self._header_avatar_imgtk = imgtk
            canvas.create_image(0, 0, anchor="nw", image=imgtk)
        else:
            canvas.create_oval(1, 1, size - 1, size - 1, outline=ACCENT, width=2, fill=BG)
            canvas.create_text(size // 2, size // 2, text="*", fill=ACCENT, font=("Segoe UI", 12, "bold"))

        # Индикатор готовности -- кружок в правом нижнем углу аватарки,
        # с тонкой рамкой цвета фона чтобы отделиться от картинки под ним.
        dot_r = max(5, size // 7)
        cx = size - dot_r
        cy = size - dot_r
        dot_color = GREEN if self._ready else "#5a5a5a"
        canvas.create_oval(
            cx - dot_r - 2, cy - dot_r - 2, cx + dot_r + 2, cy + dot_r + 2,
            fill=BG, outline="",
        )
        canvas.create_oval(
            cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r,
            fill=dot_color, outline="",
        )

    # -- Расходы на облачные модели (DeepSeek и т.п.) --------

    def _is_active_model_cloud(self) -> bool:
        try:
            import model_profiles as mp
            return mp.get_active_profile().get("type") == "cloud"
        except Exception:
            return False

    def _has_cloud_profile(self) -> bool:
        try:
            import model_profiles as mp
            return any(p.get("type") == "cloud" for p in mp.list_profiles())
        except Exception:
            return False

    def _update_cost_visibility(self):
        """Показывает блок 'РАСХОДЫ' только если активная модель облачная
        (DeepSeek и любой другой cloud-профиль). Локальные движки (Ollama,
        LM Studio) бесплатны, поэтому блок для них скрыт."""
        should_show = self._is_active_model_cloud()
        if should_show == self._cost_visible:
            return
        self._cost_visible = should_show
        if should_show:
            self.cost_separator.pack(fill="x", pady=10)
            self.cost_header.pack(anchor="w", pady=(0, 6))
            self.cost_frame.pack(fill="x")
        else:
            self.cost_separator.pack_forget()
            self.cost_header.pack_forget()
            self.cost_frame.pack_forget()

    def _refresh_cost_display(self):
        """Перечитывает накопленные расходы и остаток баланса, обновляет
        подписи в выбранной пользователем валюте."""
        if not self._cost_visible:
            return
        try:
            currency = ct.get_display_currency()
            spent_cny = ct.get_lifetime_spent_cny()
            remaining_cny = ct.get_remaining_balance_cny()
            spent_disp = ct.cny_to_currency(spent_cny, currency)
            remaining_disp = ct.cny_to_currency(remaining_cny, currency)
            self.cost_spent.config(text=ct.format_amount(spent_disp, currency))
            remaining_color = RED if remaining_disp < 0 else GREEN
            self.cost_remaining.config(text=ct.format_amount(remaining_disp, currency), fg=remaining_color)
        except Exception as e:
            self._log(f"[ERR] Не удалось обновить расходы: {e}", "error")


    def _get_icon_path(self):
        return os.path.join(self._get_base_dir(), "icon.ico")

    def _get_avatars_dir(self):
        d = os.path.join(self._get_base_dir(), "avatars")
        os.makedirs(d, exist_ok=True)
        return d

    def _save_profile_avatar(self, profile_id, src_path):
        """Копирует выбранную картинку в avatars/<profile_id>.png (всегда
        перезаписывает, конвертирует в PNG чтобы формат был предсказуемым).
        Возвращает путь к сохранённому файлу."""
        from PIL import Image
        dst_path = os.path.join(self._get_avatars_dir(), f"{profile_id}.png")
        with Image.open(src_path) as src_img:
            img = src_img.convert("RGBA")
        with open(dst_path, "wb") as f:
            img.save(f, format="PNG")
            f.flush()
            os.fsync(f.fileno())
        return dst_path

    def _delete_profile_avatar(self, profile_id):
        path = os.path.join(self._get_avatars_dir(), f"{profile_id}.png")
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass
 
    def _apply_window_icon(self, win=None):
        """Применяет icon.ico (если есть) к окну -- видно в заголовке,
        панели задач и при alt-tab. Сразу, без перезапуска.
        Без аргумента применяет к главному окну; можно передать любое
        другое окно (например, окно настроек), чтобы иконка везде была
        одинаковой и менялась синхронно."""
        target = win if win is not None else self.root
        icon_path = self._get_icon_path()
        if os.path.exists(icon_path):
            try:
                target.iconbitmap(icon_path)
            except Exception as e:
                print(f"[ICON] Не удалось применить иконку: {e}")
 
    def change_app_icon(self):
        """Совместимость со старыми вызовами -- иконка приложения теперь
        живёт внутри раздела "Кастомизация"."""
        self.open_settings(section="theme")
 
    def _get_context_path(self):
        return os.path.join(self._get_base_dir(), "context.txt")
 
    def _get_history_context_path(self):
        return os.path.join(self._get_base_dir(), "history_context.txt")
 
    # -- Запуск / остановка ---------------------------------
 
    def _get_base_dir(self):
        """Папка с файлами -- ищет main.py в нескольких местах"""
        candidates = []
        if getattr(sys, 'frozen', False):
            # Рядом с EXE
            candidates.append(os.path.dirname(sys.executable))
            # Папка выше (если EXE в dist\)
            candidates.append(os.path.dirname(os.path.dirname(sys.executable)))
        else:
            candidates.append(os.path.dirname(os.path.abspath(__file__)))
 
        # Текущая рабочая папка тоже вариант
        candidates.append(os.getcwd())
 
        for d in candidates:
            if os.path.exists(os.path.join(d, "main.py")):
                return d
 
        # Если не нашли -- вернуть папку с EXE и показать ошибку там
        return candidates[0]
 
    def _get_python(self):
        """Реальный python.exe, а не сам EXE"""
        if getattr(sys, 'frozen', False):
            base = os.path.dirname(sys.executable)
            for candidate in [
                os.path.join(base, "python.exe"),
                os.path.join(base, "..", "python.exe"),
            ]:
                if os.path.exists(candidate):
                    return candidate
            return "python"
        return sys.executable
 
    def start(self):
        if self.running:
            return
 
        base_dir = self._get_base_dir()
        main_path = os.path.join(base_dir, "main.py")
 
        if not os.path.exists(main_path):
            self._log("[ERR] Не найден main.py рядом с программой", "error")
            return
 
        python = self._get_python()
 
        creationflags = subprocess.CREATE_NO_WINDOW
        if sys.platform == "win32":
            creationflags |= subprocess.CREATE_NEW_PROCESS_GROUP
 
        self.process = subprocess.Popen(
            [python, "-u", main_path],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            cwd=base_dir,
            creationflags=creationflags,
        )
        self.running = True
        self._session_tokens = 0
        self._session_msgs   = 0
        self._set_status(True)
        self._log("[>] Ассистент запущен", "system")
        threading.Thread(target=self._read_output, args=(self.process,), daemon=True).start()
 
    def stop(self):
        if not self.process:
            return
 
        process = self.process
        self.process = None
        self.running = False
        self._stopping = True
        self._set_status(False)
        self._log("[=] Останавливаю -- жду сохранения памяти...", "system")
 
        def _do_stop():
            try:
                if sys.platform == "win32":
                    process.send_signal(signal.CTRL_BREAK_EVENT)
                    try:
                        process.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        process.terminate()
                else:
                    process.terminate()
                    try:
                        process.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        process.kill()
            except Exception:
                try:
                    process.terminate()
                except Exception:
                    pass
            self._stopping = False
            self.root.after(0, lambda: self._log("[=] Ассистент остановлен", "system"))
 
        threading.Thread(target=_do_stop, daemon=True).start()
 
    def clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self._chat_clear()
        self._chat_messages = []

    # -- Чат (вкладка ЧАТ): пузыри сообщений -----------------
    # Это только отображение -- контент реплик берётся из тех же
    # строк ([Ты]:/[Ассистент]:), что уже парсятся для журнала.
    # Логика диалога (main.py/llm.py) этим кодом не затрагивается.

    def _chat_clear(self):
        for w in self._chat_bubble_widgets:
            try:
                w.destroy()
            except Exception:
                pass
        self._chat_bubble_widgets = []

    def _chat_scroll_to_bottom(self):
        self.chat_canvas.update_idletasks()
        self.chat_canvas.configure(scrollregion=self.chat_canvas.bbox("all"))
        self.chat_canvas.yview_moveto(1.0)

    def _chat_redraw_all(self):
        """Перерисовывает весь чат с текущими настройками радиуса/масштаба
        (из theme.py). Вызывается после изменения этих настроек в разделе
        "Кастомизация" -- сами сообщения (self._chat_messages) не теряются,
        перерисовывается только их отображение."""
        self._chat_clear()
        for kind, text, who in self._chat_messages:
            if kind == "bubble":
                self._chat_add_bubble(text, who, _record=False)
            else:
                self._chat_add_status(text, _record=False)

    @staticmethod
    def _rounded_rect_points(x0, y0, x1, y1, radius):
        """Точки для create_polygon со сглаживанием (smooth=True) -- даёт
        прямоугольник со скруглёнными углами на чистом tkinter Canvas,
        без необходимости в PIL-масках."""
        r = max(0, min(radius, (x1 - x0) / 2, (y1 - y0) / 2))
        return [
            x0 + r, y0,
            x1 - r, y0,
            x1, y0,
            x1, y0 + r,
            x1, y1 - r,
            x1, y1,
            x1 - r, y1,
            x0 + r, y1,
            x0, y1,
            x0, y1 - r,
            x0, y0 + r,
            x0, y0,
        ]

    def _chat_add_bubble(self, text: str, who: str, _record: bool = True):
        """Добавляет пузырь сообщения. who: 'you' (пользователь, справа) или
        'ai' (Ария, слева) -- как в обычном мессенджере. Закруглённость и
        масштаб шрифта читаются из theme.py (настраивается в "Кастомизация"),
        применяются только здесь -- журнал ([ЖУРНАЛ]) не затрагивается."""
        if not text.strip():
            return

        if _record:
            self._chat_messages.append(("bubble", text, who))

        radius = theme.get_chat_bubble_radius()
        font_scale = theme.get_chat_font_scale()
        base_size = 10
        font_size = max(7, round(base_size * font_scale))
        msg_font = ("Segoe UI", font_size)

        is_you = (who == "you")
        bubble_bg = ACCENT if is_you else BG_PANEL
        bubble_fg = "#fff" if is_you else TEXT

        row = tk.Frame(self.chat_inner, bg=BG_MSG)
        row.pack(fill="x", padx=10, pady=(4, 4))
        self._chat_bubble_widgets.append(row)

        align_frame = tk.Frame(row, bg=BG_MSG)
        align_frame.pack(side="right" if is_you else "left", anchor="e" if is_you else "w")

        pad_x, pad_y = 12, 8
        max_wrap = 320

        # Сначала меряем текст невидимым Label, чтобы знать размер канваса
        # под пузырь (canvas не умеет авто-расти под текст сам).
        measure = tk.Label(align_frame, text=text, font=msg_font, wraplength=max_wrap, justify="left")
        measure.update_idletasks()
        text_w = min(measure.winfo_reqwidth(), max_wrap)
        text_h = measure.winfo_reqheight()
        measure.destroy()

        canvas_w = text_w + pad_x * 2
        canvas_h = text_h + pad_y * 2

        bubble_canvas = tk.Canvas(
            align_frame, width=canvas_w, height=canvas_h,
            bg=BG_MSG, highlightthickness=0,
        )
        bubble_canvas.pack()

        pts = self._rounded_rect_points(0, 0, canvas_w, canvas_h, radius)
        bubble_canvas.create_polygon(pts, fill=bubble_bg, outline=bubble_bg, smooth=True)
        bubble_canvas.create_text(
            pad_x, canvas_h / 2, text=text, font=msg_font, fill=bubble_fg,
            anchor="w", width=text_w, justify="left",
        )

        sender = tk.Label(
            row, text=("Ты" if is_you else "Ария"),
            font=("Segoe UI", 7, "bold"), fg=TEXT_DIM, bg=BG_MSG,
        )
        sender.pack(side="right" if is_you else "left", anchor="e" if is_you else "w",
                    padx=(0, 4) if is_you else (4, 0))

        self.root.after(10, self._chat_scroll_to_bottom)

    def _chat_add_status(self, text: str, _record: bool = True):
        """Компактная статусная вставка между пузырями ("Слушаю...",
        "Думаю...", "Говорю..."). Визуально отличается от реплик --
        мелкая, по центру, приглушённого цвета, без "облачка". Масштаб
        шрифта чата на неё тоже распространяется, закруглённость -- нет
        (у неё нет фона/рамки)."""
        if _record:
            self._chat_messages.append(("status", text, None))

        font_scale = theme.get_chat_font_scale()
        font_size = max(6, round(8 * font_scale))

        row = tk.Frame(self.chat_inner, bg=BG_MSG)
        row.pack(fill="x", padx=10, pady=(2, 2))
        self._chat_bubble_widgets.append(row)

        tk.Label(
            row, text=f"\u00b7  {text}  \u00b7", font=("Segoe UI", font_size), fg=TEXT_DIM, bg=BG_MSG,
        ).pack(anchor="center")

        self.root.after(10, self._chat_scroll_to_bottom)

    # -- Чтение вывода --------------------------------------
 
    def _read_output(self, process):
        for line in process.stdout:
            line = line.rstrip()
            if line:
                self.root.after(0, self._parse_line, line)
        self.root.after(0, self._on_process_end)
 
    def _parse_line(self, line: str):
        line = re.sub(r'\x1b\[[0-9;]*m', '', line)

        if line.startswith("[Ты]:"):
            text = line[5:].strip()
            self._log(f"[MIC] Ты:  {text}", "you")
            self._chat_add_bubble(text, "you")

        elif line.startswith("[Ассистент]:"):
            text = line[12:].strip().replace("<br>", "\n")
            self._log(f"[*] ИИ:  {text}", "ai")
            self._set_last_answer(text)
            self._session_msgs += 1
            self.sess_msgs.config(text=str(self._session_msgs))
            self._chat_add_bubble(text, "ai")

        elif line.startswith("[Токены]:"):
            # Формат: вход=N выход=N итого=N max=N
            self._log(f"[STAT] {line[9:].strip()}", "tokens")
            m = re.findall(r'(\w+)=(\d+)', line)
            vals = {k: v for k, v in m}
            if "вход" in vals:
                self.tok_input.config(text=vals["вход"])
            if "выход" in vals:
                self.tok_output.config(text=vals["выход"])
            if "итого" in vals:
                self.tok_total.config(text=vals["итого"])
                self._session_tokens += int(vals["итого"])
                self.sess_total.config(text=str(self._session_tokens))
            if "max" in vals:
                self.tok_max.config(text=vals["max"])

            # Учёт расходов -- только для облачных моделей (DeepSeek и т.п.).
            # Локальные движки (Ollama, LM Studio) бесплатны, поэтому для них
            # этот блок просто не выполняется (_is_active_model_cloud вернёт False).
            if "вход" in vals and "выход" in vals and self._is_active_model_cloud():
                try:
                    import model_profiles as mp
                    model_id = mp.get_active_profile()["model"]
                    ct.add_usage(model_id, int(vals["вход"]), int(vals["выход"]))
                    self._refresh_cost_display()
                except Exception as e:
                    self._log(f"[ERR] Не удалось обновить расходы: {e}", "error")


        elif line.startswith("[Vision]:"):
            text = line[9:].strip()
            self._log(f"[CAM] Экран:  {text[:100]}{'...' if len(text)>100 else ''}", "vision")

        elif line.startswith("[[MIC]] Слушаю"):
            self._chat_add_status("Слушаю...")

        elif line.startswith("[[THINK]]"):
            self._chat_add_status("Думаю...")

        elif line.startswith("[[SPEAK]]"):
            self._chat_add_status("Говорю...")

        elif line.startswith("[[EAR]]"):
            # Ассистент полностью загрузился (STT/TTS/LLM) и начал слушать --
            # это и есть момент "готов к использованию". До этого момента
            # процесс может уже быть запущен, но модели ещё грузятся.
            self._ready = True
            self._refresh_header_avatar()

        elif "[ERR]" in line or "Error" in line.lower():
            self._log(line, "error")

        elif line.startswith("[ZZZ]") or line.startswith("[EAR]") or line.startswith("[MIC]"):
            pass

        else:
            self._log(line, "system")

 
    def _on_process_end(self):
        if self.running:
            self.running = False
            self._set_status(False)
            self._log("[WARN] Ассистент завершил работу", "error")
 
    # -- Хелперы --------------------------------------------
 
    def _log(self, text: str, tag: str = "system"):
        self.log.configure(state="normal")
        ts = datetime.now().strftime("%H:%M:%S")
        self.log.insert("end", f"{ts}  ", "time")
        self.log.insert("end", text + "\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")
 
    def _set_last_answer(self, text: str):
        self.last_answer.configure(state="normal")
        self.last_answer.delete("1.0", "end")
        self.last_answer.insert("end", text)
        self.last_answer.configure(state="disabled")
 
    def _set_status(self, running: bool):
        if running:
            self.btn_start.config(state="disabled")
            self.btn_stop.config(state="normal")
        else:
            self.btn_start.config(state="normal")
            self.btn_stop.config(state="disabled")
            self._ready = False
            self._refresh_header_avatar()
 
 
if __name__ == "__main__":
    root = tk.Tk()
    _install_global_copy_paste(root)
    app = Launcher(root)
 
    def on_close():
        if app.process is not None:
            app._log("[=] Закрываю -- жду сохранения памяти...", "system")
            root.withdraw()
            app.stop()
 
            deadline = [55]  # ~11 секунд максимум (200мс * 55), запас над 8с таймаутом stop()
 
            def check_and_destroy():
                deadline[0] -= 1
                if not app._stopping or deadline[0] <= 0:
                    root.destroy()
                else:
                    root.after(200, check_and_destroy)
 
            root.after(200, check_and_destroy)
        else:
            root.destroy()
 
    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()