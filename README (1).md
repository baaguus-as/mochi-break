# Mochi Break 🐱

Pengingat istirahat layar dengan interval dan durasi yang bisa diatur, ditemani kucing **Mochi**.

Menatap layar terlalu lama membuat mata lelah dan jenuh. Dalam prinsip ergonomi kerja di depan komputer, jeda berkala sangat dianjurkan. Aplikasi ini mengingatkan kamu untuk berhenti sejenak: saat waktu kerja habis, Mochi berjalan masuk ke layar dan menemanimu istirahat.

## Fitur

- Interval kerja (1–180 menit) dan durasi istirahat (1–60 menit) bisa diatur sendiri
- Siklus otomatis: kerja, istirahat, kerja lagi (auto repeat)
- Overlay layar penuh saat istirahat, Mochi berjalan masuk dari samping layar
- Tombol "Shoo cat" untuk menutup istirahat lebih awal (bisa dimatikan lewat mode istirahat penuh)
- Desktop pet: berjalan, tidur, bisa di-drag, klik kanan untuk menu
- Klik Mochi untuk suara meow atau dengkur (dibuat otomatis, tanpa file audio tambahan)
- Gambar kucing custom (PNG/GIF/WebP/JPG)
- Pengaturan tersimpan otomatis

## Saran pengaturan

- Bawaan: 25 menit kerja, 5 menit istirahat (teknik Pomodoro)
- Untuk menjaga mata: aturan 20-20-20 yang umum dianjurkan, yaitu setiap 20 menit, alihkan pandangan ke objek berjarak sekitar 6 meter selama 20 detik. Karena durasi diatur dalam menit, pakai 20 menit kerja dan 1 menit istirahat.

## Kebutuhan

- Windows (suara memakai `winsound`)
- Python 3.9 atau lebih baru (Tkinter sudah termasuk)
- Opsional, untuk gambar WebP/JPG custom:

```
python -m pip install Pillow
```

## Cara pakai

```
python mochi_break.py
```

1. Atur durasi kerja dan istirahat, lalu klik **Start**.
2. Klik **Buka Desktop Pet Mochi** untuk memunculkan Mochi di desktop.
3. Klik **Panggil Kucing Sekarang (Test)** untuk mencoba tampilan istirahat.

## Kontribusi

Issue dan pull request dipersilakan. Ide pengembangan: durasi istirahat dalam detik, dukungan macOS/Linux untuk suara, animasi tambahan, kustomisasi suara, tema warna.

## Lisensi

MIT
