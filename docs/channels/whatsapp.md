# WhatsApp

Wyślij głosówkę do podłączonego numeru WhatsApp Business. LoudTalk przekaże jej treść
wybranemu agentowi i odeśle jego odpowiedź jako głosówkę w tej samej rozmowie.
Odpowiedź używa OGG/Opus i pola `audio.voice=true`, zgodnie z
[oficjalnym przykładem Meta](https://www.postman.com/meta/whatsapp-business-platform/documentation/wlk6lh4/whatsapp-cloud-api?entity=request-13382743-13714319-4f97-49ff-ae37-3e8b64183a83).

## Podłączenie

1. Przygotuj aplikację Meta z WhatsApp Business Platform / Cloud API oraz numerem
   biznesowym. W panelu Meta znajdziesz `Phone Number ID` i token dostępu.
   To identyfikator nadany przez Meta, a nie numer telefonu.
2. W LoudTalk wybierz **WhatsApp**, wskaż agenta i wpisz dane z tabeli poniżej.
3. Udostępnij przez HTTPS tylko ścieżkę `/hooks/{channel_id}` lokalnego serwera
   `127.0.0.1:8765`. `{channel_id}` to identyfikator połączenia z LoudTalk.
   Wstaw ten adres w konfiguracji webhooka Meta. Wpisz ten sam verify token
   w Meta i LoudTalk. Zapisz webhook i subskrybuj pole `messages`.
4. Sprawdź połączenie. Test odczytuje wyłącznie tożsamość numeru — nie wysyła wiadomości.
5. Wyślij głosówkę ze swojego telefonu i zaakceptuj swoją rozmowę w LoudTalk.
   Od tego momentu możesz używać głosu bez otwierania LoudTalk.

| Pole | Skąd je wziąć |
| --- | --- |
| Access token | Token aplikacji/system user z dostępem do numeru i uprawnieniem `whatsapp_business_messaging`; odczyt konfiguracji numeru może też wymagać `whatsapp_business_management`. |
| App secret | Ustawienia podstawowe aplikacji Meta. Weryfikuje podpisy zdarzeń. |
| Verify token | Własny losowy sekret, identyczny w konfiguracji webhooka Meta i LoudTalk. |
| Phone Number ID | WhatsApp / API Setup w aplikacji Meta. |
| Graph version | Zaawansowane: wersja dostępna dla Twojej aplikacji. Domyślny pin integracji to `v24.0`; nie jest deklaracją najnowszej wersji API. |

App secret, verify token i access token mają różne zadania; nie są zamienne.
Meta opisuje [weryfikację GET i podpisy POST](https://whatsapp.github.io/WhatsApp-Nodejs-SDK/api-reference/webhooks/start/).
Ta referencja pochodzi z archiwalnego SDK Meta; implementacja nie instaluje tego SDK.

## Co działa i jakie są granice

- Bezpośrednie rozmowy z numerem Business; prywatne konto WhatsApp nie jest logowane
  przez kod QR i aplikacja nie czyta jego historii.
- Nagrania audio do 16 MB. Odpowiedź jest głosówką OGG/Opus. Transkrypcja pozostaje
  w LoudTalk; audio WhatsApp nie ma podpisu tekstowego w tym przepływie.
- Odpowiedź kierowana jest do nadawcy i wskazuje oryginalną głosówkę jako kontekst.
  Musi mieścić się w obowiązującym oknie odpowiedzi WhatsApp; zaległa kolejka po długiej
  przerwie może wymagać nowej wiadomości od użytkownika.
- Komputer z LoudTalk musi działać, a webhook HTTPS musi być osiągalny.
- Test tokenu nie potwierdza dostarczenia audio. Do sprawdzenia końca do końca potrzebna
  jest głosówka z telefonu po podłączeniu własnego numeru.

LoudTalk weryfikuje surowe body przez HMAC SHA-256, filtruje numer docelowy, ignoruje
statusy i echa. Pobiera media dopiero po zatwierdzeniu rozmowy/nadawcy. Plik pobierany
jest przez uwierzytelniony identyfikator Meta, a potem przez kontrolowany adres Meta;
nie pobieramy dowolnych URL z treści wiadomości. API uploadu opisuje
[oficjalna kolekcja Meta](https://www.postman.com/meta/whatsapp-business-platform/request/2fuw5le/upload-audio).

## Gdy nie przychodzi odpowiedź

Sprawdź kolejno: pole `messages` w subskrypcji webhooka, działający adres HTTPS,
App secret, poprawny Phone Number ID i status połączenia w LoudTalk. Przy błędzie
uprawnień odśwież token i dostęp do numeru. Jeżeli aplikacja jest w trybie testowym,
użyj odbiorcy dopuszczonego w panelu Meta. Jeśli token jest prawidłowy, ale odpowiedź
jest odrzucana po dłuższej przerwie, wyślij nową głosówkę, aby odnowić rozmowę.

Testy repozytorium używają izolowanego transportu HTTP. Nie są certyfikacją Meta ani
potwierdzeniem dostarczenia w realnej aplikacji WhatsApp.
