# Slack

Nagraj wiadomość audio w Slacku lub dołącz plik audio w rozmowie z botem.
LoudTalk przekaże treść wybranemu agentowi i odpowie odtwarzalnym plikiem MP3
w wątku tej wiadomości. W tym konektorze odpowiedź jest **załącznikiem audio**;
nie deklarujemy identycznego wyglądu jak natywny klip nagrywany przyciskiem Slacka.

## Podłączenie

1. Utwórz własną aplikację w [panelu Slack](https://api.slack.com/apps).
2. W **OAuth & Permissions** dodaj uprawnienia bota: `files:read`, `files:write` oraz
   historię typów rozmów, których używasz: `im:history` dla DM,
   `channels:history` dla kanałów publicznych, `groups:history` dla prywatnych,
   `mpim:history` dla grupowych DM. Zainstaluj aplikację w workspace.
3. W LoudTalk wybierz **Slack**, wskaż agenta i wklej **Bot User OAuth Token**
   oraz **Signing Secret** z sekcji Basic Information.
4. Udostępnij przez HTTPS tylko `/hooks/{channel_id}` z lokalnego serwera
   `127.0.0.1:8765`. Skopiuj ten adres do **Event Subscriptions → Request URL**.
   Slack sprawdzi podpisaną wiadomość `url_verification` automatycznie.
5. W **Subscribe to bot events** dodaj `message.im` oraz odpowiednio
   `message.channels`, `message.groups`, `message.mpim` dla wybranych rozmów.
   Jeśli zmieniłeś scopes, zainstaluj aplikację ponownie. Zaproś bota na kanały,
   na których ma odpowiadać. W App Home włącz kartę Messages, jeśli chcesz DM.
6. Sprawdź połączenie w LoudTalk, wyślij głosówkę i zatwierdź swoją rozmowę.

Do tego wariantu nie potrzebujesz app-level tokenu ani Socket Mode. Używa podpisanych
HTTP Events. Bot musi mieć dostęp do plików w rozmowie. Slack wymaga tokenu
`files:read` także przy pobieraniu prywatnego URL nagrania;
[zobacz dokumentację obiektu pliku](https://docs.slack.dev/reference/objects/file-object/).

Zaawansowane ustawienia `team_id` i `bot_user_id` pozwalają przypiąć workspace oraz
tożsamość bota. Test połączenia odczytuje je przez
[`auth.test`](https://docs.slack.dev/reference/methods/auth.test/) i nie wysyła wiadomości.

## Zachowanie

Głosówki i pliki audio do 25 MB są pobierane dopiero po zatwierdzeniu rozmowy/nadawcy.
Zdarzenia od botów, zmiany wiadomości, pliki zewnętrzne i inne typy załączników są
ignorowane. Odpowiedź jest MP3; krótka wersja tekstu odpowiedzi znajduje się przy pliku.
Wątek jest zachowany: nagranie w istniejącym wątku otrzymuje odpowiedź w tym wątku,
a nagranie w kanale rozpoczyna wątek odpowiedzi.

Odpowiedzi korzystają z aktualnego procesu
[`files.getUploadURLExternal`](https://docs.slack.dev/reference/methods/files.getUploadURLExternal/),
uploadu bajtów i
[`files.completeUploadExternal`](https://docs.slack.dev/reference/methods/files.completeUploadExternal/).
Nie wymagają wycofanego `files.upload`.

Każdy webhook ma weryfikowany HMAC oraz timestamp z tolerancją pięciu minut, zgodnie
z [instrukcją Slack](https://docs.slack.dev/authentication/verifying-requests-from-slack/).
Podpis dotyczy dokładnych bajtów body. Powtórki dostarczenia nie powinny ponownie
uruchamiać agenta; kolejka LoudTalk identyfikuje zdarzenia i pliki osobno.

## Gdy głosówka nie dochodzi

Jeśli test tokenu działa, sprawdź subskrypcję odpowiedniego `message.*`, widoczność
webhooka HTTPS, zegar komputera i zatwierdzenie rozmowy w LoudTalk. Przy problemie
z plikiem sprawdź `files:read`, `files:write` i obecność bota w rozmowie. Dla nowych
scopes konieczna jest ponowna instalacja aplikacji w workspace. Komputer z LoudTalk
musi być włączony. Socket Mode nie jest zaimplementowany w tym adapterze.

Testy HTTP w repozytorium weryfikują formaty wywołań i uwierzytelnianie przy pomocy
symulowanych odpowiedzi dostawcy. Pełne potwierdzenie dostarczenia wymaga własnego
workspace i rzeczywistej głosówki po podłączeniu.
