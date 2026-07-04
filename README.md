# archsetup

🇹🇷 Arch Linux kurulumu ve kurulum sonrası yapılandırma için interaktif TUI aracı.
🇬🇧 Interactive TUI for Arch Linux installation and post-install setup.

[Python](https://www.python.org/) + [Textual](https://textual.textualize.io/) ile
yazılmıştır. `installarch` (canlı ISO'dan kurulum) ve `installarchde`
(kurulum sonrası) betiklerinin modern, tek çatı altında birleşmiş halidir.

## Tasarım

- **Sabit ID mimarisi:** Menü öğeleri ve kategoriler kimliklerle (`console`,
  `system-update`) yönetilir; ekranda görünen metin `locales/*.toml`
  dosyalarından gelir. Dil değiştirmek hiçbir mantığı etkilemez.
- **Veri güdümlü menüler:** Paket listeleri `data/` altındaki TOML
  dosyalarındadır. Yeni uygulama eklemek = birkaç satır TOML.
- **İki mod:** Kurulu sistemde *kurulum sonrası* modu (hazır); canlı ISO'da
  *kurucu* modu (yol haritasında).

## Kullanım

```bash
git clone https://github.com/drpars/archsetup
cd archsetup
./archsetup
```

Gereksinimler: `python` ve `python-textual` (resmi depoda). Root olarak
çalıştırmayın; sudo gerektiğinde sorulur.

### Fonksiyon modu (TUI olmadan tek görev)

```bash
./archsetup --list          # görevleri listele
./archsetup system-update   # tek görevi çalıştır
./archsetup --lang en       # arayüz dili
```

## Dizin yapısı

```
data/        paket tanımları (TOML) — betiğin "içeriği"
locales/     tr.toml, en.toml — tüm arayüz metinleri
src/archsetup/
  core/      i18n, pacman, donanım tespiti, görevler
  ui/        Textual ekranları
  installer/ canlı ISO modu (henüz taşınmadı)
```

## Yol haritası

- [x] Çekirdek: i18n (TR/EN), veri güdümlü menüler, pacman/AUR kurulumu
- [x] Sistem güncelleme görevleri, uygulama ve sürücü kategorileri
- [x] Masaüstü ortamları (Hyprland, Plasma, GNOME) ve giriş yöneticileri
- [ ] NVIDIA/ASUS sürücü yapılandırmaları (mkinitcpio, servisler)
- [ ] Yapılandırma görevleri: dotfiles, swap/hibernation, SDDM teması
- [ ] Kurucu modu: disk bölümleme, pacstrap, chroot yapılandırması
- [ ] Geliştirme: `installarch` (archfi türevi) + `installarchde` betiklerinin
      birleşimi. Teşekkürler [MatMoul/archfi](https://github.com/MatMoul/archfi).
