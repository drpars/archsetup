# archsetup — çalışma notları

Arch Linux kurulum + kurulum sonrası TUI'si. Python 3.11+ / Textual.
Ne yaptığı ve dizin yapısı için [README.md](README.md); burada yalnızca
koda bakarak görülemeyecek kurallar ve zor yoldan öğrenilmiş tuzaklar var.

## Komutlar

```bash
.venv/bin/pytest              # tum test paketi
./archsetup --list            # gorev kimlikleri
./archsetup <gorev-id>        # TUI olmadan tek gorev
./archsetup --lang en         # arayuz dili
```

Root olarak **çalıştırılmaz**; `__main__` reddeder, sudo gerektiği yerde
tek tek çağrılır.

## Değişmez kurallar

**Depo public.** Kişisel veri (IP, hostname, anahtar, ağ topolojisi) hiçbir
dosyaya girmez. Kullanıcıya özel şeyler kullanıcının kendi dosyasından
okunur — örnek: SSH envanteri `~/.ssh/archsetup.toml`.

**`tr.toml` ve `en.toml` anahtar kümeleri birebir aynı olmak zorunda.**
`test_locale_files_have_identical_keys` bunu zorluyor. Yeni metin eklerken
ikisine birden ekleyin.

**`i18n.t()`'nin ilk parametresi `key` adında.** Biçim argümanını `key=`
diye adlandırırsanız `TypeError` alırsınız. `{keyfile}` gibi başka bir ad
kullanın.

**Görev grupları serbest metin.** `group="netwrok"` yazarsanız görev
arayüzden sessizce kaybolur, hiçbir yerde hata çıkmaz.
`test_every_task_group_is_reachable` bunu yakalıyor — yeni grup eklerken
`screens.CONFIG_SUBMENUS`'a da ekleyin.

## Mimari

Görevler düz fonksiyondur, `int` döner (0 = başarı) ve `tasks.TASKS`
içinde bir `group` ile kaydedilir. Aynı fonksiyon hem TUI'den (terminal
askıya alınarak) hem `archsetup <id>` ile çalışır — bu yüzden `print` ve
`input` serbest, Textual'a bağımlılık yok.

Hazır yardımcılar; yenisini yazmadan önce bunlara bakın:

| İş | Kullanılacak |
|---|---|
| root dosyasına yazmak | `sysedit.sudo_write()` (kullanıcı olarak oku, sudo tee ile yaz) |
| komut çalıştırmak | `pacman.run()` (komutu ekrana basar) |
| evet/hayır sormak | `prompt.ask_yes()` |
| sistem servisi | `services.enable()` / `enable_now()` / `is_active()` |
| kullanıcı servisi | `services.user_unit_exists()` / `enable_user_now()` |
| donanım tespiti | `hardware.py` (DMI, lspci, cpuinfo) |

## Tuzaklar (hepsi gerçek hatalardan)

**`data/` altındaki varlıklar `git pull` ile devreye girmez.** Betikler ve
unit dosyaları görev çalıştırıldığında `~/.local/bin`, `~/.config/systemd/user`
gibi yerlere **kopyalanır**; depoyu güncellemek kurulu kopyayı değiştirmez.
Bir varlığı düzelten commit, ilgili görev yeniden çalıştırılana kadar
makinede etkisizdir. `ee-port-watch` düzeltmesi tam olarak böyle kaçtı:
hata giderildikten sonra bile, hatanın canlı olduğu makinede eski sürüm
çalışmaya devam etti. Kurulu kopyayı depoyla karşılaştırarak doğrulayın:
`diff data/audio/ee-port-watch ~/.local/bin/ee-port-watch`

**`services.unit_exists()` yalnızca SİSTEM unit'lerine bakar.** `--user`
unit'leri orada görünmez ve sessizce "yok" sanılır → `user_unit_exists()`.

**`services.enable_user_now()` sonunda `try-restart` yapar.** Değişen unit
dosyasını devreye almak için doğru, ama `ssh-agent.socket` gibi durumu olan
bir unit'te agent'ı boşaltır ve kullanıcı parolalarını yeniden girer. Böyle
unit'lerde önce `is-active` bakıp çalışıyorsa dokunmayın.

**`Path.with_suffix()` kullanıcıdan gelen adlarda kullanılmaz.**
`/etc/hostname` FQDN ise makine adı nokta içerir; `with_suffix(".pub")`
`github_host.example.com` için `github_host.example.pub` üretir. Adı elle
birleştirin (`ssh._pub()`).

**Kabuk varlıklarında `while read … done < <(cmd)` döngüsü, besleyen komut
ölse bile 0 döner.** `Restart=on-failure` altında bu "iş bitti" demektir ve
izleyici sessizce ölür. İzleyen betikler döngü sonrası `exit 1` yapmalı.

**Yapılandırma dosyasında bir anahtarı ararken bölüme bakın.** iwd/ini
türü dosyalarda aynı anahtar başka bölümde de olabilir; onu değiştirmek
hiçbir şey yapmaz ama başarılı görünür.

**`MenuScreen._items` bir sözlüktür, `id` ile anahtarlanır.** Aynı menüde
iki öğe aynı `id`'yi taşırsa ikincisi birincisini sessizce ezer.

**OpenSSH'ta ilk okunan değer kazanır.** `sshd_config` ve `ssh_config`'te
`Include` en üstte olduğu için drop-in ana dosyayı ezer; `Host *`
varsayılanları da en sonda kalmalıdır.

**iwd bağlanır ama adres vermez.** `EnableNetworkConfiguration`
varsayılanı `false`; iwd'yi etkinleştirip yalnızca `en*` için `.network`
dosyası yazmak, kablosuzu "bağlı ama IP'siz" bırakır. `wl*` için de dosya
gerekir. İkisinin birden adres vermesi ise ayrı bir hata — `core/iwd.py`
tam olarak onu geri almak için var.

**Secure Boot'ta imzalanması gereken dosya ESP'dekidir.** `bootctl install`
`/usr/lib/systemd/boot/efi/systemd-bootx64.efi`'nin **imzasız** kopyasını
`/efi/EFI/systemd/` ve `/efi/EFI/BOOT/` altına koyar. Yalnızca `/usr/lib`
altındakini imzalamak `sbctl` çıktısını "signed" gösterir ama firmware
imzasız kopyayı yükler ve makine Secure Boot'a takılır. İkisi de gerekli:
`/usr/lib/...efi.signed` systemd yükseltmelerinde ESP'ye kopyalanan dosyadır,
ESP'dekiler ise bu açılışta okunan dosyalardır.

**`enroll-keys` yalnızca setup mode'da çalışır.** Firmware setup mode'da
değilse PK/KEK yazılamaz; kontrol `/sys/firmware/efi/efivars/SetupMode-...`
dosyasından yapılır (4 bayt öznitelik başlığı + değer), `efivar` binary'si
gerekmez.

**pacstrap canlı ortamın `pacman.conf`'unu kullanır.** Hedefteki depoyu
eklemek pacstrap'e yardım etmez; `linux-g14` gibi depo dışı bir çekirdek
için [g14] önce `/etc/pacman.conf`'a girmeli. Depo satırı anahtar
güvenilir kılınmadan yazılırsa sonraki her `-Sy` imza hatası verir.

**`xdg-user-dir` yapılandırılmamış klasör için `$HOME` döner.** Hata
vermez, boş da dönmez. Bu cevabı olduğu gibi kullanmak duvar kağıtlarını
`~/Pictures/Wallpaper` yerine `~/Wallpaper`'a, dotfile yedeklerini de
`~/dotfiles_yedek`'e koyar. Yeni kurulumda `~/.config/user-dirs.dirs` hiç
yoktur; önce `xdg-user-dirs-update` çalışmalı.

**EFI bölümünün tipi de doğru olmalı, yalnızca dosya sistemi değil.**
"Linux filesystem" tipli bir FAT32 bölümü `mkfs`, `mount` ve `cp` için
kusursuz görünür; `bootctl` GPT tip GUID'ini doğrular ve reddeder, bazı
firmware'ler de yalnızca ESP tipli bölümleri tarar. Hata kurulumun en
sonunda çıkar. Düzeltmesi veri kaybı olmadan: `sfdisk --part-type`.

**kmscon sistem klavyesini okumaz.** `xkb-layout` kendi dosyasına
yazılmazsa libxkbcommon `us` düzenine düşer; `localectl set-x11-keymap`
X11 varsayılanını yazar, kmscon oraya hiç bakmaz. Ayrıca AUR'daki paket
adı **kmscon-git**; düz `kmscon` kaldırıldı. Bilinmeyen bir yapılandırma
anahtarı (örneğin artık var olmayan `font-dpi`) ölümcül değildir ama her
açılışta hata kaydı düşer — seçenek adları `src/config.c`'den doğrulanır.

**`OptionList.clear_options()` vurguyu (`highlighted`) `None` yapar.**
Filtreledikten sonra yeniden 0'a çekilmezse Enter hiçbir şey seçmez,
odağı listeye taşır; oradan yazılan harfler hiçbir yere gitmediği için
ekran donmuş gibi görünür.

**`authorized_keys` üretilmez, yalnızca denetlenir.** `config.local` yanlış
üretilirse dışarı bağlanamazsınız ve makinenin başındasınız; bozuk bir
`from=` ise içeri bağlanmayı imkânsız kılar ve fiziksel erişim gerektirir.
İki dosyanın hata maliyeti eşit değil.
