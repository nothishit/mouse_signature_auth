"""
signature_core.py — ядро системы аутентификации по росписи мышью.

Модуль не зависит от графической оболочки и использует только стандартную
библиотеку Python, поэтому его можно тестировать отдельно от интерфейса.

Росписью считается набор штрихов. Штрих — список точек (x, y, t), где
x, y — координаты курсора в пикселях, t — время в секундах.

Схема работы:
    1. Предобработка: удаление дублей, нормировка положения и размера,
       ресемплинг по длине дуги до N точек.
    2. Сравнение формы: DTW по нормированным координатам, нормированное
       на личный разброс пользователя.
    3. Сравнение динамики: z-оценки скоростных и временных признаков.
    4. Регистрация: несколько образцов -> шаблоны и статистика признаков.
    5. Проверка: итоговая оценка сравнивается с порогом.
"""

import json
import math
import os
import statistics
import time

# ---------------------------------------------------------------- параметры
N_POINTS = 64            # число точек после ресемплинга
DTW_BAND = 0.15          # ширина полосы Сако — Тиба (доля от N)
DYN_BETA = 0.5           # вес динамической составляющей в итоговой оценке
SIGMA_FLOOR = 0.10       # нижняя граница СКО ln-признака (защита от деления на 0)
Z_CAP = 4.0              # ограничение z-оценки одного признака
THRESHOLD = 1.6          # порог принятия решения (оценка <= порога -> «свой»)
MIN_POINTS = 15          # минимум точек в росписи
MIN_PATH = 40.0          # минимальная длина траектории, пикселей
ENROLL_SAMPLES = 5       # число образцов при регистрации
MAX_ATTEMPTS = 3         # неудачных попыток до блокировки
LOCK_SECONDS = 30        # длительность блокировки, секунд


# ------------------------------------------------------------ предобработка
def flatten(strokes):
    """Склеивает штрихи в одну последовательность (x, y, t, pen_down)."""
    pts = []
    for stroke in strokes:
        for i, (x, y, t) in enumerate(stroke):
            pts.append((float(x), float(y), float(t), i == 0))
    return pts


def clean_points(strokes):
    """Удаляет подряд идущие одинаковые точки, не трогая границы штрихов."""
    cleaned = []
    for stroke in strokes:
        out = []
        for p in stroke:
            if not out or (p[0], p[1]) != (out[-1][0], out[-1][1]):
                out.append(p)
        if len(out) >= 2:
            cleaned.append(out)
    return cleaned


def path_length(points):
    """Длина ломаной по списку (x, y, ...)."""
    return sum(math.hypot(points[i][0] - points[i - 1][0],
                          points[i][1] - points[i - 1][1])
               for i in range(1, len(points)))


def is_valid(strokes):
    """Проверка, что роспись достаточно содержательна."""
    strokes = clean_points(strokes)
    n = sum(len(s) for s in strokes)
    if n < MIN_POINTS:
        return False
    total = sum(path_length(s) for s in strokes)
    return total >= MIN_PATH


def normalize_shape(strokes, n=N_POINTS):
    """Нормировка формы.

    Все штрихи объединяются, траектория переносится так, чтобы центр
    масс оказался в начале координат, масштабируется по наибольшей стороне
    охватывающего прямоугольника (инвариантность к положению и размеру)
    и ресемплируется до n точек, равномерно по длине дуги.
    """
    pts = [(x, y) for (x, y, _t, _d) in flatten(clean_points(strokes))]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    cx = sum(xs) / len(xs)
    cy = sum(ys) / len(ys)
    size = max(max(xs) - min(xs), max(ys) - min(ys)) or 1.0
    norm = [((x - cx) / size, (y - cy) / size) for x, y in pts]
    return resample(norm, n)


def resample(points, n):
    """Равномерный по длине дуги ресемплинг ломаной до n точек."""
    if len(points) == 1:
        return [points[0]] * n
    dists = [0.0]
    for i in range(1, len(points)):
        dists.append(dists[-1] + math.hypot(points[i][0] - points[i - 1][0],
                                            points[i][1] - points[i - 1][1]))
    total = dists[-1]
    if total == 0:
        return [points[0]] * n
    out = []
    j = 0
    for k in range(n):
        target = total * k / (n - 1)
        while j < len(dists) - 2 and dists[j + 1] < target:
            j += 1
        seg = dists[j + 1] - dists[j]
        a = 0.0 if seg == 0 else (target - dists[j]) / seg
        x = points[j][0] + a * (points[j + 1][0] - points[j][0])
        y = points[j][1] + a * (points[j + 1][1] - points[j][1])
        out.append((x, y))
    return out


# ------------------------------------------------------------------ признаки
def dynamic_features(strokes):
    """Вектор динамических признаков росписи (все положительные).

    0 — длительность росписи, с
    1 — средняя скорость, пикс/с (по времени, когда курсор двигался)
    2 — максимальная скорость (95-й процентиль), пикс/с
    3 — число штрихов
    4 — доля времени пауз (между штрихами) от общей длительности
    5 — отношение длины траектории к диагонали охватывающего прямоугольника
    """
    strokes = clean_points(strokes)
    all_pts = flatten(strokes)
    t0 = all_pts[0][2]
    t1 = all_pts[-1][2]
    duration = max(t1 - t0, 1e-3)

    speeds = []
    moving_time = 0.0
    for stroke in strokes:
        for i in range(1, len(stroke)):
            dt = stroke[i][2] - stroke[i - 1][2]
            if dt <= 0:
                continue
            d = math.hypot(stroke[i][0] - stroke[i - 1][0],
                           stroke[i][1] - stroke[i - 1][1])
            speeds.append(d / dt)
            moving_time += dt
    if not speeds:
        speeds = [1.0]
    speeds.sort()
    v95 = speeds[min(len(speeds) - 1, int(0.95 * len(speeds)))]
    length = sum(path_length(s) for s in strokes)
    mean_v = length / max(moving_time, 1e-3)

    xs = [p[0] for p in all_pts]
    ys = [p[1] for p in all_pts]
    diag = math.hypot(max(xs) - min(xs), max(ys) - min(ys)) or 1.0
    pause_share = max(0.0, (duration - moving_time) / duration)
    return [duration, mean_v, max(v95, 1.0), float(len(strokes)),
            pause_share + 0.05, length / diag]


# ---------------------------------------------------------- расстояния
def dtw_distance(a, b, band=DTW_BAND):
    """Расстояние DTW между двумя последовательностями точек.

    Используется ограничение полосой Сако — Тиба. Результат делится
    на длину пути выравнивания, поэтому выражается в долях размера росписи.
    """
    n, m = len(a), len(b)
    w = max(int(band * max(n, m)), abs(n - m), 1)
    inf = float('inf')
    cost = [[inf] * (m + 1) for _ in range(n + 1)]
    steps = [[0] * (m + 1) for _ in range(n + 1)]
    cost[0][0] = 0.0
    for i in range(1, n + 1):
        lo = max(1, i - w)
        hi = min(m, i + w)
        for j in range(lo, hi + 1):
            d = math.hypot(a[i - 1][0] - b[j - 1][0], a[i - 1][1] - b[j - 1][1])
            best = cost[i - 1][j - 1]
            st = steps[i - 1][j - 1]
            if cost[i - 1][j] < best:
                best, st = cost[i - 1][j], steps[i - 1][j]
            if cost[i][j - 1] < best:
                best, st = cost[i][j - 1], steps[i][j - 1]
            cost[i][j] = d + best
            steps[i][j] = st + 1
    return cost[n][m] / max(steps[n][m], 1)


class Sample:
    """Предобработанная роспись: нормированная форма + динамические признаки."""

    def __init__(self, strokes):
        strokes = clean_points(strokes)
        self.shape = normalize_shape(strokes)
        self.features = dynamic_features(strokes)

    def to_dict(self):
        return {"shape": [list(p) for p in self.shape],
                "features": self.features}

    @classmethod
    def from_dict(cls, d):
        obj = cls.__new__(cls)
        obj.shape = [tuple(p) for p in d["shape"]]
        obj.features = list(d["features"])
        return obj


def shape_distance(s1, s2):
    """Расстояние формы между двумя росписями (DTW)."""
    return dtw_distance(s1.shape, s2.shape)


# ------------------------------------------------ регистрация и проверка
class Profile:
    """Профиль пользователя.

    Хранит образцы-шаблоны (нормированные формы), статистику динамических
    признаков (среднее и СКО логарифмов) и средний внутренний разброс формы.
    Итоговая оценка «чужести» росписи:

        S = D_shape / mu_shape + DYN_BETA * Z_dyn

    где D_shape — среднее DTW-расстояние до образцов, mu_shape — среднее
    расстояние между образцами самого пользователя (для «своей» росписи
    D_shape / mu_shape около 1), Z_dyn — средняя ограниченная z-оценка
    динамических признаков.
    """

    def __init__(self, name, samples, shape_mu, feat_mean, feat_std,
                 threshold=THRESHOLD):
        self.name = name
        self.samples = samples
        self.shape_mu = shape_mu
        self.feat_mean = feat_mean
        self.feat_std = feat_std
        self.threshold = threshold

    # --- построение профиля
    @classmethod
    def enroll(cls, name, list_of_strokes, threshold=THRESHOLD):
        if len(list_of_strokes) < 3:
            raise ValueError("Для регистрации нужно не менее 3 образцов")
        samples = [Sample(s) for s in list_of_strokes]
        n = len(samples)
        loo = []
        for i in range(n):
            ds = [shape_distance(samples[i], samples[j])
                  for j in range(n) if j != i]
            loo.append(sum(ds) / len(ds))
        shape_mu = max(sum(loo) / n, 1e-3)
        nf = len(samples[0].features)
        logs = [[math.log(s.features[k]) for s in samples] for k in range(nf)]
        feat_mean = [statistics.fmean(v) for v in logs]
        feat_std = [max(statistics.pstdev(v), SIGMA_FLOOR) for v in logs]
        return cls(name, samples, shape_mu, feat_mean, feat_std, threshold)

    # --- оценка и проверка
    def components(self, strokes):
        """Возвращает (отношение формы, динамическая z-оценка)."""
        test = Sample(strokes)
        ds = [shape_distance(test, s) for s in self.samples]
        shape_ratio = (sum(ds) / len(ds)) / self.shape_mu
        zs = [min(abs(math.log(f) - m) / sd, Z_CAP)
              for f, m, sd in zip(test.features, self.feat_mean, self.feat_std)]
        return shape_ratio, sum(zs) / len(zs)

    def score(self, strokes):
        shape_ratio, z = self.components(strokes)
        return shape_ratio + DYN_BETA * z

    def verify(self, strokes, threshold=None):
        """Возвращает (принято?, оценка, порог)."""
        thr = self.threshold if threshold is None else threshold
        if not is_valid(strokes):
            return False, float('inf'), thr
        sc = self.score(strokes)
        return sc <= thr, sc, thr

    # --- сериализация
    def to_dict(self):
        return {"name": self.name,
                "samples": [s.to_dict() for s in self.samples],
                "shape_mu": self.shape_mu,
                "feat_mean": self.feat_mean,
                "feat_std": self.feat_std,
                "threshold": self.threshold}

    @classmethod
    def from_dict(cls, d):
        return cls(d["name"], [Sample.from_dict(s) for s in d["samples"]],
                   d["shape_mu"], d["feat_mean"], d["feat_std"],
                   d["threshold"])


class ProfileStore:
    """Хранилище профилей в JSON-файле."""

    def __init__(self, path="profiles.json"):
        self.path = path
        self.profiles = {}
        self.load()

    def load(self):
        if os.path.exists(self.path):
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.profiles = {k: Profile.from_dict(v) for k, v in data.items()}

    def save(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({k: v.to_dict() for k, v in self.profiles.items()}, f,
                      ensure_ascii=False)

    def add(self, profile):
        self.profiles[profile.name] = profile
        self.save()

    def remove(self, name):
        if name in self.profiles:
            del self.profiles[name]
            self.save()

    def get(self, name):
        return self.profiles.get(name)

    def names(self):
        return sorted(self.profiles)


class AttemptLimiter:
    """Ограничитель попыток входа: после MAX_ATTEMPTS ошибок — блокировка."""

    def __init__(self, max_attempts=MAX_ATTEMPTS, lock_seconds=LOCK_SECONDS,
                 clock=time.monotonic):
        self.max_attempts = max_attempts
        self.lock_seconds = lock_seconds
        self.clock = clock
        self.failures = 0
        self.locked_until = 0.0

    def seconds_left(self):
        return max(0.0, self.locked_until - self.clock())

    def is_locked(self):
        return self.seconds_left() > 0

    def register_failure(self):
        self.failures += 1
        if self.failures >= self.max_attempts:
            self.locked_until = self.clock() + self.lock_seconds
            self.failures = 0

    def register_success(self):
        self.failures = 0
        self.locked_until = 0.0
