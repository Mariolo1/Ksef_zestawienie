# KSeF – zestawienia dla księgowej (wersja testowa)

FastAPI (backend) + React (frontend) + PostgreSQL, uruchamiane w Docker Compose.

Funkcje:
- konto: e-mail + hasło lub logowanie Google,
- 1 firma (NIP) na konto, środowisko KSeF prod/test/demo,
- certyfikat KSeF (.crt + .key albo .p12) – sprawdzany przy wgraniu, w bazie zaszyfrowany (Fernet, MASTER_KEY),
- test logowania do KSeF,
- raport .xlsx za wybrany zakres dat (zakup / sprzedaż / oba), generowany w tle,
- pobieranie i usuwanie raportów.

## Uruchomienie na VM

    cd ~/projekty/ksef-app
    cp .env.example .env && chmod 600 .env
    # uzupełnij .env: POSTGRES_PASSWORD, SECRET_KEY, MASTER_KEY (polecenia do generowania są w pliku)
    docker compose up -d --build
    docker compose ps
    docker compose logs -f backend

Aplikacja nasłuchuje na VM pod 127.0.0.1:8080. W VS Code (Remote SSH): zakładka **Ports → Forward a Port → 8080**,
potem w przeglądarce na swoim komputerze: http://localhost:8080

Bez VS Code: `ssh -L 8080:localhost:8080 ubuntu@IP_VM`

Dokumentacja API: http://localhost:8080/api/docs

## Logowanie Google (opcjonalne)

1. Google Cloud Console → APIs & Services → OAuth consent screen: typ External, tryb Testing, dodaj swoje konto jako test user.
2. Credentials → Create credentials → OAuth client ID → Web application.
3. Authorized redirect URI: `http://localhost:8080/api/auth/google/callback`
4. Client ID i Client secret wpisz do `.env` (GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET), potem `docker compose up -d`.

Google przyjmuje adres http tylko dla `localhost` – dlatego dostęp przez przekierowanie portu.
Pod własną domeną potrzebny będzie HTTPS (etap deploymentu).

## Ograniczenia wersji testowej

- tabele tworzone przy starcie (bez migracji Alembic),
- raporty przetwarzane przez wątek w backendzie – tylko 1 proces uvicorn, 1 raport naraz w całej aplikacji,
- brak limitu prób logowania, resetu hasła i weryfikacji e-maila,
- MASTER_KEY w pliku .env – docelowo menedżer sekretów; jego utrata = utrata zapisanych certyfikatów,
- HTTP bez TLS – tylko przez localhost/tunel.

## Kopia zapasowa

    docker compose exec db pg_dump -U ksef ksef > backup.sql
    # wolumen app_data: XML faktur i pliki raportów
