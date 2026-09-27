# Grok Bot: własny Bot z głosem w komunikatorze

Dotyczy **Grok Bot od xAI**, czyli istniejącego Bota z jego pamięcią,
narzędziami i komputerem. Integracja z modelem przez `api.x.ai` tworzy osobną
rozmowę API i nie daje dostępu do tej pamięci. W LoudTalk te połączenia należy
traktować oddzielnie.

Sprawdzono dokumentację producenta 22 września 2026 r. Integracja na koncie
Grok Bot wymaga jeszcze próby na zalogowanym koncie użytkownika.

## Co Grok Bot już potrafi

W swojej aplikacji ma dyktowanie do pola wiadomości, głosowe odpowiedzi Bota
i rozmowę głosową na desktopie. Dyktowanie zamienia nagranie na tekst, który
można poprawić przed wysłaniem. Producent nie dokumentuje ustawienia własnego
dostawcy STT/TTS dla tych funkcji. Nie ma więc potwierdzonego przełącznika
„używaj Loudkit zamiast wbudowanego głosu”.
[Dokumentacja wiadomości i głosu](https://docs.x.ai/grok-bot/chat-and-collaboration).

Bot pracuje na komputerze w chmurze. Może też uruchamiać polecenia na lokalnym
komputerze, jeśli ta funkcja jest dostępna i dopuszczona przez użytkownika
oraz jego organizację. Dzięki temu istniejący Bot może czytać lokalną kolejkę
LoudTalk bez wystawiania serwera w Internecie.
[Komputer Bota](https://docs.x.ai/grok-bot/computer-and-apps),
[wykonywanie poleceń lokalnych](https://docs.x.ai/grok-bot/approvals-security-and-privacy).

## Połączenie przez kolejkę głosową

```text
Telegram / iMessage / WhatsApp / Discord / Slack
    → most LoudTalk → Parakeet → kolejka istniejącego Bota
    → Grok Bot: własna pamięć, umiejętności i narzędzia
    → tekst odpowiedzi → Loudkit → ten sam czat i wątek
```

1. Uruchom LoudTalk na Macu z przygotowanymi silnikami głosu.
2. Dodaj agenta typu **MCP / skrzynka**, nazwij go np. „Mój Grok Bot” i zapisz
   jego `agent_id`. To lokalny identyfikator routingu, a nie identyfikator xAI.
3. Przypisz go do wybranego mostu komunikatora, sprawdź połączenie i włącz most.
   Wyślij pierwszą głosówkę z własnego czatu, zatwierdź widoczne powiązanie
   nadawcy i czatu, potem wyślij nową głosówkę. Dopiero wiadomości od
   zatwierdzonego nadawcy w zatwierdzonym czacie są transkrybowane.
4. W swoim **istniejącym** Grok Bocie zapisz dołączoną
   [umiejętność LoudTalk](../integrations/grokbot/loudtalk-messenger-voice/SKILL.md).
   Podaj ścieżkę do lokalnego programu `loudtalk`, jego `agent_id` i nazwę Maca.
   Poproś Bota o odczyt kolejki na tym Macu, a nie na jego komputerze w chmurze.
5. Wykonaj jedną próbę na żądanie. Sprawdź transkrypcję i potwierdzenie dostarczenia
   odpowiedzi w tym samym komunikatorze. Samo wygenerowanie pliku WAV nie
   potwierdza wysłania wiadomości.

Pierwsze polecenie tylko odczytuje lokalną kolejkę:

```sh
"/pełna/ścieżka/do/loudtalk/.venv/bin/loudtalk" conversations
"/pełna/ścieżka/do/loudtalk/.venv/bin/loudtalk" inbox AGENT_ID --after 0
```

Bot odpowiada przez `send` z dokładnym `conversation_id` i `id` odebranej
wiadomości. Dla rozmowy połączonej z mostem parametr `--reply-to` jest wymagany:
wskazuje, na którą głosówkę odpowiada Bot, i chroni przed podwójnym wysłaniem
przy ponowieniu żądania. LoudTalk kieruje odpowiedź do właściwego komunikatora.
Nie przekazuje Botowi tokenu Telegrama, Slacka ani innego kanału.

```sh
"/pełna/ścieżka/do/loudtalk/.venv/bin/loudtalk" send AGENT_ID \
  "Treść odpowiedzi do odczytania." --conversation CONVERSATION_ID --reply-to MESSAGE_ID
```

Pełna ścieżka jest potrzebna, gdy lokalne narzędzie Bota nie dziedziczy tego
samego `PATH` co terminal użytkownika. Dostępne lokalne komputery i politykę
poleceń sprawdza się w ustawieniach Grok Bot; nie trzeba zmieniać ich na
bezwarunkowe zezwolenie.

## Kto budzi Bota

Przyjście głosówki uruchamia odbiór i transkrypcję w LoudTalk. **Skrzynka ani
samo MCP nie uruchamiają nowej tury Grok Bota.** Pierwszą próbę wywołaj w jego
rozmowie. Po niej można zapisać rutynę odczytującą kolejkę z wybraną częstotliwością,
jeśli dana instalacja pozwala rutynie na wymagane polecenia lokalne.

Grok Bot dokumentuje rutyny harmonogramowe i wyzwalanie zdarzeniami z integracji
konta Cursor, np. Slacka i GitHuba. Włączenie wtyczki Slack nie włącza takiego
wyzwalacza. Dokumentacja nie gwarantuje, że dowolna głosówka lub plik audio
spełni warunek zdarzenia: to trzeba sprawdzić w konkretnym koncie.
[Umiejętności i rutyny Grok Bot](https://docs.x.ai/grok-bot/skills-routines-and-automations).

Nie znaleziono w publicznej dokumentacji Grok Bot endpointu „wyślij prompt do
tego istniejącego Bota”. Nie używamy nieudokumentowanej sesji aplikacji ani
klucza API modelu jako zamiennika takiego endpointu. Bez potwierdzonego
wyzwalacza jest to połączenie **na żądanie lub przez rutynę**, z odpowiadającym
mu czasem oczekiwania, a nie gwarantowana natychmiastowa odpowiedź.

## MCP i komputer w chmurze

Jeśli Grok Bot ma już działający, autoryzowany connector do LoudTalk, umiejętność
może używać `receive_voice_messages` i `send_voice_message` zamiast poleceń
lokalnych. Przy odpowiedzi przekazuje `reply_to_message_id` równe `id` odebranej
wiadomości. Najpierw trzeba potwierdzić, że te narzędzia faktycznie są dostępne.

`loudtalk mcp` działa przez stdio na komputerze, na którym został uruchomiony.
`loudtalk mcp-http` udostępnia HTTP MCP pod `127.0.0.1:8766/mcp` i wymaga
`LOUDTALK_MCP_TOKEN`. Adres `127.0.0.1` na komputerze Grok Bota oznacza jego
komputer w chmurze. Nie wskazuje na Maca użytkownika. Ewentualne połączenie
sieciowe wymaga osobnej, uwierzytelnionej konfiguracji.

Dokumentacja [własnych connectorów zwykłego Groka](https://docs.x.ai/grok/connectors)
dotyczy `grok.com`. Nie stanowi dowodu, że te same kroki instalacji występują
w Grok Bot. Wtyczki Grok Bot są zarządzane w jego Marketplace i podlegają
polityce connectorów konta.
[Wtyczki Grok Bot](https://docs.x.ai/grok-bot/computer-and-apps).

## Zakres weryfikacji

22 września 2026 r. wykonano osiem testów z
[`test_transport.py`](../integrations/grokbot/test_transport.py). Wszystkie przeszły.
Uruchamiają prawdziwy lokalny serwer HTTP, bazę routingu, osobny proces CLI
i narzędzia SDK MCP. Sprawdzają zatwierdzenie rozmówcy, odrzucenie duplikatu,
oczekiwanie na istniejącego agenta, powrót do tego samego czatu i wątku,
odpowiedzi na dwie głosówki w odwrotnej kolejności, brak ponownej wysyłki po
powtórzeniu żądania oraz zgłoszenie błędu, gdy komunikator nie potwierdzi
dostarczenia.

```sh
PYTHONPATH=src .venv/bin/python -m pytest integrations/grokbot/test_transport.py -q
```

Komunikator i silnik mowy są w tych testach jawnie zastąpione kontrolowanymi
obiektami: stała transkrypcja, syntetyczny WAV, brak połączeń z zewnętrznym
kontem. Testy nie weryfikują logowania, uprawnień, umiejętności ani harmonogramu
na rzeczywistym koncie Grok Bot. Te cztery elementy należy potwierdzić podczas
pierwszego podłączenia użytkownika.
