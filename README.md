# Monitor Sumber Daya Debian

Dashboard web statis yang menampilkan kondisi sistem operasi Debian: CPU, memori, disk,
input/output, dan proses. Data dibaca langsung dari `/proc` tanpa library tambahan.

## Cara kerja

```
/proc/*  -->  collector.py  -->  web/data.json  -->  web/index.html (browser)
 (kernel)     (baca tiap 2 dtk)   (file statis)       (fetch tiap 2 dtk)
```

Halaman web tidak bisa membaca `/proc` sendiri, jadi `collector.py` menulis hasil bacaan
ke `data.json`, lalu halaman statis membacanya.

## Struktur

```
debian-os-dashboard/
├── collector.py              # pembaca /proc (Python 3, tanpa dependensi)
├── debian-dashboard.service  # unit systemd (opsional)
└── web/
    └── index.html            # tampilan dashboard (HTML + CSS + JS)
```

## Menjalankan di Debian

Python 3 sudah ada di Debian. Kalau belum: `sudo apt install python3`.

```bash
# Terminal 1: jalankan collector
python3 collector.py

# Terminal 2: sajikan folder web
cd web && python3 -m http.server 8000
```

Buka `http://localhost:8000`. Kalau Debian berjalan di VM tanpa GUI, cek IP-nya dengan
`ip a`, lalu buka `http://IP-VM:8000` dari browser di komputer host.

## Menjalankan sebagai service (implementasi)

```bash
sudo apt install nginx python3
sudo cp -r debian-os-dashboard /opt/
sudo chown -R www-data:www-data /opt/debian-os-dashboard
sudo cp /opt/debian-os-dashboard/debian-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now debian-dashboard

# sajikan folder web lewat nginx
sudo ln -sfn /opt/debian-os-dashboard/web /var/www/html/dashboard
```

Akses di `http://IP-VM/dashboard/`. Cek service dengan `systemctl status debian-dashboard`.

## Pengujian (bandingkan dengan perintah bawaan)

| Modul | Bandingkan dengan | Cara memicu beban |
|---|---|---|
| CPU | `top`, `mpstat` | `sudo apt install stress` lalu `stress --cpu 2 --timeout 30` |
| Memori | `free -m` | `stress --vm 1 --vm-bytes 512M --timeout 30` |
| Disk | `df -h` | `dd if=/dev/zero of=/tmp/uji bs=1M count=500` |
| Disk I/O | `iostat -x 1` | `dd if=/dev/zero of=/tmp/uji bs=1M count=1000 oflag=direct` |
| Jaringan | `ip -s link` | unduh file besar dengan `wget` |
| Proses | `ps aux --sort=-%cpu` | jalankan `stress` lalu lihat PID-nya di tabel |

Hapus file uji setelah selesai: `rm /tmp/uji`.

## Catatan untuk laporan

- **Memori terpakai** = `MemTotal - MemFree - (Buffers + Cached + SReclaimable)`, sama seperti
  perintah `free`. Cache dihitung terpisah karena bisa dilepas kernel saat dibutuhkan.
- **% CPU** dihitung dari selisih dua pembacaan `/proc/stat`:
  `(selisih total - selisih idle) / selisih total`.
- **Kecepatan I/O** = selisih byte antara dua pembacaan dibagi selisih waktu.
  Untuk disk, sektor di `/proc/diskstats` dikali 512.
- **% CPU per proses** dihitung dari selisih `utime + stime` di `/proc/[pid]/stat`,
  dibagi `CLK_TCK` dan selisih waktu.
