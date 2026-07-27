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

**`authorized_keys` üretilmez, yalnızca denetlenir.** `config.local` yanlış
üretilirse dışarı bağlanamazsınız ve makinenin başındasınız; bozuk bir
`from=` ise içeri bağlanmayı imkânsız kılar ve fiziksel erişim gerektirir.
İki dosyanın hata maliyeti eşit değil.
