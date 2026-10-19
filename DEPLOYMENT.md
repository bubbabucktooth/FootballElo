# Deploya Beat the Model

Appen publiceras via GitHub och Streamlit Community Cloud. Dessa instruktioner
behåller projektets nuvarande mappstruktur.

## Filer som behövs

```text
app/
  app.py
  styles.css
  requirements.txt
  model.joblib
  team_states.csv
  history_last5.csv
  historical_scores.csv
  fixtures_2017.csv
  actual_results_2017.csv
  assets/
    beat-the-model-arcade.png
```

`app/test_app.py` kan följa med för tester. Appen behöver inte `Data/`,
`Notebooks/`, lokala Pythonmiljöer eller förhandsvisningsbilder.
Inga API-nycklar behövs för appen.

## Lokal installation och verifiering

Använd **Python 3.12** och kör från projektets rot i PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r app/requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s app -p test_app.py
.\.venv\Scripts\python.exe -m streamlit run app/app.py
```

Kontrollera att `python --version` visar 3.12 innan miljön skapas.
Beroendefilen låser versionerna, inklusive scikit-learn 1.9.1 som modellfilen
sparades med. Modellfilen ska följa med; modellen tränas inte vid deployment.

### Verifierat lokalt

En separat miljö i `.venv` har installerats från `app/requirements.txt` med
Python 3.12.15. Alla 14 tester passerade utan scikit-learns versionsvarning.
Streamlit-servern startade och svarade med HTTP 200 på startsidan samt `ok`
på `/_stcore/health`. Detta är verifiering på Windows; molnbygget och den
visuella kontrollen efter publicering återstår.

## Publicering

Ett paket med enbart deploymentfiler finns i `dist/beat-the-model-deploy.zip`.
Packa upp det innan uppladdning till GitHub: ladda upp innehållet, inte zipfilen.
Paketet behåller `app/`-mappen och innehåller också testerna och denna guide.
Forskningsdata, notebooks, miljöer och förhandsvisningsbilder ingår inte.

1. Skapa ett GitHub-repository och lägg till filerna ovan med samma struktur.
   Ta inte med hemligheter eller `.venv`. Granska vilka filer som laddas upp.
2. Logga in på https://share.streamlit.io med ditt GitHub-konto.
3. Välj **Create app**, repository och den branch du laddade upp till.
4. Ange **Main file path: `app/app.py`**.
5. Välj **Python 3.12** under **Advanced settings**.
6. Välj tillgänglig appadress och klicka på **Deploy**.
   Community Cloud läser `app/requirements.txt` intill startfilen.
7. Kontrollera byggloggen och den publicerade appen.

Officiell guide:
https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy

## Kontroll efter publicering

- Startsida, logga och stilar laddas.
- Kick Off visar resultatdialog och rätt poängförändring.
- Säsongsläget spelar en egen match per klick.
- Snabbsimulering av 3, 5 eller återstående matcher visar en samlad resultatruta.
- Säkrad serieseger pausar snabbsimuleringen för pokalfirande; fortsättning spelar inte om matcher.
- Starta ny säsong återgår till lagvalet efter säsongsslut.
- Faktisk tabell och poängdifferenser följer samma speldatum.
- Avslutad säsong visar jämförelsen med verkligheten.
- Kontrollera layouten på dator och mobil.

Spelresultat finns bara i den aktuella Streamlit-sessionen. En ny session,
omstart av servern eller omladdning kan tappa framstegen. Ingen databas används.

Senare ändringar publiceras genom att uppdatera filerna i den anslutna branchen.
Kör testerna innan uppdateringar laddas upp.
