# archsetup — çalışma notları

Arch Linux kurulum + kurulum sonrası TUI'si. Python 3.11+ / Textual.
Ne yaptığı ve dizin yapısı için [README.md](README.md); burada yalnızca
koda bakarak görülemeyecek kurallar ve zor yoldan öğrenilmiş tuzaklar var.

## Komutlar

```bash
.venv/bin/pytest              # tum test paketi
./archsetup --overview        # arac ne yapar + gruplanmis gorev katalogu
./archsetup --list            # gorev kimlikleri (duz, greplenebilir)
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
`screens.CONFIG_SUBMENUS`'a **ve** `overview.GROUP_ORDER`'a da ekleyin.

**Yeni paket eklerken önce resmi depoya bakılır.** `aur = true` yazmadan
önce `pacman -Sp <ad>` denenir; resmi depoda karşılığı varsa o kullanılır.
Sebebi zevk değil: AUR paketleri imzasız PKGBUILD'lerden derlenir ve
2026'daki "Atomic Arch" kampanyası sahipsiz AUR paketlerini devralarak
zehirledi. Her AUR girdisi kalıcı bir yüzey; resmi depo karşılığı olan bir
paket için o yüzeyi açmak bedavaya risk almaktır. (`hyprshot` `extra`'da,
`swww` yerine `awww` de öyle bulundu.) Mevcut girdiler `--check-packages
--aur` ile izleniyor.

**AUR yardımcısına `--noconfirm` geçilmez.** PKGBUILD diff istemi, kurulum
anındaki tek gerçek savunma. `test_aur_helper_is_never_silenced` ve kaynak
taraması bunu koruyor — resmi depo `pacman` çağrılarında bayrak serbest,
yalnızca yardımcıda yasak.

**Bir kategori konu ekseninde olmak zorunda değil, ama ekseni görünür
olmalı.** Çoğu kategori konuya göre gruplanır; `yazi_extras` ve
`passthrough` "şu iş için ne gerekir" ekseninde durur ve aynı paketi iki
kategoride listeler. Yineleme bedava (`pacman -S --needed`) ve kazancı tek
ekranın çalışan bir sonuç vermesi. İki koşulla: ekseni **etiket** söyler, ve
gereklilik ekseninde girdiler kapalı gelmez — o ekranı açan kullanıcı kararı
zaten vermiştir. Bir paket kümesini düz metinle anlatmak (not, README) yerine
kategori yapmanın sebebi: paket adı sayan cümle sessizce eskir, üyeliği
listenin kendisi olan kategori eskiyemez.

**Kategoriye ait bir cümle `post_msg` değil `note` ile söylenir.**
`post_msg` başarılı kurulumdan **sonra** çıkar; yani yönlendirmeyi, doğru
girdiyi zaten bulmuş olana verir. Kurulumdan önce okunması gereken şey
(`[[category]]` altındaki `note`) hem menü satırında hem listenin başında
görünür. Bulunamayan bir şeyi anlatan metin her zaman ikincisidir.

**Ayar mı görev mi: `config.toml`'a yazılan şey ayardır.** Ayarlar menüsü
(dil, tema) yalnızca aracın kendi davranışını değiştirir ve
`~/.config/archsetup/config.toml`'da kalır; makineye dokunan her şey
görevdir ve kendi menüsünde durur. Bu çizgi olmadan Ayarlar zamanla
"sığmayan maddelerin" çöplüğüne döner. Ayrıca bir ayar sessizce etkisiz
kalmamalı: önkoşulu sağlanmıyorsa durup söylemeli, sessizce eski davranışa
dönmemeli — yoksa kullanıcı açık sandığı bir şeyle çalışır.

## Mimari

Görevler düz fonksiyondur, `int` döner (0 = başarı) ve `tasks.TASKS`
içinde bir `group` ile kaydedilir. Aynı fonksiyon hem TUI'den (terminal
askıya alınarak) hem `archsetup <id>` ile çalışır — bu yüzden `print` ve
`input` serbest, Textual'a bağımlılık yok.

Hazır yardımcılar; yenisini yazmadan önce bunlara bakın:

| İş | Kullanılacak |
|---|---|
| root dosyasına yazmak | `sysedit.sudo_write()` (kullanıcı olarak oku, sudo tee ile yaz) |
| yedekleyerek yazmak | `sysedit.write_with_backup()` → `(rc, changed)`; aynıysa dokunmaz, `changed` pahalı ardıl adımı (mkinitcpio -P, udevadm reload) koşullar |
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

**`authorized_keys` üretilmez — eklenebilir, yeniden yazılamaz.**
`config.local` yanlış üretilirse dışarı bağlanamazsınız ve makinenin
başındasınız; bozuk bir `from=` ise içeri bağlanmayı imkânsız kılar ve
fiziksel erişim gerektirir. İki dosyanın hata maliyeti eşit değil. Ekleme
bu riski taşımaz — en kötü ihtimalle fazladan bir satır kalır — o yüzden
`ssh-authorize` dosyanın sonuna yazar, önce yedekler ve mevcut satırlara
dokunmaz. Aynı ayrım `allowed_signers` için de geçerli (bkz. `core/gitid.py`):
o da bir güven listesi, yeniden üretmek başka makinelerin anahtarını düşürür.

**AUR yardımcısının kendisi de AUR'da.** `pacman.install()` bir AUR
paketi istendiğinde yardımcı yoksa artık `yay-bin`'i klonlayıp kurmayı
öneriyor; "önce Sistem Güncelleme'den kurun" demek sorunu bir adım öteye
taşıyordu ve 2. aşamanın başında çalıştırılan ilk görevi çuvallatıyordu.

**`curl | bash` yerine indir-sonra-çalıştır.** Boruya bağlanan kabuk
baytları geldikçe çalıştırır; yarıda kopan bir bağlantı yarım kurucuyu
çalıştırmış olur. Önce dosyaya yazmak bunu "indirme başarısız, hiçbir şey
çalışmadı" haline getirir — aynı kaynak, aynı betik. `curl -f` de her şeyi
yakalamaz: boş gövdeli bir 200 başarı sayılır, boyut ayrıca kontrol edilmeli.

**Kullanıcı ev dizinine kurulan araçlarda PATH sırası kontrol edilmeli.**
Aynı komutun `npm -g` ile gelmiş eski bir kopyası PATH'te önde duruyorsa
güncelleme başarılı görünür ama eski sürüm çalışmaya devam eder.

**drpars/Wallpaper deposu Resimler dizininin aynası, düz bir resim yığını
değil.** Kökünde klasörlerin kendisi duruyor (`Icons/`, `Wallpaper/`), bu
yüzden hedef `XDG_PICTURES_DIR`'ın kendisi olmalı; repo kökünü
`Pictures/Wallpaper`'a kopyalamak `Pictures/Wallpaper/Wallpaper` üretir.
`--delete` de repo kökünden değil, her üst klasör için ayrı ayrı
çalıştırılmalı — yoksa depoda bulunmayan yerel bir klasörü (`ScreenShot/`)
siler.
