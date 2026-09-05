# Marvin: návrh dotažení produktu

Datum: 5. 9. 2026. Základ: verze 1.5.3, HEAD `0c7841f`, včetně rozpracovaných změn `webapp.py`. Stav je návrh, nikoliv implementace.

## Schválený směr a zachování funkcí 1.5.3

Uživatel schválil architektonické, funkční i vizuální změny s následujícími závaznými podmínkami. Nový vzhled nesmí odstranit fungující vlastnosti aktuální aplikace; původní vizualizace nezobrazovala úplný inventář funkcí.

Pořadí pracovních režimů je ve všech nabídkách pevné: **Diskuze → Výzkum → Psaní → Vývoj → Počítač**. Zachováváme všech pět režimů včetně plného ovládání počítače; vizuální ukázka může zobrazovat aktivní Vývoj, ale nesmí tím měnit pořadí položek.

### Přílohy jsou součástí základního composeru

- Viditelné tlačítko **Attach** s ikonou sponky zůstává dole u promptu a otevírá výběr souborů z disku. Nesmí být nahrazené pouze ikonou ani přesunuté do knihovny projektu.
- Obrázky lze přidat přetažením i vložením ze schránky přes Ctrl+V. Stejně funguje více obrázků najednou; běžné vložení textu zůstává běžným vložením textu.
- Před odesláním je každý obrázek vidět v náhledu v composeru. Jednotlivý obrázek lze odebrat, aniž by zmizel text či ostatní přílohy.
- Po odeslání jsou náhledy součástí konkrétní uživatelské zprávy. Kliknutí otevře větší zobrazení originálu. Náhled v promptu i v chatu musí ukazovat skutečný soubor, ne zástupnou ikonu.
- Příloha se dostane do modelového requestu; samotný správně vykreslený thumbnail tento požadavek nedokazuje. Textové modely musí mít jasně uvedenou dostupnost vision.
- Vyprázdnění composeru po přijetí zprávy nesmaže odeslané obrázky z chatu. Uložené obrázky a jejich odkazy přežijí reload i opětovné otevření konverzace.
- Steering a fronta nesmí ztratit obrázky ani je připojit k jiné zprávě či jinému chatu. Přijetí příloh a textu proběhne jako jedna identifikovaná zpráva.

### Podmínka převodu do nového UI

Před nahrazením staré pracovní plochy projít inventář stávajících funkcí 1.5.3 a zapsat jejich nové umístění i stav ověření. Zahrnuje model/KV/myšlení, všechny pracovní režimy, server, projekty a přesun chatů, historii a vyhledávání, přílohy, retry/undo/větve, STOP/steering, tři paměti, pins, skilly, dokumenty a jejich export, výzkumné podklady, procesy, browser/ovládání počítače, body obnovy, zálohy, jazyky a manuály. Přesunutá funkce musí zůstat dostupná a dohledatelná.

Regresní ověření příloh: Attach → více souborů → náhledy → odebrání jednoho → odeslání → modelový payload → náhledy v chatu → zvětšení → reload. Totéž zopakovat pro drag-and-drop, clipboard paste, zprávu jen s obrázkem, steering a frontu. Tyto scénáře ověřit v Edge i v používaném WebView2 okně. Dokončená migrace vyžaduje funkční shodu, nikoli pouze podobný screenshot.

## Doporučení

Marvin už má většinu potřebných nástrojů. Další posun bude spočívat hlavně v jejich spolehlivém propojení: zachovat dlouhou práci, dobře předávat kontext, ukazovat výsledky a dát uživateli přesnou kontrolu nad probíhající úlohou.

Zachovat Python, llama.cpp, jeden sekvenčně pracující model, lokální data a desktopové okno nad webovým rozhraním. Qwen Q5/Q8 zůstává rozumným výchozím profilem, který uživateli vyhovuje; tato analýza neprováděla nové měření inference a nedoporučuje změnu modelu podle dojmů. Grafickou část postupně uvolnit z omezení současného Gradio layoutu, aniž by se přepisovaly fungující nástroje a modelový backend.

Návrh respektuje produktové hranice v AGENTS.md: bez LSP, paralelních modelových agentů, hlavního terminálového workflow, nepraktických kontextových profilů a obecného plugin/MCP hostu. Nezavádí limity na délku úlohy, počet kroků ani tokenové kvóty. Skills a pracovní postupy zůstávají pomůckami; uživatel může zadat vlastní postup.

## Co je již dobré

- Rozdělení Diskuze / Výzkum / Psaní / Vývoj / Počítač a tři vrstvy paměti.
- Projekt s více chaty, přesouvání konverzací, perzistentní historie a vyhledávání.
- Jedno lokální inference API, profily GPU/KV a asynchronní přepínání modelů.
- Oddělené Python moduly nástrojů, patchování souborů, symbolový index, testovací profily a browserové nástroje.
- Research ledger, uchování zdrojů a syntéza bez filtrování podle důvěryhodnosti.
- Lokální skilly, jejich katalog a načítání až podle potřeby.
- Export PDF/DOCX, základní práce s XLSX a obrazové přílohy.
- Modelová/runtime záloha jako fallback při nedostupnosti online zdrojů.
- Tmavý vzhled se zeleným akcentem a ikonami. Tuto identitu není důvod zahazovat.

## Co bylo ověřeno

Přečteny byly hlavní moduly aplikace, UI, persistence, streamování, agent, indexy, výzkum, dokumentové nástroje, zálohování a instalační tok. Stávající testovací sada dokončila **366 kontrol bez chyby**. Testy dokazují příslušné pokryté scénáře, nikoliv bezchybnost každé interakce.

Aktuální Gradio UI bylo vykresleno v izolovaném testovacím datovém adresáři s ukázkovým chatem, bez spuštění modelu. V Edge při 1440 × 960 px mělo rozbalené nastavení sidebar o výšce obsahu 4533 px proti 912 px viditelné plochy. To odpovídá zhruba pěti obrazovkám rolování. Celá stránka v tomto rozlišení vertikálně nepřetékala. Poslední pracovní úpravy layoutu tedy řeší část reálného problému.

Pomocný test `LLMClient.stream` se simulovaným streamem požádal o STOP před prvním chunkem. V režimu čistého reasoning i čistých argumentů nástroje klient přečetl všech 8 z 8 chunků a vrátil `stopped=False`; při argumentech navíc vrátil sestavené tool calls. Jde o reprodukovanou chybu klienta, ne o benchmark skutečného modelu.

Nebyl prováděn nový GPU benchmark, kompletní instalace na jiném PC ani destruktivní test skutečných projektů. Návrh UI používá ilustrativní obsah a hodnoty, není screenshotem hotové nové verze.

Klikací návrh byl ověřen v Edge na šířkách 1024, 736, 360 a 320 px bez horizontálního přetékání ovládání. Ověřeny byly režimy, detail průběhu, fronta upřesnění, simulované zastavení, zdroje, náhled výsledku a přepnutí světlého/tmavého vzhledu; konzole neměla JavaScriptové chyby. Reakce těchto ovladačů v návrhu jsou lokální simulace, nikoliv volání produkčního modelu.

## 1. Spolehlivé řízení úlohy: první priorita

### STOP, pokračování a steering

V `harness/llm.py:211` se zastavení řeší uvnitř příchodu chunku a podmínka čeká na viditelný text či počet znaků `delta.content`. Čisté `reasoning_content` a argumenty nástroje tuto podmínku nesplní. Při čekání na další bajt navíc není co vyhodnocovat. V `webapp.py:653` watchdog v běžné větvi znovu nuluje `idle_strikes`, takže nedosáhne zamýšlených dvou neaktivních kontrol; proměnná `t` zde současně označuje vlákno i překladovou funkci v chybové větvi.

Navrhuji jednoho vlastníka běhu, samostatné přerušení transportu a jasné stavy: příprava, čtení podkladů, uvažování, generování odpovědi, příprava nástroje, provádění, čekání na uživatele, zastavování, hotovo, chyba. STOP během reasoning nebo přípravy nástroje zastaví okamžitě; u viditelného textu může dokončit větu s krátkým časovým limitem pro reakci na STOP. Tento limit se vztahuje pouze na uživatelem vyžádané zastavení, nikoliv na délku práce. Před každým dalším nástrojem se znovu zkontroluje přerušení.

Rozepsaný text, přijatá upřesnění a výsledky nástrojů ukládat průběžně. Po zastavení je dostupné Pokračovat. Změna chatu pouze mění pohled; rozpracovaný běh dál patří původnímu chatu. Pokračování po pádu neopakuje již dokončený zápis nebo příkaz bez kontroly skutečného výsledku.

U zadávání během práce nabídnout dvě jasné možnosti: **Upřesnit nyní** a **Po dokončení**. První zachová současný steering, druhá uloží další úlohu do viditelné fronty. Každá zpráva má potvrzený stav: přijato, čeká, zapracovává se, zpracováno. Zprávu ve frontě lze upravit či zrušit. Draft promptu je uložený pro každý chat a po přepnutí nezmizí.

Akceptace: opakovaný STOP ve všech fázích, steering během reasoning i nástroje, refresh stránky, ztráta spojení UI, dva otevřené pohledy stejného uživatele, přepnutí projektu při běhu a obnovení po restartu. Po obnovení se nesmí zopakovat dokončená akce ani ztratit potvrzená zpráva.

### Detekce opakování nesmí předstírat dokončení

Ve verzi 1.5.3 agent po pěti totožných voláních nástroje skončí se stavem FINAL a uloží úlohu jako complete (`harness/agent.py:627`). Porovnává podpis volání, nikoliv změnu výsledku. Opakované kontrolování běžícího procesu tedy může vypadat jako smyčka, přestože práce postupuje. Přestože obecný limit kroků zůstává nulový, tato konkrétní větev úlohu automaticky ukončuje.

Posuzovat skutečný posun: nové bajty výstupu, změny souborů, stav procesu a výsledek nástroje. Při podezření doporučit modelu jiný postup nebo nabídnout uživateli Pokračovat / Upřesnit. Neoznačovat nedokončený úkol za hotový a nepřidávat univerzální strop počtu pokusů.

### Projekt, chat a běh mají vlastní identitu

`webapp.py:100` drží sdílený AppState a modifikovatelný config. Generátor opakovaně čte aktuální `state.agent` a `state.session`. Samostatné callbacky a slash příkazy mění tytéž objekty; `prepare_submission` u obslouženého slash příkazu ruší `run_active` i v době, kdy původní běh nemusí skončit (`webapp.py:1002`). Riziko existuje i v single-user aplikaci, protože pracují vlákna, časovače a prohlížeč současně.

Zavést stabilní `project_id`, `session_id`, `run_id`, `message_id` a identifikátor přijatého vstupu. Běh dostane neměnný snapshot modelu, myšlení, pracovního režimu, workspace a pamětí. Navigace, přesun chatu a změna konfigurace budou různé příkazy. Změny nastavení pro následující otázku se nesmí zpětně propsat do právě odeslaného modelového requestu.

Rozpracované přepínání projektu je správný UX směr. Dotažení musí být atomické: nejprve vybrat cílový chat, pak od něj odvodit workspace, režim a paměti, až nakonec zveřejnit výsledný stav. Současný `set_workspace` ještě před načtením cílového chatu sahá i na metadata režimu starého chatu. Ověřit přepnutí tam a zpět včetně chatu bez projektu.

## 2. Architektura a výkon

### Oddělit aplikaci od zobrazování

`webapp.py` má přes 4300 řádků a obsahuje práci se session, spouštění úloh, instalaci, modální dialogy, CSS, JavaScript i Gradio event wiring. Pouhé rozdělení souboru nepomůže, pokud bude všude dál sdílený globální stav.

Cílová struktura:

```text
Webové rozhraní v existujícím desktopovém okně
    |
Lokální API + události se sekvenčním číslem
    |
ApplicationService
    |-- navigace projektů a chatů
    |-- RunController: jeden aktivní modelový běh, fronta a STOP
    |-- SessionStore: zprávy, drafty, události a výsledné soubory
    |-- ContextBuilder: paměti, dokumenty, skilly a souhrny
    |-- RuntimeService: llama.cpp, modely, diagnostika a fallback zálohy
    |
Existující Agent + nástroje + LLMClient -> llama.cpp
```

Je to stále jedna lokální Python aplikace. Není důvod zavádět mikroservisy, Redis ani další rezidentní služby. Události mohou být přenášeny přes SSE; klient při reconnectu požádá o pokračování od posledního pořadového čísla. V první etapě stejnou službu používá stávající Gradio UI.

Nástroje dnes předávají převážně řetězce a chyby se vracejí jako text s prefixem ERROR (`harness/tools/base.py:82`). Postupně doplnit strukturovaný výsledek se stavem, čitelným souhrnem, odkazy na artefakty, plným logem a naměřenými kontrolami. Model stále dostane čitelný text; UI a řízení úlohy budou mít spolehlivá data místo rozpoznávání úspěchu z volného textu.

### Kam s Gradiem

Doporučuji zachovat fungující Python backend a směřovat k vlastní tenké chatové UI vrstvě. Současná kombinace dlouhého CSS, selektorů interního DOM a patchování `gradio.routes.App.create_app` (`webapp.py:30`) je citlivá na změny Gradia. Samotná dokumentace Gradia upozorňuje, že selektory jeho interních elementů nejsou stabilním kontraktem.

Praktický postup: oddělit ApplicationService, zavést identifikované události a udělat skutečný prototyp jediného pracovního toku. Až prokáže lepší streamování a ovládání, převést chatový shell do malého frontendového projektu TypeScript/React nad lokálním FastAPI. Frontend se sestaví do statických souborů; uživatel nebude instalovat Node. Existující WebView2 launcher zůstane.

Menší alternativa je vlastní chatová komponenta uvnitř Gradio nebo jeho oddělený serverový režim. To je možnost přechodu, nikoli důvod dnes upgradovat závislosti naslepo. Volbu určit podle prototypu a nákladů balení. Neslibuji, že jiný frontend zrychlí generování modelu; přínos je ve vykreslování, interakcích a údržbě.

### Události a malé aktualizace

Aktuálně běží deset UI časovačů (`webapp.py:4272`) a stream posílá znovu seznam historie přibližně každých 0,6 s (`webapp.py:602`). Do dalších callbacků vstupuje znovu výpočet kontextu, procesů i katalogů. Navrhuji:

- Měnit pouze aktivní zprávu nebo dotčený stav; staré zprávy mají stabilní identitu a DOM.
- U dlouhé historie načítat starší zprávy postupně; úplný transcript zůstává uložený.
- Zachovat pozici při čtení starších odpovědí, přidat nenápadné Nové zprávy a návrat na konec.
- Stav nástroje zobrazit okamžitě z události. Klidový režim nemá opakovaně procházet projekt a přepisovat skryté panely.
- Runtime polling ponechat pouze pro údaje, které nelze dostat událostí, se sdílenou krátkou cache.

### Indexy a persistence

`harness/tools/search.py:87` pro každý dotaz vytváří nové `:memory:` FTS5, načítá soubory a plní tabulku. `Session._save_meta` spouští reindexaci celé historie; `HistoryIndex.reindex` nejprve smaže všechny řádky chatu a znovu je vloží (`harness/session.py:477`, `harness/history_index.py:34`).

Převést projektové FTS na lokální perzistentní cache s inkrementální aktualizací podle změn souborů. Zprávy v historii přidávat do indexu jednotlivě; přejmenování chatu mění jen metadata. Spojit seznam souborů pro repo mapu, symboly a dokumenty, aby každý nástroj neskenoval disk sám. Git status neidentifikuje změnu obsahu již upraveného souboru, proto invalidace potřebuje i mtime/velikost nebo revizi z nástroje (`harness/repo_index.py:115`).

JSONL historie může zůstat čitelným zdrojem pravdy. Doplnit atomické metadata, zotavení z nedopsaného posledního JSONL řádku, průběžné ukládání rozpracované odpovědi a verzování formátu dat. SQLite už používáme; nepřidávat druhou databázovou technologii.

## 3. Kontext, který udrží kvalitu dlouhé práce

### Komprese podle pracovní úlohy

`harness/context.py:7` používá ve všech režimech technický coding handoff do přibližně 500 slov. `render_messages_text` před sumarizací vynechává prostředek nad 60 000 znaků. Konverzace na disku zůstává, ale model nemusí dostat důležitou věc z prostředku podkladů.

Používat souhrn odpovídající režimu: u vývoje architektura a testy, u výzkumu tvrzení/zdroje/rozpory, u psaní osnova/styl/postavy/kontinuita, u diskuze cíle a rozhodnutí. Velké podklady zpracovat po dohledatelných částech s checkpointy. Uchovat odkazy na původní zprávy a zdroje a dát modelu nástroj k cílenému dohledání staré části historie. Po kompresi ověřit, že nezmizely explicitní požadavky, otevřené otázky a přijatá rozhodnutí.

### Přesné údaje o kontextu

Aktuální odhad je počet znaků dělený 3,6; reasoning se do persistentního odhadu nezapočítává, přestože jej request může posílat jako `reasoning_content`. Obrázky mají paušální odhad. `LLMClient.stream` zatím nezpracovává usage chunky bez choices.

Sbírat dostupné usage/timing údaje konkrétního llama.cpp buildu. UI oddělí: obsazený vstupní kontext, nově generované reasoning/odpověď, celkový limit, rychlost a dobu zpracování promptu. Kde přesné údaje nejsou, zůstane viditelné označení odhad. Runtime statistiky nebudou vydávány za měření, pokud jsou vypočtené jen z délky textu.

### Kontextový inspektor a knihovna

Současné tři paměti zachovat. Přidat přehled jejich účinné verze pro daný běh, seznam skutečně načtených skills a souborů, použitých úseků a změn od poslední otázky. Připínání znamená jasně viditelné automatické zahrnutí, ne neurčitý příslib, že model zná celý adresář.

U více konverzací filmového projektu přidat projektové podklady a seznam přijatých rozhodnutí s odkazem na původní chat. Navržená rozhodnutí lze upravit; automaticky neslučovat názory různých chatů do závazné pravdy. Není potřeba další model v paměti ani embeddings server: začít stávajícím fulltextem a strukturovanými metadaty.

## 4. Funkce, které skutečně chybí v každodenním používání

### Výsledky jako součást aplikace

Zavést registr výsledných souborů: co vzniklo, ke které úloze to patří, aktuální verze, cesta, formát, kontrola a akce Otevřít / Ukázat složku / Exportovat. Kliknutí na PDF nebo dokument otevře náhled uvnitř aplikace. Výsledek není nutné hledat v dlouhé konverzaci.

Pro vývoj má závěr nabídnout **Spustit aplikaci**, **Otevřít náhled**, **Otevřít instalátor** podle toho, co skutečně existuje. Ukázat věcně „3 změněné soubory, kontrola prošla, vizuální ověření neprovedeno“. Zelený stav testu má pocházet z dokončené kontroly, ne pouze z věty modelu. Čtení kódu ani side-by-side diff není hlavní pracovní plocha.

### Dokumenty a přílohy

Připojit k promptu nejen obrázek, ale i PDF, DOCX, XLSX, CSV a textový soubor. Uživatel zvolí příloha tohoto chatu / podklad projektu / trvale připnuté. Stejná operace má fungovat přetažením, výběrem i schránkou tam, kde to formát umožňuje.

`harness/documents.py:210` čte z DOCX nejprve všechny odstavce a teprve potom tabulky; původní pořadí dokumentu se ztrácí. PDF získává jen textovou vrstvu, takže sken bez textu neprozkoumá. Excel čte prvních pět listů a prvních sto řádků, bez výběru rozsahu; `data_only=True` zároveň neukazuje samotné vzorce. `openpyxl` vzorce nevypočítává. CSV se načte celé do paměti, ale ukáže opět jen začátek.

Doplnit adresovatelné čtení: PDF po stránkách, DOCX po sekcích v původním pořadí, XLSX podle listu/oblasti v režimu vzorce i hodnoty. Skenované PDF předat po vybraných stránkách existujícímu vision modelu nebo použít volitelnou OCR cestu. Přiznat, co bylo skutečně přečteno, a umožnit načíst další část. Existující DOCX upravovat se zachováním jeho struktury a stylů; nynější export nového dokumentu není totéž jako úprava originálu.

### Výzkum a zdroje

Ledger i syntéza už existují. Slabina je v kontrole: přítomnost `[S1]` někde v odpovědi nedokazuje, že byly použity podstatné informace zdroje (`harness/research.py:296`). Mezisouhrny dlouhých zdrojů zatím zůstávají jen v lokálních proměnných při syntéze.

Ukládat tematické poznámky a zpracované části postupně, aby šla přerušená syntéza obnovit. Každé důležité tvrzení propojit se zdrojem a místem v něm. Kliknutí na citaci otevře náhled použité pasáže a odkaz na originál. Viditelně rozlišovat nalezeno / načteno / použito / nenačteno kvůli chybě. Zachovat rozporné i neobvyklé informace a všechny nalezené zdroje; nepřidávat skóre důvěryhodnosti ani filtrování původu.

Závěr přizpůsobit otázce: stručná odpověď nahoře, přehledná syntéza, rozbalitelné detaily a dohledatelné podklady. Osm povinných stejně dlouhých sekcí není vhodných pro každou otázku.

### Body obnovy a projektová data

Současný `/checkpoint` volá `begin_task`: začne nový journal, ale nevytvoří úplný snapshot aktuálního projektu (`harness/changes.py:182`). Rollback sleduje zapisovací nástroje; obecné změny provedené shell příkazem nejsou automaticky kompletně zachycené. Neoznačovat takový bod v UI jako úplnou zálohu projektu.

Dotažení znamená pojmenované body obnovy nad vybranými pracovními soubory a jasný rozsah, nikoliv kopírování node_modules nebo modelů. Před vrácením porovnat současný stav s evidovaným výsledkem, aby pozdější ruční změna nezmizela. Po částečném selhání nevydávat celý rollback za dokončený. Uživatel má vidět co bylo obnoveno a co zůstalo.

Přidat export/import celého osobního projektu: chaty, přílohy, projektová paměť, vlastní skilly a výsledky, s přemapováním cest na druhém PC. Runtime/modelová záloha už existuje a plní jinou úlohu; není potřeba ji znovu navrhovat ani měnit její online-first pořadí.

## 5. UX a UI návrh

### Rozložení

- Vlevo: trvale dostupný seznam projektů a chatů, Nový chat, vyhledávání. Projektové operace patří do menu projektu; přejmenování do názvu chatu potvrzovaného Enterem. Chaty nejsou schované pod obecným nastavením.
- Nahoře nad konverzací: název chatu, projekt a pracovní režim. Stav uloženého chatu a informace o běhu patří k dané konverzaci.
- Uprostřed: čitelná odpověď na klidném povrchu. Uživatelská zpráva jen jemně barevně odlišená, odpověď bez vnořených rámečků. Průběžné komentáře zůstávají v časové posloupnosti.
- Vpravo: zavíratelný panel Výsledky / Průběh / Kontext; Výzkum přidá Zdroje. V Diskuzi je ve výchozím stavu zavřený. Panel se automaticky nepřepíná při každém kroku modelu.
- Dole: jednotný composer s přílohami, dostupným Thinking, odesláním a STOP. Rozšíření textového pole podle potřeby. Při práci je jasné, zda posílám upřesnění, nebo další úlohu do fronty.
- Nastavení: samostatný dialog s kategoriemi Model a zařízení / Chování / Paměť a skilly / Data a zálohy / Vzhled a jazyk / Nápověda. Běžná práce se odehrává mimo tento dialog.

Server má nahoře malý stavový indikátor; po kliknutí se otevře kompaktní start/stop/restart a výběr modelu. GPU, KV a podrobná diagnostika patří do nastavení. Myšlení je naopak blízko promptu, protože se mění mezi otázkami.

### Vizuální pravidla

Ponechat grafitové povrchy, zelené nadpisy/akcent a ikony. Zelená = připraveno/potvrzená akce, tlumená červená = zastavení/chyba, tyrkysová = běžná interakce, jantarová = čekání či neúplný stav. Vše musí být srozumitelné i bez barvy. Ovládání má konzistentní výšku 30–36 px, větší plochy pro dotyk; související řádky oddělují jemné linky a mezery, nikoli další obdélník uvnitř obdélníku.

Na menší šířce se pravý panel otevírá jako zásuvka; u velmi úzkého okna je dostupná vždy jedna pracovní plocha se zachovaným composerem. Ověřit Windows škálování 100/125/150/200 %, dlouhé české názvy, rozlišení 1366 × 768, 1440 × 900 i široký monitor.

Viditelné UI nemá být katalogem klávesových zkratek. Slash autocomplete může zůstat; stejné důležité akce mají být dostupné i myší. Větev přesunout do menu chatu, Retry k odpovědi. Rozlišit Vrátit zprávu a Vrátit změny v projektu.

## 6. Instalace a ověřování vydání

`requirements.txt` obsahuje pouze dolní hranice, například `gradio>=5.0`. Instalace téže verze aplikace v různý den tedy nemusí dostat stejné prostředí. Zafixovat ověřené verze do instalačního lock souboru pro Windows/Python 3.12. Aktualizace knihoven budou vědomým testovaným krokem vydání. V instalačním i webovém diagnostickém přehledu ověřit Python, runtime, skutečně dostupné modely, schopnost vision/tool calling a cestu k záloze.

Modely v configu mají různé schopnosti a template chování; sjednotit jejich deklaraci do malých modelových profilů a před vydáním provést kontraktové testy textu, thinking, tool calls, vision a STOP. Nenahrazovat skutečné ověření pouze jménem modelu.

K 366 současným kontrolám přidat několik rozhodujících scénářů celé aplikace: long-running chat po reloadu, čistý reasoning STOP, po sobě jdoucí steering, rychlé přepínání projektů, export a otevření výsledku, čtení pozdější stránky/listu, obnovení bodu, instalace s nedostupným online modelem a fallbackem. Vedle deterministických testů mít malou opakovatelnou sadu reálných úloh pro hlavní Qwen: oprava závady, malá aplikace s testem, dokument s revizí, výzkum s rozpornými podklady.

Měřit čas do první užitečné informace, latenci STOP, čas opakovaného hledání, odezvu UI a zachování požadavků po kompresi. Cíle dohodnout nad prvním měřením; nenahrazovat měření slibem procentního zrychlení.

## Pořadí realizace

| Etapa | Obsah | Proč v tomto pořadí | Podmínka dokončení |
|---|---|---|---|
| 1 | STOP, watchdog, identita běhu, projektové přepínání, nastavení pro příští request | Odstranit ztrátu kontroly a promíchání stavu | Přerušení a navigace projdou scénáři všech fází |
| 2 | Průběžně uložené události, reconnect, drafty, fronta a inkrementální indexy | Dlouhá práce přežije výpadek a UI nezatěžuje pozadí | Žádné duplikované akce či zmizelé potvrzené zprávy |
| 3 | Pracovní shell podle vizualizace, výsledky a náhledy, nastavení | Uživatel najde výsledek i ovládání bez hledání v sidebaru | Klíčové workflow ověřeno v Edge i WebView2 při Windows škálování |
| 4 | Komprese podle režimu, dokumentové rozsahy, citace, dohledání historie | Posun kvality dlouhého výzkumu, psaní a vývoje | Model dohledá požadovaný podklad i po kompresi |
| 5 | Body obnovy, přenos projektů, reprodukovatelné instalace a eval úlohy | Dotažené každodenní používání a vydávání | Nový počítač obnoví projekt i model z připravených dat |

Nejde o všechno-nebo-nic přepis. Po každé etapě vznikne samostatně použitelná a ověřená verze. Nejdříve bych implementoval etapu 1; vizualizace určuje směr třetí etapy a umožňuje upravit ergonomii ještě před produkčním vývojem.

## Externí podklady k architektonické volbě

- [Gradio: Custom CSS and JS](https://gradio.app/4.44.1/guides/custom-CSS-and-JS): upozornění na nestabilitu selektorů interního DOM. Verze průvodce je 4.44.1; použito pouze pro obecný kontrakt, ne jako popis nainstalovaného API.
- [Gradio: Server mode](https://gradio.app/guides/server-mode): možnost vlastního frontendu nad odděleným backendem; dostupnost v konkrétní instalační verzi je nutné ověřit při prototypu.
- [llama.cpp server README](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md): streamování a timing/progress možnosti. Konkrétní podporované položky ověřit na používaném buildu.

Zdrojové nálezy jsou odvozené z místního kódu v uvedeném stavu, nikoli z obecných marketingových seznamů funkcí. Čísla řádků se po dalších úpravách mohou posunout.
