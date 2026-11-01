# Sumber ekspresi maskot

Master: `resource/imoticon/svg/*.svg` (16 ekspresi). Jangan memakai PNG pratinjau yang berlatar opaque sebagai drawable wajah.

Android saat ini memakai bitmap hasil rasterisasi SVG saat pengembangan, bukan SVG runtime. Alasannya master memuat filter blur/glow yang tidak didukung lengkap oleh AndroidSVG. Menambahkan dependency AndroidSVG tidak berarti filter SVG telah terdukung; renderer Canvas saat ini memuat drawable PNG.

Regenerasi dengan librsvg dan Pillow:

```
server/.venv/bin/python scripts/render_mascots.py
server/.venv/bin/python scripts/render_mascots.py --check
```

Output berukuran 512x512, RGBA dengan tepi transparan, dan berasal langsung dari SVG, mempertahankan blur/gradient. `docs/mascot-assets.json` mencatat hash sumber dan hasil. Script menggunakan ukuran viewport eksplisit, bukan screenshot browser (yang dapat menghasilkan gambar kecil di sudut kiri atas). `--check` memeriksa semua 16 aset, alpha, ukuran konten, serta posisi horizontal. Latar aplikasi memakai `#000000`.

Verifikasi ini adalah pemeriksaan aset, bukan bukti hasil screenshot atau frame rate di perangkat Android.
