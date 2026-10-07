"""
experiment.py — модельный эксперимент по оценке точности метода.

Реальных росписей нужно много людей и времени, поэтому для оценки метода
используется генератор синтетических росписей. У каждого «пользователя»
есть собственная базовая форма (сплайн по случайным опорным точкам) и
собственный профиль скорости. Каждая «подлинная» роспись — это базовая
роспись с естественным разбросом (масштаб, сдвиг, наклон, дрожание
формы, изменение темпа, шум датчика).

Рассматриваются три типа нарушителя:
    random   — случайный: роспись другого пользователя;
    shape    — видел картинку росписи и перерисовывает форму, но не знает
               динамики (скорости, ритма);
    informed — видел запись самого процесса росписи: форма и темп близки
               к оригиналу, но воспроизведены с большей погрешностью.

Запуск:  python experiment.py [число_пользователей]
"""

import json
import math
import random
import statistics
import sys

import signature_core as core


# ------------------------------------------------------------- генератор
def catmull_rom(pts, per_seg=18):
    """Сплайн Катмулла — Рома через опорные точки."""
    p = [pts[0]] + list(pts) + [pts[-1]]
    out = []
    for i in range(1, len(p) - 2):
        p0, p1, p2, p3 = p[i - 1], p[i], p[i + 1], p[i + 2]
        for k in range(per_seg):
            t = k / per_seg
            t2, t3 = t * t, t * t * t
            x = 0.5 * ((2 * p1[0]) + (-p0[0] + p2[0]) * t
                       + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2
                       + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3)
            y = 0.5 * ((2 * p1[1]) + (-p0[1] + p2[1]) * t
                       + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2
                       + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3)
            out.append((x, y))
    out.append(pts[-1])
    return out


class SyntheticUser:
    """Базовая «роспись» вымышленного пользователя."""

    def __init__(self, rng):
        self.n_strokes = rng.choice([1, 2, 2, 3])
        self.strokes = []
        x0 = 0.0
        for _ in range(self.n_strokes):
            n_ctrl = rng.randint(5, 8)
            width = rng.uniform(0.7, 1.4)
            pts = []
            for k in range(n_ctrl):
                pts.append((x0 + width * k / (n_ctrl - 1) + rng.uniform(-0.08, 0.08),
                            rng.uniform(-0.45, 0.45)))
            self.strokes.append(pts)
            x0 += width * rng.uniform(0.6, 1.0)
        self.v0 = rng.uniform(250, 700)             # средняя скорость, пикс/с
        self.amp = rng.uniform(0.3, 0.65)           # глубина модуляции
        self.freq = rng.choice([2, 3, 4, 5])        # «ритм» росписи
        self.phase = rng.uniform(0, 2 * math.pi)
        self.pause = rng.uniform(0.15, 0.45)        # пауза между штрихами, с
        self.scale = rng.uniform(220, 380)          # размер росписи, пикс


def render(user, rng, shape_sigma, speed_sigma, profile_noise, px_noise=0.8,
           speed_override=None, rot_sigma=0.03):
    """Создаёт одну роспись: список штрихов [(x, y, t), ...]."""
    scale = user.scale * math.exp(rng.gauss(0, 0.08))
    ang = rng.gauss(0, rot_sigma)
    ca, sa = math.cos(ang), math.sin(ang)
    ox, oy = rng.uniform(100, 300), rng.uniform(100, 200)
    if speed_override is None:
        v0, amp, freq, phase = user.v0, user.amp, user.freq, user.phase
    else:
        v0, amp, freq, phase = speed_override
    v0 *= math.exp(rng.gauss(0, speed_sigma))
    phase += rng.gauss(0, profile_noise)
    amp = max(0.05, amp + rng.gauss(0, profile_noise * 0.3))

    strokes = []
    t = 0.0
    for ctrl in user.strokes:
        pts = [(x + rng.gauss(0, shape_sigma), y + rng.gauss(0, shape_sigma))
               for x, y in ctrl]
        dense = catmull_rom(pts)
        xy = [(ox + scale * (x * ca - y * sa), oy + scale * (x * sa + y * ca))
              for x, y in dense]
        # длина дуги и время прохождения
        s = [0.0]
        for i in range(1, len(xy)):
            s.append(s[-1] + math.hypot(xy[i][0] - xy[i - 1][0],
                                        xy[i][1] - xy[i - 1][1]))
        total = s[-1] or 1.0
        tt = [0.0]
        for i in range(1, len(xy)):
            sm = 0.5 * (s[i] + s[i - 1]) / total
            v = v0 * (1 + amp * math.sin(2 * math.pi * freq * sm + phase))
            tt.append(tt[-1] + (s[i] - s[i - 1]) / max(v, 30.0))
        dur = tt[-1]
        # события мыши с неравномерным шагом 6–16 мс
        stroke = []
        te = 0.0
        j = 0
        while te <= dur:
            while j < len(tt) - 2 and tt[j + 1] < te:
                j += 1
            seg = tt[j + 1] - tt[j]
            a = 0.0 if seg == 0 else (te - tt[j]) / seg
            x = xy[j][0] + a * (xy[j + 1][0] - xy[j][0]) + rng.gauss(0, px_noise)
            y = xy[j][1] + a * (xy[j + 1][1] - xy[j][1]) + rng.gauss(0, px_noise)
            stroke.append((x, y, t + te))
            te += rng.uniform(0.006, 0.016)
        strokes.append(stroke)
        t += dur + user.pause * math.exp(rng.gauss(0, 0.15))
    return strokes


def genuine(user, rng):
    return render(user, rng, shape_sigma=0.022, speed_sigma=0.12,
                  profile_noise=0.15)


def forgery_shape(user, rng):
    """Нарушитель видел только картинку: форма с погрешностью, темп свой."""
    own = (rng.uniform(250, 700), rng.uniform(0.3, 0.65),
           rng.choice([2, 3, 4, 5]), rng.uniform(0, 2 * math.pi))
    return render(user, rng, shape_sigma=0.045, speed_sigma=0.25,
                  profile_noise=0.3, speed_override=own)


def forgery_informed(user, rng):
    """Нарушитель видел запись росписи: форма и темп близки, но неточны."""
    return render(user, rng, shape_sigma=0.04, speed_sigma=0.22,
                  profile_noise=0.4)


# -------------------------------------------------------------- оценка
def run(n_users=30, n_enroll=5, n_test=20, seed=2026, verbose=True):
    """Возвращает по каждому пользователю оценки (shape_ratio, z) для всех типов проб."""
    rng = random.Random(seed)
    users = [SyntheticUser(rng) for _ in range(n_users)]
    records = []
    for ui, u in enumerate(users):
        prof = core.Profile.enroll(f"user{ui}", [genuine(u, rng) for _ in range(n_enroll)])
        rec = {"gen": [], "rand": [], "shape": [], "informed": []}
        for _ in range(n_test):
            rec["gen"].append(prof.components(genuine(u, rng)))
            other = users[rng.choice([k for k in range(n_users) if k != ui])]
            rec["rand"].append(prof.components(genuine(other, rng)))
            rec["shape"].append(prof.components(forgery_shape(u, rng)))
            rec["informed"].append(prof.components(forgery_informed(u, rng)))
        records.append(rec)
        if verbose:
            print(f"  пользователь {ui + 1}/{n_users} обработан", flush=True)
    return records


def rates(records, threshold, beta=core.DYN_BETA):
    """FRR и FAR при заданном пороге оценки S = shape + beta * z."""
    out = {"frr": [], "rand": [], "shape": [], "informed": []}
    for r in records:
        sc = {k: [a + beta * b for a, b in v] for k, v in r.items()}
        out["frr"].append(sum(s > threshold for s in sc["gen"]) / len(sc["gen"]))
        for key in ("rand", "shape", "informed"):
            out[key].append(sum(s <= threshold for s in sc[key]) / len(sc[key]))
    return {k: statistics.fmean(v) for k, v in out.items()}


def sweep(records, thresholds, beta=core.DYN_BETA):
    return [(t, rates(records, t, beta)) for t in thresholds]


def eer(curve, key):
    """Равная ошибка: пересечение FRR и FAR(key), линейная интерполяция."""
    for (k1, r1), (k2, r2) in zip(curve, curve[1:]):
        d1 = r1["frr"] - r1[key]
        d2 = r2["frr"] - r2[key]
        if d1 == 0:
            return k1, r1["frr"]
        if d1 * d2 < 0:
            a = d1 / (d1 - d2)
            return k1 + a * (k2 - k1), r1["frr"] + a * (r2["frr"] - r1["frr"])
    return None


def main():
    n_users = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    print(f"Модельный эксперимент: {n_users} пользователей")
    records = run(n_users)
    ths = [round(1.0 + 0.25 * i, 2) for i in range(0, 25)]
    curve = sweep(records, ths)
    print("\n порог |  FRR  | FAR(random) | FAR(shape) | FAR(informed)")
    for t, r in curve:
        print(f" {t:5.2f}  | {r['frr']*100:5.1f} | {r['rand']*100:11.1f} |"
              f" {r['shape']*100:10.1f} | {r['informed']*100:13.1f}")
    print("\nРабочая точка T =", core.THRESHOLD, ":", rates(records, core.THRESHOLD))
    for key in ("rand", "shape", "informed"):
        print("EER", key, eer(curve, key))
    with open("experiment_results.json", "w", encoding="utf-8") as f:
        json.dump({"curve": curve, "n_users": n_users}, f, ensure_ascii=False)


if __name__ == "__main__":
    main()
