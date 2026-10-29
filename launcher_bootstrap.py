# ============================================================
#  launcher_bootstrap.py -- тонкая EXE-обёртка
#
#  Этот файл компилируется в EXE один раз. Сам он почти
#  ничего не делает -- просто находит launcher.py рядом с
#  собой (или рядом с EXE) и выполняет код прямо с диска.
#
#  Благодаря этому редактирование launcher.py (логика,
#  цвета, кнопки, всё что угодно) НЕ требует пересборки EXE --
#  изменения подхватятся при следующем запуске из Пуска.
# ============================================================

import sys
import os


def _get_base_dir():
    """Папка где лежит launcher.py -- ищет в нескольких местах."""
    candidates = []
    if getattr(sys, 'frozen', False):
        candidates.append(os.path.dirname(sys.executable))
        candidates.append(os.path.dirname(os.path.dirname(sys.executable)))
    else:
        candidates.append(os.path.dirname(os.path.abspath(__file__)))
    candidates.append(os.getcwd())

    for d in candidates:
        if os.path.exists(os.path.join(d, "launcher.py")):
            return d
    return candidates[0]


def main():
    base_dir = _get_base_dir()
    launcher_path = os.path.join(base_dir, "launcher.py")

    if not os.path.exists(launcher_path):
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Голосовой ассистент",
            f"Не найден launcher.py рядом с программой.\n\nИскал тут:\n{base_dir}\n\n"
            "Положи launcher.py (и остальные .py файлы проекта) в ту же папку, "
            "что и VoiceAssistant.exe."
        )
        sys.exit(1)

    # Добавляем папку проекта в sys.path, чтобы launcher.py мог
    # делать import config, import main и т.д.
    if base_dir not in sys.path:
        sys.path.insert(0, base_dir)

    # Выполняем launcher.py как если бы он был запущен напрямую
    # (его блок if __name__ == "__main__" сработает как обычно)
    with open(launcher_path, "r", encoding="utf-8") as f:
        code = f.read()

    namespace = {"__name__": "__main__", "__file__": launcher_path}
    exec(compile(code, launcher_path, "exec"), namespace)


if __name__ == "__main__":
    main()
