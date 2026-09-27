# API i narzędzia

Aplikacja: `http://127.0.0.1:8765`. Schemat OpenAPI: `/openapi.json`.
Serwer ufa procesom tego samego użytkownika. Nie wystawiaj portu UI publicznie.

## API audio

`POST /v1/audio/speech`, JSON:

```json
{"model":"loudkit","input":"Cześć!","voice":"gosia","response_format":"wav"}
```

Przykład zwraca `audio/wav`. Domyślny format to MP3. Dostępne są `mp3`,
`opus` (kontener OGG), `wav`, `aac`, `flac`, `pcm` (24 kHz, mono, signed16le).
Opcjonalne `speed` od 0.5 do 2.0 zmienia tempo rzeczywistego audio.

`POST /v1/audio/transcriptions`, multipart: `file`, `model=parakeet`.
Formaty: `response_format=json`, `text`, `verbose_json` (tekst i długość).
Nie deklaruje obsługi wszystkich opcji, strumieniowania ani znaczników słów.

## Skrzynka

- `GET /api/bootstrap`: agenci, rozmowy, głosy i stan silników.
- `POST /api/conversations`: `{"agent_id":"hermes"}` → rozmowa.
- `POST /api/transcribe`: multipart `file` → `{text,audio_id,duration}`.
- `POST /api/conversations/{id}/messages`: `{text,audio_id?}` → wiadomość.
- `GET /api/agents/{id}/inbox?after_id=0`: do 100 wiadomości użytkownika,
  rosnąco według ID. Następny kursor: najwyższe otrzymane ID.
- `POST /api/agent-messages`: `{agent_id,text,conversation_id?,reply_to_message_id?}`
  → głosowa odpowiedź. Dla rozmowy z komunikatora podaj oba identyfikatory
  otrzymane ze skrzynki: `conversation_id` oraz `id` jako `reply_to_message_id`.
  Odpowiedź zawiera `delivery.status`: `sent`, `uncertain` lub błąd. Samo
  `status: ready` oznacza przygotowanie audio, nie potwierdzenie dostarczenia.
  Ponowne żądanie dla tego samego wejściowego ID zwraca wcześniejszy wynik,
  bez nowej syntezy i wysyłki. Nie zmieniaj ID przy ponawianiu.
  Bez ID rozmowy tworzy nową rozmowę z wiadomością od agenta.
- `GET /api/conversations/{id}/messages`: historia ze stanem generowania.
- `POST /api/messages/{id}/retry`: ponawia błąd. Jeśli tekst już istnieje,
  ponawia tylko syntezę bez ponownego wywołania agenta.

ID agenta służy routingowi, nie autoryzacji pomiędzy niezaufanymi użytkownikami.
To osobista aplikacja lokalna, nie publiczna usługa wielodostępna.

## Komunikatory

- `GET /api/channels/bootstrap`: katalog, bezpieczne ustawienia połączeń,
  prośby o sparowanie, zdarzenia i stan silników.
- `POST /api/channels`, `PATCH /api/channels/{id}`: konfiguracja. Zmiana wymaga
  wyłączenia odbiornika; pominięte lub puste sekrety zachowują istniejące wartości.
- `POST /api/channels/{id}/check`, `/enable`, `/disable`: sprawdzenie konta
  bez wysyłania wiadomości oraz włączenie lub wyłączenie odbioru.
- `POST /api/channels/{id}/pairings/{pairing_id}/approve`: zatwierdza konkretną
  parę nadawca/czat. Dopiero nowa głosówka może zostać przetworzona.
- `POST /api/channels/{id}/webhook-info`: generuje URL z `public_base_url`.
  Adres BlueBubbles zawiera sekret; nie publikuj go.
- `GET/POST /hooks/{id}`: wejście odpowiedniego webhooka. Każdy adapter
  weryfikuje uwierzytelnienie przed odczytaniem zdarzeń.
- `POST /api/native-agents/{id}/setup`: generuje konfigurację głosu dla
  `hermes` lub `openclaw`; nie modyfikuje osobistych ustawień.

Webhooki przyjmują do 1 MB metadanych. Publiczne proxy powinno wystawiać tylko
`/hooks/` i ustawiać upstream Host na `127.0.0.1:8765`. Kanały domyślnie nie
wykonują poleceń niezatwierdzonych rozmówców. Kolejka zachowuje deduplikację po
restarcie; nie ponawia działań o nieznanym wyniku.

## Webhook

POST zawiera `agent_id`, `conversation_id`, `text` (ostatnia wiadomość) i
`messages` (historia `{role,content}`). Opcjonalny klucz idzie jako Bearer token.
Zwróć `{"text":"odpowiedź"}`. LoudTalk wygeneruje głos. Przekierowania
i automatyczne ponowienia są wyłączone. Limit odpowiedzi HTTP: 120 sekund.

## Zdalny MCP

Po uruchomieniu aplikacji, w drugim terminalu:

```sh
export LOUDTALK_MCP_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
.venv/bin/loudtalk mcp-http --port 8766
```

Streamable HTTP: `http://127.0.0.1:8766/mcp`. Wymaga `Authorization: Bearer <sekret>`
przy każdym żądaniu. Klient musi znać token; powyższe polecenie generuje nowy za
każdym razem. Nie wklejaj tokenu do repozytorium ani parametru URL.

Klient chmurowy wymaga osiągalnego HTTPS. Skonfiguruj własne zaufane reverse proxy:
zachowaj Authorization, ustaw upstream Host na `127.0.0.1:8766`, wyłącz buforowanie
strumienia. Nie wystawiaj portu UI. Aplikacja nie tworzy tunelu ani nie publikuje
nagrań automatycznie.

Adresy audio odnoszą się do lokalnego API. Zdalny klient może odbierać transkrypcje
i odsyłać głosówki do połączonego komunikatora; do pobrania plików poza Maciem potrzebuje
osobnego, uwierzytelnionego dostępu do audio. Połączenie chmurowe wymaga testu
na konkretnym koncie. Dostępność HTTP nie jest dowodem integracji z produktem.
