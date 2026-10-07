"""
mouse_signature_app.py — приложение «Аутентификация по росписи мышью».

Запуск:  python mouse_signature_app.py
Требуется только стандартная библиотека Python 3.8+ (tkinter входит в состав).

Порядок работы:
    1. Введите имя пользователя и нажмите «Регистрация».
    2. Нарисуйте роспись мышью (зажав левую кнопку) и нажмите «Подтвердить»
       или Enter. Так нужно сделать 5 раз.
    3. Для входа выберите имя, нажмите «Вход», нарисуйте роспись и подтвердите.
    4. После 3 неудачных попыток вход блокируется на 30 секунд.
"""

import os
import time
import tkinter as tk
from tkinter import messagebox, ttk

import signature_core as core
from controller import AuthController

CANVAS_W, CANVAS_H = 640, 320
STORE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "profiles.json")

COLORS = {
    "granted": "#1b8a2f",
    "enroll_done": "#1b8a2f",
    "denied": "#c62828",
    "locked": "#c62828",
    "too_short": "#e65100",
}
DEFAULT_COLOR = "#222222"


class SignatureApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Аутентификация по росписи мышью")
        self.resizable(False, False)

        self.store = core.ProfileStore(STORE_FILE)
        self.ctrl = AuthController(self.store)

        self.strokes = []      # завершённые и текущий штрихи
        self.current = None    # текущий штрих (список точек)
        self.last_xy = None
        self.locked = False

        self._build_ui()
        self._refresh_users()
        self.bind("<Return>", lambda e: self.on_submit())
        self.bind("<Escape>", lambda e: self.on_clear())

    # ------------------------------------------------------------ интерфейс
    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}

        top = ttk.Frame(self)
        top.pack(fill="x", **pad)
        ttk.Label(top, text="Пользователь:").pack(side="left")
        self.name_var = tk.StringVar()
        self.name_box = ttk.Combobox(top, textvariable=self.name_var, width=22)
        self.name_box.pack(side="left", padx=6)
        ttk.Button(top, text="Регистрация", command=self.on_enroll).pack(
            side="left", padx=2)
        ttk.Button(top, text="Вход", command=self.on_login).pack(
            side="left", padx=2)
        ttk.Button(top, text="Удалить профиль", command=self.on_delete).pack(
            side="left", padx=2)

        self.title_label = tk.Label(
            self, text="Выберите режим: «Регистрация» или «Вход»",
            font=("Segoe UI", 12, "bold"))
        self.title_label.pack(fill="x", **pad)

        self.canvas = tk.Canvas(self, width=CANVAS_W, height=CANVAS_H,
                                bg="white", highlightthickness=1,
                                highlightbackground="#888888", cursor="pencil")
        self.canvas.pack(padx=8, pady=4)
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_move)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self._draw_hint()

        self.status_label = tk.Label(self, text="", font=("Segoe UI", 12))
        self.status_label.pack(fill="x", **pad)
        self.info_label = tk.Label(self, text="", font=("Segoe UI", 9),
                                   fg="#666666")
        self.info_label.pack(fill="x")

        bottom = ttk.Frame(self)
        bottom.pack(fill="x", **pad)
        self.clear_btn = ttk.Button(bottom, text="Очистить (Esc)",
                                    command=self.on_clear)
        self.clear_btn.pack(side="left", padx=2)
        self.submit_btn = ttk.Button(bottom, text="Подтвердить (Enter)",
                                     command=self.on_submit)
        self.submit_btn.pack(side="right", padx=2)

    def _draw_hint(self):
        self.canvas.create_text(CANVAS_W // 2, CANVAS_H // 2,
                                text="Распишитесь здесь, удерживая левую кнопку мыши",
                                fill="#bbbbbb", font=("Segoe UI", 12),
                                tags="hint")

    def _refresh_users(self):
        self.name_box["values"] = self.store.names()

    def _set_status(self, text, color=DEFAULT_COLOR):
        self.status_label.config(text=text, fg=color)

    # ------------------------------------------------------- рисование мышью
    def on_press(self, event):
        if self.locked:
            return
        self.canvas.delete("hint")
        self.current = [(event.x, event.y, time.perf_counter())]
        self.strokes.append(self.current)
        self.last_xy = (event.x, event.y)
        self.canvas.create_oval(event.x - 1, event.y - 1, event.x + 1,
                                event.y + 1, fill="black", outline="black")

    def on_move(self, event):
        if self.current is None:
            return
        x = min(max(event.x, 0), CANVAS_W)
        y = min(max(event.y, 0), CANVAS_H)
        self.current.append((x, y, time.perf_counter()))
        self.canvas.create_line(self.last_xy[0], self.last_xy[1], x, y,
                                width=3, capstyle=tk.ROUND, fill="black")
        self.last_xy = (x, y)

    def on_release(self, event):
        if self.current is None:
            return
        x = min(max(event.x, 0), CANVAS_W)
        y = min(max(event.y, 0), CANVAS_H)
        self.current.append((x, y, time.perf_counter()))
        self.current = None

    def _clear_canvas(self):
        self.canvas.delete("all")
        self.strokes = []          # новый список: старый уже отдан контроллеру
        self.current = None
        self.last_xy = None
        self._draw_hint()

    # ---------------------------------------------------------------- кнопки
    def on_clear(self):
        if not self.locked:
            self._clear_canvas()

    def on_enroll(self):
        name = self.name_var.get().strip()
        if name and self.store.get(name) is not None:
            if not messagebox.askyesno(
                    "Профиль существует",
                    f"Профиль «{name}» уже есть. Перезаписать его?"):
                return
        try:
            result = self.ctrl.start_enroll(name)
        except ValueError as exc:
            messagebox.showwarning("Регистрация", str(exc))
            return
        self._clear_canvas()
        self.title_label.config(text=result.message)
        self._set_status("")
        self.info_label.config(text="")

    def on_login(self):
        try:
            result = self.ctrl.start_login(self.name_var.get())
        except (ValueError, KeyError) as exc:
            messagebox.showwarning("Вход", exc.args[0])
            return
        self._clear_canvas()
        self.title_label.config(text=result.message)
        self._set_status("")
        self.info_label.config(text="")
        if result.kind == "locked":
            self._start_lock()

    def on_delete(self):
        name = self.name_var.get().strip()
        if self.store.get(name) is None:
            messagebox.showwarning("Удаление", "Такого профиля нет")
            return
        if messagebox.askyesno("Удаление", f"Удалить профиль «{name}»?"):
            self.store.remove(name)
            self.ctrl.limiters.pop(name, None)
            self.ctrl.cancel()
            self.name_var.set("")
            self._refresh_users()
            self._clear_canvas()
            self.title_label.config(
                text="Выберите режим: «Регистрация» или «Вход»")
            self._set_status("Профиль удалён")

    def on_submit(self):
        if self.locked:
            return
        if not self.strokes:
            self._set_status("Сначала нарисуйте роспись", COLORS["too_short"])
            return
        result = self.ctrl.submit(self.strokes)
        self._clear_canvas()
        self._show(result)

    # --------------------------------------------------------- вывод результата
    def _show(self, result):
        color = COLORS.get(result.kind, DEFAULT_COLOR)
        if result.kind in ("enroll_progress",):
            self.title_label.config(text=result.message)
            self._set_status(f"Собрано образцов: {result.done} из {result.total}")
            self.info_label.config(text="")
            return
        self._set_status(result.message, color)
        if result.score is not None and result.threshold is not None:
            self.info_label.config(
                text=f"оценка {result.score:.2f} (порог {result.threshold:.2f}; "
                     f"чем меньше оценка, тем больше схожесть)")
        else:
            self.info_label.config(text="")
        if result.kind in ("granted", "enroll_done"):
            self._refresh_users()
            self.title_label.config(
                text="Выберите режим: «Регистрация» или «Вход»")
        elif result.kind == "denied":
            self.title_label.config(text="Попробуйте ещё раз")
        elif result.kind == "locked":
            self._start_lock()

    # ------------------------------------------------------------- блокировка
    def _start_lock(self):
        self.locked = True
        self.clear_btn.state(["disabled"])
        self.submit_btn.state(["disabled"])
        self.canvas.config(cursor="X_cursor")
        self._tick_lock()

    def _tick_lock(self):
        left = self.ctrl.lock_seconds_left()
        if left > 0:
            self.title_label.config(text="🔒 БЛОКИРОВКА")
            self._set_status(f"Слишком много неудачных попыток. "
                             f"Подождите {int(left) + 1} с", COLORS["locked"])
            self.after(500, self._tick_lock)
        else:
            self.locked = False
            self.clear_btn.state(["!disabled"])
            self.submit_btn.state(["!disabled"])
            self.canvas.config(cursor="pencil")
            self.title_label.config(text="Блокировка снята. Нарисуйте роспись")
            self._set_status("")


def main():
    SignatureApp().mainloop()


if __name__ == "__main__":
    main()
