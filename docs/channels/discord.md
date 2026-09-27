# Discord

Wyślij botowi głosówkę na Discordzie. LoudTalk przepisze ją przez Parakeet,
przekaże Twojemu agentowi i odeśle jego odpowiedź jako głosówkę Loudkit w tej samej
rozmowie lub wątku. Nie trzeba dołączać do kanału głosowego.

## Podłączenie

1. W [Discord Developer Portal](https://discord.com/developers/applications) utwórz
   aplikację i bota. Skopiuj **Bot Token**.
2. W LoudTalk dodaj Discord, wklej token i wybierz swojego agenta. Sprawdzenie
   połączenia tylko odczytuje konto bota; nie wysyła testowej wiadomości.
3. Zaproś bota na swój serwer przez stronę instalacji aplikacji. Dla kanałów
   serwera potrzebuje View Channel, Send Messages, Attach Files i Send Voice
   Messages; dla wątków także Send Messages in Threads.
4. Włącz odbieranie wiadomości w LoudTalk i wyślij botowi prywatną głosówkę.
   Zatwierdź swoją rozmowę w LoudTalk. Wiadomości niezatwierdzonych osób nie są
   pobierane ani przekazywane agentowi.

Najprostszy start to prywatna rozmowa z botem. Domyślnie konektor odbiera DMy.
Jeśli instalacja nie ma jeszcze pakietu Discord, uruchom `uv sync --extra discord`.
Do konwersji odpowiedzi na Ogg/Opus wymagany jest FFmpeg.

## Kanały serwera i wątki

Opcja `settings.guild_messages: true` włącza odbiór wiadomości z serwerów oraz
Message Content intent. Włącz też **Message Content Intent** w Developer Portal
→ Bot → Privileged Gateway Intents. Discord może wymagać zatwierdzenia tej
intencji dla zweryfikowanych aplikacji. Bez niej bot nie może zakładać dostępu do
załączników zwykłych wiadomości na serwerze. Wiadomości prywatne mają wyjątek od
ograniczeń tej intencji.

Identyfikator wątku Discord jest identyfikatorem kanału. Odpowiedź wraca do
konkretnego wątku, z którego przyszło nagranie. Pozwolenia dostępu należy ustawić
dla tej rozmowy. Bot ignoruje wiadomości botów i webhooków, żeby nie uruchamiać
pętli odpowiedzi.

## Co jest obsługiwane

- Przychodzące natywne głosówki i pliki audio. Limit pobierania LoudTalk wynosi 25 MiB.
- Natywne wychodzące głosówki Discord: plik Ogg/Opus, flaga `IS_VOICE_MESSAGE`,
  rzeczywisty czas nagrania i wykres amplitudy policzony z wygenerowanego audio.
- Rozmowy prywatne, dopuszczone kanały serwera i ich wątki.
- Ciągłe połączenie Gateway z ponownym połączeniem po przejściowej utracie sieci.

Discord nie pozwala dołączać tekstu do natywnej głosówki. Treść odpowiedzi jest
więc dostępna w LoudTalk, a na Discordzie przychodzi nagranie. To nie jest tryb
Discord `tts=true`, który odczytuje tekst głosem klienta.

Do połączenia używaj tokenu bota. Token osobistego konta nie jest obsługiwany.
Jeżeli Twój agent już sam odbiera Discorda, najpierw podłącz LoudTalk jako jego
dostawcę STT/TTS lub użyj osobnego bota dla tego konektora. Dwa niezależne odbiorniki
tego samego bota mogłyby odpowiadać na te same wiadomości.

## Sprawdzenie i ograniczenia

Testy protokołu sprawdzają autoryzowany odczyt konta, normalizację wiadomości,
obliczanie przebiegu audio, przesyłanie multipart z właściwymi metadanymi głosówki,
odbiór Gateway i zamknięcie przy błędzie kolejki. Pobieranie sprawdza dokładne hosty
CDN Discord, rozmiar pliku i nie przekazuje tokenu bota ani nie podąża za
przekierowaniami. Nie wymaga to konta Discord i nie wysyła prawdziwych wiadomości.

Test z Twoim kontem wymaga tokenu i rzeczywistej głosówki. Bez niego nie oznaczamy
połączenia jako sprawdzonego na żywo. Gateway nie odtwarza całej historii po nowym
uruchomieniu: wysyłaj polecenia, gdy LoudTalk działa. Próba wysłania używa stałego
nonce dla danego zdarzenia; Discord ogranicza jego deduplikację do ostatnich kilku
minut, więc nie gwarantujemy dokładnie jednej odpowiedzi po niejednoznacznym błędzie
sieci i późniejszym ręcznym ponowieniu.

Dokumentacja źródłowa, sprawdzona 2026-09-22:
[wiadomości i głosówki](https://docs.discord.com/developers/resources/message),
[Gateway i intencje](https://docs.discord.com/developers/events/gateway),
[uprawnienia](https://docs.discord.com/developers/topics/permissions),
[discord.py](https://discordpy.readthedocs.io/en/stable/api.html).
