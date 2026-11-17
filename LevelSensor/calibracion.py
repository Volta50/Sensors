#!/usr/bin/env python3
"""
Calibración voltaje -> nivel (cm) con ESP32.

Uso:
    pip install pyserial numpy
    python calibracion.py                      # autodetecta el puerto
    python calibracion.py --port COM5          # o /dev/ttyUSB0
    python calibracion.py --csv calibracion.csv

Comandos dentro del programa:
    m,<nivel_cm>   toma una muestra (100 lecturas / 1 s) a ese nivel. Ej: m,1  m,2.5
    list           muestra las mediciones guardadas
    fit [grado]    ajusta nivel = f(V) por polinomio (grado 1 por defecto)
    undo           borra la última medición (resumen y crudo)
    q              salir

Archivos generados:
    calibracion.csv         una fila por medición (media, desv. est., min, max, ...)
    calibracion_crudo.csv   las 100 muestras de cada medición
"""

import argparse
import csv
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import serial
from serial.tools import list_ports

SUMMARY_FIELDS = [
    "timestamp", "nivel_cm", "n", "mean_mV", "std_mV",
    "min_mV", "max_mV", "median_mV", "mean_V", "std_V", "elapsed_ms",
]
RAW_FIELDS = ["timestamp", "nivel_cm", "idx", "mV"]

CMD_M = re.compile(r"^m[\s,]+(-?\d+(?:\.\d+)?)$", re.IGNORECASE)


# ----------------------------------------------------------------- Serial
def choose_port(port_arg):
    if port_arg:
        return port_arg
    ports = list(list_ports.comports())
    if not ports:
        sys.exit("No se encontró ningún puerto serie. Usa --port.")
    if len(ports) == 1:
        return ports[0].device
    print("Puertos disponibles:")
    for i, p in enumerate(ports):
        print(f"  [{i}] {p.device}  {p.description}")
    return ports[int(input("Elige el número: "))].device


def measure(ser, level_str, timeout=15.0):
    """Envía m,<nivel> y devuelve (lista de mV, duración en ms)."""
    ser.reset_input_buffer()
    ser.write(f"m,{level_str}\n".encode())

    samples, in_block = [], False
    deadline = time.time() + timeout
    while time.time() < deadline:
        line = ser.readline().decode(errors="replace").strip()
        if not line:
            continue
        if line.startswith("ERR"):
            raise RuntimeError(line)
        if line.startswith("BEGIN"):
            in_block, samples = True, []
        elif in_block and line.startswith("S,"):
            samples.append(int(line.split(",")[2]))
        elif in_block and line.startswith("END,"):
            _, n_expected, elapsed = line.split(",")
            if len(samples) != int(n_expected):
                raise RuntimeError(
                    f"Se recibieron {len(samples)} muestras de {n_expected}; repite la medición."
                )
            return samples, int(elapsed)
        # cualquier otra línea (lectura continua, mensajes) se ignora
    raise TimeoutError("El ESP32 no respondió a tiempo.")


# ------------------------------------------------------------------ Datos
def compute_stats(samples_mv):
    a = np.asarray(samples_mv, dtype=float)
    return {
        "n": a.size,
        "mean_mV": a.mean(),
        "std_mV": a.std(ddof=1),          # desviación estándar muestral
        "min_mV": a.min(),
        "max_mV": a.max(),
        "median_mV": np.median(a),
        "mean_V": a.mean() / 1000.0,
        "std_V": a.std(ddof=1) / 1000.0,
    }


def append_rows(path, fields, rows):
    new_file = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new_file:
            w.writeheader()
        w.writerows(rows)


def read_rows(path):
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_rows(path, fields, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def save_measurement(summary_path, raw_path, level, samples, elapsed_ms):
    ts = datetime.now().isoformat(timespec="milliseconds")
    st = compute_stats(samples)
    summary = {
        "timestamp": ts,
        "nivel_cm": level,
        "n": st["n"],
        "mean_mV": f"{st['mean_mV']:.3f}",
        "std_mV": f"{st['std_mV']:.3f}",
        "min_mV": int(st["min_mV"]),
        "max_mV": int(st["max_mV"]),
        "median_mV": f"{st['median_mV']:.1f}",
        "mean_V": f"{st['mean_V']:.5f}",
        "std_V": f"{st['std_V']:.5f}",
        "elapsed_ms": elapsed_ms,
    }
    raw = [{"timestamp": ts, "nivel_cm": level, "idx": i, "mV": mv}
           for i, mv in enumerate(samples)]
    append_rows(summary_path, SUMMARY_FIELDS, [summary])
    append_rows(raw_path, RAW_FIELDS, raw)
    return st


# --------------------------------------------------------------- Comandos
def cmd_list(summary_path):
    rows = read_rows(summary_path)
    if not rows:
        print("Aún no hay mediciones.")
        return
    print(f"{'#':>3} {'nivel_cm':>9} {'media_V':>9} {'std_mV':>8} {'min_mV':>7} {'max_mV':>7} {'n':>4}")
    for i, r in enumerate(rows, 1):
        print(f"{i:>3} {r['nivel_cm']:>9} {r['mean_V']:>9} {r['std_mV']:>8} "
              f"{r['min_mV']:>7} {r['max_mV']:>7} {r['n']:>4}")


def cmd_undo(summary_path, raw_path):
    rows = read_rows(summary_path)
    if not rows:
        print("No hay nada que borrar.")
        return
    last = rows[-1]
    write_rows(summary_path, SUMMARY_FIELDS, rows[:-1])
    raw = [r for r in read_rows(raw_path) if r["timestamp"] != last["timestamp"]]
    write_rows(raw_path, RAW_FIELDS, raw)
    print(f"Borrada la medición de nivel {last['nivel_cm']} cm ({last['timestamp']}).")


def cmd_fit(summary_path, degree):
    rows = read_rows(summary_path)
    v = np.array([float(r["mean_V"]) for r in rows])
    y = np.array([float(r["nivel_cm"]) for r in rows])
    if len(set(y)) <= degree:
        print(f"Para un ajuste de grado {degree} necesitas al menos {degree + 1} niveles distintos.")
        return

    coefs = np.polyfit(v, y, degree)
    pred = np.polyval(coefs, v)
    res = y - pred
    rmse = np.sqrt(np.mean(res ** 2))
    ss_tot = np.sum((y - y.mean()) ** 2)
    r2 = 1 - np.sum(res ** 2) / ss_tot if ss_tot > 0 else float("nan")

    terms = [f"{c:.6g}*V^{degree - i}" if degree - i > 0 else f"{c:.6g}"
             for i, c in enumerate(coefs)]
    print(f"\nAjuste grado {degree}:  nivel_cm = " + " + ".join(terms).replace("+ -", "- "))
    print(f"R² = {r2:.6f}   RMSE = {rmse:.4f} cm   error máx = {np.abs(res).max():.4f} cm")
    print(f"Coeficientes (de mayor a menor grado): {[float(f'{c:.8g}') for c in coefs]}")
    if degree == 1:
        print(f"\nPara el ESP32:  float nivel_cm = {coefs[0]:.6f}f * V {"-" if coefs[1] < 0 else "+"} {abs(coefs[1]):.6f}f;")
    print("\n  nivel_cm   V_medio   nivel_ajustado   residuo")
    for yi, vi, pi, ri in zip(y, v, pred, res):
        print(f"  {yi:8.2f}  {vi:8.4f}  {pi:14.3f}  {ri:+8.3f}")
    print()


# ------------------------------------------------------------------- Main
def main():
    ap = argparse.ArgumentParser(description="Calibración voltaje->nivel con ESP32")
    ap.add_argument("--port", help="Puerto serie (COM5, /dev/ttyUSB0...). Autodetecta si se omite.")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--csv", default="calibracion.csv", help="CSV de resumen")
    args = ap.parse_args()

    summary_path = Path(args.csv)
    raw_path = summary_path.with_name(summary_path.stem + "_crudo" + summary_path.suffix)

    port = choose_port(args.port)
    print(f"Conectando a {port} @ {args.baud}...")
    ser = serial.Serial(port, args.baud, timeout=1)
    time.sleep(2)                 # el ESP32 se reinicia al abrir el puerto
    ser.reset_input_buffer()

    print(__doc__.split("Comandos dentro del programa:")[1].split("Archivos generados:")[0])
    print(f"Guardando en: {summary_path}  y  {raw_path}\n")

    try:
        while True:
            try:
                text = input("> ").strip()
            except EOFError:
                break
            if not text:
                continue
            low = text.lower()

            if low in ("q", "quit", "exit"):
                break
            elif low == "list":
                cmd_list(summary_path)
            elif low == "undo":
                cmd_undo(summary_path, raw_path)
            elif low.startswith("fit"):
                parts = low.split()
                try:
                    deg = int(parts[1]) if len(parts) > 1 else 1
                except ValueError:
                    print("Uso: fit [grado]")
                    continue
                if not read_rows(summary_path):
                    print("Aún no hay mediciones.")
                else:
                    cmd_fit(summary_path, deg)
            elif (m := CMD_M.match(text)):
                level = m.group(1)
                print(f"Midiendo nivel {level} cm (~1 s)...")
                try:
                    samples, elapsed = measure(ser, level)
                except (RuntimeError, TimeoutError) as e:
                    print(f"Error: {e}")
                    continue
                st = save_measurement(summary_path, raw_path, level, samples, elapsed)
                print(f"  nivel {level} cm | media {st['mean_mV']:.1f} mV ({st['mean_V']:.4f} V) | "
                      f"std {st['std_mV']:.2f} mV | min {st['min_mV']:.0f} / max {st['max_mV']:.0f} mV | "
                      f"n={st['n']} | {elapsed} ms  -> guardado")
            else:
                print("Comando no reconocido. Usa: m,<nivel_cm> | list | fit [grado] | undo | q")
    except KeyboardInterrupt:
        pass
    finally:
        ser.close()
        print("\nPuerto cerrado.")


if __name__ == "__main__":
    main()
