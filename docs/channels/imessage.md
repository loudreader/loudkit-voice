# iMessage przez BlueBubbles

Wyślij głosówkę w iMessage, a Twój agent odeśle nagranie do tej samej rozmowy.
LoudTalk odbiera głosówkę przez BlueBubbles, rozpoznaje ją lokalnie Parakeetem,
przekazuje tekst wybranemu agentowi i czyta jego odpowiedź głosem Loudkita.

## Czego potrzebujesz

- Działającego [BlueBubbles Server](https://bluebubbles.app/install/) na Macu,
  zalogowanego do konta iMessage przeznaczonego dla agenta.
- Adresu serwera i hasła API ustawionego w BlueBubbles.
- Komputera z LoudTalk, który może połączyć się z serwerem; BlueBubbles musi też
  móc wywołać adres webhooka LoudTalk.

Najprostszy wariant: BlueBubbles i LoudTalk działają na tym samym Macu, a Ty
piszesz z własnego konta iMessage do osobnego konta agenta. Wiadomości oznaczone
przez mostek jako wysłane przez jego własne konto (`isFromMe`) są pomijane, żeby
odpowiedzi nie uruchamiały kolejnych odpowiedzi. Wysyłanie głosówki z telefonu
zalogowanego do tego samego konta co mostek również zostanie pominięte.

LoudTalk korzysta z HTTP API BlueBubbles. Sam nie otwiera bazy Messages, nie
uruchamia AppleScript i nie zmienia ustawień macOS. BlueBubbles ma własne
wymagania instalacyjne i uprawnienia opisane w swoim instalatorze. Ten konektor
nie jest publicznym bot API Apple.

## Podłącz

1. Dodaj połączenie **iMessage** w LoudTalk i wybierz swojego agenta.
2. Wklej adres BlueBubbles, np. `http://127.0.0.1:1234`, i jego hasło API.
   Numer portu musi odpowiadać Twojemu serwerowi. Dla innego Maca w LAN podaj jego
   prywatny adres IP. Dla serwera dostępnego poza LAN wymagany jest adres HTTPS.
3. Sprawdź połączenie. Ten krok sprawdza API; nie oznacza jeszcze, że webhook
   dociera ani że odpowiedź została wysłana do iMessage.
4. Skopiuj adres webhooka udostępniony przez LoudTalk. Ma postać
   `http://127.0.0.1:PORT/hooks/ID_POŁĄCZENIA?token=LOSOWY_TOKEN`.
   Przy osobnych komputerach użyj adresu LoudTalk osiągalnego z Maca BlueBubbles;
   `localhost` zawsze oznacza komputer, na którym działa nadawca webhooka.
5. W BlueBubbles otwórz **API & Webhooks → Manage → Add Webhook**, wklej cały
   adres wraz z tokenem i wybierz zdarzenie **New Messages** (`new-message`).
6. Wyślij głosówkę ze swojego konta do konta mostka. Zatwierdź nadawcę i rozmowę
   w LoudTalk, po czym wyślij kolejną głosówkę. Nagrania z niezatwierdzonych
   rozmów nie są pobierane ani przekazywane agentowi.

BlueBubbles wysyła webhook jako zwykły POST pod podany adres. Nie dodaje
podpisu ani nagłówka uwierzytelniającego. Dlatego osobny, losowy token w adresie
webhooka jest wymagany; nie jest to hasło API BlueBubbles. Nie usuwaj parametru
`token`, nie udostępniaj tego adresu i używaj HTTPS, gdy webhook wychodzi poza
zaufaną sieć lokalną.

## Jak wyglądają odpowiedzi

Domyślnie agent wysyła plik M4A do odtworzenia w tej samej rozmowie iMessage.
To załącznik audio. Odbierane są zarówno natywne głosówki iMessage (w tym CAF),
jak i załączniki audio, do 25 MiB. SMS/MMS i obrazy są pomijane.

Opcja `settings.native_audio_message: true` włącza wysyłanie natywnej głosówki.
Wymaga ona już włączonego Private API BlueBubbles i połączonego helpera;
LoudTalk sprawdza te możliwości przed wysyłką. W tym trybie wysyła MP3 z
`isAudioMessage=true`, a BlueBubbles obsługuje konwersję do CAF.
Zwykły załącznik M4A jest ustawieniem domyślnym, ponieważ upstream zgłosił
[problem z nieodtwarzalnymi natywnymi głosówkami na części konfiguracji](https://github.com/BlueBubblesApp/bluebubbles-server/issues/773).
Samo przyjęcie żądania przez API nie dowodzi odtwarzalności na telefonie.
Nie włączaj Private API tylko dla podstawowych odpowiedzi audio.

Po błędzie wysyłki LoudTalk nie wysyła automatycznie drugiej kopii innym trybem:
pierwsze żądanie mogło już dotrzeć do rozmowy. Tekst odpowiedzi nie może zmienić
odbiorcy, adresu serwera ani sposobu uwierzytelnienia.

## Sprawdzenie na własnym koncie

1. Zatwierdź właściwy kontakt i rozmowę; wyślij krótkie nagranie z poleceniem.
2. Sprawdź transkrypcję oraz odpowiedź agenta w LoudTalk.
3. Otwórz odpowiedź audio w iMessage i odsłuchaj ją do końca.
4. Sprawdź, że własna odpowiedź mostka nie uruchomiła kolejnego polecenia.
5. Jeśli chcesz natywną głosówkę, sprawdź osobno jej odtwarzalność po włączeniu
   tej opcji. W razie problemu wróć do domyślnego załącznika M4A.

Testy automatyczne używają sztucznych, zgodnych z protokołem danych oraz
lokalnego transportu HTTP w pamięci. Sprawdzają token, filtrowanie wiadomości,
adresy pobierania, limit rozmiaru, brak przekierowań z hasłem, strukturę wysyłki,
oryginalną rozmowę docelową i obsługę błędów. Nie zastępują testu z Twoim
kontem BlueBubbles; podczas implementacji nie wysyłano wiadomości iMessage.

## Źródła protokołu

Zweryfikowano 22 września 2026:

- [Oficjalne REST API i webhooks BlueBubbles](https://docs.bluebubbles.app/server/developer-guides/rest-api-and-webhooks).
- [Oficjalna konfiguracja webhooka w UI BlueBubbles](https://docs.bluebubbles.app/server/developer-guides/simple-web-server-for-webhooks).
- [Wysyłka webhooka bez dodatkowych nagłówków autoryzacji](https://github.com/BlueBubblesApp/bluebubbles-server/blob/f2e2286241a7c3b6617a82b37d4afaab4df3a6b9/packages/server/src/server/services/webhookService/index.ts).
- [Pola formularza wysyłki załącznika](https://github.com/BlueBubblesApp/bluebubbles-server/blob/f2e2286241a7c3b6617a82b37d4afaab4df3a6b9/packages/server/src/server/api/http/api/v1/validators/messageValidator.ts).
- [Obsługa wysyłki MP3/CAF i Private API](https://github.com/BlueBubblesApp/bluebubbles-server/blob/f2e2286241a7c3b6617a82b37d4afaab4df3a6b9/packages/server/src/server/api/interfaces/messageInterface.ts).
- [Metadane wersji i możliwości serwera](https://github.com/BlueBubblesApp/bluebubbles-server/blob/f2e2286241a7c3b6617a82b37d4afaab4df3a6b9/packages/server/src/server/api/interfaces/generalInterface.ts).
- [Emisja nowych wiadomości do webhooków](https://github.com/BlueBubblesApp/bluebubbles-server/blob/f2e2286241a7c3b6617a82b37d4afaab4df3a6b9/packages/server/src/server/index.ts): webhook korzysta z uproszczonego serializatora powiadomień, więc rozpoznawanie audio nie wymaga obecności pola `isAudioMessage`.
