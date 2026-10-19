# Gemensam statistik med Supabase Free

## Vad som sparas

- Kick Off: ett unikt match-ID, modellversion, lag, spelplats, sannolikheter,
  utfall och tid i UTC. Bonuspoäng och personuppgifter sparas inte.
- Säsong: ett unikt körnings-ID, valt lag, modellversion och simulerade matcher
  med datum, målresultat och prognoser. Båda lagen räknas i statistiken.
- När alla 380 matcher är sparade räknar databasen fram slutplacering och
  ligapoäng för samtliga 20 lag i samma transaktion.
- Avbrutna säsonger ingår inte i slutplaceringar eller poängsnitt.

Säsongsstatistiken är fördelningen av hela simulerade ligor, inte bara det lag
besökaren valde. Kick Off är självvalt: vinstprocenten påverkas av vilka
motståndare och spelplatser besökarna väljer. Det är inte ett fristående mått på
modellens prognoskvalitet.

## Aktivera databasen

1. Skapa en separat organisation på **Free** och ett projekt på
   https://supabase.com/dashboard. Ingen betald tjänst behöver aktiveras.
2. Öppna projektets **SQL Editor**. Klistra in innehållet i
   `app/sql/001_shared_statistics.sql` och kör det. Spara inga lösenord i SQL-filen.
3. Kopiera projektets HTTPS-adress och en **Secret API key** (`sb_secret_...`)
   från projektets inställningar för API-nycklar.
4. Öppna Streamlit Community Cloud -> din app -> **Settings -> Secrets**.
   Lägg till värdena nedan. Behåll eventuella andra secrets.

```toml
[statistics]
enabled = true
url = "https://YOUR_PROJECT_REF.supabase.co"
secret_key = "sb_secret_DIN_HEMLIGA_SERVERNYCKEL"
```

5. Spara och låt appen starta om. Spela en Kick Off-match och kontrollera att
   den visas på sidan **Statistik**. Använd **Uppdatera statistiken** för att
   hoppa över den högst 30 sekunder långa läscachen.
6. Kör en full säsong och kontrollera att antalet avslutade säsonger ökar med
   ett, och att varje lag får en ny slutplacering.

För lokal användning: kopiera `.streamlit/secrets.toml.example` till
`.streamlit/secrets.toml` och fyll i den lokalt. Den riktiga secretsfilen
ignoreras av Git. Skicka aldrig hemliga nycklar i chatten eller till GitHub.

## Säkerhet och drift

Databasen har Row Level Security och saknar läs-/skrivbehörigheter för
`anon` och `authenticated` på statistikobjekten. Endast appservern får anropa
funktionerna med den hemliga nyckeln. Nyckeln skickas inte till besökarens
webbläsare. Använd gärna ett separat projekt för denna app eftersom en secret
key har höga behörigheter i projektet.

Varje match får ett beständigt ID före första skrivförsöket. Vid borttappat svar
kan samma skrivning skickas igen; unika databasnycklar hindrar dubbelräkning.
Matchbatch och säsongsavslut skrivs atomärt. SQL räknar själv slutpoängen från
de sparade matcherna. Historik läses i sidor om 50 matcher; totaler räknas i SQL
så att Supabases standardgräns för antal svarsrader inte trunkerar statistiken.

Utan anslutning fungerar spelet och sidan Statistik visar att insamlingen inte
är aktiverad. Vid tillfälligt fel ligger osparade resultat kvar i den aktuella
sessionen och kan skickas igen. Även vid omstart av en säsong behålls dessa i
sessionens kö. **Om sessionen eller servern avslutas före sparning kan kön gå
förlorad.** Detta är inte en beständig lokal kö och sparar inte spelprogress.
Tidigare sessioners simuleringar kan inte återskapas. Pågående sessioner från
före uppdateringen registreras inte som nya statistikkörningar.

Modellversionen är ett fingeravtryck av modellen, simuleringsdata och en
simuleringsversion i `statistics_store.py`. Höj simuleringsversionen där om
beräkningsreglerna ändras. Filtrera per version när olika modeller jämförs.

Gratisprojekt kan pausas vid låg aktivitet och användningsgränser kan begränsa
skrivning. Följ databasstorleken i Supabase och exportera data regelbundet om
historiken ska bevaras som projektresultat. Varje full säsong lagrar 380 matcher;
undvik att lagra till exempel grafik eller modellfiler i databasen.

Alla kan generera simuleringar via den publika appen. Det finns ingen
användarverifiering eller gräns för körningar per person i denna version.
Statistiken kan därför påverkas av mycket flitig användning. Vid hög trafik
bör körningsbegränsning införas innan lagringskvoten fylls.

## Verifiering

Kör alla tester från projektroten:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s app -p "test_*.py"
```

Lokala tester använder ersättningslager och simulerade HTTP-svar. Anslutning,
SQL-behörigheter och transaktioner måste även verifieras i ett faktiskt
Supabase-projekt innan insamlingen betraktas som driftsatt.

Dokumentation:
https://supabase.com/docs/guides/getting-started/api-keys
https://supabase.com/docs/guides/database/postgres/row-level-security
