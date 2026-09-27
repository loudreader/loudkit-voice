# Weryfikacja — 2026-09-22

Ten raport rozdziela działanie silników i klientów agentów od dostarczenia do
rzeczywistych komunikatorów. Pełna macierz agentów: [zgodność](agent-compatibility.md).

Końcowy zestaw: **338 testów przeszło**, Ruff oraz składnia JavaScript i skryptu
startowego poprawne. Jedno ostrzeżenie deprecacji dotyczy Starlette/AnyIO.
Polecenie: `pytest -q tests integrations/grokbot/test_transport.py`.

## Rzeczywiste modele i kod dostawców

- Loudkit `loudreader/loudr-1-turbo` i Parakeet
  `mlx-community/parakeet-tdt-0.6b-v3` działają na tym Macu. Modele zajmują
  około 3 GB; produkcyjny serwer nie zawiera atrap syntezy ani rozpoznawania.
- **Hermes:** wykonano funkcje dostawców wyodrębnione z przypiętej rewizji
  upstream, z prawdziwym OpenAI SDK i lokalnym HTTP. Loudkit wygenerował
  OGG/Opus oraz MP3, a Parakeet oddał dokładnie
  „Jutro rano przypomnij mi o spotkaniu z zespołem.”
- **OpenClaw:** wykonano przypięte moduły dostawcy TTS i STT przeciwko temu
  samemu HTTP. Opus i MP3 przeszły pełne dekodowanie FFmpeg; rzeczywiste
  multipart STT zwróciło to samo poprawne polskie zdanie.
- Harness OpenClaw zastępuje zależności SDK, obsługę sekretów i pomocniczy
  transport testowymi adapterami. Harness Hermes izoluje funkcje dostawców
  od całej bramki. To rzeczywiste testy warstwy dostawcy, nie pełnych instalacji
  ani dostarczenia do czatu. Rewizje, hashe i zakres wyłączeń są w raportach.
- **Test negatywny:** krótkie „Dzień dobry.” zostało zapisane przez Parakeeta
  jako „Джейн Добри.”. Krótkie wypowiedzi mogą mieć błędnie rozpoznany język;
  pozytywny test dłuższego zdania nie usuwa tego ograniczenia.

Dowody: [zbiorcze](evidence/native-contracts.json),
[Hermes](evidence/hermes-native-provider.json),
[OpenClaw](evidence/openclaw-native-provider.json),
[nieudany krótki polski klip](evidence/hermes-short-polish-utterance.json).
Skrypty powtarzalne są w `scripts/verify_native_contracts.py` oraz
`scripts/verify_openclaw_voice.mjs`.

Wcześniejsze testy obejmują polski i angielski TTS → WebM/Opus → STT oraz
76,8 s nagrania: [speech.json](evidence/speech.json),
[long-speech.json](evidence/long-speech.json). Rzeczywiste testy kodeków obejmują
WAV, MP3, Opus/OGG, AAC, FLAC, PCM oraz zmianę tempa i sprzątanie eksportów.
FFmpeg odrzuca playlisty, wadliwe nagrania i audio przekraczające 180 s.

## Programy agentów

[Raport uruchomień](evidence/agent-commands.json) pochodzi z zainstalowanych
programów z normalnym dostępem do własnego stanu sesji:

- Codex odpowiedział przez rzeczywisty adapter LoudTalk.
- Claude Code wymaga logowania.
- Gemini CLI ma poprawne flagi, lecz dostawca odrzucił zainstalowanego klienta
  jako nieobsługiwanego.
- OpenCode ma konfigurację uwierzytelnienia, lecz zwrócił błąd serwera.
- Muse Code nie jest zainstalowany; potwierdzono dokumentację CLI/MCP.

Nie instalowano ani nie logowano użytkownika do nowych kont agentów. Zwykła
odpowiedź tekstowa programu nie potwierdza dostarczenia głosówki w komunikatorze.

## Mosty komunikatorów i istniejący agent

Testy Telegrama, Discorda, WhatsAppa, Slacka i BlueBubbles wykonują kod
produkcyjnych adapterów z kontrolowanymi odpowiedziami HTTP. Sprawdzają
podpisy, weryfikację webhooków, filtrowanie wiadomości, ograniczenia pobierania,
format wysyłki, tożsamość czatu/wątku i brak ujawniania tokenów. Nie używają
rzeczywistych kont ani nie wysyłają wiadomości do osób.

Testy kolejki obejmują trwałą deduplikację, brak pobierania przed zatwierdzeniem
rozmówcy, oddzielne konteksty, oczekiwanie na odpowiedź zewnętrznego agenta,
restart i niepowtarzanie operacji o nieznanym wyniku.

Pełny lokalny test z **rzeczywistym Codexem** także przeszedł: Loudkit wygenerował
wejściowe nagranie, Parakeet je przepisał, produkcyjna kolejka uruchomiła Codexa,
a Loudkit przeczytał jego odpowiedź. Produkcyjny adapter Telegrama przygotował
multipart `sendVoice` z OGG/Opus 48 kHz. Ponowna transkrypcja odpowiedzi była
identyczna z tekstem Codexa. Test potwierdził dokładny czat, wątek, wiadomość
wejściową i jedną wysyłkę mimo duplikatu zdarzenia. Przed sparowaniem nagranie
nie zostało pobrane. Całość trwała 19,54 s w jednej próbie, z ładowaniem modeli.

Transport Telegrama w tej próbie był jawną atrapą HTTP; żadne żądanie nie trafiło
na konto Telegrama. Raport: [voice-pipeline.json](evidence/voice-pipeline.json).
Powtórzenie wymaga działającego LoudTalk i zalogowanego Codexa:

```sh
.venv/bin/python scripts/verify_voice_pipeline.py --output /tmp/voice-pipeline.json
```

Skrypt zużywa jedną turę Codexa z jego zwykłymi uprawnieniami w tymczasowym repo.

Test `integrations/grokbot/test_transport.py` uruchamia prawdziwy lokalny serwer
HTTP i proces CLI, ze sztuczną próbką audio i kontrolowanym komunikatorem.
Sprawdza przejście przez skrzynkę istniejącego agenta, odpowiedź do oryginalnego
czatu i zgłoszenie niepotwierdzonej wysyłki. **Nie jest testem uruchomionego
Grok Bota**; jego skill wymaga aktywnego agenta i zatwierdzonego dostępu lokalnego.

## Panel i wcześniejszy plac zabaw

Panel konfiguracji sprawdzono w przeglądarce na desktopie i w układzie 390 px:
generowanie konfiguracji Hermes/OpenClaw, kopiowanie do schowka, pięć
komunikatorów, formularze Telegram/iMessage i odzyskanie instrukcji MCP.
Nie używano rzeczywistych tokenów botów. Plac zabaw zachowano pod
`/playground`. Wcześniejszy test Safari potwierdził wybór pliku WebM, rzeczywistą
transkrypcję, wysłanie do lokalnej skrzynki i odtwarzanie audio do końca.
Wcześniej sprawdzono także rzeczywistą odpowiedź Codexa i syntezę jej tekstu.
Nie nagrywano otoczenia użytkownika fizycznym mikrofonem.

## Pozostaje do sprawdzenia na kontach

- Instalacja konfiguracji w pełnych bramkach Hermes/OpenClaw i rzeczywiste
  dostarczenie audio w komunikatorach.
- Uruchomienie skilla we własnym Grok Bocie, jego dostęp lokalny i wybrany
  mechanizm budzenia. Samo MCP nie uruchamia zatrzymanego agenta.
- Logowanie Claude, zgodny klient/konto Gemini, odpowiedź OpenCode i instalacja Muse Code.
- Rzeczywiste webhooki Slack/WhatsApp, konto BlueBubbles oraz boty Telegram/Discord.

Nie ma podstaw, aby nazwać wszystkie produkty i konta w pełni przetestowanymi.
