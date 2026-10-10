# Vyterlix mobile app

The phone app for Vyterlix (iOS and Android, one Flutter code base). It talks to the same server as the
website, with the same logins, roles and business separation.

The rule for the app: the core functionality of the system is in it, with the website's wording, roles and
look. What is in it so far: log in (with forgot password), a list of the person's businesses (the one open
last time opens by itself), and inside a business a bar along the bottom with **Today** (what needs
attention, the health score and the key figures), **Actions** (the work decided on, with its steps and
results, and Vyterlix's ideas to take up or leave), **Alerts** (see, take on and close them), **Inbox**
(what you have been told) and **More** (Ask Vyterlix, key figures, business health, forecast with what to stock, and what changed). The refresh token is kept in the iOS Keychain / Android Keystore and swapped for a new one each
time; the app signs the person out if the server ends the session from anywhere.

## Run it

You need the Flutter SDK (`flutter doctor` should be happy). The server must be running (see the main
README: the API on port 8000).

```bash
cd mobile
flutter pub get
flutter run -d windows          # the quickest way to try it on a PC
```

Other targets:

| Target | Command | Notes |
|---|---|---|
| Windows window | `flutter run -d windows` | Needs Windows Developer Mode on (Settings, For developers) because the secure-storage plugin uses symbolic links |
| Android emulator | `flutter run --dart-define=API_BASE_URL=http://10.0.2.2:8000/api/v1` | `10.0.2.2` is how an emulator reaches your PC. Accept the Android licences first: `flutter doctor --android-licenses` |
| A real phone on your Wi-Fi | `flutter run --dart-define=API_BASE_URL=http://<your PC's address>:8000/api/v1` | Start the API with `--host 0.0.0.0`; allow port 8000 through the firewall |
| iOS | on a Mac only | Needs Xcode and an Apple Developer account for a real phone |

Log in with a demo account (the logins are in `backend/.demo-logins.local.md`).

## Check it

```bash
flutter analyze
flutter test                    # about 60 tests against a pretend server
```

An optional test against your real running server (skipped unless you give it a refresh token):

```bash
flutter test test/live_smoke_test.dart \
  --dart-define=LIVE_API_BASE_URL=http://localhost:8000/api/v1 \
  --dart-define=LIVE_REFRESH_TOKEN=<a refresh token from a mobile session>
```

## How it is laid out

| Folder | What is there |
|---|---|
| `lib/api` | The connection to the server: one place that adds the login token, renews it when it runs out, and turns errors into plain words |
| `lib/auth` | Who is signed in, and secure storage of the sign-in |
| `lib/screens` | Log in, the list of businesses, Today |
| `lib/widgets`, `lib/theme.dart` | The shared look (matches the website: soft background, white cards, indigo accent; dark mode follows the phone) |
| `lib/format.dart` | UK money (£), dates (dd/mm/yyyy) and months |
| `test` | Tests, with a pretend server in `test/support.dart` |

The identifiers (`com.vyterlix.vyterlix_mobile`) and the app name are placeholders until the Apple and
Google developer accounts exist; change them before anything is published.

## Not built yet

 data upload
and quick entry, reports, what we know, settings and billing, fingerprint / Face ID unlock, push
notifications, working without a signal, and links in emails that open the app. They are listed in `docs/CHECKLIST.md` under Phase 18.
