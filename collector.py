#!/usr/bin/env python3
"""
collector.py - Pengumpul data sistem operasi Debian/Linux.

Membaca langsung file virtual di /proc (tanpa psutil) lalu menulis hasilnya
ke web/data.json setiap INTERVAL detik. Halaman web statis (web/index.html)
membaca file JSON tersebut.

Sumber data:
  /proc/meminfo     -> memori & swap
  /proc/stat        -> waktu CPU (dihitung selisihnya untuk persen pemakaian)
  /proc/loadavg     -> load average
  /proc/cpuinfo     -> model & jumlah core
  /proc/uptime      -> lama sistem menyala
  /proc/mounts      -> daftar partisi (kapasitas via os.statvfs)
  /proc/diskstats   -> sektor baca/tulis disk
  /proc/net/dev     -> byte terima/kirim jaringan
  /proc/[pid]/stat  -> daftar proses
"""
import json
import os
import platform
import sys
import time

INTERVAL = 2                      # detik antar pembacaan
TOP_PROCESS = 15                  # jumlah proses teratas yang ditampilkan
OUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "data.json")
SECTOR = 512                      # ukuran sektor di /proc/diskstats (selalu 512 byte)
PAGE = os.sysconf("SC_PAGE_SIZE")
CLK_TCK = os.sysconf("SC_CLK_TCK")
FS_ABAIKAN = {"proc", "sysfs", "devtmpfs", "devpts", "tmpfs", "cgroup", "cgroup2",
              "securityfs", "pstore", "bpf", "autofs", "mqueue", "hugetlbfs",
              "debugfs", "tracefs", "fusectl", "configfs", "binfmt_misc",
              "overlay", "squashfs", "nsfs", "ramfs", "efivarfs"}


def baca(path):
    with open(path) as f:
        return f.read()


# ---------------------------------------------------------------- MEMORI
def info_memori():
    m = {}
    for baris in baca("/proc/meminfo").splitlines():
        kunci, nilai = baris.split(":", 1)
        m[kunci] = int(nilai.split()[0]) * 1024          # kB -> byte
    total = m["MemTotal"]
    bebas = m["MemFree"]
    cache = m.get("Buffers", 0) + m.get("Cached", 0) + m.get("SReclaimable", 0)
    tersedia = m.get("MemAvailable", bebas + cache)
    terpakai = total - bebas - cache
    return {
        "total": total, "terpakai": max(terpakai, 0), "cache": cache,
        "bebas": bebas, "tersedia": tersedia,
        "swap_total": m.get("SwapTotal", 0),
        "swap_terpakai": m.get("SwapTotal", 0) - m.get("SwapFree", 0),
    }


# ---------------------------------------------------------------- CPU
def waktu_cpu():
    """Return dict nama_cpu -> (total, idle) dari /proc/stat."""
    hasil = {}
    for baris in baca("/proc/stat").splitlines():
        if not baris.startswith("cpu"):
            continue
        bagian = baris.split()
        angka = list(map(int, bagian[1:]))
        idle = angka[3] + (angka[4] if len(angka) > 4 else 0)   # idle + iowait
        hasil[bagian[0]] = (sum(angka[:8]), idle)
    return hasil


def persen_cpu(lama, baru):
    hasil = {}
    for nama, (t2, i2) in baru.items():
        t1, i1 = lama.get(nama, (t2, i2))
        dt, di = t2 - t1, i2 - i1
        hasil[nama] = round(100.0 * (dt - di) / dt, 1) if dt > 0 else 0.0
    return hasil


def info_cpu():
    model, core = "Tidak diketahui", 0
    for baris in baca("/proc/cpuinfo").splitlines():
        if baris.startswith("model name") and model == "Tidak diketahui":
            model = baris.split(":", 1)[1].strip()
        if baris.startswith("processor"):
            core += 1
    return model, core or os.cpu_count()


# ---------------------------------------------------------------- DISK
def info_disk():
    hasil, terlihat = [], set()
    for baris in baca("/proc/mounts").splitlines():
        dev, mnt, fs = baris.split()[:3]
        if fs in FS_ABAIKAN or dev in terlihat or not dev.startswith("/"):
            continue
        terlihat.add(dev)
        try:
            s = os.statvfs(mnt)
        except OSError:
            continue
        total = s.f_blocks * s.f_frsize
        if total == 0:
            continue
        bebas = s.f_bavail * s.f_frsize
        hasil.append({"dev": dev, "mount": mnt, "fs": fs, "total": total,
                      "terpakai": total - s.f_bfree * s.f_frsize, "bebas": bebas})
    return hasil


def io_disk():
    """Total byte baca/tulis semua disk fisik (sda, vda, nvme0n1, ...)."""
    baca_b = tulis_b = 0
    for baris in baca("/proc/diskstats").splitlines():
        f = baris.split()
        nama = f[2]
        fisik = (nama.startswith(("sd", "vd", "xvd", "hd")) and not nama[-1].isdigit()) \
            or (nama.startswith("nvme") and "p" not in nama.split("n")[-1]) \
            or (nama.startswith("mmcblk") and "p" not in nama[6:])
        if fisik:
            baca_b += int(f[5]) * SECTOR
            tulis_b += int(f[9]) * SECTOR
    return baca_b, tulis_b


# ---------------------------------------------------------------- JARINGAN
def io_jaringan():
    rx = tx = 0
    for baris in baca("/proc/net/dev").splitlines()[2:]:
        nama, data = baris.split(":", 1)
        if nama.strip() == "lo":
            continue
        f = data.split()
        rx += int(f[0])
        tx += int(f[8])
    return rx, tx


# ---------------------------------------------------------------- PROSES
STATUS = {"R": "Running", "S": "Sleeping", "D": "Disk wait", "Z": "Zombie",
          "T": "Stopped", "t": "Tracing", "I": "Idle", "X": "Dead"}


def daftar_proses(mem_total):
    proses, hitung = [], {}
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            isi = baca(f"/proc/{pid}/stat")
            # nama proses ada di dalam tanda kurung dan bisa memuat spasi
            kiri, kanan = isi.index("("), isi.rindex(")")
            nama = isi[kiri + 1:kanan]
            f = isi[kanan + 2:].split()
            status = f[0]
            utime, stime = int(f[11]), int(f[12])
            rss = int(f[21]) * PAGE
            threads = int(f[17])
            uid = os.stat(f"/proc/{pid}").st_uid
        except (OSError, ValueError, IndexError):
            continue                                     # proses sudah selesai
        hitung[status] = hitung.get(status, 0) + 1
        proses.append({"pid": int(pid), "nama": nama, "status": STATUS.get(status, status),
                       "uid": uid, "threads": threads, "rss": rss,
                       "mem_persen": round(100.0 * rss / mem_total, 1),
                       "_cpu": utime + stime})
    return proses, hitung


def nama_user(uid, cache={}):
    if uid not in cache:
        cache[uid] = str(uid)
        try:
            for baris in baca("/etc/passwd").splitlines():
                f = baris.split(":")
                if int(f[2]) == uid:
                    cache[uid] = f[0]
                    break
        except OSError:
            pass
    return cache[uid]


# ---------------------------------------------------------------- UTAMA
def tulis_atomik(data):
    tmp = OUT_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, OUT_FILE)                            # cegah JSON setengah jadi


def main():
    if not os.path.isdir("/proc"):
        sys.exit("Skrip ini harus dijalankan di Linux (butuh /proc).")
    os.makedirs(os.path.dirname(OUT_FILE), exist_ok=True)

    model, core = info_cpu()
    cpu_lama, waktu_lama = waktu_cpu(), time.time()
    disk_lama, net_lama = io_disk(), io_jaringan()
    proses_lama = {}
    print(f"Collector berjalan, menulis ke {OUT_FILE} tiap {INTERVAL} detik. Ctrl+C untuk berhenti.")

    while True:
        time.sleep(INTERVAL)
        sekarang = time.time()
        dt = sekarang - waktu_lama

        cpu_baru = waktu_cpu()
        persen = persen_cpu(cpu_lama, cpu_baru)
        d_baru, n_baru = io_disk(), io_jaringan()
        mem = info_memori()

        proses, hitung = daftar_proses(mem["total"])
        for p in proses:                                 # % CPU per proses dari selisih tick
            sebelum = proses_lama.get(p["pid"], p["_cpu"])
            p["cpu_persen"] = round(100.0 * (p["_cpu"] - sebelum) / CLK_TCK / dt, 1)
            p["user"] = nama_user(p.pop("uid"))
        proses_lama = {p["pid"]: p.pop("_cpu") for p in proses}
        proses.sort(key=lambda p: (p["cpu_persen"], p["rss"]), reverse=True)

        load = baca("/proc/loadavg").split()
        data = {
            "waktu": int(sekarang),
            "sistem": {
                "host": platform.node(), "kernel": platform.release(),
                "arsitektur": platform.machine(),
                "uptime": float(baca("/proc/uptime").split()[0]),
            },
            "cpu": {
                "model": model, "core": core, "total": persen.get("cpu", 0.0),
                "per_core": [persen[k] for k in sorted(
                    (k for k in persen if k != "cpu"), key=lambda x: int(x[3:]))],
                "load": [float(load[0]), float(load[1]), float(load[2])],
            },
            "memori": mem,
            "disk": info_disk(),
            "io": {
                "disk_baca": max(d_baru[0] - disk_lama[0], 0) / dt,     # byte/detik
                "disk_tulis": max(d_baru[1] - disk_lama[1], 0) / dt,
                "net_rx": max(n_baru[0] - net_lama[0], 0) / dt,
                "net_tx": max(n_baru[1] - net_lama[1], 0) / dt,
            },
            "proses": {"total": len(proses), "status": hitung,
                       "teratas": proses[:TOP_PROCESS]},
        }
        tulis_atomik(data)
        cpu_lama, waktu_lama, disk_lama, net_lama = cpu_baru, sekarang, d_baru, n_baru


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nBerhenti.")
