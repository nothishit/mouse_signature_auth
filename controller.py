"""
controller.py — логика работы приложения без привязки к графике.

Контроллер хранит режим (регистрация / вход), считает образцы, вызывает
ядро (signature_core) и возвращает результат в виде объекта Result.
Графическая оболочка только рисует и передаёт сюда готовые штрихи.
"""

import time
from dataclasses import dataclass
from typing import Optional

import signature_core as core


@dataclass
class Result:
    kind: str                       # см. ниже
    message: str                    # текст для пользователя
    accepted: Optional[bool] = None
    score: Optional[float] = None
    threshold: Optional[float] = None
    done: int = 0                   # сколько образцов собрано при регистрации
    total: int = core.ENROLL_SAMPLES

    # kind:
    #   'idle'            — режим не выбран
    #   'too_short'       — роспись слишком короткая, образец не принят
    #   'enroll_progress' — образец принят, нужны ещё
    #   'enroll_done'     — профиль создан
    #   'granted'         — доступ разрешён
    #   'denied'          — доступ запрещён
    #   'locked'          — вход заблокирован на время


class AuthController:
    def __init__(self, store, clock=time.monotonic):
        self.store = store
        self.clock = clock
        self.mode = "idle"
        self.name = ""
        self.enroll_data = []
        self.limiters = {}

    # ---------------------------------------------------------- режимы
    def start_enroll(self, name):
        name = name.strip()
        if not name:
            raise ValueError("Введите имя пользователя")
        self.mode = "enroll"
        self.name = name
        self.enroll_data = []
        return Result("enroll_progress",
                      f"Регистрация «{name}»: нарисуйте роспись "
                      f"(образец 1 из {core.ENROLL_SAMPLES})",
                      done=0)

    def start_login(self, name):
        name = name.strip()
        if not name:
            raise ValueError("Введите имя пользователя")
        if self.store.get(name) is None:
            raise KeyError(f"Пользователь «{name}» не зарегистрирован")
        self.mode = "login"
        self.name = name
        lim = self._limiter(name)
        if lim.is_locked():
            return self._locked_result(lim)
        return Result("idle", f"Вход «{name}»: нарисуйте роспись")

    def cancel(self):
        self.mode = "idle"
        self.name = ""
        self.enroll_data = []

    # ----------------------------------------------------------- вспомогательное
    def _limiter(self, name):
        if name not in self.limiters:
            self.limiters[name] = core.AttemptLimiter(clock=self.clock)
        return self.limiters[name]

    def lock_seconds_left(self, name=None):
        lim = self.limiters.get(name or self.name)
        return 0.0 if lim is None else lim.seconds_left()

    def _locked_result(self, lim):
        left = int(lim.seconds_left()) + 1
        return Result("locked", f"Вход заблокирован. Подождите {left} с",
                      accepted=False)

    # --------------------------------------------------------------- ввод
    def submit(self, strokes):
        """Принимает нарисованную роспись (список штрихов) и обрабатывает её."""
        if self.mode == "idle":
            return Result("idle", "Сначала выберите режим: регистрация или вход")

        if self.mode == "enroll":
            if not core.is_valid(strokes):
                return Result("too_short",
                              "Роспись слишком короткая, попробуйте ещё раз",
                              done=len(self.enroll_data))
            self.enroll_data.append(strokes)
            n = len(self.enroll_data)
            if n < core.ENROLL_SAMPLES:
                return Result("enroll_progress",
                              f"Образец принят. Нарисуйте роспись "
                              f"(образец {n + 1} из {core.ENROLL_SAMPLES})",
                              done=n)
            profile = core.Profile.enroll(self.name, self.enroll_data)
            self.store.add(profile)
            self.limiters.pop(self.name, None)
            name = self.name
            self.cancel()
            return Result("enroll_done",
                          f"Профиль «{name}» создан. Теперь можно войти",
                          done=n)

        # mode == "login"
        profile = self.store.get(self.name)
        lim = self._limiter(self.name)
        if lim.is_locked():
            return self._locked_result(lim)
        if not core.is_valid(strokes):
            return Result("too_short", "Роспись слишком короткая",
                          accepted=False)
        ok, score, thr = profile.verify(strokes)
        if ok:
            lim.register_success()
            name = self.name
            self.cancel()
            return Result("granted", f"ДОСТУП РАЗРЕШЁН: {name}",
                          accepted=True, score=score, threshold=thr)
        lim.register_failure()
        if lim.is_locked():
            r = self._locked_result(lim)
            r.score, r.threshold = score, thr
            return r
        left = lim.max_attempts - lim.failures
        return Result("denied", f"Роспись не совпала. Осталось попыток: {left}",
                      accepted=False, score=score, threshold=thr)
