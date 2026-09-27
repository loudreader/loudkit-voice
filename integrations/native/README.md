# Głos w Twoim istniejącym bocie

LoudTalk udostępnia Parakeet do rozpoznawania poleceń i Loudkit do odpowiedzi
głosowych. Wiadomości nadal odbiera Twój Hermes lub OpenClaw: zachowuje rozmowę,
pamięć, narzędzia, model i swoje zasady zatwierdzania działań.

**Nie wklejaj tokena już działającego bota do drugiego odbiornika Telegram.**
W tym trybie konfigurujesz tylko dostawcę mowy. Dotychczasowy agent nadal jako
jedyny obsługuje swoje połączenie z komunikatorem.

## Hermes

1. Uruchom LoudTalk i przygotuj modele. Hermes musi mieć dostęp do
   `http://127.0.0.1:8765/v1`.
2. Połącz [hermes.yaml](hermes.yaml) z konfiguracją aktywnego profilu
   (`~/.hermes/config.yaml` dla głównego profilu). Zmień pola wewnątrz istniejących
   sekcji `stt` i `tts`; nie zastępuj całego pliku ani pozostałych ustawień.
3. Uruchom ponownie istniejącą bramkę. W swoim czacie Telegram lub Discord wpisz
   `/voice on`, a następnie wyślij głosówkę. `/voice status` pokazuje tryb,
   `/voice off` wyłącza odpowiedzi głosowe.

Wybierz `voice: gosia` lub `voice: darkman`. `use_gateway: false` jest celowe:
starsze `use_gateway: true` ma w Hermes pierwszeństwo przed wyborem `openai`.
`api_key: loudtalk-local` to lokalna wartość wymagana przez klienta; nie jest
kluczem OpenAI ani konfiguracją modelu, który wykonuje Twoje polecenia.

## OpenClaw

1. Uruchom LoudTalk i przygotuj modele. Dla każdego agenta, który odbiera głosówki,
   dodaj osobny lokalny profil audio:

   ```sh
   openclaw models auth paste-api-key --agent NAZWA_AGENTA --provider openai --profile-id openai:loudtalk
   ```

   W pytaniu o klucz wpisz `loudtalk-local`. Jeśli OpenAI służy też do rozmów,
   zachowaj dotychczasowy profil jako pierwszy w kolejności uwierzytelniania.
   Pole `profile` w konfiguracji audio wybierze profil lokalny tylko dla audio.

2. Połącz [openclaw.json](openclaw.json) z `~/.openclaw/openclaw.json`.
   Zastąp stare wpisy audio w `tools.media.models`, zachowując wpisy obrazu i wideo.
   Usuń stare nadpisania `tools.media.audio.models` lub `preferredModel`, jeśli
   wskazują innego dostawcę. Zachowaj pozostałą konfigurację komunikatorów i modeli.
   **Nie ustawiaj `models.providers.openai.apiKey` na lokalną wartość:** jest to
   ustawienie wspólne z modelem rozmów.
3. Uruchom ponownie istniejącą bramkę. Wyślij głosówkę w swoim komunikatorze.
   `tts.auto: inbound` odpowiada głosem na głosówkę; zapisane preferencje czatu
   sprawdzisz przez `/tts status`. `/tts chat default` usuwa nadpisanie konkretnego
   czatu. Tryb `inbound` ustawiaj w konfiguracji; aktualna komenda `/tts on`
   włącza głos dla wszystkich wiadomości.

Aktualny format OpenClaw używa głównej sekcji `tts.providers.openai`, nie starej
`messages.tts`. Fragment używa `voice`, obsługiwanego pola dostawcy; dokumentacja
opisuje też jego nowszą nazwę `speakerVoice`. Jeżeli masz nadpisania TTS dla
konkretnego agenta, kanału lub konta, ustaw tam również lokalnego dostawcę.

## Co trafia do komunikatora

Poniższa tabela opisuje zachowanie deklarowane przez upstream, nie wynik testu
na Twoim koncie. Żadna wygenerowana konfiguracja nie oznacza „połączono”.

| Agent | Telegram | WhatsApp | Discord | Slack | iMessage |
|---|---|---|---|---|---|
| Hermes | głosówka ↔ głosówka, `/voice on` | odbiór głosu + TTS jako plik; auto-reply zależy od wersji | głosówka ↔ głosówka, `/voice on` | odbiór głosu + TTS jako plik; auto-reply zależy od wersji | brak zweryfikowanego przepisu |
| OpenClaw | głosówka ↔ głosówka | głosówka ↔ głosówka | odbiór głosu, odpowiedź jako plik audio | odbiór głosu, odpowiedź jako plik audio | przez istniejący BlueBubbles; odpowiedź jako plik audio |

BlueBubbles potrafi dodatkowo wysłać natywną notatkę iMessage przez narzędzie
`upload-file` z `asVoice: true` (MP3/CAF). Samo ogólne auto-TTS tego nie gwarantuje.

LoudTalk musi zwracać **rzeczywiste** Ogg/Opus dla `response_format: opus` i MP3
dla `response_format: mp3`. Sama zmiana rozszerzenia pliku WAV nie wystarcza.
Hermes dobiera Opus dla Telegram/Discord; OpenClaw dla Telegram/WhatsApp.
Pozostałe kanały zwykle wybierają MP3. Konwersja wymaga `ffmpeg` z `libopus`.
Wejście STT przyjmuje plik komunikatora w `/v1/audio/transcriptions` i zwraca
`{"text":"…"}`; format JSON jest używany z modelem `parakeet`.

Adres `127.0.0.1` oznacza komputer agenta. Agent uruchomiony na serwerze potrzebuje
tunelu do Maca z LoudTalk. Obecny generator celowo tworzy adres lokalny, także dla
lokalnego końca tunelu. Nie wystawiaj niechronionego portu aplikacji publicznie.

## Testy lokalne i źródła (2026-09-22)

Przeprowadzono też testy prawdziwego HTTP do uruchomionego LoudTalk z Loudkit i
Parakeet, bez kont komunikatorów:

- [Hermes: udany pełny przebieg dostawcy](../../docs/evidence/hermes-native-provider.json):
  oficjalne funkcje upstream + prawdziwy OpenAI SDK wygenerowały Opus i MP3;
  transkrypcja polecenia „Jutro rano przypomnij mi o spotkaniu z zespołem.” była
  identyczna z tekstem wejściowym.
- [OpenClaw: prawdziwe odpowiedzi audio](../../docs/evidence/openclaw-native-provider.json):
  oficjalne moduły TypeScript wygenerowały żądania i odebrały poprawne Opus oraz
  MP3; `ffprobe` potwierdził kodeki, a `ffmpeg` zdekodował oba pliki w całości.
  Oficjalna ścieżka transkrypcji wysłała też plik Ogg przez multipart HTTP i dostała
  identyczny polski tekst (470 ms na rozgrzanym modelu).
- [Wykryty błąd krótkiej polskiej wypowiedzi](../../docs/evidence/hermes-short-polish-utterance.json):
  Parakeet zapisał „Dzień dobry.” jako „Джейн Добри.”. Poprawne połączenie API nie
  gwarantuje poprawnego rozpoznania każdej wypowiedzi. Tego przypadku nie oznaczamy
  jako zaliczonego.

Powtórzenie testów przy działającym LoudTalk, gotowych modelach i `ffmpeg`:

```sh
uv run --with openai python scripts/verify_native_contracts.py --output-dir /tmp/hermes-audio --evidence /tmp/hermes-proof.json
node --experimental-vm-modules scripts/verify_openclaw_voice.mjs --base-url http://127.0.0.1:8765/v1 --source-dir /tmp/loudtalk-openclaw-source --output-dir /tmp/openclaw-proof --audio-file /tmp/hermes-audio/hermes-reply.ogg --expect-text "Jutro rano przypomnij mi o spotkaniu z zespołem."
```

Skrypty pobierają kod z przypiętych rewizji i sprawdzają jego SHA256. Hermes
używa wybranych funkcji odczytanych przez AST oraz prawdziwego klienta OpenAI.
OpenClaw wykonuje oryginalne moduły po usunięciu typów TypeScript. Zależności
konfiguracji, sekretów i ochrony sieciowej są odizolowane testowymi adapterami.
Testy potwierdzają integrację dostawcy mowy; nie uruchamiają całej bramki i nie
potwierdzają dostarczenia wiadomości na Telegram, WhatsApp, Discord, Slack ani
iMessage.

[Zbiorczy wynik](../../docs/evidence/native-contracts.json) zawiera także wykonane
porównanie transkrypcji obu agentów z oryginalnym tekstem syntezy. OpenClaw ma już
własną obsługę lokalnego `parakeet-mlx`; endpoint LoudTalk jest alternatywną,
stale uruchomioną usługą oraz dostawcą Loudkit TTS. Nie jest warunkiem samego
rozpoznawania głosówek przez OpenClaw.

Hermes: wykonano wyizolowane funkcje STT/TTS z oficjalnego kodu z atrapą klienta
SDK. Potwierdzono lokalny adres i klucz, `parakeet` + JSON, `loudkit` + wybrany głos,
Opus dla `.ogg`, MP3 dla `.mp3`, oraz zamykanie klientów. Nie był to test bramki
na koncie użytkownika. Źródła:

- [STT: credentials, model and request](https://github.com/NousResearch/hermes-agent/blob/main/tools/transcription_cloud.py)
- [TTS: credentials and request](https://github.com/NousResearch/hermes-agent/blob/main/tools/tts_tool_openai.py)
- [Voice mode and chat commands](https://hermes-agent.nousresearch.com/docs/user-guide/features/voice-mode)
- [Transcription and channel delivery](https://hermes-agent.nousresearch.com/docs/user-guide/features/tts)

OpenClaw: sprawdzono dokumentację i kod rewizji
`b068eab40767fc6796899cdc85d08102e8035b2b`. Profil audio, główna sekcja `tts`,
lokalny adres i wybór formatu pochodzą z tych źródeł:

- [Audio model and auth profile](https://docs.openclaw.ai/nodes/audio)
- [TTS configuration](https://docs.openclaw.ai/tools/tts/configuration)
- [Fields and local endpoint behavior](https://docs.openclaw.ai/tools/tts/field-reference)
- [Channel output formats](https://docs.openclaw.ai/tools/tts/output)
- [BlueBubbles and voice memos](https://docs.openclaw.ai/channels/bluebubbles)
- [OpenAI speech adapter at checked revision](https://github.com/openclaw/openclaw/blob/b068eab40767fc6796899cdc85d08102e8035b2b/extensions/openai/speech-provider.ts)
- [OpenAI transcription auth at checked revision](https://github.com/openclaw/openclaw/blob/b068eab40767fc6796899cdc85d08102e8035b2b/extensions/openai/audio-transcription.ts)

Generatory konfiguracji testują zakres zmian, brak dostępu do prywatnych plików,
poprawność adresu oraz uczciwe oznaczenie niezweryfikowanych połączeń. Pełny test
wymaga wysłania przez użytkownika głosówki do jego istniejącego bota i otrzymania
odpowiedzi w tym samym czacie.
