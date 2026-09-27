# Telegram

Głosówka do Twojego bota → Parakeet → Twój agent → Loudkit → odpowiedź głosowa
w tej samej rozmowie i temacie. Nie trzeba otwierać LoudTalk do każdej wiadomości.

1. Utwórz bota przez [BotFather](https://t.me/BotFather), jeśli nie masz jeszcze
   własnego bota dla tego połączenia.
2. W LoudTalk wybierz **Telegram**, wklej token i wybierz agenta.
3. Sprawdź połączenie i włącz kanał. Otwórz rozmowę z botem, naciśnij **Start**,
   nagraj krótką wiadomość, a następnie zatwierdź swój profil w LoudTalk.
4. Kolejne głosówki obsługuje już Twój agent. Dla innej osoby lub grupy zatwierdź
   osobne powiązanie w LoudTalk.

Masz już Telegrama w Hermesie, OpenClaw lub innym agencie? Skonfiguruj w nim
LoudTalk jako dostawcę STT/TTS. Token bota pozostaje wtedy w agencie. Ten adapter
służy do sytuacji, w której to LoudTalk odbiera wiadomości bota. Nie uruchamiaj
dwóch odbiorników z tym samym tokenem. Wykryty webhook zatrzymuje konfigurację;
LoudTalk nie usuwa go ani nie odłącza istniejącego agenta.

Adapter odbiera głosówki i załączniki audio, pomija wiadomości botów oraz edycje.
Odpowiedź wysyła przez `sendVoice` jako Ogg/Opus, z odniesieniem do oryginalnej
wiadomości i identyfikatorem tematu. Limit pobierania przez publiczne API Telegrama
wynosi 20 MB. [Dokumentacja Telegram Bot API](https://core.telegram.org/bots/api).

W grupach ustawienia prywatności bota wpływają na otrzymywane wiadomości.
Najłatwiej zacząć od prywatnej rozmowy; konfigurację grup opisuje
[oficjalne FAQ Telegrama](https://core.telegram.org/bots/faq#what-messages-will-my-bot-get).

Przed potwierdzeniem kolejki następny `getUpdates` nie przesuwa kursora. Po awarii
niepotwierdzone zdarzenia mogą wrócić; lokalna deduplikacja zapobiega ponownemu
uruchomieniu tego samego polecenia. Nie ma gwarancji odzyskania zdarzeń po długim
wyłączeniu: Telegram przechowuje oczekujące aktualizacje do 24 godzin.
[Opis odbierania aktualizacji](https://core.telegram.org/bots/api#getting-updates).

Parametry adaptera: `secrets.bot_token`; brak dodatkowych wymaganych `settings`.
Token jest używany wyłącznie pod `api.telegram.org`, a pobieranie nie podąża za
przekierowaniami. Testy używają atrap HTTP i sprawdzają protokół. Przed oznaczeniem
konta jako sprawdzonego end-to-end potrzebna jest rzeczywista głosówka użytkownika
i odsłuch odpowiedzi z jego botem.
