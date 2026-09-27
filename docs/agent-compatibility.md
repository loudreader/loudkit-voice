# Agenci: co rzeczywiście da się podłączyć

Stan sprawdzenia: **2026-09-22**. Obsługa MCP, uruchomienie CLI i własny komunikator
to trzy osobne możliwości. Zielony test jednej z nich nie potwierdza pozostałych.
LoudTalk może obsługiwać komunikatory i wywoływać tekstowy interfejs agenta;
istniejącego bota agenta można zachować tylko wtedy, gdy jego faktyczna konfiguracja
pozwala podmienić STT/TTS albo rozszerzyć obsługę audio.

## Udokumentowane interfejsy

`Brak potwierdzenia` oznacza brak takiej funkcji w sprawdzonych źródłach, a nie dowód,
że żadne rozszerzenie społecznościowe jej nie zapewnia.

| Produkt | Interfejs dostępny dla LoudTalk | Komunikatory opisane przez producenta | Wniosek dla głosówek |
| --- | --- | --- | --- |
| **Hermes Agent — Nous Research** | Konfigurowalny adres OpenAI-compatible dla STT i TTS; istniejący agent pozostaje właścicielem rozmowy. | Telegram i Discord: natywne głosówki oraz `/voice on`. WhatsApp i Slack: audio, ale automatyczna odpowiedź wymaga sprawdzenia wersji. iMessage niepotwierdzony w tym audycie. | **Kontrakt dostawcy przetestowany z prawdziwymi modelami**; pełny proces agenta i konta komunikatorów nieprzetestowane. [STT — kod](https://github.com/NousResearch/hermes-agent/blob/main/tools/transcription_cloud.py), [TTS — kod](https://github.com/NousResearch/hermes-agent/blob/main/tools/tts_tool_openai.py), [głos](https://hermes-agent.nousresearch.com/docs/user-guide/features/voice-mode). |
| **OpenClaw** | Konfigurowalny dostawca STT/TTS OpenAI-compatible; lokalny profil uwierzytelniania dotyczy wyłącznie audio. | Telegram i WhatsApp: głosówki. Discord, Slack i standardowe auto-TTS iMessage przez BlueBubbles: pliki audio. Natywna głosówka BlueBubbles wymaga `asVoice`. | **Kontrakt dostawcy przetestowany z prawdziwymi modelami**; nie jest to test całego agenta ani dostarczenia wiadomości. [STT](https://docs.openclaw.ai/nodes/audio), [TTS](https://docs.openclaw.ai/tools/tts/configuration), [formaty](https://docs.openclaw.ai/tools/tts/output), [iMessage](https://docs.openclaw.ai/channels/bluebubbles). |
| **Grok Bot — xAI, istniejący Bot** | Udokumentowane umiejętności i autoryzowane polecenia na lokalnym komputerze mogą odczytywać kolejkę LoudTalk. Nie potwierdzono publicznego API uruchamiającego turę istniejącego Bota ani ustawienia własnego dostawcy STT/TTS. | Własny głos w aplikacji nie potwierdza głosu w pięciu komunikatorach. Dostępne konektory/rutyny konta wymagają osobnej weryfikacji. | Gotowy most kolejki i umiejętność; **wykonanie przez rzeczywistego Grok Bota nieprzetestowane**. Sześć testów sprawdza lokalny HTTP/CLI/MCP z jawnymi atrapami komunikatora i mowy. [Dokładna konfiguracja i granice](grokbot.md), [komputer Bota](https://docs.x.ai/grok-bot/computer-and-apps), [rutyny](https://docs.x.ai/grok-bot/skills-routines-and-automations). |
| **Muse — osobisty agent Meta** | Producent dokumentuje tworzenie Custom Connectors do zewnętrznych API. W sprawdzonych źródłach nie potwierdzono własnego URL STT/TTS, CLI/headless API do istniejącego Muse ani protokołu klienta MCP. | Oficjalnie rozmowa w aplikacji Muse i WhatsApp; pozostałych czterech kanałów nie potwierdzono jako wejścia do osobistego agenta. | Możliwy kierunek: własny konektor do dostępnego sieciowo API LoudTalk. **Integracja głosu i konto Muse nieprzetestowane.** [Produkt](https://about.fb.com/news/2026/09/introducing-muse-personal-ai-agent/), [Custom Connectors](https://www.meta.com/help/artificial-intelligence/1687253048996149/). |
| **Codex CLI** | `codex exec PROMPT`, końcowa odpowiedź na stdout; klient MCP. | Oficjalna integracja Slack uruchamia pracę w chmurze. Pozostałych czterech komunikatorów nie potwierdzono jako natywnych odbiorników CLI. | Adapter CLI jest sprawdzony rzeczywistą odpowiedzią. Konfiguracja MCP nie włącza samodzielnie odbierania głosówek. [CLI](https://learn.chatgpt.com/docs/non-interactive-mode), [MCP](https://learn.chatgpt.com/docs/extend/mcp?surface=cli), [Slack](https://learn.chatgpt.com/docs/third-party/slack). |
| **Claude Code** | `claude -p --output-format json PROMPT`, odpowiedź w `result`; klient MCP. | Oficjalne Channels: Telegram, Discord, iMessage, obecnie research preview. Osobno oficjalny Slack. WhatsApp niepotwierdzony w tych źródłach. | Istnieją prawdziwe kanały do działającej sesji, ale sam ich opis nie potwierdza podmiany STT/TTS na LoudTalk. Lokalny test odpowiedzi blokuje logowanie. [CLI](https://code.claude.com/docs/en/headless), [Channels](https://code.claude.com/docs/en/channels), [Slack](https://code.claude.com/docs/en/slack). |
| **Gemini CLI — Google** | `gemini -p PROMPT --output-format json`, odpowiedź w `response`; MCP stdio i zdalny. | W sprawdzonych dokumentach nie potwierdzono natywnego odbiornika pięciu komunikatorów. Poradnik opisuje zewnętrzne narzędzia Slack przez MCP. | Dostępny interfejs tekstowy do mostka LoudTalk. Lokalny test odpowiedzi nie przeszedł; szczegóły poniżej. [Headless](https://geminicli.com/docs/cli/headless/), [MCP](https://geminicli.com/docs/tools/mcp-server/), [poradnik MCP](https://geminicli.com/docs/cli/tutorials/mcp-setup/). |
| **OpenCode — Anomaly, V1** | `opencode run --format json -- PROMPT`, JSONL `type=text` / `part.text`; MCP, serwer HTTP. | Oficjalne repo ma pakiet Slack z Socket Mode. Kod pakietu opisuje tekst; nie stanowi dowodu STT/TTS. Discord Kimaki jest wymieniony jako projekt społecznościowy. Telegram/iMessage/WhatsApp niepotwierdzone jako natywne funkcje CLI. | Lokalny interfejs istnieje, ale próba odpowiedzi nie przeszła. [CLI](https://opencode.ai/docs/cli/), [MCP](https://opencode.ai/docs/mcp-servers/), [Slack](https://github.com/anomalyco/opencode/blob/dev/packages/slack/README.md), [ekosystem](https://opencode.ai/docs/ecosystem/). |
| **Muse Code — Meta** | `muse exec PROMPT`; również `--prompt-file`, JSONL przez `--json`, klient MCP. | W sprawdzonych dokumentach brak natywnego odbiornika tych komunikatorów. „Session messaging” dotyczy lokalnych sesji tego samego użytkownika. | Udokumentowana ścieżka CLI/MCP; brak lokalnej binarki, więc bez testu konta. To konkretny produkt Meta, odrębny od osobistego agenta Muse. [Produkt](https://dev.meta.ai/docs/muse-code), [CLI/MCP](https://dev.meta.ai/docs/muse-code/extending), [session messaging](https://dev.meta.ai/docs/muse-code/session-messaging#know-the-current-scope). |

OpenCode V2 ma osobną dokumentację i inny schemat MCP (`mcp.servers`). Ten audyt
dotyczy zainstalowanej wersji **1.18.30**; nie należy stosować konfiguracji V2 do V1.
[V2 CLI](https://opencode.ai/v2/docs/cli/commands/),
[V2 MCP](https://opencode.ai/v2/docs/mcp-servers).

## Rzeczywiste testy na tym Macu

Raport [agent-commands.json](evidence/agent-commands.json) pochodzi z uruchomień
zainstalowanych programów, nie z atrap. Sprawdzono wersję i właściwe `--help`.
Tam, gdzie było to możliwe, użyto bieżącego logowania, pustego tymczasowego repo
i rzeczywistego adaptera `loudtalk.dispatch` z poleceniem odpowiedzi
`LOUDTALK_AGENT_OK`, bez narzędzi. Nie zmieniano polityki uprawnień agentów.

| Agent | Zainstalowana wersja | Wynik |
| --- | --- | --- |
| Codex | 0.153.0 | **Przeszedł:** rzeczywista odpowiedź zgodna z oczekiwaną, przez adapter LoudTalk. |
| Claude Code | 2.1.268 | Interfejs poprawny; CLI zgłasza brak aktywnego logowania. Nie wykonano modelowej odpowiedzi. |
| Gemini CLI | 0.31.0 | Interfejs poprawny; serwer odrzucił istniejące logowanie błędem `IneligibleTierError`, `UNSUPPORTED_CLIENT`: klient nieobsługiwany dla tego konta Gemini Code Assist. Nie uzyskano odpowiedzi modelu. |
| OpenCode | 1.18.30 | Interfejs poprawny, poświadczenia skonfigurowane; rzeczywisty proces zwrócił zdarzenie `error` / `UnknownError` z ogólnym błędem serwera. Nie uzyskano odpowiedzi modelu. |
| Muse Code | — | Nie ma `muse` w PATH. Bez instalowania i logowania nie można wykonać testu konta. |

Pierwsze kontrole w ograniczonym sandboxie nie dawały wiarygodnego obrazu dostępu
do stanu CLI. Powyższy zapis pochodzi z ponownego sprawdzenia z normalnym dostępem
programów do ich własnych plików sesji. Raport nie zawiera tokenów, adresów kont,
pełnych logów ani prywatnych ścieżek.

Powtórzenie (bez `--smoke` skrypt tylko sprawdza interfejsy i stan logowania):

```sh
uv run python tools/verify_agent_commands.py --smoke --output docs/evidence/agent-commands.json
```

`--smoke` używa istniejącego konta agenta i jego limitów. Nie instaluje agentów,
nie loguje użytkownika i nie konfiguruje komunikatorów. Wynik tekstowy nie jest
testem Telegrama, iMessage, WhatsAppa, Discorda ani Slacka; pełny test wymaga
rzeczywistego wejściowego nagrania, odpowiedzi agenta i odsłuchu w danym kanale.

## Testy dostawców głosu i istniejącego Bota

[native-contracts.json](evidence/native-contracts.json) zbiera rzeczywiste wywołania
funkcji dostawców mowy z przypiętych wersji źródeł Hermes i OpenClaw, skierowane
przez HTTP do LoudTalk z prawdziwym Loudkit i Parakeetem. Obie implementacje
poprawnie przepisały zdanie „Jutro rano przypomnij mi o spotkaniu z zespołem.”
Nie uruchamiano całych agentów ani ich kont komunikatorów. Test ujawnił również
błąd jakości: krótkie „Dzień dobry.” rozpoznano jako „Джейн Добри.” — ten przypadek
pozostaje niezaliczony. Dokładne wyniki:
[Hermes](evidence/hermes-native-provider.json),
[OpenClaw](evidence/openclaw-native-provider.json),
[krótka polska wypowiedź](evidence/hermes-short-polish-utterance.json).

Grok Bot ma inny rodzaj dowodu: sześć testów
[transportu kolejki](../integrations/grokbot/test_transport.py) uruchamia prawdziwe
HTTP, CLI i MCP, ale zastępuje mowę i komunikator kontrolowanymi atrapami.
Nie dowodzi to, że istniejący Bot potrafi już uruchomić umiejętność na koncie
użytkownika. [Procedura testu z Botem](grokbot.md#zakres-weryfikacji).

## Dlaczego natywny kanał Claude nie wystarcza

Audyt oficjalnych pluginów Claude przypięto do commita
`c447c3207a425bc4e2a0d068435f64b0477ae981`:

- Telegram przyjmuje załączniki voice/audio i pozwala pobrać plik, ale odpowiedź
  niebędąca obrazem używa `sendDocument`, bez `sendVoice`.
  [Kod Telegrama](https://github.com/anthropics/claude-plugins-official/blob/c447c3207a425bc4e2a0d068435f64b0477ae981/external_plugins/telegram/server.ts#L578).
- Discord udostępnia załączniki, a odpowiedź wysyła jako zwykły plik, bez
  `IS_VOICE_MESSAGE`, czasu i waveform.
  [Kod Discorda](https://github.com/anthropics/claude-plugins-official/blob/c447c3207a425bc4e2a0d068435f64b0477ae981/external_plugins/discord/server.ts#L630).
- iMessage przekazuje ścieżkę pierwszego obrazu; rozpoznane załączniki inne niż
  obrazy pomija, więc samo dodanie MCP do transkrypcji nie zapewnia wejścia audio.
  [Kod iMessage](https://github.com/anthropics/claude-plugins-official/blob/c447c3207a425bc4e2a0d068435f64b0477ae981/external_plugins/imessage/server.ts#L846).

W tych implementacjach nie znaleziono konfiguracji własnego URL STT/TTS.
Telegram i Discord można rozszerzyć narzędziami głosu, ale pełny automatyczny
obieg natywnych głosówek wymaga dodatkowej implementacji albo mostu LoudTalk.

**Nazwy produktów mają znaczenie:** osobisty Muse Meta nie jest Muse Code,
a Grok Bot nie jest Grok API. Udokumentowany CLI Muse Code nie dowodzi dostępu
do istniejącego osobistego Muse. Produkt Muse jest potwierdzony, ale jego
połączenie z głosem LoudTalk pozostaje niesprawdzone.
Zestaw dodatkowych klientów MCP jest w
[integrations/README.md](../integrations/README.md); wpis oznacza udokumentowany
interfejs, a nie zakończony test głosówek na koncie użytkownika.

## Osobisty Muse: potwierdzony produkt, osobna integracja

Meta ogłosiła osobistego Muse 8 września 2026 r.; działa na dedykowanej maszynie
Muse Secure VM i można z nim rozmawiać w WhatsApp.
[Ogłoszenie producenta](https://about.fb.com/news/2026/09/introducing-muse-personal-ai-agent/).
Pomoc Meta opisuje **Custom Connectors**: użytkownik może poprosić Muse o zbudowanie
połączenia z usługą, której nie ma na liście, i podać informacje o jej API.
To potwierdza możliwość rozszerzenia, lecz nie potwierdza schematu MCP ani podmiany
wewnętrznych silników głosu. [Pomoc Meta](https://www.meta.com/help/artificial-intelligence/1687253048996149/).

Dla LoudTalk jest to możliwa ścieżka przez zewnętrzny konektor HTTP. To wniosek
architektoniczny, jeszcze bez testu konta: potrzebne są osiągalny adres,
uwierzytelnienie, odczyt kolejki i potwierdzone dostarczenie odpowiedzi. Lokalny
`127.0.0.1` Maca nie wskazuje na niego z maszyny Muse Secure VM.
Nie publikowano lokalnego API ani nie konfigurowano konta podczas audytu.

Meta Model API udostępnia modele i Muse Code, ale jego endpoint inferencji nie
jest udokumentowanym interfejsem do pamięci i rozmowy istniejącego osobistego Muse.
[Zakres Meta Model API](https://dev.meta.ai/docs/overview).
