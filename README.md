# Mochi Break 🐱

Timer Pomodoro dengan maskot kucing **Mochi**. Saat waktu kerja habis, Mochi berjalan masuk ke layar dan menemanimu istirahat. Bisa juga dipakai sebagai desktop pet yang berjalan, di-drag, dan diklik (bunyi meow atau dengkur).

## Fitur

- Timer kerja / istirahat (Pomodoro) dengan auto repeat
- Overlay layar penuh saat istirahat, Mochi berjalan masuk dari samping layar
- Tombol "Shoo cat" untuk menutup istirahat lebih awal (bisa dimatikan lewat mode istirahat penuh)
- Desktop pet: berjalan, tidur, bisa di-drag, klik kanan untuk menu
- Klik Mochi untuk suara meow atau dengkur (dibuat otomatis, tanpa file audio tambahan)
- Gambar kucing custom (PNG/GIF/WebP/JPG)
- Pengaturan tersimpan otomatis

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

Issue dan pull request dipersilakan. Ide pengembangan: dukungan macOS/Linux untuk suara, animasi tambahan, kustomisasi suara, tema warna.

## Lisensi

MIT
