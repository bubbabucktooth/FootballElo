<p align="center">
  <img src="app/assets/super-soccer.svg" alt="Beat the Model" width="360">
</p>

# Beat the Model

Ett interaktivt fotbollsprojekt där historiska matchdata, Elo och maskininlärning möter fotbollens slump. Utforska modellens prognoser i enskilda matcher eller simulera en alternativ Premier League-säsong 2017/18.

Appen är byggd med Streamlit och har ett SNES-inspirerat gränssnitt för dator och mobil.

**[Spela Beat the Model](https://beatthemodel.streamlit.app/)**

## Idén bakom projektet

Hur långt kommer vi med historiska data när vi försöker förutse fotboll?

Modellen uppskattar sannolikheten för hemmavinst, oavgjort och bortavinst. Appen drar sedan ett utfall enligt dessa sannolikheter. Favoriten kan alltså förlora, och samma match kan få olika resultat vid olika försök.

Syftet är att göra modellens sannolikheter och osäkerhet begripliga genom att låta användaren utforska dem.

## Spellägen

### Kick-off

- Välj ett lag och en omgång på **10, 20 eller 30 matcher**.
- Välj motståndare och hemma/borta, eller använd de föreslagna riskvalen.
- Se modellens sannolikheter och möjliga poäng före matchen.
- Lämna ett frivilligt matchtips och jämför med modellens favoritutfall.
- Samla poäng och försök slå rekordet för omgångslängden.

Kick-off använder lagens exporterade Elo-värden och historiska form inför 2017/18. Värdena ligger fasta: spelade Kick-off-matcher förändrar inte lagens Elo eller form.

Om `p` är ditt lags vinstsannolikhet beräknas poängen så här:

| Resultat för ditt lag | Poäng |
| --- | --- |
| Vinst | `round(100 / p)` |
| Oavgjort | `0` |
| Förlust | `-round(100 * p)` |

En seger med en underdog ger därmed fler poäng än en favoritvinst.

**Din känsla mot modellen** är en separat duell som räknar antal rätt på matcher där du själv lämnat ett tips. Modellens val är utfallet med högst sannolikhet; vid lika sannolikhet prioriteras hemmavinst, sedan oavgjort, sedan bortavinst. Tipset påverkar inte matchutfallet, poängen eller highscore.

### Säsong

Välj ett lag och spela igenom dess 38 matcher i det verkliga spelschemat för **Premier League 2017/18**. Övriga ligan simuleras automatiskt, så hela säsongen omfattar 380 matcher.

Du kan spela en match i taget eller snabbsimulera 3 matcher, 5 matcher eller resten av säsongen. Om ditt lag säkrar seriesegern pausar snabbsimuleringen för pokalfirande.

Till skillnad från Kick-off uppdateras lagens **Elo, form och senaste fem matchernas målskillnad** efter simulerade resultat. Dessa förändringar påverkar kommande prognoser. Modellens tränade parametrar ändras däremot inte under spelet.

Ligatabellen kan jämföras med den verkliga tabellen fram till motsvarande datum. Efter säsongen visas slutplacering, poäng, resultatfördelning, största seger och jämförelsen med verkligheten.

### Gemensam statistik

Med en konfigurerad Supabase-anslutning kan alla besökares simuleringar sammanställas:

- Kick-off-resultat, förväntade vinster och matchhistorik.
- Slutplaceringar, poängsnitt, ligatitlar och placeringsfördelningar från avslutade säsonger.
- Highscore för avslutade Kick-off-omgångar, uppdelat på 10, 20 och 30 matcher.
- Filter för modellversion och relevanta matchurval samt CSV-export av tabeller.

Statistiken beskriver **simuleringar**, inte verkliga matcher. Kick-off påverkas också av vilka motståndare och spelplatser användarna väljer.

## Modell och data

Appen laddar en färdigtränad modell från `model.joblib`. Följande tre variabler beräknas inför varje match:

| Variabel | Innehåll |
| --- | --- |
| `EloDiff_Enhanced` | Hemmalagets Elo minus bortalagets Elo. |
| `FormDiff` | Skillnaden i sammanlagda ligapoäng från lagens fem senaste matcher. |
| `GoalDiff5` | Skillnaden i sammanlagd målskillnad från lagens fem senaste matcher. |

### Datafiler i appen

| Fil | Användning |
| --- | --- |
| [`model.joblib`](app/model.joblib) | Den sparade modellen som ger sannolikheter för H/D/A. |
| [`team_states.csv`](app/team_states.csv) | Lagens ursprungliga Elo-värden och exporterade startvärden. |
| [`history_last5.csv`](app/history_last5.csv) | Historiska formpoäng och målskillnader för de fem senaste matcherna. |
| [`fixtures_2017.csv`](app/fixtures_2017.csv) | Spelschemat för 2017/18. |
| [`historical_scores.csv`](app/historical_scores.csv) | Historiska målresultat, grupperade efter hemmavinst, oavgjort och bortavinst. |
| [`actual_results_2017.csv`](app/actual_results_2017.csv) | Verkliga resultat för jämförelser med den simulerade säsongen. |

I säsongsläget dras först ett utfall från modellens sannolikheter. Därefter dras ett målresultat från motsvarande historiska resultatpool. **Modellen förutser alltså inte ett exakt målresultat för det aktuella lagparet.**

Alla matcher på samma kalenderdag prognostiseras innan dagens Elo- och formuppdateringar görs. De verkliga resultaten används för jämförelse, inte för att styra simuleringens utfall.

Det här repositoryt innehåller den körbara appen, modellen och exporterade datafiler. Ursprungliga forskningsdata och träningsnotebooks ingår inte i den versionerade appen. Modellen tränas inte om när appen startas.

## Kör lokalt

Använd **Python 3.12**. Kör följande från projektets rot i PowerShell:

```powershell
git clone https://github.com/bubbabucktooth/FootballElo.git
cd FootballElo

python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r app/requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app/app.py
```

Öppna adressen som Streamlit skriver ut, normalt `http://localhost:8501`.

På macOS/Linux används `.venv/bin/python` i stället för `.venv\Scripts\python.exe`.

Behåll versionerna i [`app/requirements.txt`](app/requirements.txt), eftersom modellen är sparad med en bestämd scikit-learn-version.

### Valfri databas

Spelet fungerar utan databas. Gemensam statistik och highscore kräver Supabase.

Kör SQL-filerna i ordning i Supabase SQL Editor:

1. [`001_shared_statistics.sql`](app/sql/001_shared_statistics.sql)
2. [`002_kick_highscores.sql`](app/sql/002_kick_highscores.sql)
3. [`003_statistics_summary.sql`](app/sql/003_statistics_summary.sql)

Lägg sedan till serverns anslutningsuppgifter i `.streamlit/secrets.toml` lokalt, eller i Streamlit Clouds secrets:

```toml
[statistics]
enabled = true
url = "https://YOUR_PROJECT_REF.supabase.co"
secret_key = "YOUR_SUPABASE_SECRET_KEY"
```

Secrets ska inte läggas i Git. Se [`SHARED_STATISTICS.md`](SHARED_STATISTICS.md) för detaljer om konfiguration, lagring och återförsök.

## Tester

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s app -p "test_*.py"
```

Testerna omfattar bland annat simulering, tabellberäkning, matchtips, spel- och dialogflöden samt statistikens lagring och återförsök. Databastesterna använder ersättningslager och simulerade HTTP-svar.

## Projektstruktur

```text
app/
  app.py                  # Spelflöden, prognoser och simulering
  styles.css              # Gemensam design och mobilanpassning
  ui_tables.py            # Tabeller och mobilens matchhistorik
  statistics_page.py      # Gemensam statistik och diagram
  statistics_store.py     # Databasanslutning och synkronisering
  assets/                 # Logga och lokala grafik-/typsnittsresurser
  sql/                    # Databasfunktioner och migreringar
  test_app.py
  test_statistics.py
  requirements.txt
  model.joblib
  *.csv                   # Exporterade startvärden och matchdata
```

## Publicering

Appen kan publiceras med Streamlit Community Cloud från detta repository. Ange `app/app.py` som startfil och Python 3.12 som miljö. Lägg databasuppgifter i tjänstens secrets om gemensam statistik ska aktiveras.

Se [`DEPLOYMENT.md`](DEPLOYMENT.md) för installations- och publiceringsinstruktioner.

## Avgränsningar

- Pågående spel och den personliga tipsduellen finns i Streamlit-sessionen. De kan försvinna vid en ny session eller serveromstart; databasen återställer inte spelprogress.
- Gemensam statistik och highscore kan sparas med Supabase. Tipsduellen sparas inte där.
- Att slå modellens favoritutfall i simulerade matcher är inte samma sak som att prognostisera verklig fotboll bättre än modellen.
- En simulerad säsongs avvikelse från verkligheten är inte ensamt ett mått på modellens prognoskvalitet. Den innehåller även slump och antaganden om målresultat och lagens utveckling.
